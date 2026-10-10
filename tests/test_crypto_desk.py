"""The crypto desk: the rule's daily call, what it buys and sells, and its safety checks."""

from __future__ import annotations

from datetime import datetime, timezone

from helpers import DeskTestCase

import crypto_desk as cd
from common import save_json

CFG = {"account_name": "test", "account_number": "PA34EVDSIJ4G", "live_unlocked": False, "enabled": True,
       "rule": "trend200", "symbols": ["BTC/USD", "ETH/USD"], "per_symbol_usd": 480, "start_usd": 1000}
UP = [100.0] * 199 + [150.0]    # last close above its 200-day average
DOWN = [100.0] * 199 + [60.0]   # below


class FakeCryptoBroker:
    def __init__(self, positions=None, cash=1000.0, fail=False):
        self.positions, self.cash, self.sent, self.fail = positions or {}, cash, [], fail

    def snapshot(self):
        return {"account_number": CFG["account_number"], "equity": 1000.0, "cash": self.cash, "last_equity": 1000.0,
                "positions": dict(self.positions)}

    def submit(self, order, coid):
        if self.fail:
            raise RuntimeError("rejected")
        self.sent.append((order, coid))
        return {"order_id": f"o{len(self.sent)}", "status": "accepted"}


class CryptoDeskTests(DeskTestCase):
    def setUp(self):
        super().setUp()
        save_json(cd.CONFIG, CFG)
        self.now = datetime(2026, 10, 11, 0, 7, tzinfo=timezone.utc)

    def test_buys_each_coin_above_its_average_once(self):
        b = FakeCryptoBroker()
        st = cd.run(self.now, b, {"BTC/USD": UP, "ETH/USD": UP})
        self.assertEqual([(o["symbol"], o["side"], o["notional"]) for o, _ in b.sent],
                         [("BTC/USD", "buy", 480.0), ("ETH/USD", "buy", 480.0)])
        self.assertTrue(st["signals"]["BTC/USD"]["hold"])
        self.assertEqual((st["pnl_periods"]["all"], st["pnl_periods"]["today"]), (0.0, 0.0))
        held = FakeCryptoBroker(positions={"BTCUSD": {"qty": 0.005}, "ETHUSD": {"qty": 0.2}})
        cd.run(self.now, held, {"BTC/USD": UP, "ETH/USD": UP})
        self.assertEqual(held.sent, [])  # already holding: nothing to do

    def test_sells_everything_below_the_average(self):
        b = FakeCryptoBroker(positions={"BTCUSD": {"qty": 0.005}})
        cd.run(self.now, b, {"BTC/USD": DOWN, "ETH/USD": DOWN})
        self.assertEqual([(o["symbol"], o["side"], o["qty"]) for o, _ in b.sent], [("BTC/USD", "sell", 0.005)])

    def test_never_spends_more_than_the_cash(self):
        orders = cd.decide({"BTC/USD": cd.signal(UP, "trend200"), "ETH/USD": cd.signal(UP, "trend200")}, {}, 600.0, CFG)
        self.assertEqual([o["notional"] for o in orders], [480.0, round(120 * 0.98, 2)])
        self.assertLessEqual(sum(o["notional"] for o in orders), 600.0)

    def test_status_sends_nothing_and_failures_are_logged(self):
        b = FakeCryptoBroker()
        cd.run(self.now, b, {"BTC/USD": UP, "ETH/USD": UP}, trade=False)
        self.assertEqual(b.sent, [])
        bad = FakeCryptoBroker(fail=True)
        cd.run(self.now, bad, {"BTC/USD": UP, "ETH/USD": UP})
        self.assertEqual([e["event"] for e in cd.read_journal() if e["event"].startswith("order")], ["order_failed"] * 2)

    def test_refuses_without_the_paper_lock(self):
        save_json(cd.CONFIG, {**CFG, "live_unlocked": True})
        with self.assertRaises(cd.Refused):
            cd.config()
        save_json(cd.CONFIG, {**CFG, "account_number": ""})
        with self.assertRaises(cd.Refused):
            cd.config()

    def test_same_call_as_the_backtest(self):
        import crypto_research as research

        self.assertEqual(cd.signal(UP, "trend200")["hold"], research.signals(UP, "trend200")[-1])
        self.assertIsNone(cd.signal([100.0] * 50, "trend200")["average"])
        self.assertFalse(cd.signal([100.0] * 50, "trend200")["hold"])  # not enough history: cash
