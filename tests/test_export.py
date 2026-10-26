import sys
from pathlib import Path
import unittest
from datetime import datetime, timezone

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'scripts'))
from publish_site import merge_articles


class SnapshotTest(unittest.TestCase):
    def test_history_survives_incremental_and_unknown_dates_remain_unknown(self):
        start=datetime(2026,7,26,tzinfo=timezone.utc)
        end=datetime(2026,10,26,tzinfo=timezone.utc)
        def row(url,published):
            return {'url':url,'published_at':published,'collected_at':'2026-10-25T00:00:00+00:00'}
        old=[row('history','2026-08-01T00:00:00+00:00'),row('expired','2026-06-01T00:00:00+00:00'),row('unknown',None)]
        new=[row('history',None),row('recent','2026-10-25T00:00:00+00:00')]
        merged,truncated=merge_articles(old,new,start,end)
        self.assertFalse(truncated)
        indexed={a['url']:a for a in merged}
        self.assertEqual(set(indexed),{'history','unknown','recent'})
        self.assertEqual(indexed['history']['published_at'],'2026-08-01T00:00:00+00:00')
        self.assertIsNone(indexed['unknown']['published_at'])
        limited,truncated=merge_articles(old,new,start,end,limit=1)
        self.assertTrue(truncated)
        self.assertEqual(len(limited),1)
