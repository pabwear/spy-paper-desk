"""The crypto learning book: SOL only, mom7d, $45 a buy, 6% stop, one stop a day, never BTC or ETH."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from helpers import DeskTestCase

import crypto_desk as cd
import crypto_learning as cl
from common import save_json
from test_crypto_desk import CFG, UP, FakeCryptoBroker

LS = cl.settings({})
RISING = [100.0 + i for i in range(20)]    # last close above the close 7 days earlier
FALLING = [120.0 - i for i in range(20)]


def pos(entry, price, qty=0.4):
    return {"qty": qty, "avg_entry_price": entry, "current_price": price, "market_value": qty * price,
            "unrealized_pl": qty * (price - entry)}


class LearningRuleTests(DeskTestCase):
    def test_buys_45_when_rising_and_sells_when_falling(self):
        self.assertEqual(cl.decide(RISING, None, 58.0, LS, False),
                         [{"book": "learning", "symbol": "SOL/USD", "side": "buy", "notional": 45.0, "why": "mom7d_up"}])
        self.assertEqual(cl.decide(FALLING, None, 58.0, LS, False), [])
        o = cl.decide(FALLING, pos(100, 99), 0.0, LS, False)
        self.assertEqual((o[0]["side"], o[0]["why"], o[0]["qty"]), ("sell", "mom7d_down", 0.4))
        self.assertEqual(cl.decide(RISING, pos(100, 99), 0.0, LS, False), [])  # holding and still rising

    def test_stop_at_six_percent_and_no_rebuy_that_day(self):
        o = cl.decide(RISING, pos(100, 94), 0.0, LS, False)
        self.assertEqual((o[0]["why"], o[0]["est_pnl"]), ("stop", -2.4))
        self.assertEqual(cl.decide(RISING, pos(100, 94.1), 0.0, LS, False), [])
        self.assertEqual(cl.decide(RISING, None, 58.0, LS, True), [])  # stopped today: wait for tomorrow
        j = [{"event": "order", "book": "learning", "symbol": "SOL/USD", "why": "stop", "ts": "2026-10-11T05:07:00+00:00"}]
        self.assertTrue(cl.stopped_today(j, "SOL/USD", date(2026, 10, 11)))
        self.assertFalse(cl.stopped_today(j, "SOL/USD", date(2026, 10, 12)))

    def test_size_never_over_45_or_the_cash(self):
        self.assertEqual(cl.decide(RISING, None, 30.0, LS, False)[0]["notional"], round(30 * 0.98, 2))
        self.assertEqual(cl.decide(RISING, None, 9.0, LS, False), [])  # under $10: nothing
        self.assertEqual(cl.decide(RISING, None, 5000.0, LS, False)[0]["notional"], 45.0)

    def test_backtest_takes_the_stop_and_fees(self):
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        h = [{"t": t0 + timedelta(hours=i), "o": 100.0 + i // 24, "c": 100.0 + i // 24} for i in range(24 * 10)]
        h += [{"t": h[-1]["t"] + timedelta(hours=1 + i), "o": 80.0, "c": 80.0} for i in range(3)]
        r = cl.backtest(h)
        self.assertEqual((r["trades"], r["stops"]), (1, 1))
        self.assertLess(r["total"], -45 * 0.2)


class LearningDeskTests(DeskTestCase):
    def setUp(self):
        super().setUp()
        save_json(cd.CONFIG, CFG)
        self.now = datetime(2026, 10, 11, 0, 7, tzinfo=timezone.utc)

    def test_runs_beside_the_trend_book_without_touching_it(self):
        b = FakeCryptoBroker(positions={"BTCUSD": {"qty": 0.005}, "ETHUSD": {"qty": 0.2}}, cash=58.0)
        st = cd.run(self.now, b, {"BTC/USD": UP, "ETH/USD": UP, "SOL/USD": RISING})
        self.assertEqual([(o["symbol"], o["side"], o.get("notional"), c) for o, c in b.sent],
                         [("SOL/USD", "buy", 45.0, "crypto-learn-20261011-buy")])
        self.assertEqual(sorted(st["signals"]), ["BTC/USD", "ETH/USD"])  # SOL is not a trend200 coin
        self.assertTrue(st["learning"]["hold"])
        self.assertEqual(st["learning"]["orders"][0]["why"], "mom7d_up")

    def test_switch_off_and_status_mode(self):
        save_json(cd.CONFIG, {**CFG, "learning": {"enabled": False}})
        b = FakeCryptoBroker(cash=58.0, positions={"BTCUSD": {"qty": 1}, "ETHUSD": {"qty": 1}})
        st = cd.run(self.now, b, {"BTC/USD": UP, "ETH/USD": UP, "SOL/USD": RISING})
        self.assertEqual((b.sent, st["learning"]), ([], None))
        save_json(cd.CONFIG, CFG)
        cd.run(self.now, b, {"BTC/USD": UP, "ETH/USD": UP, "SOL/USD": RISING}, trade=False)
        self.assertEqual(b.sent, [])

    def test_broker_refuses_learning_orders_on_other_coins_or_too_big(self):
        br = cd.CryptoBroker.__new__(cd.CryptoBroker)
        br.cfg = {**CFG, "learning": {"symbol": "SOL/USD", "usd": 45}}
        with self.assertRaises(cd.Refused):
            br.submit({"book": "learning", "symbol": "BTC/USD", "side": "buy", "notional": 45}, "x")
        with self.assertRaises(cd.Refused):
            br.submit({"book": "learning", "symbol": "SOL/USD", "side": "buy", "notional": 46}, "x")
        with self.assertRaises(cd.Refused):
            br.submit({"symbol": "SOL/USD", "side": "buy", "notional": 40}, "x")  # not a trend coin
