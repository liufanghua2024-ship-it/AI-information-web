"""Local healthcare recruiting intelligence prototype. Python 3.11+, no packages."""
import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('INTEL_DATA_DIR', str(ROOT / '.local')))
TZ = timezone(timedelta(hours=8))
SOURCES = [
    ('医药中文资讯 · Google News 聚合', 'https://news.google.com/rss/search?' + urllib.parse.urlencode({'q': '医药 创新药 医疗器械 融资 招聘 when:7d', 'hl': 'zh-CN', 'gl': 'CN', 'ceid': 'CN:zh-Hans'})),
    ('Fierce Biotech', 'https://www.fiercebiotech.com/rss/xml'),
    ('Fierce Pharma', 'https://www.fiercepharma.com/rss/xml'),
]
RULES = [
    ('融资与交易', ['融资', '收购', '并购', '授权', 'funding', 'acqui', 'deal', 'licens'], '商务拓展 / 财务 / 整合管理', '核实交易阶段、资金用途及团队建设计划。'),
    ('产品与研发', ['临床', '获批', '批准', 'trial', 'approval', 'fda', 'phase'], '临床运营 / 注册 / 医学事务', '核实产品阶段及临床、注册或商业化团队配置。'),
    ('组织与人事', ['任命', '离职', '裁员', '重组', 'ceo', 'appoint', 'layoff', 'restructur'], '高管 / 组织管理 / 相关职能', '核实组织变化和职责调整，不推断个人求职意愿。'),
    ('业务扩张', ['扩建', '工厂', '出海', '总部', 'expan', 'facility', 'manufactur'], '生产 / 质量 / 海外业务', '核实落地时间、地点和关键岗位配置。'),
    ('政策与监管', ['医保', '集采', '监管', '政策', 'regulat', 'policy'], '市场准入 / 合规 / 注册', '确认政策适用范围及企业能力缺口。'),
    ('公开招聘', ['招聘', 'hiring', 'vacancy', 'career'], '以招聘原文为准', '打开原文确认雇主、岗位发布日期及是否仍开放。'),
]
COLLECT_LOCK = threading.Lock()


