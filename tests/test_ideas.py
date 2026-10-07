"""The four ideas' mechanics: overnight hold, buy-and-hold yardstick, the trailing-stop swing, shares with a
broker-held stop, and the bounce-only filter."""

from __future__ import annotations

import random
import unittest
from datetime import datetime, timedelta

from helpers import THURSDAY, at
from test_backtest import RED, day_with

import backtest_areas as bt
import backtest_ideas as ideas
from common import ET, load_json
from studies import auto


def session_day(d, prices, start=(9, 30)):
    """Regular-hours 1-minute bars through the given closes (open = the previous close)."""
    t, out, prev = datetime(d.year, d.month, d.day, *start, tzinfo=ET), [], prices[0]
    for i, p in enumerate(prices):
        out.append({"t": t + timedelta(minutes=i), "o": prev, "h": max(prev, p) + 0.01, "l": min(prev, p) - 0.01,
                    "c": p, "v": 1000.0})
        prev = p
    return out


D1, D2, D3 = THURSDAY.date(), (THURSDAY + timedelta(days=1)).date(), (THURSDAY + timedelta(days=4)).date()


class OvernightAndHoldTests(unittest.TestCase):
    def test_overnight_buys_the_close_and_sells_the_open(self):
        a = session_day(D1, [100.0] * 389 + [100.0])
        b = session_day(D2, [101.0] * 10)
        b[0]["o"] = 101.0  # gapped up overnight
        tr = ideas.overnight([(D1, a), (D2, b)])
        self.assertEqual(len(tr), 1)
        # $1,000 buys 10 shares at $100: +$1 each = $10, less a penny each way per share = $0.20
        self.assertEqual((tr[0]["usd"], tr[0]["net"], tr[0]["t"][11:16]), (10.0, 9.8, "15:59"))

    def test_no_trade_without_a_real_open(self):
        a = session_day(D1, [100.0] * 390)
        late = session_day(D2, [101.0] * 10, start=(10, 0))  # the morning's bars are missing
        self.assertEqual(ideas.overnight([(D1, a), (D2, late)]), [])

    def test_buy_and_hold_is_close_to_close_on_1000(self):
        a, b = session_day(D1, [100.0] * 5), session_day(D2, [102.0] * 5)
        tr = ideas.buy_hold([(D1, a), (D2, b)])
        self.assertEqual((tr[0]["net"], tr[0]["t"][:10]), (20.0, D2.isoformat()))


class SwingTests(unittest.TestCase):
    def days(self, *closes_by_day):
        return [(d, session_day(d, c)) for d, c in zip((D1, D2, D3), closes_by_day)]

    def test_trailing_stop_rises_with_the_price(self):
        # buy 100 at 10:00; rises to 102; then falls: the stop is 0.5% under 102 = 101.49
        up = [100.0] * 30 + [100.0 + 0.1 * k for k in range(1, 21)] + [102.0] * 10 + [101.8, 101.6, 101.4, 101.2]
        ds = self.days(up)
        tr = ideas.swing(ds, {D1: [(ds[0][1][30]["t"], 100.0)]})
        self.assertEqual(tr[0]["why"], "stop")
        self.assertAlmostEqual(tr[0]["exit"], 101.49, places=2)
        self.assertGreater(tr[0]["net"], 0)

    def test_a_gap_below_the_stop_fills_at_the_open(self):
        ds = self.days([100.0] * 40, [97.0] * 5)
        ds[1][1][0]["o"] = 97.0
        tr = ideas.swing(ds, {D1: [(ds[0][1][30]["t"], 100.0)]})
        self.assertEqual((tr[0]["why"], tr[0]["exit"], tr[0]["days_held"]), ("stop", 97.0, 1))

    def test_sold_after_the_last_day_and_one_at_a_time(self):
        ds = self.days([100.0] * 40, [100.2] * 40, [100.4] * 40)
        sig = {D1: [(ds[0][1][30]["t"], 100.0)], D2: [(ds[1][1][5]["t"], 100.2)]}
        tr = ideas.swing(ds, sig, max_days=2)
        self.assertEqual(len(tr), 1)  # still holding when D2's signal came
        self.assertEqual((tr[0]["why"], tr[0]["exit"], tr[0]["days_held"]), ("time", 100.4, 2))


class SharesAndBounceTests(unittest.TestCase):
    def test_shares_stop_fills_the_minute_it_is_hit(self):
        # touch the red area at a 10-minute check, then slide: the broker's stop fills at its level
        p = [100.5] * 10 + [100.7, 100.9, 101.05, 101.1, 101.1] + [101.1] * 5 + [100.9, 100.85, 100.8, 100.7, 100.6] * 3
        v = {**ideas.SHARES}
        tr = day_with(p, [RED]).trades(v)
        self.assertEqual((tr[0]["contract"], tr[0]["why"]), ("shares", "stop"))
        entry = tr[0]["entry"]
        self.assertAlmostEqual(tr[0]["exit"], round(entry * (1 - 0.0025), 2), places=2)
        qty = 1000 / entry
        self.assertAlmostEqual(tr[0]["net"], tr[0]["usd"] - 0.02 * qty, places=1)

    def test_bounce_idea_takes_only_bounces_up(self):
        # a bounce down off the red area (a put) is not taken; the desk's touch rule would buy a call there
        p = [100.5] * 10 + [100.7, 100.9, 101.1, 100.9, 100.8] + [100.6, 100.4, 100.2, 100.1, 100.0] * 3
        self.assertEqual(day_with(p, [RED]).trades({**ideas.BOUNCE, "poll": 0, "expiry": "0d"}), [])
        # from above: dips into the area and closes back above it → a call
        p = [101.6] * 10 + [101.4, 101.25, 101.15, 101.3, 101.4] + [101.5, 101.6, 101.7, 101.8, 101.9] * 3
        tr = day_with(p, [RED]).trades({**ideas.BOUNCE, "poll": 0, "expiry": "0d"})
        self.assertEqual((tr[0]["kind"], tr[0]["contract"]), ("bounce", "call"))


class IdeasRunTests(unittest.TestCase):
    def test_run_reports_every_idea(self):
        random.seed(5)
        bars, p = [], 600.0
        for n in range(14):
            d = THURSDAY - timedelta(days=20 - n)
            if d.weekday() >= 5:
                continue
            for m in range(240, 1200):
                o = p
                p += random.gauss(0, 0.08)
                bars.append({"t": at(d, 0, 0) + timedelta(minutes=m), "o": o, "h": max(o, p) + 0.03,
                             "l": min(o, p) - 0.03, "c": p, "v": 1000.0 + random.random() * 500})
        res = ideas.run(bt.group_days(bars, False), auto.config(load_json("rules.json")))
        self.assertEqual([i["name"] for i in res["ideas"]],
                         ["desk_now", "bounce", "shares", "swing", "overnight", "buy_hold"])
        bh = res["ideas"][-1]["all"]
        self.assertEqual(bh["trades"], res["days"] - 1)
        self.assertIn("| Buy and hold SPY |", ideas.markdown(res))
        self.assertEqual(set(res["monthly"]), {i["name"] for i in res["ideas"]})


if __name__ == "__main__":
    unittest.main()
