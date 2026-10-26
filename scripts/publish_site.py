"""Build a static Pages site with a daily RSS data snapshot. No server required."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


def merge_articles(previous, current, start, end, limit=20000):
    combined = {a['url']:dict(a) for a in previous}
    for article in current:
        old = combined.get(article['url'], {})
        merged = {**old, **article}
        if not article.get('published_at'):
            merged['published_at'] = old.get('published_at')
        if old.get('collected_at'):
            merged['collected_at'] = min(old['collected_at'], article['collected_at'])
        combined[article['url']] = merged
    retained = [a for a in combined.values() if not a.get('published_at') or start <= datetime.fromisoformat(a['published_at']) <= end]
    return sorted(retained, key=lambda a:a.get('published_at') or a['collected_at'], reverse=True)[:limit], len(retained)>limit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--collect', action='store_true')
    parser.add_argument('--backfill', action='store_true')
    parser.add_argument('--previous-url')
    args = parser.parse_args()
    app.init()
    previous = None
    if args.previous_url:
        try:
            with urllib.request.urlopen(args.previous_url, timeout=20) as response:
                previous = json.load(response)
        except Exception as exc:
            print('Previous public snapshot unavailable:', type(exc).__name__)
    if args.collect:
        backfill = args.backfill or (previous or {}).get('coverage', {}).get('version') != 2 or datetime.now(app.TZ).weekday() == 6
        print('Collection mode:', 'three-month weekly backfill' if backfill else 'seven-day incremental')
        app.collect(backfill=backfill)
    with app.db() as c:
        articles = [dict(r) for r in c.execute('SELECT * FROM articles ORDER BY COALESCE(published_at,collected_at) DESC')]
        runs = [dict(r) for r in c.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 20')]
    for r in runs:
        r['results'] = json.loads(r['results'])
    start = app.coverage_start()
    end = datetime.now(app.TZ)
    articles, truncated = merge_articles((previous or {}).get('articles', []), articles, start, end)
    # Never export a local user's private settings, watch list or saved flags.
    for a in articles:
        a['saved'] = 0
    data = {'articles':articles, 'runs':(runs + (previous or {}).get('runs', []))[:20],
            'companies':[], 'settings':{'daily_time':'08:00'}, 'collecting':False,
            'timezone':'Asia/Shanghai', 'server_time':app.now(), 'hosting':'github-pages', 'coverage_start':start.isoformat(), 'source_catalog':app.SOURCE_CATALOG,
            'coverage':{'version':2, 'start':start.isoformat(), 'end':app.now(), 'dated':sum(bool(a.get('published_at')) for a in articles),
                        'undated':sum(not a.get('published_at') for a in articles), 'by_type':dict(Counter(a.get('source_type','news') for a in articles)),
                        'truncated':truncated, 'limit':20000, 'wechat_note':'仅覆盖公开搜索可索引的公众号文章，不代表微信全量；缺失有效标题的结果不收录。'}}
    output = app.ROOT / '.local' / 'site'
    output.mkdir(parents=True, exist_ok=True)
    for name in ['style.css', 'app.js']:
        shutil.copyfile(app.ROOT/'static'/name, output/name)
    html = (app.ROOT/'static'/'index.html').read_text()
    html = html.replace('href="/style.css"','href="./style.css"').replace('src="/app.js"','src="./app.js"').replace('href="/"','href="./"')
    html = html.replace('<script defer src="./app.js">','<script defer src="./pages.js"></script><script defer src="./app.js">')
    html = html.replace('服务运行期间自动执行；启动时补采当天已错过的任务。','GitHub 每日定时更新，可能延迟；个人收藏和企业关注仅保存在当前浏览器。')
    html = html.replace('<form id="schedule-form">','<form id="schedule-form" hidden>')
    html = html.replace('公开 RSS 原型 ·','GitHub Pages 公开资讯版 ·')
    (output/'index.html').write_text(html)
    shutil.copyfile(app.ROOT/'scripts'/'pages.js', output/'pages.js')
    (output/'data.json').write_text(json.dumps(data,ensure_ascii=False))
    (output/'.nojekyll').touch()
    print(f'Built static site: {len(articles)} articles; destination {output}')


if __name__ == '__main__':
    main()
