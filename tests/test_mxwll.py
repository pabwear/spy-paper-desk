"""The Mxwll Suite port: ATR, Areas of Interest, pivots, structure breaks, order blocks, and the desk's use of them."""

from __future__ import annotations

import unittest
from datetime import timedelta

from helpers import THURSDAY, DeskTestCase, FakeBroker, at

import journal
import run_study
from common import load_json, save_json
from gate import is_approximate
from signals import rank_zones
from studies import auto, mxwll

WEDNESDAY = THURSDAY - timedelta(days=1)


def candles(closes: list[float], wick: float = 0.1) -> list[dict]:
    """Candles that open at the previous close; highs/lows a wick beyond the body."""
    out, prev = [], closes[0]
    t = at(THURSDAY, 9, 30)
    for i, c in enumerate(closes):
        o = prev
        out.append({"t": t + timedelta(minutes=5 * i), "o": o, "h": max(o, c) + wick, "l": min(o, c) - wick,
                    "c": c, "v": 1000.0})
        prev = c
    return out


def leg(a: float, b: float, n: int) -> list[float]:
    return [a + (b - a) * i / n for i in range(1, n + 1)]


def minute_bars(day, start_hhmm=(9, 30), end_hhmm=(16, 0), price=600.0, step=0.0) -> list[dict]:
    t, end = at(day, *start_hhmm), at(day, *end_hhmm)
    out, p = [], price
    while t < end:
        out.append({"t": t, "o": p, "h": p + 0.2, "l": p - 0.2, "c": p + step, "v": 1000.0})
        p += step
        t += timedelta(minutes=1)
    return out


class AtrTests(unittest.TestCase):
    def test_wilder_seeded_with_simple_average(self):
        cs = candles([10, 11, 12, 11, 13], wick=0.5)
        trs = []
        for i, k in enumerate(cs):
            pc = cs[i - 1]["c"] if i else None
            trs.append(k["h"] - k["l"] if pc is None else max(k["h"] - k["l"], abs(k["h"] - pc), abs(k["l"] - pc)))
        a = mxwll.atr(cs, 3)
        self.assertEqual(a[:2], [None, None])
        self.assertAlmostEqual(a[2], sum(trs[:3]) / 3)
        self.assertAlmostEqual(a[3], (a[2] * 2 + trs[3]) / 3)
        self.assertAlmostEqual(a[4], (a[3] * 2 + trs[4]) / 3)


class AoiTests(unittest.TestCase):
    def test_boxes_from_last_50_bodies_plus_minus_atr(self):
        closes = leg(600, 610, 30) + leg(610, 595, 30)
        cs = candles(closes)
        aois = mxwll.areas_of_interest(cs)
        self.assertTrue(all(a is None for a in aois[:50]))  # closeArr.size() > 50
        last = aois[-1]
        window = cs[-50:]
        top_body = max(max(k["o"], k["c"]) for k in window)
        low_body = min(min(k["o"], k["c"]) for k in window)
        atr = mxwll.atr(cs, 14)[-1]
        self.assertAlmostEqual(last["red"]["low"], round(top_body, 4))
        self.assertAlmostEqual(last["red"]["high"], round(top_body + atr, 4))
        self.assertAlmostEqual(last["green"]["high"], round(low_body, 4))
        self.assertAlmostEqual(last["green"]["low"], round(low_body - atr, 4))
        self.assertTrue(last["red"]["visible"] and last["green"]["visible"])

    def test_red_box_hides_when_price_runs_far_above_it(self):
        cs = candles([600.0] * 55 + [700.0])
        self.assertFalse(mxwll.areas_of_interest(cs)[-1]["red"]["visible"])


