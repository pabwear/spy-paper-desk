"""The daily SPY put-spread book: sold at 10:00 on days SPY has a same-day expiry, one two-leg order with the
credit from real quotes and a capped worst case, bought back at 15:40, never more than one a day."""

from __future__ import annotations

from datetime import datetime, timedelta

from helpers import THURSDAY, DeskTestCase, FakeBroker, at, falling_bars

import journal
import run_study
from common import ET, load_json, save_json
from gate import check_spread, failures, passed

SPREAD_BOOK = {"spy_spreads": {"label": "SPY daily put spread", "symbol": "SPY", "asset": "spread", "enabled": True,
                               "budget_usd": 600, "spread": {"sd": 1.0, "width": 5},
                               "entry_window": ["10:00", "11:00"], "max_entries_per_day": 1, "max_hold_minutes": 0}}


def vix_days(level=16.0, n=5):
    start = datetime(2026, 9, 24, tzinfo=ET)
    return [{"t": start + timedelta(days=i), "o": level, "h": level, "l": level, "c": level, "v": 0.0} for i in range(n)]


def quotes(strike):
    """A put's (bid, ask) by strike: richer closer to the money (SPY about 590 in these bars)."""
    bid = max(0.01, round(0.04 * (strike - 560), 2))
    return bid, round(bid + 0.03, 2)


