"""Decision engine outcomes, the execution boundary (shadow/paper/live), numpy model export, and shadow mode."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from ml.tests.helpers import CFG
from ml import config as mlconfig, data, decision, execution, features, infer, risk, splits
from common import ET, PAPER_HOST

GOOD = {"ok": True, "p": 0.62, "threshold": 0.58, "expected_net_bps": 2.0, "approved": True, "ood": False}
CTX = {"has_position": False, "data_age_minutes": 0.5, "max_data_age_minutes": 2}
OK_RISK = {"ok": True, "qty": 0.8, "passed": ["all"], "failed": []}


class DecisionTests(unittest.TestCase):
    def test_buy_only_when_everything_agrees(self):
        d = decision.decide(GOOD, CTX, OK_RISK)
        self.assertEqual((d["outcome"], d["qty"]), (decision.BUY, 0.8))

    def test_every_other_outcome_with_its_reason(self):
        cases = [
            ({**GOOD, "approved": False}, CTX, OK_RISK, decision.NO_TRADE, "not approved"),
            ({**GOOD, "p": 0.55}, CTX, OK_RISK, decision.NO_TRADE, "no-trade zone"),
            ({**GOOD, "threshold": None}, CTX, OK_RISK, decision.NO_TRADE, "abstains"),
            ({**GOOD, "expected_net_bps": -0.1}, CTX, OK_RISK, decision.NO_TRADE, "after costs"),
            (GOOD, CTX, {"ok": False, "failed": ["daily_loss_ok"]}, decision.RISK_REJECTED, "daily_loss_ok"),
            ({"ok": False, "error": "feed down"}, CTX, OK_RISK, decision.UNAVAILABLE, "feed down"),
            (GOOD, {**CTX, "data_age_minutes": 7}, OK_RISK, decision.UNAVAILABLE, "too old"),
            (GOOD, {**CTX, "data_age_minutes": None}, OK_RISK, decision.UNAVAILABLE, "too old"),
            ({**GOOD, "ood": True}, CTX, OK_RISK, decision.UNAVAILABLE, "training range"),
            ({**GOOD, "p": 0.4}, {**CTX, "has_position": True}, None, decision.EXIT, "signal turned"),
            (GOOD, {**CTX, "has_position": True, "minutes_held": 60, "horizon": 60}, None, decision.EXIT, "holding time"),
            (GOOD, {**CTX, "has_position": True, "minutes_held": 5, "horizon": 60}, None, decision.HOLD, "no exit"),
        ]
        for pred, ctx, rv, want, text in cases:
            d = decision.decide(pred, ctx, rv)
            self.assertEqual(d["outcome"], want, (pred, ctx))
            self.assertIn(text, " ".join(d["reasons"]))
            if want != decision.BUY:
                self.assertEqual(d["qty"], 0.0)

    def test_confidence_does_not_change_quantity(self):
        a = decision.decide({**GOOD, "p": 0.6}, CTX, OK_RISK)
        b = decision.decide({**GOOD, "p": 0.99}, CTX, OK_RISK)
        self.assertEqual(a["qty"], b["qty"])


class FakeOrder(SimpleNamespace):
    pass


class FakeClient:
    def __init__(self, account="PAML1", fail=False, status_fail=False):
        self.account, self.fail, self.status_fail, self.sent = account, fail, status_fail, []

    def get_account(self):
        return SimpleNamespace(account_number=self.account)

    def submit_order(self, order_data):
        if self.fail:
            raise RuntimeError("insufficient buying power")
        self.sent.append(order_data)
        return FakeOrder(id="o1", status="accepted")

    def get_order_by_id(self, oid):
        if self.status_fail:
            raise TimeoutError("timeout")
        return FakeOrder(id=oid, status="filled", filled_qty="0.5")


class ExecutionTests(unittest.TestCase):
    PAPER = mlconfig.merge(CFG, {"execution": {"mode": "paper", "approved_by_roy": True, "account_number": "PAML1"}})

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DESK_DIR"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("DESK_DIR", None)
        self.tmp.cleanup()

    def test_shadow_by_default_and_every_gate_needed(self):
        ok, why = execution.gates(CFG, {"approved": True}, False)
        self.assertFalse(ok)
        self.assertIn("shadow mode", " ".join(why))
        self.assertTrue(execution.gates(self.PAPER, {"approved": True}, False)[0])
        for cfg, meta, ks in [(self.PAPER, {"approved": False}, False), (self.PAPER, {"approved": True}, True),
                              (mlconfig.merge(self.PAPER, {"execution": {"approved_by_roy": False}}), {"approved": True}, False),
                              (mlconfig.merge(self.PAPER, {"execution": {"account_number": ""}}), {"approved": True}, False),
                              (mlconfig.merge(self.PAPER, {"execution": {"mode": "live"}}), {"approved": True}, False)]:
            self.assertFalse(execution.gates(cfg, meta, ks)[0])

    def test_refuses_wrong_host_or_account(self):
        with self.assertRaises(execution.ExecutionRefused):
            execution.PaperExecutor(FakeClient(), self.PAPER, "api.alpaca.markets")
        with self.assertRaises(execution.ExecutionRefused):
            execution.PaperExecutor(FakeClient(account="PA36VOEO5PHB"), self.PAPER, PAPER_HOST)

    def test_orders_are_read_back_deduplicated_and_journaled(self):
        ex = execution.PaperExecutor(FakeClient(), self.PAPER, PAPER_HOST)
        t = datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)
        r = ex.submit("SPY", "buy", 0.5, t, set())
        self.assertEqual((r["status"], r["filled_qty"]), ("filled", 0.5))
        dup = ex.submit("SPY", "buy", 0.5, t, {r["client_order_id"]})
        self.assertEqual(dup["status"], "duplicate")
        self.assertEqual(len(ex.c.sent), 1)
        bad = execution.PaperExecutor(FakeClient(fail=True), self.PAPER, PAPER_HOST).submit("SPY", "buy", 0.5, t, set())
        self.assertEqual(bad["status"], "rejected")
        unk = execution.PaperExecutor(FakeClient(status_fail=True), self.PAPER, PAPER_HOST).submit("SPY", "buy", 0.5, t, set())
        self.assertEqual(unk["status"], "unknown")  # never assumed filled
        events = [json.loads(x)["event"] for x in (Path(self.tmp.name) / "ml_journal.jsonl").read_text().splitlines()]
        for e in ("order_request", "order_sent", "order_status", "order_duplicate_skipped", "order_rejected", "order_status_unknown"):
            self.assertIn(e, events)
        with self.assertRaises(execution.ExecutionRefused):
            ex.submit("SPY", "short", 1, t, set())


SMALL = mlconfig.merge(CFG, {"splits": {"train_months": 2, "validation_months": 1, "holdout_months": 1}})


class ShadowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ml import walkforward

        cls.tmp = tempfile.TemporaryDirectory()
        cls.prov = data.SyntheticProvider(seed=8)
        raw = cls.prov.fetch("SPY", date(2026, 1, 1), date(2026, 5, 1))
        b, _ = data.clean(raw, SMALL["session"])
        cls.lock = Path(cls.tmp.name) / "h.json"
        splits.set_lock(date(2026, 4, 30), 1, cls.lock)
        cls.model_dir = Path(cls.tmp.name) / "model"
        cls.info = walkforward.train_final(b, SMALL, "logistic", 30, cls.model_dir, holdout_path=cls.lock)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.desk = tempfile.TemporaryDirectory()
        os.environ["DESK_DIR"] = self.desk.name

    def tearDown(self):
        os.environ.pop("DESK_DIR", None)
        self.desk.cleanup()

    def test_export_is_unapproved_numpy_and_matches_its_meta(self):
        self.assertFalse(self.info["approved"])
        self.assertEqual(self.info["train"], ["2026-01-01", "2026-03-01"])
        m = infer.load(self.model_dir)
        F = np.tile(np.array(m.meta["mean"]), (3, 1))  # typical inputs (all-zero rows are out of range)
        p, ood = m.predict_rows(F, np.zeros(3, int), np.arange(3))
        self.assertTrue(((p > 0) & (p < 1)).all())
        self.assertFalse(ood.any())

    def test_shadow_run_logs_never_orders_and_scores_forward(self):
        from ml import shadow

        now = datetime(2026, 4, 28, 11, 0, 20, tzinfo=ET)
        st = shadow.run(now, provider=self.prov, model_dir=self.model_dir, cfg=SMALL)
        self.assertTrue(st["prediction"]["ok"], st["prediction"])
        self.assertEqual(st["data"]["last_bar"][11:16], "10:59")  # the 11:00 minute is not complete yet
        self.assertEqual(st["decision"]["outcome"], decision.NO_TRADE)  # model not approved
        self.assertEqual(st["execution"]["orders_sent"], 0)
        self.assertFalse(st["execution"]["orders_allowed"])
        self.assertIn("decisions", st["forward"])
        later = datetime(2026, 4, 28, 13, 0, 20, tzinfo=ET)
        st2 = shadow.run(later, provider=self.prov, model_dir=self.model_dir, cfg=SMALL)
        first = st2["recent"][0]
        self.assertIsNotNone(first.get("result"))  # 30 minutes have passed: the 11:00 decision is scored
        self.assertEqual(st2["forward"]["scored"], 1)

    def test_fails_closed_without_a_model_or_with_the_kill_switch(self):
        from ml import shadow

        now = datetime(2026, 4, 28, 11, 0, 20, tzinfo=ET)
        st = shadow.run(now, provider=self.prov, model_dir=Path(self.desk.name) / "none", cfg=SMALL)
        self.assertEqual(st["decision"]["outcome"], decision.UNAVAILABLE)
        (Path(self.desk.name) / "ml_kill_switch.json").write_text(json.dumps({"on": True, "why": "test"}))
        st2 = shadow.run(now, provider=self.prov, model_dir=self.model_dir, cfg=SMALL)
        self.assertTrue(st2["kill_switch"]["on"])
        self.assertIn("kill_switch_off", st2["risk"]["failed"])
        self.assertIn("kill switch is on", st2["execution"]["why_not"])
