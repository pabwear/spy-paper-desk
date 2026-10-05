"""Cloud runs: the heartbeat does what the clock calls for; forms are parsed safely; state round-trips."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

from helpers import SATURDAY, THURSDAY, DeskTestCase, FakeBroker, at, falling_bars

import cloud_state
import journal
import run_study
from common import desk_dir, load_json, save_json


def no_broker():
    raise AssertionError("no broker before 10:00 or on a weekend")


class TickTests(DeskTestCase):
    def test_weekend_and_before_open_do_nothing(self):
        self.assertEqual(run_study.cmd_tick(at(SATURDAY, 11, 0), broker_factory=no_broker, bars=[]), "idle")
        self.assertEqual(run_study.cmd_tick(at(THURSDAY, 9, 0), broker_factory=no_broker, bars=[]), "idle")

    def test_watch_window_only_evaluates(self):
        now = at(THURSDAY, 9, 45)
        self.assertEqual(run_study.cmd_tick(now, broker_factory=no_broker, bars=falling_bars(THURSDAY, now)), "watch")

    def test_trade_window_trades_and_syncs(self):
        save_json("aoi_override.json", {"symbol": "SPY", "tradable": True, "written_at": at(THURSDAY, 9, 45).isoformat(),
                                        "approximate": False,
                                        "zones": [{"color": "red", "low": 589.5, "high": 590.5, "confluence": []}]})
        broker = FakeBroker()
        now = at(THURSDAY, 10, 30)
        self.assertEqual(run_study.cmd_tick(now, broker_factory=lambda: broker, bars=falling_bars(THURSDAY, now)),
                         "trade_window")
        self.assertEqual(len(broker.submitted), 1)
        self.assertTrue(any(e["event"] == "account" for e in journal.read_events()))

    def test_closing_time_exits_only(self):
        self.assertEqual(load_json("risk.json")["flatten_start"], "15:40")
        journal.log("order", now=at(THURSDAY, 10, 5), role="entry", order_id="e1", symbol="SPY261001C00590000",
                    underlying_price=590.0)
        broker = FakeBroker(positions=[{"symbol": "SPY261001C00590000", "qty": 1.0, "avg_entry_price": 2.0}])
        now = at(THURSDAY, 15, 41)
        self.assertEqual(run_study.cmd_tick(now, broker_factory=lambda: broker, bars={}), "flatten_window")
        self.assertEqual([o["intent"] for o in broker.submitted], ["sell_to_close"])

    def test_review_once_after_close(self):
        broker = FakeBroker()
        now = at(THURSDAY, 16, 10)
        self.assertEqual(run_study.cmd_tick(now, broker_factory=lambda: broker, bars={}), "review")
        self.assertEqual(run_study.cmd_tick(at(THURSDAY, 16, 20), broker_factory=no_broker, bars={}), "idle")


class FormTests(DeskTestCase):
    def test_ranges(self):
        self.assertEqual(cloud_state.parse_ranges("571.20-572.05, 578.4 to 579.1"), [(571.2, 572.05), (578.4, 579.1)])
        self.assertEqual(cloud_state.parse_ranges("572.05-571.20"), [(571.2, 572.05)])
        for bad in ("abc", "571", "5-5", "1-2; rm -rf /"):
            with self.assertRaises(ValueError):
                cloud_state.parse_ranges(bad)

    def test_zones_form(self):
        z = cloud_state.zones_from_env({"SYMBOL": "spy", "RED": "589.5-590.5", "GREEN": "", "TAGS": "1:CHoCH"})
        self.assertEqual(z, {"action": "set", "symbol": "SPY", "zones": ["red:589.5:590.5"], "tags": ["1:CHoCH"]})
        self.assertEqual(cloud_state.zones_from_env({"NONE": "true"})["action"], "clear")
        with self.assertRaises(ValueError):
            cloud_state.zones_from_env({"RED": "", "GREEN": ""})
        with self.assertRaises(ValueError):
            cloud_state.zones_from_env({"RED": "1-2", "TAGS": "1:$(whoami)"})

    def test_zones_form_end_to_end_inside_the_window(self):
        with mock.patch.object(cloud_state, "now_et", return_value=at(THURSDAY, 9, 41)), \
                mock.patch.dict(os.environ, {"SYMBOL": "SPY", "RED": "589.5-590.5", "GREEN": "609.5-610.5"}):
            self.assertEqual(cloud_state.main(["x", "zones"]), 0)
        self.assertEqual(len(load_json("aoi_override.json")["zones"]), 2)
        with mock.patch.object(cloud_state, "now_et", return_value=at(THURSDAY, 10, 5)), \
                mock.patch.dict(os.environ, {"SYMBOL": "SPY", "RED": "589.5-590.5"}):
            self.assertEqual(cloud_state.main(["x", "zones"]), 1)  # refused after 09:59

    def test_focus_form(self):
        with mock.patch.dict(os.environ, {"ACTION": "add", "SYMBOL": "nvda"}):
            self.assertEqual(cloud_state.main(["x", "focus"]), 0)
        self.assertEqual(load_json("watchlist.json")["focus"], ["SPY", "NVDA"])
        with mock.patch.dict(os.environ, {"ACTION": "delete-everything", "SYMBOL": "NVDA"}):
            self.assertEqual(cloud_state.main(["x", "focus"]), 1)

    def test_state_round_trip(self):
        journal.log("skip", reasons=["weekend"])
        save_json("aoi_override.NVDA.json", {"symbol": "NVDA"})
        out = Path(tempfile.mkdtemp())
        names = cloud_state.copy_state(desk_dir(), out)
        self.assertIn("journal.jsonl", names)
        self.assertIn("aoi_override.NVDA.json", names)
        self.assertNotIn("rules.json", names)  # config stays on main
        (desk_dir() / "journal.jsonl").unlink()
        cloud_state.copy_state(out, desk_dir())
        self.assertEqual(len(journal.read_events()), 1)