class SpreadBookTests(DeskTestCase):
    def setUp(self):
        super().setUp()
        r = load_json("rules.json")
        r["books"] = SPREAD_BOOK
        save_json("rules.json", r)
        self.now = at(THURSDAY, 10, 10)

    def bars(self, now=None):
        now = now or self.now
        return {"SPY": falling_bars(THURSDAY, now, start=591.0, end=590.0), "^VIX|1Day": vix_days()}

    def paper(self, broker, now=None):
        return run_study.cmd_paper(now or self.now, broker_factory=lambda: broker, bars=self.bars(now))

    def test_sells_one_put_spread_below_the_price(self):
        broker = FakeBroker(cash=1000.0, quote_fn=quotes)
        r = self.paper(broker)
        self.assertEqual(r["decision"], "enter", r.get("reasons"))
        o = broker.spreads[0]
        (short, s_side, s_int), (long_, l_side, l_int) = [(x["symbol"], x["side"], x["intent"]) for x in o["legs"]]
        self.assertEqual((s_side, s_int, l_side, l_int), ("sell", "sell_to_open", "buy", "buy_to_open"))
        ks, kl = int(short[-8:]) / 1000, int(long_[-8:]) / 1000
        self.assertEqual(ks - kl, 5)
        self.assertLess(ks, 590.0)
        self.assertEqual(short[3:9], f"{THURSDAY:%y%m%d}")  # expires today
        credit = quotes(ks)[0] - quotes(kl)[1]
        self.assertAlmostEqual(o["limit_price"], -round(credit, 2))  # a credit is a negative price on Alpaca
        e = [x for x in journal.read_events() if x["event"] == "order"][-1]
        self.assertEqual((e["book"], e["asset"], e["role"]), ("spy_spreads", "spread", "entry"))
        self.assertAlmostEqual(e["worst_case"], round((5 - credit) * 100, 2))

    def test_one_a_day_and_only_in_its_window(self):
        broker = FakeBroker(cash=1000.0, quote_fn=quotes)
        self.paper(broker)
        self.paper(broker, now=at(THURSDAY, 10, 20))
        self.assertEqual(len(broker.spreads), 1)
        late = FakeBroker(cash=1000.0, quote_fn=quotes)
        journal_events_before = len(journal.read_events())
        r = self.paper(late, now=at(THURSDAY, 11, 30))
        self.assertEqual(late.spreads, [])
        self.assertIn("time_window", r["reasons"])
        self.assertGreater(len(journal.read_events()), journal_events_before)

    def test_too_small_a_credit_or_too_little_money_sends_nothing(self):
        flat = FakeBroker(cash=1000.0, quote_fn=lambda k: (0.02, 0.03))
        r = self.paper(flat)
        self.assertEqual(flat.spreads, [])
        self.assertIn("credit_too_small", r["reasons"])
        poor = FakeBroker(cash=200.0, quote_fn=quotes)
        r = self.paper(poor)
        self.assertEqual(poor.spreads, [])
        self.assertIn("worst_case_affordable", r["reasons"])

    def test_no_vix_no_trade(self):
        broker = FakeBroker(cash=1000.0, quote_fn=quotes)
        r = run_study.cmd_paper(self.now, broker_factory=lambda: broker,
                                bars={"SPY": falling_bars(THURSDAY, self.now, start=591.0, end=590.0)})
        self.assertEqual(broker.spreads, [])
        self.assertIn("no_vix", r["reasons"])

    def _open_spread(self, broker):
        self.paper(broker)
        legs = broker.spreads[0]["legs"]
        broker._positions = [{"symbol": legs[0]["symbol"], "qty": -1.0, "avg_entry_price": 0.4},
                             {"symbol": legs[1]["symbol"], "qty": 1.0, "avg_entry_price": 0.2}]
        return legs

    def test_bought_back_at_1540_as_one_order(self):
        broker = FakeBroker(cash=1000.0, quote_fn=quotes)
        legs = self._open_spread(broker)
        run_study.cmd_manage(at(THURSDAY, 15, 40), broker, bars=self.bars(at(THURSDAY, 15, 40)))
        close = broker.spread_closes[0]
        self.assertEqual([(x["symbol"], x["side"], x["intent"]) for x in close["legs"]],
                         [(legs[0]["symbol"], "buy", "buy_to_close"), (legs[1]["symbol"], "sell", "sell_to_close")])
        ex = [e for e in journal.read_events() if e["event"] == "order" and e.get("role") == "exit"]
        self.assertEqual({(e["book"], e["reason"]) for e in ex}, {("spy_spreads", "flatten")})

    def test_close_falls_back_to_short_first(self):
        broker = FakeBroker(cash=1000.0, quote_fn=quotes, reject_spread_close=True)
        legs = self._open_spread(broker)
        run_study.cmd_manage(at(THURSDAY, 15, 40), broker, bars=self.bars(at(THURSDAY, 15, 40)))
        self.assertEqual([(o["symbol"], o["side"], o["intent"]) for o in broker.submitted],
                         [(legs[0]["symbol"], "buy", "buy_to_close"), (legs[1]["symbol"], "sell", "sell_to_close")])

    def test_held_through_the_day_no_stock_stop_or_time_limit(self):
        broker = FakeBroker(cash=1000.0, quote_fn=quotes)
        self._open_spread(broker)
        crash = {"SPY": falling_bars(THURSDAY, at(THURSDAY, 13, 0), start=591.0, end=575.0), "^VIX|1Day": vix_days()}
        run_study.cmd_manage(at(THURSDAY, 13, 0), broker, bars=crash)
        self.assertEqual((broker.submitted, broker.spread_closes), ([], []))  # the worst case is already capped

    def test_leg_fills_join_into_one_trade(self):
        broker = FakeBroker(cash=1000.0, quote_fn=quotes)
        legs = self._open_spread(broker)
        entry = [e for e in journal.read_events() if e["event"] == "order"][-1]
        s, lo = legs[0]["symbol"], legs[1]["symbol"]
        ts = at(THURSDAY, 10, 11).isoformat()
        fills = [{"order_id": entry["leg_order_ids"][0], "symbol": s, "side": "sell", "qty": 1.0, "price": 0.40, "filled_at": ts},
                 {"order_id": entry["leg_order_ids"][1], "symbol": lo, "side": "buy", "qty": 1.0, "price": 0.20, "filled_at": ts}]
        run_study.cmd_sync(self.now, broker=FakeBroker(fills=fills))
        ts2 = at(THURSDAY, 15, 41).isoformat()
        journal.log("order", now=at(THURSDAY, 15, 40), role="exit", reason="flatten", order_id="c1", book="spy_spreads",
                    leg_order_ids=["c1-0", "c1-1"], leg_symbols=[s, lo], symbol=s)
        fills += [{"order_id": "c1-0", "symbol": s, "side": "buy", "qty": 1.0, "price": 0.05, "filled_at": ts2},
                  {"order_id": "c1-1", "symbol": lo, "side": "sell", "qty": 1.0, "price": 0.01, "filled_at": ts2}]
        run_study.cmd_sync(at(THURSDAY, 15, 41), broker=FakeBroker(fills=fills))
        import rebuild_dashboard

        book = next(b for b in rebuild_dashboard.build_state(at(THURSDAY, 15, 45))["books"] if b["id"] == "spy_spreads")
        # sold for 0.40, bought for 0.20 (credit 0.20); closed paying 0.05, getting 0.01: (0.20 − 0.04) × 100 = $16
        self.assertEqual((book["closed_trades"], book["realized"]), (1, 16.0))