def now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def db():
    conn = sqlite3.connect(DATA / 'intel.sqlite3', timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    DATA.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS articles (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
          source TEXT NOT NULL, published_at TEXT, collected_at TEXT NOT NULL,
          category TEXT NOT NULL, roles TEXT NOT NULL, action TEXT NOT NULL,
          saved INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS companies (name TEXT PRIMARY KEY, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
          added INTEGER DEFAULT 0, results TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT OR IGNORE INTO settings VALUES ('daily_time', '08:00');
        ''')


def classify(title):
    value = title.lower()
    return next(((name, roles, action) for name, keys, roles, action in RULES if any(k in value for k in keys)),
                ('行业动态', '需结合企业阶段判断', '阅读原文，确认事件事实后再判断业务机会。'))


def safe_url(value):
    try:
        u = urllib.parse.urlsplit(value)
        return u.scheme in ('http', 'https') and bool(u.hostname) and not u.username and not u.password
    except ValueError:
        return False


def parse_feed(payload, source):
    root = ET.fromstring(payload)
    entries = root.findall('.//item') or root.findall('{http://www.w3.org/2005/Atom}entry')
    rows = []
    for item in entries[:100]:
        title = (item.findtext('title') or item.findtext('{http://www.w3.org/2005/Atom}title') or '').strip()
        link = item.findtext('link') or ''
        if not link:
            elem = item.find('{http://www.w3.org/2005/Atom}link')
            link = elem.get('href', '') if elem is not None else ''
        link = link.strip()
        if not title or not safe_url(link):
            continue
        raw_date = item.findtext('pubDate') or item.findtext('{http://www.w3.org/2005/Atom}published') or item.findtext('{http://www.w3.org/2005/Atom}updated')
        published = None
        if raw_date:
            try:
                try:
                    dt = parsedate_to_datetime(raw_date)
                except (ValueError, TypeError):
                    dt = datetime.fromisoformat(raw_date.replace('Z', '+00:00'))
                if dt.tzinfo:
                    published = dt.astimezone(TZ).isoformat(timespec='seconds')
            except (ValueError, TypeError, OverflowError):
                pass
        # Keep dates as source claims; never substitute collection time for publication.
        if published and datetime.fromisoformat(published) < datetime.now(TZ) - timedelta(days=7):
            continue
        category, roles, action = classify(title)
        rows.append((hashlib.sha256(link.encode()).hexdigest()[:24], title, link, source, published, now(), category, roles, action))
    return rows


def fetch(source):
    name, url = source
    request = urllib.request.Request(url, headers={'User-Agent': 'HealthcareRecruitingIntel/0.1 RSS Reader'})
    with urllib.request.urlopen(request, timeout=18) as response:
        payload = response.read(2_000_001)
        if len(payload) > 2_000_000:
            raise ValueError('订阅内容超过 2 MB 限制')
    return parse_feed(payload, name)


def collect():
    if not COLLECT_LOCK.acquire(blocking=False):
        return False
    try:
        with db() as c:
            run_id = c.execute('INSERT INTO runs (started_at) VALUES (?)', (now(),)).lastrowid
        results, added = [], 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            tasks = {pool.submit(fetch, source): source[0] for source in SOURCES}
            for task in concurrent.futures.as_completed(tasks):
                name = tasks[task]
                try:
                    rows = task.result()
                    with db() as c:
                        before = c.total_changes
                        c.executemany('INSERT OR IGNORE INTO articles (id,title,url,source,published_at,collected_at,category,roles,action) VALUES (?,?,?,?,?,?,?,?,?)', rows)
                        count = c.total_changes - before
                    added += count
                    results.append({'source': name, 'ok': True, 'found': len(rows), 'added': count})
                except Exception as exc:
                    results.append({'source': name, 'ok': False, 'error': str(exc)[:400]})
        with db() as c:
            c.execute('UPDATE runs SET finished_at=?,added=?,results=? WHERE id=?', (now(), added, json.dumps(results, ensure_ascii=False), run_id))
        return True
    finally:
        COLLECT_LOCK.release()


def scheduler():
    # Persist each day's dispatch claim; restart after scheduled time catches up once.
    while True:
        try:
            current = datetime.now(TZ)
            with db() as c:
                clock = c.execute("SELECT value FROM settings WHERE key='daily_time'").fetchone()[0]
                previous = c.execute("SELECT value FROM settings WHERE key='last_scheduled_day'").fetchone()
                due = current.strftime('%H:%M') >= clock and (not previous or previous[0] != current.date().isoformat())
                if due and not COLLECT_LOCK.locked():
                    c.execute("INSERT OR REPLACE INTO settings VALUES ('last_scheduled_day', ?)", (current.date().isoformat(),))
                else:
                    due = False
            if due:
                collect()
        except Exception as exc:
            print('Scheduler error:', type(exc).__name__, flush=True)
        time.sleep(20)


class Handler(BaseHTTPRequestHandler):
    def send_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == '/api/state':
            with db() as c:
                articles = [dict(r) for r in c.execute('SELECT * FROM articles ORDER BY COALESCE(published_at,collected_at) DESC LIMIT 1000')]
                companies = [r[0] for r in c.execute('SELECT name FROM companies ORDER BY created_at DESC')]
                runs = [dict(r) for r in c.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 20')]
                settings = dict(c.execute('SELECT key,value FROM settings'))
            for run in runs:
                run['results'] = json.loads(run['results'])
            self.send_json({'articles': articles, 'companies': companies, 'runs': runs, 'settings': settings,
                            'collecting': COLLECT_LOCK.locked(), 'timezone': 'Asia/Shanghai', 'server_time': now(),
                            'analysis_mode': '关键词规则筛选；未接入大模型；未验证新闻原文'})
            return
        files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if path not in files:
            self.send_json({'error': '未找到'}, 404)
            return
        filename, content_type = files[path]
        body = (ROOT / 'static' / filename).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        # Local prototype: reject cross-site requests; loopback is the default binding.
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            self.send_json({'error': '禁止跨站请求'}, 403)
            return
        origin = self.headers.get('Origin')
        if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get('Host'):
            self.send_json({'error': '来源不匹配'}, 403)
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= 8192:
                raise ValueError('请求过大')
            value = json.loads(self.rfile.read(size) or b'{}')
            if not isinstance(value, dict):
                raise ValueError('请求格式错误')
            path = urllib.parse.urlsplit(self.path).path
            if path == '/api/collect':
                threading.Thread(target=collect, daemon=True).start()
                self.send_json({'accepted': True}, 202)
                return
            with db() as c:
                if path == '/api/company':
                    name = str(value.get('name', '')).strip()
                    if not name or len(name) > 80:
                        raise ValueError('企业名称需要 1–80 个字符')
                    if value.get('remove'):
                        c.execute('DELETE FROM companies WHERE name=?', (name,))
                    else:
                        c.execute('INSERT OR IGNORE INTO companies VALUES (?,?)', (name, now()))
                elif path == '/api/save':
                    if type(value.get('saved')) is not bool:
                        raise ValueError('收藏状态无效')
                    c.execute('UPDATE articles SET saved=? WHERE id=?', (int(value['saved']), str(value.get('id', ''))))
                elif path == '/api/settings':
                    clock = value.get('daily_time', '')
                    if not isinstance(clock, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', clock):
                        raise ValueError('请输入有效的 24 小时时间')
                    c.execute("UPDATE settings SET value=? WHERE key='daily_time'", (clock,))
                else:
                    self.send_json({'error': '未找到'}, 404)
                    return
            self.send_json({'ok': True})
        except (ValueError, TypeError) as exc:
            self.send_json({'error': str(exc)}, 400)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--collect-once', action='store_true')
    parser.add_argument('--no-scheduler', action='store_true')
    args = parser.parse_args()
    init()
    if args.collect_once:
        collect()
        with db() as c:
            run = dict(c.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 1').fetchone())
        print(json.dumps(run, ensure_ascii=False))
        return 0 if any(r['ok'] for r in json.loads(run['results'])) else 1
    if not args.no_scheduler:
        threading.Thread(target=scheduler, daemon=True).start()
    print(f'Healthcare Intel listening on port {args.port}', flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == '__main__':
    raise SystemExit(main())
