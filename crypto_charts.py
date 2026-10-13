"""Studies for the Crypto page's chart: the same Mxwll read the SPY chart shows (Areas of Interest, BoS /
CHoCH breaks, swing labels, order blocks, fair value gaps, auto Fibonacci), worked out on Alpaca's free
crypto bars for BTC, ETH and SOL on 1-hour, 4-hour and daily candles (UTC clock; crypto trades all day).

Written to crypto_charts.json by crypto.yml every hour; the page draws it over the live Yahoo candles.
Watch only: nothing here trades. Pure except for `fetch` and `write`.

    python crypto_charts.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from common import save_json

FILE = "crypto_charts.json"
COINS = {"BTC-USD": "BTC/USD", "ETH-USD": "ETH/USD", "SOL-USD": "SOL/USD"}
FRAMES = {"1h": 1, "4h": 4, "1D": 24}


def resample(hourly: list[dict], hours: int) -> list[dict]:
    """Hourly candles -> `hours`-hour candles on the UTC clock (00:00, 04:00, ...; 1D = the UTC day)."""
    out: list[dict] = []
    for b in hourly:
        t = b["t"].astimezone(timezone.utc)
        k = t.replace(hour=t.hour - t.hour % hours, minute=0, second=0, microsecond=0)
        if out and out[-1]["t"] == k:
            c = out[-1]
            c["h"], c["l"], c["c"], c["v"] = max(c["h"], b["h"]), min(c["l"], b["l"]), b["c"], c["v"] + b["v"]
        else:
            out.append({"t": k, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]})
    return out


def study(candles: list[dict]) -> dict | None:
    from studies import mxwll

    try:
        out = mxwll.analyze(candles, mxwll.DEFAULTS)
    except Exception as e:  # noqa: BLE001 - a study problem never stops the chart
        return {"error": f"{type(e).__name__}: {e}"[:200]}
    if not out:
        return None
    lb = int(mxwll.DEFAULTS["aoi_lookback"])
    keep = ("aoi", "internal", "external", "order_blocks", "internal_events", "external_events", "swing_points",
            "fibs", "fvgs")
    return {**{k: out[k] for k in keep}, "as_of": out["as_of"], "candles": len(candles),
            "from": candles[-lb]["t"].isoformat(timespec="minutes") if len(candles) > lb else None}


def build(hourly: list[dict], daily: list[dict], now: datetime) -> dict:
    """{"1h": study, "4h": study, "1D": study} from hourly and daily candles, complete or forming."""
    hourly = [b for b in hourly if b["t"] <= now]
    out = {"1h": study(hourly[-1200:]) if hourly else None,
           "4h": study(resample(hourly, 4)[-600:]) if hourly else None,
           "1D": study([b for b in daily if b["t"] <= now][-400:]) if daily else None}
    return out


def fetch(symbol: str, now: datetime) -> tuple[list[dict], list[dict]]:
    from alpaca.data.historical import CryptoHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = CryptoHistoricalDataClient()

    def bars(tf, days):
        data = client.get_crypto_bars(CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=tf,
                                                        start=now - timedelta(days=days))).data.get(symbol, [])
        return [{"t": b.timestamp.astimezone(timezone.utc), "o": float(b.open), "h": float(b.high), "l": float(b.low),
                 "c": float(b.close), "v": float(b.volume)} for b in data]

    return bars(TimeFrame.Hour, 100), bars(TimeFrame.Day, 420)


def write(now: datetime | None = None, fetcher=fetch) -> dict:
    now = now or datetime.now(timezone.utc)
    out = {"updated_at": now.isoformat(timespec="seconds"), "coins": {}}
    for key, sym in COINS.items():
        try:
            h, d = fetcher(sym, now)
            out["coins"][key] = build(h, d, now)
        except Exception as e:  # noqa: BLE001 - one coin failing never stops the others
            out["coins"][key] = {"error": f"{type(e).__name__}: {e}"[:200]}
    save_json(FILE, out)
    return out


if __name__ == "__main__":
    res = write()
    for k, v in res["coins"].items():
        print(k, {tf: ("ok" if s and "aoi" in s else s) for tf, s in v.items()} if "error" not in v else v["error"])