class PivotTests(unittest.TestCase):
    def test_swings_confirm_length_bars_later(self):
        closes = leg(600, 590, 10) + leg(590, 605, 10) + leg(605, 595, 10)
        cs = candles(closes)
        tops, bots = mxwll.pivots(cs, 3)
        # With equal lows (or highs) the swing is the last of them: Pine compares with a strict < (>).
        low_i = max(i for i in range(len(cs)) if cs[i]["l"] == min(k["l"] for k in cs))
        high_i = max(i for i in range(len(cs)) if cs[i]["h"] == max(k["h"] for k in cs))
        self.assertEqual(bots[low_i + 3], cs[low_i]["l"])
        self.assertEqual(tops[high_i + 3], cs[high_i]["h"])
        self.assertEqual(sum(1 for b in bots if b), 1)
        self.assertEqual(sum(1 for t in tops if t), 1)

    def test_nothing_before_length_plus_two_bars(self):
        tops, bots = mxwll.pivots(candles(leg(600, 590, 10)), 3)
        self.assertFalse(any(tops[:5]) or any(bots[:5]))  # runs only when bar_index > length + 1


class StructureTests(unittest.TestCase):
    def setUp(self):
        # down, up (top at ~605), down below the trough (bear break), up through the top (bull break)
        self.cs = candles(leg(600, 590, 10) + leg(590, 605, 10) + leg(605, 585, 12) + leg(585, 612, 14))

    def test_internal_breaks(self):
        s = mxwll.structure(self.cs, 3, internal=True)
        kinds = [(e["dir"], e["kind"]) for e in s["events"]]
        self.assertGreaterEqual(len(kinds), 2)
        self.assertEqual(kinds[0], ("bear", "BoS"))  # first break: nothing to change character from
        bull = next(e for e in s["events"] if e["dir"] == "bull")
        self.assertEqual(bull["kind"], "CHoCH")  # first bullish break after a bearish one
        self.assertGreater(self.cs[bull["i"]]["c"], bull["level"])
        self.assertLessEqual(self.cs[bull["i"] - 1]["c"], bull["level"])

    def test_external_labels_and_blocks(self):
        s = mxwll.structure(self.cs, 3, internal=False)
        labels = [sw["label"] for sw in s["swings"]]
        self.assertIn("HH", labels)  # first top is above the 0.0 start level
        lows = [sw for sw in s["swings"] if sw["side"] == "low"]
        self.assertEqual(lows[0]["label"], "HL")  # 590 is not below the 0.0 start level
        if len(lows) > 1:
            self.assertEqual(lows[1]["label"], "LL")
        # the close above the old top mitigates its high block
        self.assertFalse(any(abs(b["top"] - s["swings"][0]["price"]) < 1e-9 for b in s["high_blocks"]))

    def test_block_cap_matches_pine_loop(self):
        blocks = [{"from_i": i, "top": 1.0, "bottom": 0.9} for i in range(11)]
        keep = 10
        del blocks[: len(blocks) - keep + 1]
        self.assertEqual(len(blocks), 9)  # `for i = size - showLast to 0` removes 2 of 11

    def test_analyze_summary(self):
        out = mxwll.analyze(candles(leg(600, 590, 20) + leg(590, 605, 20) + leg(605, 585, 20)))
        self.assertIsNotNone(out["aoi"])
        self.assertIn(out["internal"]["dir"], ("bull", "bear"))
        for b in out["order_blocks"]["low"] + out["order_blocks"]["high"]:
            self.assertLess(b["low"], b["high"])


class ResampleTests(unittest.TestCase):
    def test_regular_hours_five_minute_candles(self):
        bars = minute_bars(THURSDAY, (9, 0), (10, 0), step=0.01)
        cs = mxwll.resample(bars, 5, regular_hours_only=True)
        self.assertEqual(cs[0]["t"], at(THURSDAY, 9, 30))
        self.assertEqual(len(cs), 6)
        self.assertEqual(cs[0]["o"], bars[30]["o"])
        self.assertEqual(cs[0]["c"], bars[34]["c"])
        self.assertEqual(len(mxwll.resample(bars, 5, regular_hours_only=False)), 12)


