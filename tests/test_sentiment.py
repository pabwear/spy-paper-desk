"""The hourly reader's history: readings and plays recorded, plays scored against SPY's real move."""

from __future__ import annotations

from datetime import timedelta

from helpers import THURSDAY, DeskTestCase, at, falling_bars, rising_bars

import cloud_state
import run_study
import sentiment
from common import load_json

READINGS = {
    "as_of": "2026-10-01T14:05:00Z", "spy_price": 600.0, "spy_change_pct": 0.4, "vix": 15.8, "fear_greed": 52,
    "stocktwits": {"SPY": {"score": 64, "bullish_pct": 58}},
    "reddit": {"score": 0.3, "posts": 40, "top": ["calls printing"]},
    "news": {"score": -0.1, "headlines": ["Fed speaker at 2 PM"]},
    "rumors": [{"text": "big buyer at 605", "source": "reddit", "tickers": ["SPY"], "direction": "bullish", "credibility": 0.4}],
    "plays": [{"ticker": "SPY", "direction": "up", "horizon": "1h", "confidence": 0.6, "reason": "momentum + buyer rumor"},
              {"ticker": "NVDA", "direction": "down", "horizon": "close", "confidence": 0.5, "reason": "earnings leak talk"},
              {"ticker": "SPY", "direction": "sideways"}],
    "whats_new": "Reddit turned bullish since the last hour.",
}


class RecordTests(DeskTestCase):
    def test_reading_and_plays_are_kept(self):
        line = sentiment.record(at(THURSDAY, 10, 5), READINGS)
        self.assertEqual((line["stocktwits"], line["reddit"], line["news"], line["fear_greed"]), (64.0, 0.3, -0.1, 52.0))
        self.assertEqual(line["rumors"][0]["direction"], "up")
        plays = sentiment._read(sentiment.PLAYS)
        self.assertEqual([(p["ticker"], p["status"]) for p in plays], [("SPY", "open"), ("NVDA", "unscored")])  # no direction → dropped
        self.assertEqual(plays[0]["ref_price"], 600.0)
        self.assertEqual(sentiment.series()[0]["reddit"], 0.3)
        self.assertEqual(sentiment.latest_brief()["rumors"], ["big buyer at 605"])

    def test_bad_values_are_clamped_or_dropped(self):
        line = sentiment.record(at(THURSDAY, 10, 5), {"reddit": {"score": 7}, "fear_greed": "n/a", "plays": ["junk"]})
        self.assertEqual((line["reddit"], line["fear_greed"]), (1.0, None))
        self.assertEqual(sentiment._read(sentiment.PLAYS), [])

    def test_files_travel_with_the_desk_state(self):
        self.assertIn("sentiment.jsonl", cloud_state.STATE_FILES)
        self.assertIn("plays.jsonl", cloud_state.STATE_FILES)

    def test_pulse_form_records_it(self):
        run_study.cmd_pulse_ingest(at(THURSDAY, 10, 5), READINGS)
        self.assertEqual(len(sentiment._read(sentiment.LOG)), 1)
        state = load_json("dashboard_state.json")
        self.assertEqual(state["sentiment"]["latest"]["whats_new"], READINGS["whats_new"])
        self.assertEqual(state["sentiment"]["plays"]["open"], 1)


class DueTests(DeskTestCase):
    def test_horizons(self):
        t = at(THURSDAY, 10, 5)
        self.assertEqual(sentiment.due(t, "1h"), at(THURSDAY, 11, 5))
        self.assertEqual(sentiment.due(at(THURSDAY, 15, 30), "1h"), at(THURSDAY, 16, 0))
        self.assertEqual(sentiment.due(t, "close"), at(THURSDAY, 16, 0))
        self.assertEqual(sentiment.due(t, "1d"), at(THURSDAY + timedelta(days=1), 16, 0))
        self.assertEqual(sentiment.due(at(THURSDAY, 8, 0), "1h"), at(THURSDAY, 10, 30))  # before the open: from 09:30
        friday_eve = at(THURSDAY + timedelta(days=1), 17, 0)
        self.assertEqual(sentiment.due(friday_eve, "close"), at(THURSDAY + timedelta(days=4), 16, 0))  # Monday


class ScoreTests(DeskTestCase):
    def test_right_and_wrong(self):
        sentiment.record(at(THURSDAY, 10, 5), READINGS)
        now = at(THURSDAY, 11, 30)
        self.assertEqual(sentiment.score(at(THURSDAY, 10, 30), rising_bars(THURSDAY, now)), 0)  # not due yet
        self.assertEqual(sentiment.score(now, rising_bars(THURSDAY, now)), 1)
        p = sentiment._read(sentiment.PLAYS)[0]
        self.assertEqual((p["status"], p["right"]), ("scored", True))
        self.assertGreater(p["move_pct"], 0)
        s = sentiment.plays_summary()
        self.assertEqual((s["all"]["n"], s["all"]["right_pct"], s["by_horizon"]["1h"]["n"]), (1, 100.0, 1))

    def test_wrong_way(self):
        sentiment.record(at(THURSDAY, 10, 5), READINGS)
        now = at(THURSDAY, 11, 30)
        sentiment.score(now, falling_bars(THURSDAY, now))
        self.assertFalse(sentiment._read(sentiment.PLAYS)[0]["right"])

    def test_never_covered_becomes_unscored(self):
        sentiment.record(at(THURSDAY, 10, 5), READINGS)
        sentiment.score(at(THURSDAY + timedelta(days=5), 12, 0), [])
        self.assertEqual(sentiment._read(sentiment.PLAYS)[0]["status"], "unscored")
