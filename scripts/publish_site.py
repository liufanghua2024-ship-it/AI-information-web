"""Build a static Pages site with a daily RSS data snapshot. No server required."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--collect', action='store_true')
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
        app.collect()
    with app.db() as c:
        articles = [dict(r) for r in c.execute('SELECT * FROM articles ORDER BY COALESCE(published_at,collected_at) DESC')]
        runs = [dict(r) for r in c.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 20')]
    for r in runs:
        r['results'] = json.loads(r['results'])
    combined = {a['url']: a for a in (previous or {}).get('articles', [])}
    combined.update({a['url']: a for a in articles})
    articles = sorted(combined.values(), key=lambda a: a.get('published_at') or a['collected_at'], reverse=True)[:1000]
    # Never export a local user's private settings, watch list or saved flags.
    for a in articles:
        a['saved'] = 0
    data = {'articles':articles, 'runs':(runs + (previous or {}).get('runs', []))[:20],
            'companies':[], 'settings':{'daily_time':'08:00'}, 'collecting':False,
            'timezone':'Asia/Shanghai', 'server_time':app.now(), 'hosting':'github-pages'}
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
