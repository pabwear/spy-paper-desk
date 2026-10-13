"""Command line for the ML engine. Research and paper only: nothing here can place an order.

    python -m ml.run audit       --since 2020-07-01                  # fetch + clean, print the data audit
    python -m ml.run walkforward --since 2020-07-01 --horizons 15,30,60 --models base_rate,logistic,gbt
    python -m ml.run walkforward ... --synthetic                     # same pipeline on fake data (no keys)

Market data comes from Alpaca (ALPACA_API_KEY / ALPACA_SECRET_KEY in the environment, never printed) through a
month-by-month cache in ml_cache/. Reports go to ml/reports/ and one line per run to ml/experiments.jsonl.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml import config as mlconfig  # noqa: E402
from ml import data, report, walkforward  # noqa: E402


def _provider(args, cfg):
    if args.synthetic:
        return data.SyntheticProvider(seed=int(cfg["seed"]))
    d = cfg["data"]
    return data.CachedProvider(data.AlpacaMinuteProvider(d["feed"], d["adjustment"]), ROOT / "ml_cache")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ml.run", description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["audit", "walkforward"])
    ap.add_argument("--symbol")
    ap.add_argument("--since")
    ap.add_argument("--until", help="default: today")
    ap.add_argument("--horizons", default=None, help="e.g. 15,30,60 (default: the config's)")
    ap.add_argument("--models", default="base_rate,logistic,gbt")
    ap.add_argument("--target", default="up_net")
    ap.add_argument("--max-folds", type=int, default=None)
    ap.add_argument("--synthetic", action="store_true", help="fake data, for a dry run without keys")
    ap.add_argument("--out", default=str(HERE / "reports"))
    a = ap.parse_args(argv)
    cfg = mlconfig.load()
    symbol = a.symbol or cfg["symbol"]
    since = date.fromisoformat(a.since or cfg["data"]["since"])
    until = date.fromisoformat(a.until) if a.until else date.today()
    bars, audit = data.load(_provider(a, cfg), symbol, since, until, cfg["session"])
    print(json.dumps({k: v for k, v in audit.items() if k != "dropped_sessions"}, indent=1))
    if a.command == "audit":
        return 0
    horizons = [int(x) for x in (a.horizons.split(",") if a.horizons else cfg["horizons_minutes"])]
    models = a.models.split(",")
    rep = walkforward.run(bars, cfg, horizons, models, target=a.target, max_folds=a.max_folds)
    h = mlconfig.digest(cfg)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    name = f"{stamp}-{symbol}-{a.target}{'-SYNTHETIC' if a.synthetic else ''}"
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    md = report.markdown(rep, audit, h)
    (out / f"{name}.md").write_text(md)
    (out / f"{name}.json").write_text(json.dumps({"config_hash": h, "config": cfg, "audit": audit, "report": rep},
                                                 default=str, indent=1))
    line = {"at": stamp, "name": name, "symbol": symbol, "target": a.target, "config_hash": h,
            "data_sha": audit.get("data_sha"), "range": [audit.get("first"), audit.get("last")], "horizons": horizons,
            "models": models, "synthetic": a.synthetic, "seed": cfg["seed"],
            "passed": {hz: [m for m, e in res.items() if (e.get("acceptance") or {}).get("passed")]
                       for hz, res in rep["results"].items()}}
    if not a.synthetic:  # the registry records real-data runs only
        with open(HERE / "experiments.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line, default=str) + "\n")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
