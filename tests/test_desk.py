"""Paper desk tests: the order gate, eval/paper runs, ledger math and the PIN console.

Run from paper-trading/:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import http.client
import json
import os
import threading
import unittest
from unittest import mock

from helpers import SATURDAY, THURSDAY, DeskTestCase, FakeBroker, at, falling_bars

import alpaca_client
import console
import journal
import run_study
from common import load_json, save_json
from gate import check_order, failures, passed
from signals import rsi, session_vwap, size_qty, volume_above_average

RED = {"color": "red", "low": 589.5, "high": 590.5, "confluence": []}
GREEN = {"color": "green", "low": 609.0, "high": 610.0, "confluence": ["CHoCH"]}


def publish(day, zones, hh=9, mm=45, **extra):
    save_json("aoi_override.json", {
        "symbol": "SPY", "tradable": True, "written_at": at(day, hh, mm).isoformat(),
        "source": "Ops live read", "approximate": False, "zones": zones, **extra,
    })


def no_broker():
    raise AssertionError("a broker client must not be constructed")


class GateTests(DeskTestCase):
    def base(self, **over):
        publish(THURSDAY, [RED])
        args = dict(
            now=at(THURSDAY, 10, 30), base_url="https://paper-api.alpaca.markets", client_is_paper=True,
            config=load_json("alpaca_config.json"), rules=load_json("rules.json"), risk=load_json("risk.json"),
            override=load_json("aoi_override.json"), symbol="SPY", side="buy", qty=0.4237, price=590.0,
            zone=RED, tags=["rsi"], account_number="PA3R32D8LP4Q", market_open=True,
        )
        args.update(over)
        return check_order(**args)

    def test_all_checks_pass_for_a_clean_setup(self):
        checks = self.base()
        self.assertTrue(passed(checks), failures(checks))

    def test_live_host_refused(self):
        self.assertIn("paper_client", failures(self.base(base_url="https://api.alpaca.markets")))

    def test_live_unlocked_refused(self):
        cfg = {**load_json("alpaca_config.json"), "live_unlocked": True}
        self.assertIn("live_locked", failures(self.base(config=cfg)))

    def test_other_paper_account_refused(self):
        self.assertIn("paper_1000_account", failures(self.base(account_number="PA_OLD_500")))

    def test_symbol_must_be_spy(self):
        self.assertIn("symbol_spy", failures(self.base(symbol="QQQ")))

    def test_weekend_refused(self):
        self.assertIn("weekday", failures(self.base(now=at(SATURDAY, 11, 0))))

    def test_before_ten_and_at_close_refused(self):
        self.assertIn("time_window", failures(self.base(now=at(THURSDAY, 9, 59, 59))))
        self.assertIn("time_window", failures(self.base(now=at(THURSDAY, 16, 0))))
        self.assertNotIn("time_window", failures(self.base(now=at(THURSDAY, 10, 0))))

    def test_prior_day_override_refused(self):
        publish(THURSDAY.replace(day=30, month=9), [RED])
        checks = self.base(override=load_json("aoi_override.json"))
        self.assertIn("aoi_from_today_open", failures(checks))

    def test_override_written_after_ten_refused(self):
        publish(THURSDAY, [RED], hh=10, mm=5)
        self.assertIn("aoi_from_today_open", failures(self.base(override=load_json("aoi_override.json"))))

    def test_approximate_override_refused(self):
        publish(THURSDAY, [RED], approximate=True)
        self.assertIn("aoi_not_approximate", failures(self.base(override=load_json("aoi_override.json"))))

    def test_empty_override_refused(self):
        self.assertIn("aoi_tradable", failures(self.base(override=load_json("aoi_override.json") | {"zones": []})))

    def test_color_must_match_side(self):
        self.assertIn("zone_color_matches_side", failures(self.base(side="sell")))

    def test_confluence_required(self):
        self.assertIn("confluence", failures(self.base(tags=[])))

    def test_no_chase_outside_zone(self):
        self.assertIn("price_at_zone", failures(self.base(price=592.0)))
        self.assertNotIn("price_at_zone", failures(self.base(price=590.8)))  # 0.136% from mid

    def test_size_cap(self):
        self.assertIn("size_within_cap", failures(self.base(qty=0.5)))

    def test_market_clock_must_be_read_and_open(self):
        self.assertIn("market_clock", failures(self.base(market_open=False)))
        self.assertIn("market_clock", failures(self.base(market_open=None)))


class RunTests(DeskTestCase):
    def test_eval_on_weekend_logs_skip_and_sends_nothing(self):
        with mock.patch.object(alpaca_client, "PaperBroker", side_effect=no_broker):
            r = run_study.cmd_eval(at(SATURDAY, 11, 0), bars=[])
        self.assertEqual(r["decision"], "skip")
        self.assertEqual(r["reasons"], ["weekend"])
        events = journal.read_events()
        self.assertEqual([e["event"] for e in events], ["skip"])

    def test_eval_never_orders_even_when_setup_is_clean(self):
        publish(THURSDAY, [RED])
        now = at(THURSDAY, 10, 30)
        with mock.patch.object(alpaca_client, "PaperBroker", side_effect=no_broker):
            r = run_study.cmd_eval(now, bars=falling_bars(THURSDAY, now))
        self.assertEqual(r["decision"], "would_enter")
        self.assertFalse(any(e["event"] == "order" for e in journal.read_events()))

    def test_watch_only_window_logs_and_sends_nothing(self):
        publish(THURSDAY, [RED])
        now = at(THURSDAY, 9, 50)
        r = run_study.cmd_paper(now, broker_factory=no_broker, bars=falling_bars(THURSDAY, now))
        self.assertEqual(r["decision"], "watch")

    def test_paper_before_ten_sends_nothing(self):
        broker = FakeBroker()
        now = at(THURSDAY, 9, 59)
        run_study.cmd_paper(now, broker_factory=lambda: broker, bars=falling_bars(THURSDAY, now))
        self.assertEqual(broker.submitted, [])

    def test_paper_on_weekend_sends_nothing(self):
        broker = FakeBroker()
        r = run_study.cmd_paper(at(SATURDAY, 11, 0), broker_factory=lambda: broker, bars=[])
        self.assertEqual(broker.submitted, [])
        self.assertEqual(r["reasons"], ["weekend"])

    def test_prior_day_override_sends_nothing(self):
        publish(THURSDAY.replace(day=30, month=9), [RED])
        broker = FakeBroker()
        now = at(THURSDAY, 10, 30)
        r = run_study.cmd_paper(now, broker_factory=lambda: broker, bars=falling_bars(THURSDAY, now))
        self.assertEqual(broker.submitted, [])
        self.assertIn("aoi_from_today_open", r["reasons"])

    def test_approximate_override_sends_nothing(self):
        publish(THURSDAY, [RED], approximate=True)
        broker = FakeBroker()
        now = at(THURSDAY, 10, 30)
        r = run_study.cmd_paper(now, broker_factory=lambda: broker, bars=falling_bars(THURSDAY, now))
        self.assertEqual(broker.submitted, [])
        self.assertIn("aoi_not_approximate", r["reasons"])

    def test_wrong_paper_account_sends_nothing(self):
        publish(THURSDAY, [RED])
        broker = FakeBroker(account_number="PA_OLD_500")
        now = at(THURSDAY, 10, 30)
        r = run_study.cmd_paper(now, broker_factory=lambda: broker, bars=falling_bars(THURSDAY, now))
        self.assertEqual(broker.submitted, [])
        self.assertIn("paper_1000_account", r["reasons"])

    def test_same_day_zone_plus_confluence_submits_one_order_sized_off_1000(self):
        publish(THURSDAY, [RED, GREEN])
        broker = FakeBroker()
        now = at(THURSDAY, 10, 30)
        bars = falling_bars(THURSDAY, now)
        r = run_study.cmd_paper(now, broker_factory=lambda: broker, bars=bars)
        self.assertEqual(r["decision"], "enter", r.get("reasons"))
        self.assertEqual(len(broker.submitted), 1)
        order = broker.submitted[0]
        self.assertEqual(order["side"], "buy")
        self.assertEqual(order["qty"], round(1000 * 0.25 / bars[-1]["c"], 4))
        self.assertIn("rsi", r["candidate"]["tags"])
        self.assertIn("vwap", r["candidate"]["tags"])
        self.assertIn("volume", r["candidate"]["tags"])
        self.assertTrue(any(e["event"] == "order" for e in journal.read_events()))

    def test_no_second_buy_while_long(self):
        publish(THURSDAY, [RED])
        broker = FakeBroker(position={"symbol": "SPY", "qty": 0.42, "avg_entry_price": 590.0})
        now = at(THURSDAY, 10, 30)
        r = run_study.cmd_paper(now, broker_factory=lambda: broker, bars=falling_bars(THURSDAY, now))
        self.assertEqual(broker.submitted, [])
        self.assertIn("already_long", r["reasons"])

    def test_pulse_contradiction_reduces_size(self):
        publish(THURSDAY, [RED])
        save_json("market_pulse.json", {"date": "2026-10-01", "bias": "bearish", "sources": {}})
        broker = FakeBroker()
        now = at(THURSDAY, 10, 30)
        bars = falling_bars(THURSDAY, now)
        run_study.cmd_paper(now, broker_factory=lambda: broker, bars=bars)
        self.assertEqual(broker.submitted[0]["qty"], round(1000 * 0.25 * 0.5 / bars[-1]["c"], 4))

    def test_aoi_set_only_during_the_open(self):
        with self.assertRaises(SystemExit):
            run_study.cmd_aoi_set(at(THURSDAY, 10, 5), ["red:589.5:590.5"], [], "Ops")
        data = run_study.cmd_aoi_set(at(THURSDAY, 9, 41), ["red:589.5:590.5", "green:609:610"], ["2:CHoCH"], "Ops")
        self.assertTrue(data["tradable"])
        self.assertEqual(data["zones"][1]["confluence"], ["CHoCH"])

    def test_sync_records_fills_once_and_review_counts_them(self):
        fills = [
            {"order_id": "a", "side": "buy", "qty": 0.4, "price": 590.0, "filled_at": "2026-10-01T10:30:05-04:00"},
            {"order_id": "b", "side": "sell", "qty": 0.4, "price": 600.0, "filled_at": "2026-10-01T14:00:00-04:00"},
        ]
        broker = FakeBroker(fills=fills)
        now = at(THURSDAY, 16, 15)
        run_study.cmd_sync(now, broker=broker)
        run_study.cmd_sync(now, broker=broker)  # idempotent
        trades = journal.read_trades()
        self.assertEqual(len(trades), 2)
        self.assertAlmostEqual(float(trades[1]["realized_pnl"]), 4.0)
        summary = run_study.cmd_review(now)
        self.assertEqual(summary["fills"], 2)
        self.assertAlmostEqual(summary["realized_pnl"], 4.0)


class LedgerTests(unittest.TestCase):
    def test_average_cost_long_round_trip(self):
        q, a, r = journal.apply_fill(0, 0, "buy", 1.0, 100.0)
        q, a, r = journal.apply_fill(q, a, "buy", 1.0, 110.0)
        self.assertEqual((q, a, r), (2.0, 105.0, None))
        q, a, r = journal.apply_fill(q, a, "sell", 2.0, 100.0)
        self.assertEqual((q, a, r), (0.0, 0.0, -10.0))

    def test_short_round_trip(self):
        q, a, _ = journal.apply_fill(0, 0, "sell", 1.0, 600.0)
        q, a, r = journal.apply_fill(q, a, "buy", 1.0, 590.0)
        self.assertEqual((q, r), (0.0, 10.0))

    def test_stats(self):
        rows = [{"realized_pnl": v, "filled_at": "2026-10-01T15:00:00-04:00"} for v in ("", "5", "-2", "3")]
        s = journal.trade_stats(rows)
        self.assertEqual((s["wins"], s["losses"], s["closed_trades"]), (2, 1, 3))
        self.assertEqual(s["realized_pnl"], 6.0)
        self.assertEqual(s["profit_factor"], 4.0)
        self.assertEqual(s["max_drawdown"], 2.0)
        self.assertEqual(s["streak"], {"kind": "win", "count": 1})


class SignalTests(unittest.TestCase):
    def test_rsi_extremes(self):
        self.assertEqual(rsi([float(i) for i in range(30)]), 100.0)
        self.assertLess(rsi([float(30 - i) for i in range(30)]), 1.0)
        self.assertIsNone(rsi([1.0] * 5))

    def test_vwap_and_volume(self):
        now = at(THURSDAY, 10, 0)
        bars = falling_bars(THURSDAY, now)
        self.assertGreater(session_vwap(bars), bars[-1]["c"])
        self.assertTrue(volume_above_average([b["v"] for b in bars]))

    def test_size(self):
        self.assertEqual(size_qty(600.0, {"size_as_if_equity_usd": 1000, "max_notional_pct_of_sizing_equity": 25}),
                         0.4167)


class PaperOnlyTests(DeskTestCase):
    def test_live_base_url_in_env_refused(self):
        with mock.patch.dict(os.environ, {"APCA_API_BASE_URL": "https://api.alpaca.markets"}):
            with self.assertRaises(alpaca_client.LiveTradingRefused):
                alpaca_client._assert_paper_config()

    def test_live_unlocked_config_refused(self):
        save_json("alpaca_config.json", {**load_json("alpaca_config.json"), "live_unlocked": True})
        with self.assertRaises(alpaca_client.LiveTradingRefused):
            alpaca_client._assert_paper_config()

    def test_env_alpaca_is_gitignored(self):
        from helpers import DESK

        ignored = (DESK / ".gitignore").read_text().splitlines()
        self.assertIn(".env.alpaca", ignored)


class ConsoleTests(DeskTestCase):
    def setUp(self):
        super().setUp()
        self.server = console.make_server(port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def req(self, method, path, body=None, cookie=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = cookie
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res, data

    def test_locked_until_pin(self):
        res, data = self.req("GET", "/")
        self.assertEqual(res.status, 200)
        self.assertIn(b"Enter your PIN", data)
        res, _ = self.req("GET", "/api/state")
        self.assertEqual(res.status, 401)

    def test_wrong_pin_rejected_right_pin_unlocks(self):
        res, _ = self.req("POST", "/unlock", {"pin": "0000"})
        self.assertEqual(res.status, 401)
        res, _ = self.req("POST", "/unlock", {"pin": "4793"})
        self.assertEqual(res.status, 200)
        cookie = res.getheader("Set-Cookie").split(";")[0]
        self.assertIn("HttpOnly", res.getheader("Set-Cookie"))
        res, data = self.req("GET", "/api/state", cookie=cookie)
        self.assertEqual(res.status, 200)
        state = json.loads(data)
        self.assertEqual(state["account"]["equity"], 1000.0)
        self.assertFalse(state["config"]["live_unlocked"])
        res, data = self.req("GET", "/", cookie=cookie)
        self.assertIn(b"SPY Paper Desk", data)
        self.req("POST", "/lock", {}, cookie=cookie)
        res, _ = self.req("GET", "/api/state", cookie=cookie)
        self.assertEqual(res.status, 401)

    def test_lockout_after_five_wrong_pins(self):
        for _ in range(5):
            self.req("POST", "/unlock", {"pin": "1111"})
        res, data = self.req("POST", "/unlock", {"pin": "4793"})
        self.assertEqual(res.status, 401)
        self.assertIn(b"Too many", data)

    def test_foreign_host_header_refused(self):
        res, _ = self.req("GET", "/", host="evil.example:80")
        self.assertEqual(res.status, 403)

    def test_only_loopback(self):
        with self.assertRaises(SystemExit):
            console.make_server(port=0, host="0.0.0.0")


if __name__ == "__main__":
    unittest.main()