class AutoZoneTests(DeskTestCase):
    def bars(self):
        return minute_bars(WEDNESDAY, step=-0.01) + minute_bars(THURSDAY, (9, 30), (10, 30), price=596.0, step=0.01)

    def test_snapshot_zones_are_approximate_until_trusted(self):
        cfg = auto.config(load_json("rules.json"))
        self.assertFalse(cfg["trusted"])
        self.assertIsNone(auto.zones_file("SPY", at(THURSDAY, 9, 35), self.bars(), cfg))
        z = auto.zones_file("SPY", at(THURSDAY, 10, 5), self.bars(), cfg)
        self.assertEqual(z["written_at"], at(THURSDAY, 9, 39, 59).isoformat())
        self.assertEqual(z["source"], auto.SOURCE)
        self.assertTrue(z["approximate"] and is_approximate(z))
        self.assertFalse(z["tradable"])
        self.assertEqual({x["color"] for x in z["zones"]}, {"red", "green"})
        # the snapshot ignores candles after 09:39
        later = auto.zones_file("SPY", at(THURSDAY, 10, 25), self.bars(), cfg)
        self.assertEqual(later["zones"], z["zones"])
        trusted = auto.zones_file("SPY", at(THURSDAY, 10, 5), self.bars(), {**cfg, "trusted": True})
        self.assertTrue(trusted["tradable"] and not trusted["approximate"])

    def test_no_snapshot_without_todays_bars(self):
        cfg = auto.config(load_json("rules.json"))
        self.assertIsNone(auto.zones_file("SPY", at(THURSDAY, 10, 5), minute_bars(WEDNESDAY), cfg))

    def test_tick_writes_auto_zones_but_never_over_ops_zones(self):
        now = at(THURSDAY, 10, 20)
        source = run_study.Bars(now, {"SPY": self.bars()})
        save_json("aoi_override.json", {"symbol": "SPY", "written_at": at(WEDNESDAY, 9, 45).isoformat(),
                                        "tradable": True, "zones": []})
        self.assertEqual(run_study.cmd_auto_zones(now, source), ["SPY"])
        self.assertEqual(load_json("aoi_override.json")["source"], auto.SOURCE)
        self.assertEqual(run_study.cmd_auto_zones(now, source), [])  # already done today
        ops = {"symbol": "SPY", "tradable": True, "written_at": at(THURSDAY, 9, 45).isoformat(), "approximate": False,
               "zones": [{"color": "red", "low": 589.5, "high": 590.5, "confluence": []}]}
        save_json("aoi_override.json", ops)
        self.assertEqual(run_study.cmd_auto_zones(now, source), [])
        self.assertEqual(load_json("aoi_override.json"), ops)

    def test_auto_zones_never_reach_the_broker(self):
        now = at(THURSDAY, 10, 20)
        broker = FakeBroker()
        run_study.cmd_tick(now, broker_factory=lambda: broker, bars={"SPY": self.bars()})
        self.assertEqual(load_json("aoi_override.json")["source"], auto.SOURCE)
        self.assertEqual(broker.submitted, [])
        self.assertTrue(any(e["event"] == "aoi" and e.get("source") == auto.SOURCE for e in journal.read_events()))
        self.assertIn("study", load_json("charts.json")["SPY"])


class StudyTagTests(unittest.TestCase):
    zones = [{"color": "red", "low": 599.0, "high": 600.0, "confluence": ["CHoCH"]},
             {"color": "green", "low": 610.0, "high": 611.0, "confluence": []}]

    def test_structure_tag_follows_latest_internal_break(self):
        study = {"internal": {"dir": "bull"}, "order_blocks": {"high": [], "low": []}}
        self.assertEqual(auto.zone_tags(study, self.zones), {"red": ["structure"]})
        study = {"internal": {"dir": "bear"}, "order_blocks": {"high": [], "low": []}}
        self.assertEqual(auto.zone_tags(study, self.zones), {"green": ["structure"]})
        self.assertEqual(auto.zone_tags(None, self.zones), {})

    def test_order_block_tag_on_overlap(self):
        study = {"internal": None, "order_blocks": {"high": [{"low": 610.5, "high": 612.0}], "low": []}}
        self.assertEqual(auto.zone_tags(study, self.zones), {"green": ["order_blocks"]})

    def test_tags_count_once_and_zones_stay_unchanged(self):
        before = [dict(z) for z in self.zones]
        ranked = rank_zones(self.zones, 599.5, None, None, None, {"structure": 1.0}, 0.15, None,
                            study_tags={"red": ["structure"]})
        red = next(r for r in ranked if r["zone"]["color"] == "red")
        self.assertEqual(red["tags"], ["structure"])
        self.assertEqual(self.zones, before)  # the gate compares zone dicts; the study must not change them


if __name__ == "__main__":
    unittest.main()
