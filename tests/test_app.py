import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer

os.environ['INTEL_DATA_DIR'] = tempfile.mkdtemp(prefix='intel-test-')
spec = importlib.util.spec_from_file_location('app', Path(__file__).resolve().parents[1] / 'app.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class WorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.init()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, path, data=None):
        request = urllib.request.Request(self.base+path, data=None if data is None else json.dumps(data).encode(), headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def test_feed_collect_deduplicate_save_and_persist(self):
        feed = f'<rss><channel><item><title>Test Bio 融资</title><link>https://example.org/news/1</link><pubDate>{app.now()}</pubDate></item><item><title>Invalid</title><link>javascript:alert(1)</link></item></channel></rss>'.encode()
        rows = app.parse_feed(feed, 'TEST fixture')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][6], '融资与交易')
        with patch.object(app, 'fetch', return_value=rows):
            self.assertTrue(app.collect())
            self.assertTrue(app.collect())
        state = self.request('/api/state')
        self.assertEqual(len(state['articles']), 1)
        self.assertEqual(state['runs'][0]['added'], 0)
        article_id = state['articles'][0]['id']
        self.request('/api/save', {'id':article_id,'saved':True})
        self.request('/api/company', {'name':'Test Bio'})
        self.request('/api/settings', {'daily_time':'09:15'})
        app.init()
        state = self.request('/api/state')
        self.assertEqual(state['articles'][0]['saved'], 1)
        self.assertIn('Test Bio', state['companies'])
        self.assertEqual(state['settings']['daily_time'], '09:15')
        self.request('/api/company', {'name':'Test Bio','remove':True})
        self.assertNotIn('Test Bio', self.request('/api/state')['companies'])

    def test_missing_date_not_fabricated(self):
        rows = app.parse_feed(b'<rss><channel><item><title>Headline</title><link>https://example.org/no-date</link></item></channel></rss>', 'fixture')
        self.assertIsNone(rows[0][4])

    def test_nested_feed_title_and_ambiguous_date(self):
        rows = app.parse_feed(b'<rss><channel><item><title><a href="/news">Biotech funding</a></title><link>https://example.org/nested</link><pubDate>Oct 25, 2026 12:03pm</pubDate></item></channel></rss>', 'fixture')
        self.assertEqual(rows[0][1], 'Biotech funding')
        self.assertEqual(rows[0][6], '融资与交易')
        self.assertIsNone(rows[0][4])

    def test_calendar_month_window_and_history_dates(self):
        self.assertEqual(app.coverage_start(datetime(2026,5,31,tzinfo=app.TZ)).date().isoformat(), '2026-02-28')
        self.assertEqual(app.coverage_start(datetime(2026,1,31,tzinfo=app.TZ)).date().isoformat(), '2025-10-31')
        current = datetime.now(app.TZ)
        for days, included in [(60,True),(110,False),(-2,False)]:
            stamp=(current-timedelta(days=days)).isoformat()
            feed=f'<rss><channel><item><title>History</title><link>https://example.org/history</link><pubDate>{stamp}</pubDate></item></channel></rss>'.encode()
            self.assertEqual(bool(app.parse_feed(feed,'fixture')), included)

    def test_publisher_identity_is_not_search_query(self):
        stamp=app.now()
        feed=f'<rss><channel><item><title>Public article</title><link>https://example.org/article</link><pubDate>{stamp}</pubDate><source url="https://mp.weixin.qq.com">测试公众号</source></item></channel></rss>'.encode()
        row=app.parse_feed(feed,'公众号公开检索')[0]
        self.assertEqual(row[9:11],('测试公众号','wechat'))
        self.assertEqual(app.publisher_type('普通新闻','https://example.org','微信公众号公开索引'),'news')
        self.assertEqual(app.publisher_type('FDA','https://www.fda.gov','fixture'),'official')
        invalid='<rss><channel><item><title> - 微信公众平台</title><link>https://example.org/empty</link></item></channel></rss>'.encode()
        self.assertEqual(app.parse_feed(invalid,'fixture'),[])

    def test_backfill_has_historical_and_incremental_windows(self):
        ref=datetime(2026,10,26,tzinfo=app.TZ)
        backfill=app.build_sources(True,ref)
        incremental=app.build_sources(False,ref)
        self.assertGreater(len(backfill),len(incremental))
        urls=[url for name,url in backfill]
        self.assertTrue(any('2026-07-25' in url for url in urls))

    def test_failure_is_reported_and_invalid_settings_rejected(self):
        with patch.object(app, 'fetch', side_effect=OSError('fixture network failure')):
            app.collect()
        run = self.request('/api/state')['runs'][0]
        self.assertTrue(all(not r['ok'] for r in run['results']))
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/settings', {'daily_time':'28:00'})
        self.assertEqual(error.exception.code, 400)

    def test_shell_and_assets(self):
        for path in ['/', '/app.js', '/style.css']:
            with urllib.request.urlopen(self.base+path) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(response.read())


if __name__ == '__main__':
    unittest.main()