class SpreadGateTests(DeskTestCase):
    def base(self, **over):
        kw = dict(now=at(THURSDAY, 10, 10), base_url="https://paper-api.alpaca.markets", client_is_paper=True,
                  config=load_json("alpaca_config.json"), rules=load_json("rules.json"),
                  book={**SPREAD_BOOK["spy_spreads"], "id": "spy_spreads"},
                  plan={"credit": 0.30, "worst_case": 470.0, "expiry": THURSDAY.date().isoformat(),
                        "legs": [{"symbol": "SPY261001P00586000"}, {"symbol": "SPY261001P00581000"}]},
                  account_number=load_json("alpaca_config.json")["account_number"], market_open=True,
                  entries_today=0, open_positions=[], open_orders=0, money=1000.0)
        kw.update(over)
        return check_spread(**kw)

    def test_clean_setup_passes(self):
        self.assertTrue(passed(self.base()), failures(self.base()))

    def test_each_guard(self):
        self.assertIn("live_locked", failures(self.base(config={**load_json("alpaca_config.json"), "live_unlocked": True})))
        self.assertIn("entries_today", failures(self.base(entries_today=1)))
        self.assertIn("one_position", failures(self.base(open_positions=[{"symbol": "SPY261001P00586000", "qty": -1}])))
        self.assertIn("expires_today", failures(self.base(plan={"credit": 0.3, "worst_case": 470.0, "expiry": "2026-10-02",
                                                                 "legs": [{"symbol": "a"}, {"symbol": "b"}]})))
        self.assertIn("worst_case_in_budget", failures(self.base(plan={"credit": 0.3, "worst_case": 700.0,
                                                                        "expiry": THURSDAY.date().isoformat(),
                                                                        "legs": [{"symbol": "a"}, {"symbol": "b"}]})))
        self.assertIn("time_window", failures(self.base(now=at(THURSDAY, 11, 5))))
        self.assertIn("market_clock", failures(self.base(market_open=False)))


class RepriceTests(SpreadBookTests):
    def test_an_unfilled_spread_is_cancelled_and_resent_at_fresh_quotes(self):
        broker = FakeBroker(cash=1000.0, quote_fn=quotes)
        self.paper(broker)
        first = [e for e in journal.read_events() if e["event"] == "order"][-1]
        broker._open = [{"id": first["order_id"], "symbol": None, "side": None, "order_class": "mleg",
                         "legs": [{"symbol": x["symbol"]} for x in broker.spreads[0]["legs"]]}]
        self.paper(broker, now=at(THURSDAY, 10, 16))
        self.assertEqual(broker.cancelled_ids, [first["order_id"]])
        self.assertEqual(len(broker.spreads), 2)
        cancels = [e for e in journal.read_events() if e["event"] == "order_canceled"]
        self.assertEqual((cancels[0]["book"], cancels[0]["order_id"]), ("spy_spreads", first["order_id"]))

    def test_a_young_unfilled_order_is_left_alone(self):
        broker = FakeBroker(cash=1000.0, quote_fn=quotes)
        self.paper(broker)
        first = [e for e in journal.read_events() if e["event"] == "order"][-1]
        broker._open = [{"id": first["order_id"], "symbol": None, "side": None, "order_class": "mleg", "legs": []}]
        self.paper(broker, now=at(THURSDAY, 10, 13))
        self.assertEqual((broker.cancelled_ids, len(broker.spreads)), ([], 1))
