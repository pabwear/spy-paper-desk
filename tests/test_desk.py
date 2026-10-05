"""Paper desk tests: gates, instruments (SPY options on, SPY shares/SNDK off), exits, ledger, console.

Run from paper-trading/:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import http.client
import json
import os
import threading
import unittest
from datetime import date
from unittest import mock

from helpers import SATURDAY, THURSDAY, DeskTestCase, FakeBroker, at, falling_bars, rising_bars, use_shares

import alpaca_client
import console
import instruments
import journal
import run_study
from common import load_json, save_json
from gate import check_exit, check_order, failures, passed
from signals import rsi, session_vwap, size_qty, volume_above_average

RED = {"color": "red", "low": 589.5, "high": 590.5, "confluence": []}
GREEN = {"color": "green", "low": 609.5, "high": 610.5, "confluence": ["CHoCH"]}
CALL_590 = "SPY261001C00590000"


def publish(day, zones, hh=9, mm=45, **extra):
    save_json("aoi_override.json", {
        "symbol": "SPY", "tradable": True, "written_at": at(day, hh, mm).isoformat(),
        "source": "Ops live read", "approximate": False, "zones": zones, **extra,
    })


def no_broker():
    raise AssertionError("a broker client must not be constructed")


def option_plan(**over):
    plan = {"instrument": "spy_options", "asset": "option", "underlying": "SPY", "signal": "buy",
            "order_side": "buy", "intent": "buy_to_open", "right": "call", "contracts": 1, "qty": 1,
            "target_strike": 590.0, "symbol": CALL_590, "expiry_is_nearest": True, "strike_is_nearest": True}
    plan.update(over)
    return plan


class GateTests(DeskTestCase):
    def base(self, **over):
        publish(THURSDAY, [RED])
        args = dict(
            now=at(THURSDAY, 10, 30), base_url="https://paper-api.alpaca.markets", client_is_paper=True,
            config=load_json("alpaca_config.json"), rules=load_json("rules.json"), risk=load_json("risk.json"),
            override=load_json("aoi_override.json"), plan=option_plan(), price=590.0, zone=RED,
            tags=["rsi", "vwap"], account_number="PA3R32D8LP4Q", market_open=True, entries_today=0,
            open_positions=[], open_orders=0,
        )
        args.update(over)
        return check_order(**args)

    def test_one_call_passes_a_clean_setup(self):
        checks = self.base()
        self.assertTrue(passed(checks), failures(checks))

    def test_two_confluence_signals_required(self):
        self.assertIn("confluence", failures(self.base(tags=["rsi"])))

    def test_max_two_entries_a_day(self):
        self.assertNotIn("entries_today", failures(self.base(entries_today=1)))
        self.assertIn("entries_today", failures(self.base(entries_today=2)))

    def test_one_position_at_a_time(self):
        self.assertIn("one_position", failures(self.base(open_positions=[{"symbol": CALL_590, "qty": 1}])))
        self.assertIn("one_position", failures(self.base(open_orders=1)))
        self.assertIn("one_position", failures(self.base(open_positions=None)))

    def test_only_one_contract_never_resized(self):
        self.assertIn("option_one_contract", failures(self.base(plan=option_plan(qty=2, contracts=2))))
        self.assertIn("option_one_contract", failures(self.base(plan=option_plan(order_side="sell"))))

    def test_call_on_buy_put_on_sell(self):
        self.assertIn("option_right", failures(self.base(plan=option_plan(right="put"))))
        green = self.base(plan=option_plan(signal="sell", right="put", symbol="SPY261001P00610000"),
                          zone=GREEN, price=610.0,
                          override={**load_json("aoi_override.json"), "zones": [RED, GREEN]})
        self.assertTrue(passed(green), failures(green))

    def test_contract_must_be_nearest_listed(self):
        self.assertIn("option_contract", failures(self.base(plan=option_plan(expiry_is_nearest=False))))
        self.assertIn("option_contract", failures(self.base(plan=option_plan(symbol=None))))

    def test_shares_off_refuses_share_plan(self):
        plan = {"instrument": "spy_shares", "asset": "shares", "underlying": "SPY", "signal": "buy",
                "order_side": "buy", "qty": 1.3, "symbol": "SPY"}
        self.assertIn("instrument_enabled", failures(self.base(plan=plan)))

    def test_sndk_refused(self):
        plan = {"instrument": "sndk_shares", "asset": "shares", "underlying": "SNDK", "signal": "buy",
                "order_side": "buy", "qty": 1, "symbol": "SNDK"}
        f = failures(self.base(plan=plan))
        self.assertIn("instrument_enabled", f)
        self.assertIn("symbol_in_focus", f)
        self.assertIn("sndk_off", f)

    def test_live_host_and_unlock_refused(self):
        self.assertIn("paper_client", failures(self.base(base_url="https://api.alpaca.markets")))
        cfg = {**load_json("alpaca_config.json"), "live_unlocked": True}
        self.assertIn("live_locked", failures(self.base(config=cfg)))

    def test_other_paper_account_refused(self):
        self.assertIn("paper_1000_account", failures(self.base(account_number="PA_OLD_500")))

    def test_time_window(self):
        self.assertIn("weekday", failures(self.base(now=at(SATURDAY, 11, 0))))
        self.assertIn("time_window", failures(self.base(now=at(THURSDAY, 9, 59, 59))))
        self.assertIn("time_window", failures(self.base(now=at(THURSDAY, 15, 55))))
        self.assertNotIn("time_window", failures(self.base(now=at(THURSDAY, 10, 0))))

    def test_stale_or_stand_in_override_refused(self):
        publish(THURSDAY.replace(day=2), [RED])  # an October 2 read is not today's
        self.assertIn("aoi_from_today_open", failures(self.base(override=load_json("aoi_override.json"))))
        publish(THURSDAY, [RED], approximate=True)
        self.assertIn("aoi_not_approximate", failures(self.base(override=load_json("aoi_override.json"))))
        publish(THURSDAY, [RED], hh=10, mm=5)
        self.assertIn("aoi_from_today_open", failures(self.base(override=load_json("aoi_override.json"))))

    def test_zone_rules(self):
        self.assertIn("zone_color_matches_side", failures(self.base(plan=option_plan(signal="sell", right="put"))))
        self.assertIn("price_at_zone", failures(self.base(price=592.0)))

    def test_exit_gate(self):
        args = dict(now=at(THURSDAY, 15, 56), base_url="https://paper-api.alpaca.markets", client_is_paper=True,
                    config=load_json("alpaca_config.json"), rules=load_json("rules.json"),
                    position={"symbol": CALL_590, "qty": 1}, account_number="PA3R32D8LP4Q", market_open=True)
        self.assertTrue(passed(check_exit(**args)))
        self.assertIn("desk_position", failures(check_exit(**{**args, "position": {"symbol": "SNDK", "qty": 3}})))
        self.assertIn("paper_client", failures(check_exit(**{**args, "base_url": "https://api.alpaca.markets"})))


class InstrumentTests(unittest.TestCase):
    def test_occ_round_trip(self):
        sym = instruments.occ_symbol("SPY", date(2026, 10, 1), "put", 589.5)
        self.assertEqual(sym, "SPY261001P00589500")
        self.assertEqual(instruments.parse_occ(sym)["strike"], 589.5)
        self.assertEqual(instruments.multiplier(sym), 100)
        self.assertTrue(instruments.is_desk_symbol(sym))
        self.assertFalse(instruments.is_desk_symbol("SNDK261001C00050000"))

    def test_pick_nearest_expiry_and_dollar_strike(self):
        today = date(2026, 10, 1)
        listed = FakeBroker(expiries=(1, 0, 3)).option_contracts("call", 590.4, today)
        c = instruments.pick_contract(listed, 590.4, "call", today)
        self.assertEqual((c["expiry"], c["strike"]), (today, 590.0))
        c = instruments.pick_contract(listed, 590.6, "call", today)
        self.assertEqual(c["strike"], 591.0)

    def test_exposure_and_stop(self):
        self.assertEqual(instruments.direction(CALL_590, 1), "long")
        self.assertEqual(instruments.direction("SPY261001P00590000", 1), "short")
        self.assertEqual(instruments.stop_level("long", 600.0, 0.35), 597.9)
        self.assertEqual(instruments.stop_level("short", 600.0, 0.35), 602.1)

    def test_active_switches(self):
        self.assertTrue(instruments.active({"active": "spy_options", "spy_options_enabled": True})["enabled"])
        self.assertFalse(instruments.active({"active": "spy_shares", "shares_enabled": False})["enabled"])
        self.assertFalse(instruments.active({"active": "sndk_shares", "sndk_enabled": True})["enabled"])


class RunTests(DeskTestCase):
    def paper(self, broker, now=None, bars=None):
        now = now or at(THURSDAY, 10, 30)
        return run_study.cmd_paper(now, broker_factory=lambda: broker,
                                   bars=bars if bars is not None else falling_bars(THURSDAY, now))

    def test_eval_on_weekend_logs_skip_and_sends_nothing(self):
        with mock.patch.object(alpaca_client, "PaperBroker", side_effect=no_broker):
            r = run_study.cmd_eval(at(SATURDAY, 11, 0), bars=[])
        self.assertEqual((r["decision"], r["reasons"]), ("skip", ["weekend"]))
        self.assertEqual([e["event"] for e in journal.read_events()], ["skip"])

    def test_eval_never_orders_even_when_setup_is_clean(self):
        publish(THURSDAY, [RED])
        now = at(THURSDAY, 10, 30)
        with mock.patch.object(alpaca_client, "PaperBroker", side_effect=no_broker):
            r = run_study.cmd_eval(now, bars=falling_bars(THURSDAY, now))
        self.assertEqual(r["decision"], "would_enter")
        self.assertEqual((r["plan"]["right"], r["plan"]["qty"]), ("call", 1))
        self.assertFalse(any(e["event"] == "order" for e in journal.read_events()))

    def test_watch_only_and_before_ten_send_nothing(self):
        publish(THURSDAY, [RED])
        broker = FakeBroker()
        r = self.paper(broker, now=at(THURSDAY, 9, 50))
        self.assertEqual(r["decision"], "watch")
        self.paper(broker, now=at(THURSDAY, 9, 59))
        self.assertEqual(broker.submitted, [])

    def test_weekend_paper_sends_nothing(self):
        r = run_study.cmd_paper(at(SATURDAY, 11, 0), broker_factory=no_broker, bars=[])
        self.assertEqual(r["reasons"], ["weekend"])

    def test_stale_approx_or_wrong_account_send_nothing(self):
        for setup, expect, broker in [
            (lambda: publish(THURSDAY.replace(day=2), [RED]), "aoi_from_today_open", FakeBroker()),
            (lambda: publish(THURSDAY, [RED], approximate=True), "aoi_not_approximate", FakeBroker()),
            (lambda: publish(THURSDAY, [RED]), "paper_1000_account", FakeBroker(account_number="PA_OLD_500")),
        ]:
            setup()
            r = self.paper(broker)
            self.assertEqual(broker.submitted, [])
            self.assertIn(expect, r["reasons"])

    def test_buy_in_red_sends_exactly_one_call(self):
        publish(THURSDAY, [RED, GREEN])
        broker = FakeBroker()
        r = self.paper(broker)
        self.assertEqual(r["decision"], "enter", r.get("reasons"))
        self.assertEqual(len(broker.submitted), 1)
        o = broker.submitted[0]
        self.assertEqual((o["side"], o["qty"], o["intent"]), ("buy", 1, "buy_to_open"))
        occ = instruments.parse_occ(o["symbol"])
        self.assertEqual((occ["underlying"], occ["right"], occ["expiry"], occ["strike"]),
                         ("SPY", "call", date(2026, 10, 1), 590.0))
        entry = [e for e in journal.read_events() if e["event"] == "order"][0]
        self.assertEqual(entry["role"], "entry")
        self.assertIn("features", entry)
        self.assertIsNotNone(entry["underlying_price"])

    def test_sell_in_green_sends_one_put(self):
        publish(THURSDAY, [RED, GREEN])
        broker = FakeBroker()
        now = at(THURSDAY, 10, 30)
        r = self.paper(broker, bars=rising_bars(THURSDAY, now))
        self.assertEqual(r["decision"], "enter", r.get("reasons"))
        self.assertEqual(instruments.parse_occ(broker.submitted[0]["symbol"])["right"], "put")
        self.assertEqual(broker.submitted[0]["qty"], 1)

    def test_pulse_against_an_option_skips(self):
        publish(THURSDAY, [RED])
        save_json("market_pulse.json", {"date": "2026-10-01", "bias": "bearish", "sources": {}})
        broker = FakeBroker()
        r = self.paper(broker)
        self.assertEqual(broker.submitted, [])
        self.assertEqual(r["reasons"], ["pulse_contradicts"])

    def test_shares_off_sends_no_share_order(self):
        publish(THURSDAY, [RED])
        use_shares(enabled=False)
        broker = FakeBroker()
        r = self.paper(broker)
        self.assertEqual(broker.submitted, [])
        self.assertEqual(r["reasons"], ["instrument_off"])

    def test_shares_back_on_by_config_only(self):
        publish(THURSDAY, [RED])
        use_shares(enabled=True)
        broker = FakeBroker()
        now = at(THURSDAY, 10, 30)
        bars = falling_bars(THURSDAY, now)
        r = self.paper(broker, bars=bars)
        self.assertEqual(r["decision"], "enter", r.get("reasons"))
        o = broker.submitted[0]
        self.assertEqual((o["symbol"], o["side"], o["qty"]), ("SPY", "buy", round(800 / bars[-1]["c"], 4)))

    def test_sndk_off_sends_nothing(self):
        publish(THURSDAY, [RED])
        rules = load_json("rules.json")
        for enabled in (False, True):
            rules.update(active="sndk_shares", sndk_enabled=enabled)
            save_json("rules.json", rules)
            broker = FakeBroker()
            r = self.paper(broker)
            self.assertEqual(broker.submitted, [])
            self.assertEqual(r["reasons"], ["instrument_off"])

    def test_third_entry_of_the_day_refused(self):
        publish(THURSDAY, [RED])
        for i in range(2):
            journal.log("order", now=at(THURSDAY, 10, 5 + i), role="entry", order_id=f"x{i}", symbol=CALL_590)
        broker = FakeBroker()
        r = self.paper(broker)
        self.assertEqual(broker.submitted, [])
        self.assertIn("entries_today", r["reasons"])

    def test_no_entry_while_a_position_is_open(self):
        publish(THURSDAY, [RED])
        journal.log("order", now=at(THURSDAY, 10, 5), role="entry", order_id="x", symbol=CALL_590,
                    underlying_price=590.0)
        broker = FakeBroker(positions=[{"symbol": CALL_590, "qty": 1.0, "avg_entry_price": 2.0}])
        r = self.paper(broker)
        self.assertEqual(broker.submitted, [])
        self.assertIn("one_position", r["reasons"])

    def test_rejected_order_is_logged_not_retried(self):
        publish(THURSDAY, [RED])
        r = self.paper(FakeBroker(reject=True))
        self.assertEqual(r["reasons"], ["order_rejected"])
        self.assertTrue(any(e["event"] == "order_rejected" for e in journal.read_events()))


class ExitTests(DeskTestCase):
    def setUp(self):
        super().setUp()
        journal.log("order", now=at(THURSDAY, 10, 5), role="entry", order_id="e1", symbol=CALL_590,
                    underlying_price=600.0)

    def manage(self, now, last_price, broker=None):
        broker = broker or FakeBroker(positions=[{"symbol": CALL_590, "qty": 1.0, "avg_entry_price": 2.0}])
        bars = falling_bars(THURSDAY, now, start=601.0, end=last_price)
        bars[-1]["c"] = last_price
        sent = run_study.cmd_manage(now, broker, bars=bars)
        return broker, sent

    def test_call_stopped_when_spy_falls_035pct(self):
        broker, sent = self.manage(at(THURSDAY, 11, 0), 597.85)  # stop 597.90
        self.assertEqual(len(sent), 1)
        o = broker.submitted[0]
        self.assertEqual((o["symbol"], o["side"], o["qty"], o["intent"]), (CALL_590, "sell", 1.0, "sell_to_close"))
        self.assertEqual([e for e in journal.read_events() if e["event"] == "order"][-1]["reason"], "stop")

    def test_no_exit_before_the_stop_or_on_leaving_the_zone(self):
        broker, sent = self.manage(at(THURSDAY, 11, 0), 598.5)  # 0.25% against: hold
        self.assertEqual(sent, [])
        broker, sent = self.manage(at(THURSDAY, 11, 0), 606.0)  # far above any zone: hold
        self.assertEqual(sent, [])

    def test_put_stopped_when_spy_rises(self):
        put = "SPY261001P00600000"
        journal.log("order", now=at(THURSDAY, 10, 6), role="entry", order_id="e2", symbol=put, underlying_price=600.0)
        broker = FakeBroker(positions=[{"symbol": put, "qty": 1.0, "avg_entry_price": 2.0}])
        now = at(THURSDAY, 11, 0)
        bars = rising_bars(THURSDAY, now, start=599.0, end=602.2)
        run_study.cmd_manage(now, broker, bars=bars)
        self.assertEqual(broker.submitted[0]["symbol"], put)

    def test_flatten_from_1555_even_if_options_switched_off(self):
        rules = load_json("rules.json")
        rules["spy_options_enabled"] = False
        save_json("rules.json", rules)
        broker, sent = self.manage(at(THURSDAY, 15, 56), 600.5)
        self.assertEqual(len(sent), 1)
        self.assertEqual([e for e in journal.read_events() if e["event"] == "order"][-1]["reason"], "flatten")

    def test_overnight_position_closed_next_morning(self):
        broker, sent = self.manage(at(THURSDAY.replace(day=2), 10, 1), 600.0)  # entry was Oct 1
        self.assertEqual(len(sent), 1)
        self.assertEqual([e for e in journal.read_events() if e["event"] == "order"][-1]["reason"], "overnight")

    def test_exit_refused_on_live_host(self):
        broker = FakeBroker(positions=[{"symbol": CALL_590, "qty": 1.0, "avg_entry_price": 2.0}])
        broker.base_url = "https://api.alpaca.markets"
        _, sent = self.manage(at(THURSDAY, 15, 56), 600.0, broker=broker)
        self.assertEqual(sent, [])
        self.assertTrue(any(e["event"] == "exit_failed" for e in journal.read_events()))

    def test_paper_run_exits_then_does_not_reenter_same_run(self):
        publish(THURSDAY, [RED])
        broker = FakeBroker(positions=[{"symbol": CALL_590, "qty": 1.0, "avg_entry_price": 2.0}])
        now = at(THURSDAY, 11, 0)
        bars = falling_bars(THURSDAY, now, start=601.0, end=590.0)
        r = run_study.cmd_paper(now, broker_factory=lambda: broker, bars=bars)
        self.assertEqual(len(broker.submitted), 1)
        self.assertEqual(r["reasons"], ["exit_in_progress"])


class LedgerTests(DeskTestCase):
    def test_average_cost_long_round_trip(self):
        q, a, r = journal.apply_fill(0, 0, "buy", 1.0, 100.0)
        q, a, r = journal.apply_fill(q, a, "buy", 1.0, 110.0)
        self.assertEqual((q, a, r), (2.0, 105.0, None))
        q, a, r = journal.apply_fill(q, a, "sell", 2.0, 100.0)
        self.assertEqual((q, a, r), (0.0, 0.0, -10.0))

    def test_option_pnl_uses_the_100_multiplier(self):
        fills = [
            {"order_id": "a", "symbol": CALL_590, "side": "buy", "qty": 1, "price": 2.00,
             "filled_at": "2026-10-01T10:30:05-04:00"},
            {"order_id": "b", "symbol": CALL_590, "side": "sell", "qty": 1, "price": 2.50,
             "filled_at": "2026-10-01T14:00:00-04:00"},
        ]
        broker = FakeBroker(fills=fills)
        now = at(THURSDAY, 16, 15)
        run_study.cmd_sync(now, broker=broker)
        run_study.cmd_sync(now, broker=broker)  # idempotent
        trades = journal.read_trades()
        self.assertEqual(len(trades), 2)
        self.assertEqual(trades[0]["notional"], "200.0")
        self.assertAlmostEqual(float(trades[1]["realized_pnl"]), 50.0)
        summary = run_study.cmd_review(now)
        self.assertAlmostEqual(summary["realized_pnl"], 50.0)
        trips = journal.round_trips(journal.read_trades(), journal.read_events())
        self.assertEqual((len(trips), trips[0]["result"]), (1, "win"))

    def test_stats(self):
        rows = [{"realized_pnl": v, "filled_at": "2026-10-01T15:00:00-04:00"} for v in ("", "5", "-2", "3")]
        s = journal.trade_stats(rows)
        self.assertEqual((s["wins"], s["losses"], s["closed_trades"]), (2, 1, 3))
        self.assertEqual((s["profit_factor"], s["max_drawdown"]), (4.0, 2.0))


class SignalTests(unittest.TestCase):
    def test_rsi_extremes(self):
        self.assertEqual(rsi([float(i) for i in range(30)]), 100.0)
        self.assertLess(rsi([float(30 - i) for i in range(30)]), 1.0)
        self.assertIsNone(rsi([1.0] * 5))

    def test_vwap_and_volume(self):
        bars = falling_bars(THURSDAY, at(THURSDAY, 10, 0))
        self.assertGreater(session_vwap(bars), bars[-1]["c"])
        self.assertTrue(volume_above_average([b["v"] for b in bars]))

    def test_shares_size_is_800(self):
        risk = {"size_as_if_equity_usd": 1000, "max_notional_pct_of_sizing_equity": 80}
        self.assertEqual(size_qty(600.0, risk), 1.3333)


class PaperOnlyTests(DeskTestCase):
    def test_live_base_url_in_env_refused(self):
        with mock.patch.dict(os.environ, {"APCA_API_BASE_URL": "https://api.alpaca.markets"}):
            with self.assertRaises(alpaca_client.LiveTradingRefused):
                alpaca_client._assert_paper_config()

    def test_live_unlocked_config_refused(self):
        save_json("alpaca_config.json", {**load_json("alpaca_config.json"), "live_unlocked": True})
        with self.assertRaises(alpaca_client.LiveTradingRefused):
            alpaca_client._assert_paper_config()

    def test_config_is_merged(self):
        from helpers import DESK

        rules = json.loads((DESK / "rules.json").read_text())
        self.assertEqual((rules["active"], rules["shares_enabled"], rules["sndk_enabled"],
                          rules["spy_options_enabled"], rules["min_confluence"], rules["max_entries_per_day"],
                          rules["stop_underlying_pct"], rules["live_trading"]),
                         ("spy_options", False, False, True, 2, 2, 0.35, False))
        self.assertFalse(json.loads((DESK / "alpaca_config.json").read_text())["live_unlocked"])

    def test_env_alpaca_is_gitignored(self):
        from helpers import DESK

        self.assertIn(".env.alpaca", (DESK / ".gitignore").read_text().splitlines())


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
        self.assertIn(b"Enter your PIN", data)
        res, _ = self.req("GET", "/api/state")
        self.assertEqual(res.status, 401)

    def test_wrong_pin_rejected_right_pin_unlocks(self):
        res, _ = self.req("POST", "/unlock", {"pin": "0000"})
        self.assertEqual(res.status, 401)
        res, _ = self.req("POST", "/unlock", {"pin": "4793"})
        self.assertEqual(res.status, 200)
        cookie = res.getheader("Set-Cookie").split(";")[0]
        res, data = self.req("GET", "/api/state", cookie=cookie)
        state = json.loads(data)
        self.assertEqual(state["account"]["equity"], 1000.0)
        self.assertEqual(state["instrument"]["active"], "spy_options")
        self.assertFalse(state["instrument"]["flags"]["spy_shares"])
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
