"""Trading books: SPY shares, SNDK shares and SPY options side by side, each with its own budget, position,
entry count and stop; shares carry a stop held at Alpaca."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from common import ET
from signals import range_stop_pct


def daily(ranges_pct, close=100.0, start=datetime(2026, 9, 1, tzinfo=ET)):
    return [{"t": start + timedelta(days=i), "o": close, "h": close * (1 + r / 200), "l": close * (1 - r / 200),
             "c": close, "v": 1.0} for i, r in enumerate(ranges_pct)]


class RangeStopTests(unittest.TestCase):
    def test_a_quarter_of_the_average_daily_range(self):
        self.assertAlmostEqual(range_stop_pct(daily([4.0] * 20), 0.25, 20), 1.0, places=6)

    def test_uses_only_the_last_n_days(self):
        self.assertAlmostEqual(range_stop_pct(daily([100.0] * 5 + [2.0] * 20), 0.25, 20), 0.5, places=6)

    def test_too_little_history_gives_none(self):
        self.assertIsNone(range_stop_pct(daily([2.0] * 4), 0.25, 20))


import instruments  # noqa: E402

BOOKS = {
    "spy_shares": {"label": "SPY shares", "symbol": "SPY", "asset": "shares", "enabled": True, "budget_usd": 4000,
                   "stop": {"pct": 0.25}, "stop_at_broker": True},
    "sndk_shares": {"label": "SNDK shares", "symbol": "SNDK", "asset": "shares", "enabled": True, "budget_usd": 3000,
                    "stop": {"range_fraction": 0.25, "days": 20}, "stop_at_broker": True},
    "spy_options": {"label": "SPY options (learning)", "symbol": "SPY", "asset": "option", "enabled": True,
                    "budget_usd": 3000, "stop": {"pct": 0.25}},
}
RULES = {"books": BOOKS, "max_hold_minutes": 30, "max_entries_per_day": 2, "stop_underlying_pct": 0.25,
         "option": {"contracts": 1, "right_on_buy": "call", "right_on_sell": "put"}}
WATCH = {"focus": ["SPY", "SNDK", "TSLA"], "symbols": {"SPY": {}, "SNDK": {}, "TSLA": {}}}


class BookTests(unittest.TestCase):
    def test_books_in_order_with_defaults_filled_in(self):
        bs = instruments.books(RULES, WATCH)
        self.assertEqual([b["id"] for b in bs], ["spy_shares", "sndk_shares", "spy_options"])
        self.assertEqual((bs[0]["max_hold_minutes"], bs[0]["max_entries_per_day"]), (30, 2))
        self.assertEqual(instruments.books_for("SPY", RULES, WATCH)[1]["id"], "spy_options")
        self.assertEqual(instruments.books_for("TSLA", RULES, WATCH), [])  # watch only

    def test_a_position_belongs_to_one_book(self):
        bs = instruments.books(RULES, WATCH)
        self.assertEqual(instruments.book_of("SPY", bs)["id"], "spy_shares")
        self.assertEqual(instruments.book_of("SPY261106C00780000", bs)["id"], "spy_options")
        self.assertEqual(instruments.book_of("SNDK", bs)["id"], "sndk_shares")
        self.assertIsNone(instruments.book_of("TSLA", bs))

    def test_shares_are_whole_and_fit_the_budget_and_the_cash(self):
        b = instruments.books(RULES, WATCH)[0]
        self.assertEqual(instruments.plan_book_entry(b, "buy", 779.0, 50_000.0, RULES)["qty"], 5)  # $4,000 budget
        self.assertEqual(instruments.plan_book_entry(b, "buy", 779.0, 1_000.0, RULES)["qty"], 1)   # $1,000 cash
        self.assertEqual(instruments.plan_book_entry(b, "buy", 779.0, 700.0, RULES)["qty"], 0)
        self.assertEqual(instruments.plan_book_entry(b, "sell", 779.0, 50_000.0, RULES)["qty"], 0)  # no shorts

    def test_option_book_buys_one_contract(self):
        b = instruments.books(RULES, WATCH)[2]
        p = instruments.plan_book_entry(b, "buy", 779.4, 3_000.0, RULES)
        self.assertEqual((p["asset"], p["qty"], p["right"], p["target_strike"], p["book"]), ("option", 1, "call", 779.0, "spy_options"))

    def test_without_books_the_old_switches_still_decide(self):
        old = {"active": "spy_options", "spy_options_enabled": True, "book_usd": 1000}
        bs = instruments.books(old, {"focus": ["SPY"], "symbols": {"SPY": {}}})
        self.assertEqual([(b["id"], b["asset"], b["enabled"]) for b in bs], [("spy_options", "option", True)])


from helpers import THURSDAY, DeskTestCase, FakeBroker, at, falling_bars  # noqa: E402

import journal  # noqa: E402
import run_study  # noqa: E402
from common import aoi_file, load_json, save_json  # noqa: E402

RED_SPY = {"color": "red", "low": 589.5, "high": 590.5, "confluence": []}
RED_SNDK = {"color": "red", "low": 94.5, "high": 95.5, "confluence": []}


def publish(symbol, zones):
    save_json(aoi_file(symbol), {"symbol": symbol, "tradable": True, "written_at": at(THURSDAY, 9, 45).isoformat(),
                                 "source": "Ops live read", "approximate": False, "zones": zones})


class MultiBookTests(DeskTestCase):
    """The desk's real books: SPY shares ($4,000), SNDK shares ($3,000), SPY options ($3,000)."""

    real_books = True

    def setUp(self):
        super().setUp()
        self.now = at(THURSDAY, 10, 30)
        w = load_json("watchlist.json")
        w["focus"] = ["SPY", "SNDK"]
        save_json("watchlist.json", w)

    def bars(self, sndk_history=True):
        b = {"SPY": falling_bars(THURSDAY, self.now),
             "SNDK": falling_bars(THURSDAY, self.now, start=100.0, end=95.0)}
        if sndk_history:  # SNDK's last 20 days swung 4% a day: its stop is a quarter of that, 1%
            b["SNDK|1Day"] = daily([4.0] * 20, close=95.0, start=datetime(2026, 9, 1, tzinfo=ET))
        return b

    def paper(self, broker, bars=None):
        return run_study.cmd_paper(self.now, broker_factory=lambda: broker, bars=bars or self.bars())

    def test_each_book_enters_on_its_own(self):
        publish("SPY", [RED_SPY])
        publish("SNDK", [RED_SNDK])
        broker = FakeBroker(cash=10_000.0)
        self.paper(broker)
        by = {("option" if len(o["symbol"]) > 5 else o["symbol"]): o for o in broker.submitted}
        spy_px, sndk_px = self.bars()["SPY"][-1]["c"], self.bars()["SNDK"][-1]["c"]
        self.assertEqual((by["SPY"]["qty"], by["SPY"]["stop_price"]), (int(4000 // spy_px), round(spy_px * 0.9975, 2)))
        self.assertEqual((by["SNDK"]["qty"], by["SNDK"]["stop_price"]), (int(3000 // sndk_px), round(sndk_px * 0.99, 2)))
        opt = [o for o in broker.submitted if len(o["symbol"]) > 5]
        self.assertEqual((len(opt), opt[0]["intent"]), (1, "buy_to_open"))
        books = sorted(e["book"] for e in journal.read_events() if e["event"] == "order")
        self.assertEqual(books, ["sndk_shares", "spy_options", "spy_shares"])

    def test_the_cash_is_shared(self):
        publish("SPY", [RED_SPY])
        broker = FakeBroker(cash=700.0)  # one SPY share ($590) leaves $110: no contract fits
        self.paper(broker)
        self.assertEqual([(o["symbol"], o["qty"]) for o in broker.submitted], [("SPY", 1)])
        skip = [e for e in journal.read_events() if e.get("book") == "spy_options" and e["event"] == "skip"][-1]
        self.assertIn("no_contract", skip["reasons"])

    def test_one_position_per_book(self):
        publish("SPY", [RED_SPY])
        broker = FakeBroker(cash=10_000.0, positions=[{"symbol": "SPY", "qty": 6.0, "avg_entry_price": 590.0}])
        journal.log("order", now=at(THURSDAY, 10, 25), role="entry", order_id="e0", symbol="SPY", book="spy_shares",
                    underlying="SPY", underlying_price=590.0, stop_level=588.5)
        self.paper(broker)
        self.assertEqual([len(o["symbol"]) > 5 for o in broker.submitted], [True])  # only the option book
        skip = [e for e in journal.read_events() if e.get("book") == "spy_shares" and e["event"] == "skip"][-1]
        self.assertIn("one_position", skip["reasons"])

    def test_entries_are_counted_per_book(self):
        publish("SPY", [RED_SPY])
        for i in range(2):
            journal.log("order", now=at(THURSDAY, 10, 5 + i), role="entry", order_id=f"o{i}",
                        symbol="SPY261001C00590000", book="spy_options")
        broker = FakeBroker(cash=10_000.0)
        self.paper(broker)
        self.assertEqual([o["symbol"] for o in broker.submitted], ["SPY"])
        skip = [e for e in journal.read_events() if e.get("book") == "spy_options" and e["event"] == "skip"][-1]
        self.assertIn("entries_today", skip["reasons"])

    def test_no_daily_history_no_sndk_trade(self):
        publish("SNDK", [RED_SNDK])
        broker = FakeBroker(cash=10_000.0)
        self.paper(broker, bars=self.bars(sndk_history=False))
        self.assertFalse(any(o["symbol"] == "SNDK" for o in broker.submitted))
        skip = [e for e in journal.read_events() if e.get("book") == "sndk_shares" and e["event"] == "skip"][-1]
        self.assertIn("no_stop", skip["reasons"])

    def test_time_limit_cancels_the_stop_then_sells(self):
        journal.log("order", now=at(THURSDAY, 9, 58), role="entry", order_id="e1", stop_order_id="s1", symbol="SPY",
                    book="spy_shares", underlying="SPY", underlying_price=590.0, stop_level=588.53, stop_pct=0.25)
        broker = FakeBroker(cash=10_000.0, positions=[{"symbol": "SPY", "qty": 6.0, "avg_entry_price": 590.0}],
                            open_orders=[{"id": "s1", "symbol": "SPY", "side": "sell", "qty": 6}])
        run_study.cmd_manage(self.now, broker, bars=self.bars())
        self.assertEqual(broker.cancelled, ["SPY"])
        self.assertEqual([(o["symbol"], o["side"], o["qty"]) for o in broker.submitted], [("SPY", "sell", 6.0)])
        ex = [e for e in journal.read_events() if e["event"] == "order" and e.get("role") == "exit"][-1]
        self.assertEqual((ex["reason"], ex["book"]), ("time", "spy_shares"))

    def test_the_brokers_stop_fill_is_a_stop_exit(self):
        journal.log("order", now=at(THURSDAY, 10, 10), role="entry", order_id="e1", stop_order_id="s1", symbol="SPY",
                    book="spy_shares", underlying="SPY", underlying_price=590.0, stop_level=588.53, stop_pct=0.25,
                    side="buy", qty=6)
        fills = [{"order_id": "e1", "symbol": "SPY", "side": "buy", "qty": 6.0, "price": 590.0,
                  "filled_at": at(THURSDAY, 10, 10).isoformat()},
                 {"order_id": "s1", "symbol": "SPY", "side": "sell", "qty": 6.0, "price": 588.5,
                  "filled_at": at(THURSDAY, 10, 21).isoformat()}]
        run_study.cmd_sync(self.now, broker=FakeBroker(fills=fills))
        ex = [e for e in journal.read_events() if e["event"] == "order" and e.get("role") == "exit"]
        self.assertEqual([(e["reason"], e["book"], e["via"]) for e in ex], [("stop", "spy_shares", "alpaca_stop")])
        trip = journal.round_trips(journal.read_trades(), journal.read_events())[0]
        self.assertEqual((trip["exit_reason"], trip["book"], trip["pnl"]), ("stop", "spy_shares", -9.0))


class ChartFileTests(DeskTestCase):
    real_books = True

    def test_other_stocks_charts_get_their_own_files(self):
        import cloud_state
        import rebuild_dashboard
        from common import desk_dir, path

        save_json("charts.json", {s: {"symbol": s, "frames": {"1m": [1, 2, 3]}, "updated_at": "t", "last": 1.0}
                                  for s in ("SPY", "SNDK", "TSLA")})
        rebuild_dashboard.write_state(at(THURSDAY, 10, 30))
        st = load_json("dashboard_state.json")
        self.assertEqual(st["charts"]["SPY"]["frames"], {"1m": [1, 2, 3]})
        self.assertEqual(st["charts"]["SNDK"], {"symbol": "SNDK", "stub": True, "file": "chart_SNDK.json",
                                                "updated_at": "t", "last": 1.0, "date": None})
        self.assertEqual(load_json("chart_TSLA.json")["frames"], {"1m": [1, 2, 3]})
        self.assertNotIn("\n  ", path("dashboard_state.json").read_text())  # compact
        self.assertIn("chart_SNDK.json", cloud_state._names(desk_dir()))
        self.assertEqual([b["id"] for b in st["books"]], ["spy_spreads", "spy_shares", "sndk_shares", "spy_options"])


class PulseBookTests(MultiBookTests):
    def test_a_halved_single_share_is_a_pulse_skip(self):
        publish("SPY", [RED_SPY])
        save_json("market_pulse.json", {"date": "2026-10-01", "bias": "bearish", "sources": {}})
        broker = FakeBroker(cash=1_000.0)  # one SPY share; halved → none
        self.paper(broker)
        self.assertFalse(any(o["symbol"] == "SPY" for o in broker.submitted))
        skip = [e for e in journal.read_events() if e.get("book") == "spy_shares" and e["event"] == "skip"][-1]
        self.assertEqual(skip["reasons"], ["pulse_contradicts"])
