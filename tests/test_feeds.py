"""The desk's own sentiment feed: parsers, timing, and the readings it builds (no network)."""

from __future__ import annotations

import unittest
from datetime import timedelta

from helpers import THURSDAY, DeskTestCase, at, rising_bars

import feeds
import run_study
import sentiment
from common import load_json

ST = {"messages": [{"body": "calls", "entities": {"sentiment": {"basic": "Bullish"}}},
                   {"body": "puts", "entities": {"sentiment": {"basic": "Bearish"}}},
                   {"body": "to the moon", "entities": {"sentiment": {"basic": "Bullish"}}},
                   {"body": "no tag", "entities": {}}]}
RSS = b"""<?xml version="1.0"?><rss><channel><item><title>Stocks rise ahead of Fed minutes</title></item>
<item><title>Oil slips</title></item></channel></rss>"""


class ParserTests(unittest.TestCase):
    def test_stocktwits(self):
        s = feeds.parse_stocktwits(ST)
        self.assertEqual((s["score"], s["tagged"], s["messages"]), (66.7, 3, 4))
        self.assertIsNone(feeds.parse_stocktwits({"messages": []}))

    def test_rss(self):
        self.assertEqual(feeds.parse_rss(RSS), ["Stocks rise ahead of Fed minutes", "Oil slips"])
        self.assertEqual(feeds.parse_rss(b"not xml"), [])


class TimingTests(DeskTestCase):
    def test_due(self):
        now = at(THURSDAY, 11, 0)
        self.assertTrue(feeds.due(now, None))
        own = {"ts": (now - timedelta(minutes=20)).isoformat(), "source": "desk_feeds"}
        self.assertFalse(feeds.due(now, own))
        self.assertTrue(feeds.due(now, {**own, "ts": (now - timedelta(minutes=31)).isoformat()}))
        reader = {"ts": (now - timedelta(minutes=50)).isoformat(), "source": "reader"}
        self.assertFalse(feeds.due(now, reader))  # the AI reader's reading is fresh: leave it
        self.assertTrue(feeds.due(now, {**reader, "ts": (now - timedelta(minutes=75)).isoformat()}))


class CollectTests(DeskTestCase):
    def test_readings_feed_the_pulse_and_history(self):
        now = at(THURSDAY, 11, 0)
        bars = rising_bars(THURSDAY, now)
        r = feeds.collect(now, bars, fetch={"stocktwits": lambda: feeds.parse_stocktwits(ST),
                                            "headlines": lambda: feeds.parse_rss(RSS),
                                            "vix": lambda: {"vix": 16.2, "vix_change_pct": -6.0}})
        self.assertEqual(r["stocktwits"]["SPY"]["score"], 66.7)
        self.assertEqual(r["vix_change_pct"], -6.0)
        self.assertAlmostEqual(r["spy_price"], bars[-1]["c"], places=3)
        run_study.cmd_pulse_ingest(now, r)
        self.assertEqual(load_json("market_pulse.json")["bias"], "bullish")  # stocktwits ≥ 60 and VIX down 6 %
        self.assertEqual(sentiment.latest_full()["source"], "desk_feeds")

    def test_dead_sources_are_just_missing(self):
        r = feeds.collect(at(THURSDAY, 11, 0), None, fetch={"stocktwits": lambda: None, "headlines": list, "vix": lambda: None})
        self.assertNotIn("stocktwits", r)
        self.assertEqual(len(r), 3)  # as_of, source, sources: nothing to send
