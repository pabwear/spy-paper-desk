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


class TimeframeTests(DeskTestCase):
    def setUp(self):
        super().setUp()
        self.now = at(THURSDAY, 15, 0)
        self.minutes = minute_bars(WEDNESDAY, step=0.01) + minute_bars(THURSDAY, (9, 30), (15, 1), price=604.0, step=-0.01)
        self.half = [{"t": at(THURSDAY - timedelta(days=d), 9, 30) + timedelta(minutes=30 * k), "o": 600.0, "h": 601.0,
                      "l": 599.0, "c": 600.5, "v": 1e5} for d in range(30, 1, -1) for k in range(13)]
        self.daily = [{"t": at(THURSDAY - timedelta(days=d), 0, 0), "o": 590.0 + d % 7, "h": 600.0, "l": 580.0,
                       "c": 592.0, "v": 1e7} for d in range(120, 0, -1)]

    def frames(self, **kw):
        import charts

        cfg = auto.config(load_json("rules.json"))
        return charts.frames(kw.get("m", self.minutes), kw.get("h", self.half), kw.get("d", self.daily), self.now, cfg)

    def test_every_timeframe_shortest_to_longest(self):
        f = self.frames()
        self.assertEqual(list(f), ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1D"])
        self.assertEqual(len(f["1m"]["c"]), 390)
        self.assertLessEqual(len(f["1D"]["c"]), 250)
        self.assertIsNotNone(f["30m"]["study"]["aoi"])  # 30-minute history gives it 50+ candles
        self.assertIsNone(f["15m"]["study"]["aoi"])  # two days of minutes is under 50 candles on 15m

    def test_four_hour_candles_follow_the_session(self):
        import charts

        cfg = auto.config(load_json("rules.json"))
        rth = charts.frames(self.minutes, self.half, self.daily, self.now, cfg, rth=True)
        eth = charts.frames(self.minutes, self.half, self.daily, self.now, cfg, rth=False)
        self.assertEqual({row[0][11:] for row in rth["4h"]["c"]}, {"09:30", "13:30"})  # regular hours: from the open
        self.assertEqual({row[0][11:] for row in eth["4h"]["c"]}, {"08:00", "12:00"})  # extended: on the clock

    def test_extended_hours_setting(self):
        rules = load_json("rules.json")
        self.assertFalse(auto.config(rules)["regular_hours_only"])  # Roy's chart: extended hours on
        self.assertTrue(auto.config(rules, {"extended_hours": False})["regular_hours_only"])
        self.assertFalse(auto.config(rules, {"extended_hours": "yes"})["regular_hours_only"])  # only a real true/false counts

    def test_extended_session_bounds(self):
        bars = minute_bars(THURSDAY, (3, 0), (21, 0), step=0.0)
        eth = mxwll.resample(bars, 60, regular_hours_only=False)
        self.assertEqual((eth[0]["t"].hour, eth[-1]["t"].hour), (4, 19))
        self.assertEqual(len(mxwll.resample(bars, 60, regular_hours_only=True)), 7)

    def test_today_daily_candle_comes_from_todays_minutes(self):
        last = self.frames()["1D"]["c"][-1]
        today = [b for b in self.minutes if b["t"].date() == THURSDAY.date()]
        self.assertEqual(last[0], THURSDAY.date().isoformat())
        self.assertEqual(last[1], round(today[0]["o"], 4))
        self.assertEqual(last[4], round(today[-1]["c"], 4))
        self.assertEqual(sum(1 for r in self.frames()["1D"]["c"] if r[0] == last[0]), 1)  # history's today row is not doubled

    def test_vwap_starts_over_each_day(self):
        f = self.frames()["5m"]
        first_today = next(i for i, r in enumerate(f["c"]) if r[0].startswith(THURSDAY.date().isoformat()))
        o, h, l, c = f["c"][first_today][1:5]
        self.assertAlmostEqual(f["vwap"][first_today], round((h + l + c) / 3, 4), places=3)
        self.assertIsNone(self.frames()["4h"]["vwap"])

    def test_missing_history_drops_only_the_long_timeframes(self):
        f = self.frames(h=None, d=None)
        self.assertIn("1m", f)
        self.assertEqual([r[0] for r in f["1D"]["c"]], [THURSDAY.date().isoformat()])

    def test_heartbeat_sends_timeframes_to_the_dashboard(self):
        bars = {"SPY": self.minutes, "SPY|30Min": self.half, "SPY|1Day": self.daily}
        run_study.cmd_tick(at(THURSDAY, 9, 50), broker_factory=lambda: FakeBroker(), bars=bars)
        chart = load_json("charts.json")["SPY"]
        self.assertIn("1h", chart["frames"])
        self.assertIn("1h", chart["frames_eth"])
        self.assertIs(chart["extended_hours"], True)
        self.assertEqual(chart["frames_eth"]["1D"], chart["frames"]["1D"])
        import projection_log

        logged = projection_log.read()
        self.assertTrue(logged and all(r["extended"] for r in logged))  # the desk's own view gets logged for scoring
        self.assertEqual(chart["frames_eth"]["30m"]["projection"]["version"], 2)
        st = chart["frames"]["30m"]["study"]
        for key in ("order_blocks", "internal_events", "external_events", "swing_points"):
            self.assertIn(key, st)
        self.assertIn("frames", load_json("dashboard_state.json")["charts"]["SPY"])
        offline = run_study.Bars(self.now, {"SPY": self.minutes})
        self.assertIsNone(offline.history("SPY", "30Min"))


class FibTests(unittest.TestCase):
    def test_fibs_cover_the_leg_until_the_old_low_breaks(self):
        # the pullback holds above the swing low: the line spans the whole leg up, levels measured from the high
        k = candles([600.0] * 30 + leg(600, 610, 30) + leg(610, 604, 30) + [604.0] * 30)
        f = mxwll.fibs(k, 25)
        self.assertEqual(f["dir"], "up")
        self.assertEqual((f["from"]["price"], f["to"]["price"]), (599.9, 610.1))
        self.assertEqual([lv["ratio"] for lv in f["levels"]], [0.236, 0.382, 0.5, 0.618, 0.786])
        self.assertAlmostEqual(f["levels"][0]["price"], 610.1 - 10.2 * 0.236, places=3)
        self.assertLess(f["from"]["t"], f["to"]["t"])

    def test_fibs_follow_a_break_below_the_old_low(self):
        k = candles([600.0] * 30 + leg(600, 610, 30) + leg(610, 595, 30) + [595.0] * 30)
        f = mxwll.fibs(k, 25)
        self.assertEqual(f["dir"], "down")
        self.assertEqual((f["from"]["price"], f["to"]["price"]), (610.1, 594.9))
        self.assertAlmostEqual(f["levels"][2]["price"], (610.1 + 594.9) / 2, places=3)

    def test_no_swing_no_fibs(self):
        self.assertIsNone(mxwll.fibs(candles([600.0] * 20), 25))


class FvgTests(unittest.TestCase):
    def k(self, o, h, lo, c, i):
        return {"t": at(THURSDAY, 9, 30) + timedelta(minutes=5 * i), "o": o, "h": h, "l": lo, "c": c, "v": 1.0}

    def test_three_down_candles_leave_a_gap_until_price_returns(self):
        rows = [self.k(601, 601.2, 600.5, 600.6, 0), self.k(600.6, 600.6, 598.0, 598.2, 1),
                self.k(598.2, 598.3, 597.0, 597.1, 2)]
        g = mxwll.fair_value_gaps(rows)
        self.assertEqual(g["down"], [{"dir": "down", "low": 598.3, "high": 600.5, "t": rows[1]["t"].isoformat(timespec="minutes")}])
        self.assertEqual(g["up"], [])
        rows.append(self.k(597.1, 599.0, 597.0, 598.9, 3))  # trades into it but not through: still open
        self.assertEqual(len(mxwll.fair_value_gaps(rows)["down"]), 1)
        rows.append(self.k(598.9, 600.5, 598.8, 600.4, 4))  # reaches the first candle's low: closed
        self.assertEqual(mxwll.fair_value_gaps(rows)["down"], [])

    def test_overlapping_candles_are_no_gap(self):
        rows = [self.k(600, 601, 599.5, 600.8, 0), self.k(600.8, 601.5, 600.2, 601.4, 1),
                self.k(601.4, 602, 600.9, 601.9, 2)]  # third low 600.9 is below the first high 601
        self.assertEqual(mxwll.fair_value_gaps(rows)["up"], [])

    def test_up_gap(self):
        rows = [self.k(600, 600.5, 599.8, 600.4, 0), self.k(600.4, 602, 600.4, 601.9, 1),
                self.k(601.9, 603, 601.0, 602.8, 2)]
        self.assertEqual([(g["low"], g["high"]) for g in mxwll.fair_value_gaps(rows)["up"]], [(600.5, 601.0)])


class RollingLevelTests(unittest.TestCase):
    def test_four_hour_and_day_levels_from_minutes(self):
        bars = minute_bars(WEDNESDAY, step=0.01) + minute_bars(THURSDAY, step=-0.01)
        bars[-10]["h"] = 650.0
        r = mxwll.rolling_levels(bars, True)
        self.assertEqual(r["4h"]["bars"], 240)
        self.assertEqual(r["4h"]["high"], 650.0)
        self.assertEqual(r["4h"]["high_t"], bars[-10]["t"].isoformat(timespec="minutes"))
        self.assertEqual(r["1d"]["bars"], 780)  # two regular sessions are fewer than 1,440 minutes
        self.assertFalse(r["1d"]["full"])
        self.assertIn(r["4h"]["activity"], ("Very Low", "Low", "Average", "High", "Very High"))

    def test_extended_hours_count_pre_market(self):
        bars = minute_bars(THURSDAY, (4, 0), (9, 30), price=590.0) + minute_bars(THURSDAY)
        self.assertEqual(mxwll.rolling_levels(bars, False)["1d"]["low"], 589.8)
        self.assertEqual(mxwll.rolling_levels(bars, True)["1d"]["low"], 599.8)

    def test_activity_ranks(self):
        hist = list(range(1, 101))
        self.assertEqual(mxwll.activity(hist, 5), "Very Low")
        self.assertEqual(mxwll.activity(hist, 50), "Average")
        self.assertEqual(mxwll.activity(hist, 99), "Very High")
