"""Local healthcare recruiting intelligence prototype. Python 3.11+, no packages."""
import argparse
import calendar
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
    ('Fierce Biotech', 'https://www.fiercebiotech.com/rss/xml'),
    ('Fierce Pharma', 'https://www.fiercepharma.com/rss/xml'),
    ('STAT News', 'https://www.statnews.com/feed/'),
    ('BioPharma Dive', 'https://www.biopharmadive.com/feeds/news/'),
    ('Endpoints News', 'https://endpts.com/feed/'),
]
SEARCH_TRACKS = [
    ('创新药与生物技术', '(创新药 OR 生物医药 OR 生物技术)'),
    ('医疗器械与诊断', '(医疗器械 OR 体外诊断 OR 医疗影像)'),
    ('融资并购与授权', '(医药 OR 医疗) (融资 OR 并购 OR 授权 OR 收购)'),
    ('高管与组织变化', '(医药 OR 医疗器械 OR 生物科技) (任命 OR 高管 OR 裁员 OR 重组)'),
    ('临床与商业化', '(新药 OR 创新药) (临床 OR 获批 OR 上市 OR 商业化)'),
    ('CXO与生命科学工具', '(CXO OR CDMO OR CRO OR 生命科学工具) (医药 OR 生物)'),
    ('医疗服务与数字医疗', '(数字医疗 OR 民营医院 OR 消费医疗 OR 医疗AI)'),
    ('行业媒体·研发', '("医药魔方" OR "Insight数据库" OR "药渡")'),
    ('行业媒体·市场', '("赛柏蓝" OR "医药经济报" OR "E药经理人")'),
    ('行业媒体·创新', '("动脉网" OR "生物探索" OR "器械之家")'),
    ('企业与人才公众号线索', '("医药代表" OR "药明康德" OR "医药观澜")'),
    ('国内官方监管', '(site:nmpa.gov.cn OR site:cde.org.cn OR site:nhsa.gov.cn OR site:nhc.gov.cn OR site:chinadrugtrials.org.cn) (医药 OR 医疗器械 OR 药品 OR 临床)'),
    ('国际官方监管', '(site:fda.gov OR site:ema.europa.eu OR site:clinicaltrials.gov OR site:ich.org) (drug OR biotech OR clinical)'),
    ('数据库公开资讯·研发', '("摩熵医药" OR "药融云" OR "药智网" OR "医药魔方" OR "药渡")'),
    ('数据库公开资讯·市场', '("米内网" OR "IQVIA" OR "PDB" OR "Wind") (医药 OR 药品 OR healthcare)'),
    ('文献与临床公开线索', '("PubMed" OR "Cortellis" OR "Medtracker") (drug OR 临床 OR 医药)'),
    ('行业媒体·深度与技术', '("深蓝观" OR "生物谷" OR "同写意" OR "医药观澜")'),
    ('国际行业资讯', '(site:statnews.com OR site:endpts.com OR site:biopharmadive.com) (biotech OR pharma OR drug)'),
    ('公众号·深度与商业化', '("医药笔记" OR "医药云端工作室" OR "丁香园" OR "肿瘤时间") (医药 OR 医疗 OR 药品)'),
    ('券商医药公开研报', '("中金公司" OR "中信证券" OR "国泰君安" OR "国泰海通" OR "广发证券" OR "慧博") (医药 OR 医疗器械) (研报 OR 报告)'),
    ('咨询机构公开报告', '("IQVIA" OR "BCG" OR "LEK" OR "艾意凯") (医药 OR healthcare) (报告 OR 白皮书 OR report)'),
    ('微信公众号公开索引', 'site:mp.weixin.qq.com (医药 OR 医疗器械 OR 生物医药)'),
]
SOURCE_CATALOG = [
    {'group':'官方监管与公示', 'names':['NMPA','CDE','国家医保局','国家卫健委','药物临床试验登记与信息公示平台','FDA / Drugs@FDA / 橙皮书','EMA / EPAR','ClinicalTrials.gov','ICH'], 'method':'官方域名公开索引检索；不是审批或试验数据库的完整同步。'},
    {'group':'专业数据库与文献', 'names':['摩熵医药（药融云）','药智网','药渡','医药魔方','米内网','PDB','IQVIA','Wind 医药库','PubMed','Cortellis','Medtracker'], 'method':'检索公开资讯、引用与报告；不访问付费数据库或订阅内容，不代表已接入其数据接口。'},
    {'group':'行业媒体与资讯', 'names':['赛柏蓝','E药经理人','医药经济报','深蓝观','医药观澜','生物谷','同写意','动脉网','Insight数据库','STAT News','Endpoints News','Fierce Biotech','Fierce Pharma','BioPharma Dive'], 'method':'公开 RSS 与分段新闻索引检索；每个来源的实际成功与失败见下方。'},
    {'group':'微信公众号、券商与咨询报告', 'names':['医药笔记','医药云端工作室','医药代表','器械之家','丁香园 / 肿瘤时间','药明康德','中金公司医药组','中信证券医药组','国泰君安 / 国泰海通医药组','广发证券医药组','慧博','IQVIA','BCG','LEK（艾意凯）'], 'method':'按账号或机构名称检索公开文章及转载，并检索 mp.weixin.qq.com 公开索引；无授权的付费报告不采集。名称匹配不能证明文章来自对应公众号。'},
]


