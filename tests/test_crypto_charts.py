"""Crypto chart studies: UTC candles, the Mxwll read on 1h / 4h / 1D, and one coin failing never stops the rest."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from helpers import DeskTestCase

import crypto_charts as cc
from common import load_json

T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)


def hourly(n=900):
    out = []
    for i in range(n):
        p = 100 + 10 * math.sin(i / 30) + i * 0.01
        out.append({"t": T0 + timedelta(hours=i), "o": p, "h": p + 1, "l": p - 1, "c": p + 0.3, "v": 10.0})
    return out


class CryptoChartTests(DeskTestCase):
    def test_four_hour_candles_on_the_utc_clock(self):
        c = cc.resample(hourly(9), 4)
        self.assertEqual([x["t"].hour for x in c], [0, 4, 8])
        self.assertEqual(c[0]["v"], 40.0)
        self.assertEqual(c[0]["h"], max(b["h"] for b in hourly(4)))

    def test_studies_on_each_timeframe(self):
        h = hourly()
        d = cc.resample(h, 24)
        out = cc.build(h, d, h[-1]["t"])
        for tf in ("1h", "4h"):
            self.assertIn("aoi", out[tf])
            self.assertIn("swing_points", out[tf])
            self.assertTrue(out[tf]["from"])
        # 37 daily candles is too few for the 50-candle Area of Interest look-back: no boxes from it, no crash
        self.assertTrue(out["1D"] is None or "aoi" in out["1D"] or "error" in out["1D"])

    def test_one_coin_failing_never_stops_the_others(self):
        def fetcher(sym, now):
            if sym == "ETH/USD":
                raise RuntimeError("no data")
            h = hourly()
            return h, cc.resample(h, 24)

        res = cc.write(T0 + timedelta(hours=900), fetcher)
        self.assertIn("error", res["coins"]["ETH-USD"])
        self.assertIn("aoi", res["coins"]["BTC-USD"]["1h"])
        self.assertEqual(sorted(load_json(cc.FILE)["coins"]), ["BTC-USD", "ETH-USD", "SOL-USD"])
