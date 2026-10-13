"""Market Pulse from live readings."""

from __future__ import annotations

import unittest

from helpers import THURSDAY, DeskTestCase, FakeBroker, at, falling_bars

import pulse
import run_study
from common import load_json, save_json

READINGS = {"as_of": "2026-10-01T13:00:00Z", "spy_change_pct": 0.62, "fear_greed": 31.2, "vix": 15.8,
            "stocktwits": {"SPY": {"score": 72, "label": "BULLISH"}},
            "sources": {"market_pulse": "stocklake", "sentiment_flow": "stocktwits"}}


class PulseRuleTests(unittest.TestCase):
    def test_mixed_readings_are_neutral(self):
        r = pulse.derive_bias(READINGS)  # +1 SPY up, -1 fear, +1 Stocktwits, VIX change missing
        self.assertEqual((r["bias"], r["net_votes"]), ("neutral", 1))

    def test_agreeing_readings_give_a_bias(self):
        self.assertEqual(pulse.derive_bias({**READINGS, "fear_greed": 60})["bias"], "bullish")
        bear = {"spy_change_pct": -1.0, "vix_change_pct": 8.0, "fear_greed": 20,
                "stocktwits": {"SPY": {"score": 30}}}
        self.assertEqual(pulse.derive_bias(bear)["bias"], "bearish")

    def test_missing_readings_never_vote(self):
        self.assertEqual(pulse.derive_bias({})["bias"], "neutral")


class PulseIngestTests(DeskTestCase):
    def test_ingest_writes_todays_pulse_and_it_gates_options(self):
        now = at(THURSDAY, 9, 20)
        bear = {"spy_change_pct": -1.0, "vix_change_pct": 8.0, "fear_greed": 20, "stocktwits": {"SPY": {"score": 30}}}
        data = run_study.cmd_pulse_ingest(now, bear)
        self.assertEqual((data["date"], data["bias"]), ("2026-10-01", "bearish"))
        self.assertEqual(load_json("market_pulse.json")["net_votes"], -4)
        save_json("aoi_override.json", {"symbol": "SPY", "tradable": True, "written_at": at(THURSDAY, 9, 45).isoformat(),
                                        "approximate": False,
                                        "zones": [{"color": "red", "low": 589.5, "high": 590.5, "confluence": []}]})
        later = at(THURSDAY, 10, 30)
        broker = FakeBroker()
        r = run_study.cmd_paper(later, broker_factory=lambda: broker, bars=falling_bars(THURSDAY, later))
        self.assertEqual(broker.submitted, [])
        self.assertEqual(r["reasons"], ["pulse_contradicts"])

    def test_thresholds_come_from_rules(self):
        rules = load_json("rules.json")
        rules["pulse_rules"]["min_net_votes"] = 1
        save_json("rules.json", rules)
        self.assertEqual(run_study.cmd_pulse_ingest(at(THURSDAY, 9, 20), READINGS)["bias"], "bullish")


class SheetsTests(DeskTestCase):
    def test_push_needs_an_apps_script_url_and_never_raises(self):
        import os
        from unittest import mock

        import sheets_sync

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SHEETS_WEBHOOK_URL", None)
            self.assertFalse(sheets_sync.push()["sent"])
        with mock.patch.dict(os.environ, {"SHEETS_WEBHOOK_URL": "https://evil.example/x"}):
            self.assertFalse(sheets_sync.push()["sent"])
        sent = []
        with mock.patch.dict(os.environ, {"SHEETS_WEBHOOK_URL": "https://script.google.com/macros/s/abc/exec",
                                          "SHEETS_WEBHOOK_TOKEN": "t"}):
            self.assertTrue(sheets_sync.push(post=lambda url, body: sent.append(body) or 200)["sent"])

            def boom(url, body):
                raise OSError("offline")
            self.assertFalse(sheets_sync.push(post=boom)["sent"])
        import json as _json
        payload = _json.loads(sent[0])
        self.assertEqual(payload["token"], "t")
        self.assertEqual(set(payload["tabs"]), {"Trades", "Closed trades", "Daily reviews"})