def coverage_start(reference=None):
    current = reference or datetime.now(TZ)
    month_index = current.year * 12 + current.month - 1 - 3
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return current.replace(year=year, month=month, day=min(current.day, calendar.monthrange(year, month)[1]), hour=0, minute=0, second=0, microsecond=0)


def build_sources(backfill=False, reference=None):
    current = reference or datetime.now(TZ)
    start = coverage_start(current).date() if backfill else (current - timedelta(days=7)).date()
    end = current.date() + timedelta(days=1)
    sources = list(SOURCES)
    while start < end:
        stop = min(start + timedelta(days=7), end)
        for label, query in SEARCH_TRACKS:
            # Overlap one day at search boundaries; URL dedup removes repeated hits.
            search = f'{query} after:{start - timedelta(days=1)} before:{stop}'
            url = 'https://news.google.com/rss/search?' + urllib.parse.urlencode({'q':search, 'hl':'zh-CN', 'gl':'CN', 'ceid':'CN:zh-Hans'})
            sources.append((f'{label} | {start}—{stop}', url))
        start = stop
    return sources


def publisher_type(publisher, publisher_url, source):
    host = urllib.parse.urlsplit(publisher_url).hostname or ''
    if host == 'mp.weixin.qq.com':
        return 'wechat'
    if host.endswith('.gov.cn') or host in ('fda.gov','www.fda.gov','ema.europa.eu','www.ema.europa.eu','clinicaltrials.gov','ich.org','www.ich.org','www.cde.org.cn','cde.org.cn','chinadrugtrials.org.cn','www.chinadrugtrials.org.cn'):
        return 'official'
    if source.startswith(('Fierce','STAT News','Endpoints News','BioPharma Dive')) or any(k in publisher.lower() for k in ['fierce','stat news','endpoints','biopharma dive']):
        return 'international'
    if any(k in publisher for k in ['摩熵','药融云','药智','药渡','医药魔方','米内','IQVIA','Cortellis','Medtracker']):
        return 'database'
    if any(k in publisher for k in ['中金','中信证券','国泰君安','国泰海通','广发证券','慧博','BCG','波士顿咨询','艾意凯']):
        return 'report'
    if any(k in publisher for k in ['Insight','赛柏蓝','医药经济','E药','动脉','生物探索','器械之家','医药代表','药明康德','医药观澜','深蓝观','生物谷','同写意','医药笔记','医药云端','丁香园','肿瘤时间']):
        return 'industry'
    return 'news'
RULES = [
    ('融资与交易', ['融资', '收购', '并购', '授权', 'funding', 'acqui', 'deal', 'licens'], '商务拓展 / 财务 / 整合管理', '核实交易阶段、资金用途及团队建设计划。'),
    ('产品与研发', ['临床', '获批', '批准', 'trial', 'approval', 'fda', 'phase'], '临床运营 / 注册 / 医学事务', '核实产品阶段及临床、注册或商业化团队配置。'),
    ('组织与人事', ['任命', '离职', '裁员', '重组', 'ceo', 'appoint', 'layoff', 'restructur'], '高管 / 组织管理 / 相关职能', '核实组织变化和职责调整，不推断个人求职意愿。'),
    ('业务扩张', ['扩建', '工厂', '出海', '总部', 'expan', 'facility', 'manufactur'], '生产 / 质量 / 海外业务', '核实落地时间、地点和关键岗位配置。'),
    ('政策与监管', ['医保', '集采', '监管', '政策', 'regulat', 'policy'], '市场准入 / 合规 / 注册', '确认政策适用范围及企业能力缺口。'),
    ('公开招聘', ['招聘', 'hiring', 'vacancy', 'career'], '以招聘原文为准', '打开原文确认雇主、岗位发布日期及是否仍开放。'),
]
COLLECT_LOCK = threading.Lock()


