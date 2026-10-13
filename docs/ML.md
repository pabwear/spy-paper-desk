# ML trading-research engine (`ml/`)

A research pipeline that asks one question honestly: **can a model predict SPY's next 15–60 minutes well enough to
make money after costs?** It learns from minute bars, makes calibrated probability predictions, tests them
walk-forward with a realistic cost-aware backtester, and runs in **shadow mode** on the desk: it predicts and logs,
and never places an order. Paper orders need a model that passed rules written in advance, a locked final test, and
Roy's say-so. Live trading is not implemented.

Results so far: see [Results](#results). Short version: **no model has shown an edge after costs.**

## Architecture

```
            ┌────────────────────────── research (GitHub Actions: ml.yml, manual) ───────────────────────────┐
 Alpaca IEX │ data.py ──► clean ──► features.py ──► labels.py ──► splits.py (walk-forward, purge, holdout lock) │
 minute bars│                                                    │                                            │
 (free)     │                       models.py (base rate, logistic, GBT) · tcn.py (PyTorch TCN)              │
            │                                                    │ calibrate + threshold on VALIDATION only   │
            │                       backtest.py ◄── risk.py      ▼                                            │
            │                       evaluate.py ──► report.py ──► ml-results branch (reports, models)        │
            └────────────────────────────────────────────────────────────────────────────────────────────────┘
                                         │ export (numpy weights, reviewed, copied to ml/models/active)
            ┌──────────────── desk.yml, every 10 min in market hours (shadow.py) ──────────────────┐
            │ last 20 days of bars ─► features ─► infer.py (numpy) ─► risk.py ─► decision.py         │
            │                                                     execution.py: shadow = NO orders  │
            │ ml_state.json ─► site_publish ─► trade.riosventures.org (Stocks ▸ "Model (research)")  │
            └────────────────────────────────────────────────────────────────────────────────────────┘
```

The core (features, labels, splits, models, backtest, risk, decision) is pure: no network, no broker. Data enters
through `data.py` providers. Orders can only leave through `execution.py`, which refuses in shadow mode. The desk's
existing books, rules and gates are untouched; shadow mode is a separate step that cannot stop the desk
(`continue-on-error`).

## Modules

| File | Job |
|---|---|
| `config.py`, `default_config.json` | All settings, validated; a hash of them is stored with every result |
| `data.py` | Providers (Alpaca IEX with split adjustment, monthly cache, synthetic for tests) and cleaning |
| `features.py` | 25 point-in-time features, versioned (`FEATURE_VERSION`) |
| `labels.py` | What a long trade would have earned after costs, per horizon |
| `splits.py` | Walk-forward folds, purging/embargo, the holdout lock (`holdout.json`) |
| `models.py`, `tcn.py` | Base rate, logistic regression, gradient-boosted trees; the TCN (PyTorch + numpy inference) |
| `backtest.py` | Minute-by-minute simulation of the trading rules |
| `risk.py` | Independent risk engine (sizing and limits) |
| `decision.py` | Prediction → BUY_CANDIDATE / EXIT_LONG / HOLD / NO_TRADE / RISK_REJECTED / UNAVAILABLE |
| `execution.py` | Shadow / paper boundary; live refused |
| `walkforward.py`, `evaluate.py`, `report.py`, `run.py` | Research runs, scoring, reports, command line |
| `infer.py`, `shadow.py` | Exported numpy models; the desk's shadow step |
| `acceptance.json` | The pass rules, written before any result |

## Data

- **Source:** Alpaca market data, IEX feed, 1-minute bars, split-adjusted, from 2020-07 (free; uses the desk's
  existing keys). IEX is one exchange, so its volume is a small slice of the market and quiet minutes can be
  missing.
- **Timestamps:** a bar stamped `t` covers `[t, t+1 min)` and is known at `t+1 min`. Everything is New York time.
- **Cleaning:** regular hours only (09:30–16:00; 13:00 on half days). Duplicates: the last one wins. Impossible
  bars are dropped. Every session is put on a full minute grid: **missing minutes stay missing (NaN, flagged),
  never filled or zeroed**. Sessions under 50% coverage (counted up to their last bar) are dropped. Each run writes
  an audit: rows, missing minutes, duplicates, invalid bars, half days, dropped sessions, and a data hash.
- **Not used (yet):** quotes and spreads (historical IEX quotes are huge; a fixed cost is used instead), sector and
  market context, corporate actions beyond splits.

## Features (version 1)

All features at minute *i* use minutes ≤ *i* only. This is tested: rows ≤ *i* stay identical when everything after
*i* is changed, including when a session opens with missing minutes. Definitions are in `features.DEFINITIONS`:
returns over 1/5/15/30 minutes and since the open, the overnight gap, realized volatility over 15 and 60 minutes and
their ratio, ATR and range expansion, candle body and wicks, distance from the VWAP and from the day's high and low,
relative volume against the same minutes of the previous 10 sessions, volume acceleration, time of day (and its
sine and cosine), day of week, staleness, and the missing share over the last 30 minutes.

A row is *usable* when every feature is known and the price is at most 2 minutes stale.

## Labels

For a decision on bar *i* and horizon *h*:
- entry at the **open of bar i + 1 + entry_delay_bars** (1 bar of delay for the cloud run's start-up);
- exit at the close *h* minutes later;
- `net = log(exit/entry) − ($0.02 per share round trip / price + 2 × 0.5 bp slippage)`.

| Target | Meaning |
|---|---|
| `up_net` | Main target: a long trade made money after costs |
| `up` | The price rose |
| `big_up` / `big_down` | The move beat costs + 5 bp, up or down |
| `net` | The net return itself |

Never across sessions: a window that would pass the close is NaN, not guessed. `label_end` is kept for purging.

## Testing method (no leakage, no shuffling across time)

- **Walk-forward, by month:**
  - train on 24 months (every usable minute, every 2nd for the TCN);
  - pick settings on the next 3 months (the desk's decision times only);
  - score the 3 months after that;
  - step 3 months and repeat. 14 folds, test windows Nov 2022 → Apr 2026.
- **Settings fitted on train or validation only:** scalers on the train window; model settings, calibration (Platt)
  and the entry threshold on the validation window. The threshold is the lowest p ≥ 0.50 whose validation backtest
  made money after costs with ≥ 15 trades; if none does, the model **abstains** in that fold.
- **Purging:** training rows whose labels reach past the next window's start (minus a 60-minute embargo) are dropped.
- **Locked holdout:** 2026-05-01 onward (`ml/holdout.json`, set 2026-10-13 before any training). Every training and
  research path calls `splits.guard()`. A final evaluation is recorded, and a second one with different settings is
  refused.
- **Controls:**
  - null: on a pure random walk, nothing may pass;
  - positive: on a planted, learnable trend, every model must find it and make money.
  Both are in the test suite.

## Models

| Model | Notes |
|---|---|
| Base rate | Predicts the training share every time: the "no skill" yardstick |
| Logistic regression | Standardized, L2; C ∈ {0.01, 0.1, 1} picked on validation log loss |
| Gradient-boosted trees | scikit-learn HistGradientBoosting; depth/trees picked on the *time-ordered* validation window. Internal early stopping is off, because its random hold-out would leak overlapping labels |
| TCN | 4 causal residual conv blocks (dilations 1/2/4/8, 32 channels, ~20k weights), last 30 minutes in. AdamW, early stopping (patience 3, ≤ 15 epochs), LR halving, gradient clipping, fixed seeds, deterministic ops |
| LSTM | Not built: the plan only adds one if the TCN shows incremental value over the baselines, and it has not |

All models are calibrated on validation and scored on identical rows, folds, thresholds rules and costs.

## Backtester

Minute by minute, long only:
- Fills at the next open after the decision (+ delay), with slippage and per-share costs on both sides.
- Stops sized from ATR (3 × ATR, at least 8 bp). A bar opening through the stop fills at that worse open. When one
  minute touches both the stop and the target, **the stop is assumed first**.
- Time exit at the close of the last minute of the horizon; flat at 15:40.
- One position at a time, a cooldown, ≤ 4 entries a day, cash-only buying power.
- Every refused signal is counted with its reason.

## Risk engine (`risk.py`, independent of every model)

| Setting | Default | Why |
|---|---|---|
| `risk_per_trade_pct` | 0.5 | Loss at the stop ≤ 0.5% of equity ($5 on $1,000) |
| `max_position_pct` / `max_exposure_pct` | 80 / 80 | Never all-in; never above buying power |
| `max_daily_loss_pct` | 1.5 | No new entries after −1.5% on the day |
| `max_consecutive_losses` | 3 | Stop for the day after 3 losses in a row |
| `max_positions` | 1 | One at a time |
| `max_orders_per_day` | 8 | Caps churn |
| `max_spread_bps` | 5 | When a quote is known |
| `max_data_age_minutes` | 2 | Stale data → no entry |
| `cooldown_after_loss_minutes` | 30 | No revenge trades |
| `model_timeout_ms` | 2000 | A slow model counts as failed |
| `min_stop_bps` | 8 | Stops inside the noise are refused |
| `allow_short` / `live` | false / false | Long only; never live |

Size = (equity × risk %) / stop distance, then capped. **Confidence never raises the size.** Any unknown input means
no entry (fail closed). The kill switch (`ml_kill_switch.json` in the desk state: `{"on": true, "why": "..."}`)
blocks new entries at once. Exits stay allowed, because closing reduces risk, and the desk's 15:40 flatten still
runs. Turning the kill switch on does not by itself liquidate anything.

## Execution modes

- **shadow** (default): log decisions and send nothing.
- **paper:** needs ALL of these:
  - `model.json approved: true` (passed `acceptance.json` and its single holdout run);
  - `execution.approved_by_roy: true` (only after Roy says so in chat);
  - a separate ML paper account number in the config, with its own keys `ALPACA_ML_API_KEY` /
    `ALPACA_ML_SECRET_KEY`;
  - the paper host and that exact account confirmed by the broker;
  - the kill switch off.

  Orders get a client id per decision (no duplicates), are read back (never assumed filled), and every step is
  journaled in `ml_journal.jsonl`.
- **live:** refused. There is no live code path.

## Commands

```bash
pip install -r requirements-ml.txt                       # numpy, pandas, scikit-learn, PyTorch (CPU), alpaca-py
python -m unittest discover -s ml/tests -t . -v          # ML tests (synthetic data, no keys, no network)
python -m unittest discover -s tests                     # the desk's own tests
python -m ml.run walkforward --synthetic --since 2022-01-01 --until 2026-05-01 --horizons 15 --max-folds 2   # dry run
python -m ml.run audit --since 2020-07-01                # needs ALPACA_API_KEY / ALPACA_SECRET_KEY
python -m ml.run walkforward --since 2020-07-01 --horizons 15,30,60 --models base_rate,logistic,gbt,tcn
python -m ml.run export --model logistic --horizon 60    # the shadow model -> ml/models/active
python -m ml.shadow                                      # one shadow decision now (what desk.yml runs)
```

In the cloud: **Actions → "ML research" → Run workflow** (command walkforward or export). Reports and exported
models land on the `ml-results` branch. An exported model is reviewed and then copied to `ml/models/active` on main
for the desk to use.

## Results

See the latest report on the `ml-results` branch and `ml/research_summary.json` (also shown on the dashboard).
The verdicts are recorded in RESEARCH_NOTES.md.

## Known limits

- One price per minute (IEX), with a fixed cost model. Real spreads and queue position are not modelled; partial
  fills are not modelled for the backtest.
- The desk acts every ~10 minutes with up to ~2 minutes of start-up delay. This is not true scalping, and horizons
  are 15–60 minutes.
- 14 folds of 3 months each; per-fold numbers are noisy. Several models and horizons are compared, so one could look
  good by luck. That is why only one candidate may use the holdout, once.
- The holdout period overlaps with months the desk's *other* research (intraday.py etc.) already looked at for
  different strategies.
- The forward (shadow) results are hypothetical: no order, no fill, no slippage measured.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Dashboard "Model" says Unavailable | Read its *Why* line: no model installed, feed down, data stale, inputs out of range, or features incomplete (e.g. first minutes of the day) |
| `HoldoutError` | Something tried to use data on or after the holdout start; that is the guard working |
| `ModelLoadError: feature schema differs` | Features changed (version bump); re-export the model |
| `ConfigError` | A setting is unsafe (e.g. embargo shorter than the longest horizon); the message says which |
| ML research run slow | The TCN dominates; run `--models base_rate,logistic,gbt` for a quick look |

## Security

- Keys come only from environment variables (GitHub secrets); they are never printed or logged.
- The repo is public: reports and exported models are public too. No account data is in them.
- The ML paper account (if ever created) uses its own keys, and its number is checked on every connection.
- Model files are numpy `.npz` loaded with `allow_pickle=False` plus JSON, so no code is loaded from model files.

## Before any live trading (not planned)

All of these would be needed first:
- months of forward paper results that agree with the backtest;
- real spread and fill measurements;
- a separate, reviewed live code path with its own approvals;
- position-level broker stops;
- Roy's explicit decision.

None of this exists, by design.
