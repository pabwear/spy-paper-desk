"""trade.riosventures.org: the desk's files are copied to Cloudflare KV after each run, only when they changed,
and a missing secret or a Cloudflare failure never stops the desk."""

from __future__ import annotations

import io
import json

from helpers import DeskTestCase

import site_publish
from common import load_json, save_json

ENV = {"CLOUDFLARE_API_TOKEN": "t", "CLOUDFLARE_ACCOUNT_ID": "acct"}


class FakeOpener:
    def __init__(self, ok=True, boom=False):
        self.requests, self.ok, self.boom = [], ok, boom

    def __call__(self, req, timeout=None):
        if self.boom:
            raise OSError("network down")
        self.requests.append(req)
        return io.BytesIO(json.dumps({"success": self.ok, "errors": [] if self.ok else [{"message": "bad token"}]}).encode())


class SitePublishTests(DeskTestCase):
    def setUp(self):
        super().setUp()
        save_json("dashboard_state.json", {"generated_at": "t1"})
        save_json("chart_SPY.json", {"symbol": "SPY"})

    def sent(self, opener, i=-1):
        return {x["key"]: json.loads(x["value"]) for x in json.loads(opener.requests[i].data)}

    def test_sends_the_files_once_then_only_what_changed(self):
        op = FakeOpener()
        self.assertIn("sent", site_publish.publish(ENV, op))
        req = op.requests[0]
        self.assertEqual(req.get_method(), "PUT")
        self.assertIn(f"/accounts/acct/storage/kv/namespaces/{site_publish.KV_NAMESPACE}/bulk", req.full_url)
        self.assertEqual(req.headers["Authorization"], "Bearer t")
        self.assertEqual(set(self.sent(op)), {"dashboard_state.json", "chart_SPY.json", "account.json"})
        self.assertEqual(site_publish.publish(ENV, op), "nothing changed")
        save_json("dashboard_state.json", {"generated_at": "t2"})
        site_publish.publish(ENV, op)
        self.assertEqual(self.sent(op), {"dashboard_state.json": {"generated_at": "t2"}})

    def test_no_secrets_no_calls(self):
        op = FakeOpener()
        self.assertTrue(site_publish.publish({}, op).startswith("skipped"))
        self.assertEqual(op.requests, [])

    def test_a_failure_is_reported_not_raised_and_retried_next_run(self):
        for op in (FakeOpener(boom=True), FakeOpener(ok=False)):
            self.assertTrue(site_publish.publish(ENV, op).startswith("failed"))
            self.assertIsNone(load_json(site_publish.MANIFEST, None))  # nothing marked as sent
        op = FakeOpener()
        site_publish.publish(ENV, op)
        self.assertEqual(set(self.sent(op)), {"dashboard_state.json", "chart_SPY.json", "account.json"})