class FeedRows(list):
    at_search_limit = False


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
        columns = {r[1] for r in c.execute('PRAGMA table_info(articles)')}
        for name, definition in [('publisher', "TEXT NOT NULL DEFAULT ''"), ('source_type', "TEXT NOT NULL DEFAULT 'news'")]:
            if name not in columns:
                c.execute(f'ALTER TABLE articles ADD COLUMN {name} {definition}')


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
    rows = FeedRows()
    rows.at_search_limit = len(entries) >= 100
    for item in entries[:100]:
        title_element = item.find('title')
        if title_element is None:
            title_element = item.find('{http://www.w3.org/2005/Atom}title')
        title = ''.join(title_element.itertext()).strip() if title_element is not None else ''
        if re.fullmatch(r'[-–—\s]*(?:微信公众平台)?', title):
            continue
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
        if published and (datetime.fromisoformat(published) < coverage_start() or datetime.fromisoformat(published) > datetime.now(TZ)):
            continue
        category, roles, action = classify(title)
        publisher_element = item.find('source')
        publisher = ''.join(publisher_element.itertext()).strip() if publisher_element is not None else source.split(' | ')[0]
        publisher_url = publisher_element.get('url', '') if publisher_element is not None else link
        source_type = publisher_type(publisher, publisher_url, source)
        rows.append((hashlib.sha256(link.encode()).hexdigest()[:24], title, link, source, published, now(), category, roles, action, publisher, source_type))
    return rows


def fetch(source):
    name, url = source
    request = urllib.request.Request(url, headers={'User-Agent': 'HealthcareRecruitingIntel/0.1 RSS Reader'})
    with urllib.request.urlopen(request, timeout=18) as response:
        payload = response.read(2_000_001)
        if len(payload) > 2_000_000:
            raise ValueError('订阅内容超过 2 MB 限制')
    return parse_feed(payload, name)


def collect(backfill=False):
    if not COLLECT_LOCK.acquire(blocking=False):
        return False
    try:
        with db() as c:
            run_id = c.execute('INSERT INTO runs (started_at) VALUES (?)', (now(),)).lastrowid
        results, added = [], 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            tasks = {pool.submit(fetch, source): source[0] for source in build_sources(backfill)}
            for task in concurrent.futures.as_completed(tasks):
                name = tasks[task]
                try:
                    rows = task.result()
                    with db() as c:
                        before = c.execute('SELECT COUNT(*) FROM articles').fetchone()[0]
                        c.executemany('''INSERT INTO articles (id,title,url,source,published_at,collected_at,category,roles,action,publisher,source_type)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET
                          publisher=excluded.publisher, source_type=excluded.source_type,
                          published_at=COALESCE(excluded.published_at,articles.published_at)''', rows)
                        count = c.execute('SELECT COUNT(*) FROM articles').fetchone()[0] - before
                    added += count
                    results.append({'source': name, 'ok': True, 'found': len(rows), 'added': count, 'at_search_limit':getattr(rows,'at_search_limit',len(rows)>=100)})
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
                articles = [dict(r) for r in c.execute('SELECT * FROM articles ORDER BY COALESCE(published_at,collected_at) DESC')]
                companies = [r[0] for r in c.execute('SELECT name FROM companies ORDER BY created_at DESC')]
                runs = [dict(r) for r in c.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 20')]
                settings = dict(c.execute('SELECT key,value FROM settings'))
            for run in runs:
                run['results'] = json.loads(run['results'])
            self.send_json({'articles': articles, 'companies': companies, 'runs': runs, 'settings': settings,
                            'collecting': COLLECT_LOCK.locked(), 'timezone': 'Asia/Shanghai', 'server_time': now(), 'coverage_start':coverage_start().isoformat(), 'source_catalog':SOURCE_CATALOG,
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
    parser.add_argument('--backfill', action='store_true', help='Search the last three calendar months in weekly windows')
    parser.add_argument('--no-scheduler', action='store_true')
    args = parser.parse_args()
    init()
    if args.collect_once:
        collect(backfill=args.backfill)
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
