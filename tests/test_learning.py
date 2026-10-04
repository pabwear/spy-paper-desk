"""Learner tests: mistake catalog, signal multipliers, loss model, and that it can only ever skip."""

from __future__ import annotations

import random
import unittest
from datetime import timedelta

from helpers import THURSDAY, DeskTestCase, FakeBroker, at, falling_bars

import journal
import learning
import run_study
from common import load_json, save_json

RED = {"color": "red", "low": 589.5, "high": 590.5, "confluence": []}


def features(tags, pulse=0.0, minutes=0.2, inside=1.0, rsi_edge=0.3):
    f = {f"tag_{t}": 1.0 if t in tags else 0.0 for t in learning.TAG_KEYS}
    f.update(signal_buy=1.0, rsi_edge=rsi_edge, vwap_edge_pct=0.1, dist_mid_pct=0.02, inside_box=inside,
             minutes_after_10=minutes, pulse=pulse, entry_number=0.0, n_tags=float(len(tags)))
    return f


def write_trips(n: int, seed: int = 3) -> None:
    """Synthetic closed option trades: without the VWAP tag, trades mostly lose (the pattern to learn)."""
    rng = random.Random(seed)
    day = THURSDAY - timedelta(days=60)
    for i in range(n):
        has_vwap = i % 2 == 0
        tags = ["rsi", "vwap"] if has_vwap else ["rsi", "volume"]
        lose = rng.random() < (0.2 if has_vwap else 0.85)
        sym = f"SPY{(day + timedelta(days=i)):%y%m%d}C00590000"
        t_open = (day + timedelta(days=i)).replace(hour=10, minute=30)
        t_close = t_open.replace(hour=13)
        journal.log("order", now=t_open, role="entry", order_id=f"o{i}", symbol=sym, tags=tags,
                    features=features(tags, pulse=-1.0 if not has_vwap else 0.0), underlying_price=590.0)
        journal.log("order", now=t_close, role="exit", order_id=f"c{i}", symbol=sym,
                    reason="stop" if lose else "flatten")
        journal.record_fill(filled_at=t_open.isoformat(), order_id=f"o{i}", side="buy", qty=1, price=2.0,
                            context={"role": "entry", "tags": tags}, symbol=sym)
        journal.record_fill(filled_at=t_close.isoformat(), order_id=f"c{i}", side="sell", qty=1,
                            price=1.5 if lose else 2.6, context={"role": "exit", "reason": "stop" if lose else "flatten"},
                            symbol=sym)


class LearningTests(DeskTestCase):
    def test_nothing_to_learn_yet(self):
        r = learning.learn(at(THURSDAY, 16, 15))
        self.assertEqual((r["closed_trades"], r["model"]), (0, None))
        self.assertEqual(load_json("learning_weights.json")["learned_multipliers"], {})

    def test_learns_the_losing_pattern(self):
        write_trips(40)
        r = learning.learn(at(THURSDAY, 16, 15))
        self.assertEqual(r["closed_trades"], 40)
        model = learning.load_model()
        self.assertEqual(model["trained_on"], 40)
        p_good = learning.predict(model, features(["rsi", "vwap"]))
        p_bad = learning.predict(model, features(["rsi", "volume"], pulse=-1.0))
        self.assertLess(p_good, 0.5)
        self.assertGreater(p_bad, 0.5)
        self.assertTrue(model["walk_forward"]["beats_base_rate"], model["walk_forward"])
        mult = load_json("learning_weights.json")["learned_multipliers"]
        self.assertGreater(mult["vwap"], 1.0)
        self.assertLess(mult["volume"], 1.0)
        tags = {m["tag"]: m for m in r["mistakes"]}
        self.assertGreater(tags["stopped_out"]["trades"], 0)
        self.assertGreater(tags["against_market_pulse"]["loss_rate"], r["loss_rate"])
        self.assertTrue(any("Market Pulse" in lesson for lesson in r["lessons"]))

    def test_ops_failures_are_counted(self):
        journal.log("order_rejected", now=at(THURSDAY, 10, 30), symbol="X")
        journal.log("skip", now=at(THURSDAY, 10, 31), reasons=["no_market_data"])
        r = learning.learn(at(THURSDAY, 16, 15))
        ops = {f["tag"]: f["count"] for f in r["ops_failures"]}
        self.assertEqual((ops["order_rejected"], ops["no_market_data"]), (1, 1))

    def test_shadow_mode_never_acts(self):
        model = {"trained_on": 500, "walk_forward": {"beats_base_rate": True}}
        self.assertFalse(learning.may_veto(model, 0.99, {"mode": "shadow"})[0])

    def test_veto_needs_enough_trades_and_skill(self):
        cfg = {"mode": "veto", "min_trades_to_act": 30, "veto_threshold": 0.65}
        self.assertFalse(learning.may_veto({"trained_on": 12, "walk_forward": {"beats_base_rate": True}}, .9, cfg)[0])
        self.assertFalse(learning.may_veto({"trained_on": 40, "walk_forward": {"beats_base_rate": False}}, .9, cfg)[0])
        self.assertFalse(learning.may_veto({"trained_on": 40, "walk_forward": {"beats_base_rate": True}}, .5, cfg)[0])
        self.assertTrue(learning.may_veto({"trained_on": 40, "walk_forward": {"beats_base_rate": True}}, .9, cfg)[0])

    def test_veto_only_skips_and_shadow_still_enters(self):
        write_trips(40)
        learning.learn(at(THURSDAY - timedelta(days=1), 16, 15))
        save_json("aoi_override.json", {"symbol": "SPY", "tradable": True, "written_at": at(THURSDAY, 9, 45).isoformat(),
                                        "source": "Ops", "approximate": False, "zones": [RED]})
        now = at(THURSDAY, 10, 30)
        bars = falling_bars(THURSDAY, now)

        broker = FakeBroker()  # shadow: P(loss) is logged, the order still goes
        r = run_study.cmd_paper(now, broker_factory=lambda: broker, bars=bars)
        self.assertEqual(len(broker.submitted), 1)
        self.assertIsNotNone(r["p_loss"])

        # Veto mode with a model that is sure this setup loses: the only effect is a skip.
        lw = load_json("learning_weights.json")
        lw["ml"]["mode"] = "veto"
        save_json("learning_weights.json", lw)
        model = learning.load_model()
        model["bias"] = 10.0
        save_json(learning.MODEL_FILE, model)
        broker2 = FakeBroker()  # flat again; 1 of 2 entries used, so only the learner can stop this one
        r = run_study.cmd_paper(now, broker_factory=lambda: broker2, bars=bars)
        self.assertEqual(broker2.submitted, [])
        self.assertEqual(r["reasons"], ["ml_veto"])


class FitTests(unittest.TestCase):
    def test_predict_without_model(self):
        self.assertIsNone(learning.predict(None, {}))


if __name__ == "__main__":
    unittest.main()
