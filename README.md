# SPY paper desk

A weekday desk on the **Alpaca paper** account `PA36VOEO5PHB` (SPY shares, SNDK shares,
SPY options). It fades the Mxwll Price Action Suite areas of interest:
buy only in red, sell only in green, and only after the 09:30–09:59 ET open has
produced real zones from Roy's TradingView chart.

**Three trading books (since 2026-10-07, Roy's request).** Each trades on its own: one
position at a time, its own daily entry limit and budget, all capped by the cash in the
account (`rules.json` `books`). Shares are bought in whole shares with the stop held at
Alpaca (one order triggers the other), so it fills the minute it's hit; the options book
keeps the desk's own stop check every 10 minutes (Alpaca holds no stops for options).

| Book | Trades | Budget | Stop | Why |
|---|---|---|---|---|
| `spy_shares` | SPY shares | $4,000 | 0.25%, held at Alpaca | the idea that turned the desk's signals profitable in the 6-year backtest (`backtests/ideas.md`) |
| `sndk_shares` | SNDK shares | $3,000 | a quarter of SNDK's average daily range (≈1–3%), held at Alpaca | Roy's pick; roughly break-even in its 1.6-year backtest (`backtests/ideas-sndk.md`) |
| `spy_options` | 1 SPY call, ~30 days out | $3,000 | 0.25% of SPY, checked every 10 minutes | kept so the learner keeps learning options; lost money in the backtest |

TSLA is on the focus list to watch: charts, areas and studies like SPY, no trading.
The budgets add up to $10,000; until the paper account holds that, each book buys only
what the cash allows (a $1,000 account buys 1 SPY share and no SNDK share).

**Current setup (since 2026-10-07).** The settings that came closest to break-even
in the six-year backtest (`backtest_areas.py`; results in `backtests/areas.md` on
desk-state). It still lost a little there, so this is paper practice, not a proven edge.
`backtest_areas.py --current` replays exactly this setup (10-minute checks, the
affordable ~30-day pick); `rehearsal.py` runs the real desk program heartbeat by
heartbeat over recent days with a simulated broker, to catch bugs before they cost a day.

| Setting (`rules.json`) | Now | Originally |
|---|---|---|
| `trade_colors` | `["red"]`: a call in the red area only | red and green (puts in green) |
| `min_confluence` | 0 (signals still logged) | 2 |
| `stop_underlying_pct` | 0.25 | 0.35 |
| `max_hold_minutes` | 30 (checked each run, so about 30–40 minutes) | none (to the flatten) |
| `option.expiry_target_days` / `expiry_min_days` / `max_cost_usd` | about 30 days out, at least 7, one contract at most $1,000 (priced from Alpaca's free option quotes before choosing; no affordable expiry → no trade) | nearest listed expiry |
| `studies.mxwll.trusted` | true: the desk trades its own 09:39 boxes when you enter none that morning (yours win when you do) | false: its own boxes were practice-only |

Where the rest of this README says 0.35 %, two signals, puts in green or the nearest
expiry, it describes the original rules; the table wins.

This is a simulated study, not financial advice. It never places a live order.

**Free sources only (Roy's rule, 2026-10-07).** Every data source and service stays free or on a free
tier: Alpaca paper + IEX data, GitHub Actions free minutes, cron-job.org, Stocktwits' public stream and
connector, Stocklake's free tier, Yahoo Finance RSS, yfinance, and the AI reader running on Roy's Claude
plan. Nothing that bills per use or per month (X's API, Alpaca's real-time data plan, a Claude API key,
paid scrapers) gets added without Roy saying so in chat.

## Instruments and switches (`rules.json`)

| Key | Now | Meaning |
| --- | --- | --- |
| `active` | `spy_options` | Which path may trade: `spy_options`, `spy_shares`, `sndk_shares`, `sndk_options` |
| `spy_options_enabled` | `true` | 1 contract, never resized to a dollar target |
| `shares_enabled` | `false` | SPY shares, $800 notional (80% of the $1,000 book in `risk.json`) |
| `sndk_enabled` | `false` | Always refuses. There is no SNDK strategy here, and the gate only allows SPY |

The active instrument trades only if its switch is `true`. To turn shares back
on, set `"active": "spy_shares"` and `"shares_enabled": true`. To go back to
options, set `"active": "spy_options"`. No code changes either way.

A contract is never resized. One SPY contract is already a large bet against a
$1,000 book. Premium × 100 is often $100–$300 and can be more. The paper
account rejects an order it cannot afford. The desk logs the rejection and
does not retry.

This repository is standalone. It isn't connected to any website or web app,
and nothing outside this folder imports it or serves it.

## Running in the cloud (GitHub Actions): nothing to install

This is the easiest way to run the desk. GitHub runs it for you on a schedule,
inside this private repo.

| Workflow | When | What it does |
| --- | --- | --- |
| **Desk (every 10 minutes)** | weekdays, about 09:30–16:20 New York time | `run_study.py tick`: watch-only until 10:00; then exits plus at most one gated entry; exits only from 15:40; at 16:10 sync and review |
| **Today's zones** | you, 09:30–09:59 | A form: the stock, its RED and GREEN boxes as LOW-HIGH, optional tags, or "no readable boxes" |
| **Focus list** | any time | add, remove, up, down; trade-on and trade-off for a non-SPY stock |
| **Market Pulse** | mornings | live readings JSON → today's bias |
| **Tests** | every push | the test suite |

**One-time setup:** in **Settings → Secrets and variables → Actions → New
repository secret**, add `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`. Use the
**paper** keys for Paper 1000. Never paste keys anywhere else.

**Running state** (journal, ledger, account, zones, focus, pulse, learner) lives
on the `desk-state` branch, so `main` keeps only code and settings. Each run
loads it, works, and commits it back. Every run uses one queue, so two runs
never write at the same time.

**Timing.** GitHub's schedule can run a few minutes late. So the 0.35% stop is
checked about every 10 minutes, and closing starts at 15:40 (`risk.json`) so
the desk is flat by 16:00. If you need checks to the minute, run it on your own
computer instead (below).

**Cost.** About 48 short runs per weekday fit inside GitHub's free 2,000
minutes a month for private repos.

## Choosing which stocks to focus on (`watchlist.json`)

The desk evaluates every stock on the **focus list** each run, in priority order.
SPY is first by default. Change the list from the console's **Focus list** tab
(add, stop watching, move up or down) or from the command line:

```bash
python3 run_study.py focus list
python3 run_study.py focus add NVDA          # watch: read bars, score zones, log what it would do
python3 run_study.py focus remove NVDA
python3 run_study.py focus trade NVDA --instrument options --on   # local CLI only: paper-trade it
python3 run_study.py focus trade NVDA --off
```

**Adding a stock only watches it.** It trades only after its switch is on:

| Stock | Instrument | Switch |
| --- | --- | --- |
| SPY | `rules.json` `active` | `spy_options_enabled` / `shares_enabled` |
| SNDK | `watchlist.json` `instrument` | `rules.json` `sndk_enabled` (false) |
| any other | `watchlist.json` `instrument` (`options` or `shares`) | that entry's `trading_enabled` |

The console can change the focus list but can't switch trading on. Each stock
needs its own same-day Mxwll read from 09:30–09:59:
`python3 run_study.py aoi set --symbol NVDA --zone red:LOW:HIGH`. That writes
`aoi_override.NVDA.json`; SPY keeps `aoi_override.json`. These limits apply across
all stocks: one position at a time and 2 entries a day. When more than one stock
has a valid setup, the higher confluence score wins and ties go to focus order.
The other stocks are logged as "another stock had the stronger setup".

The exit manager also handles any position the desk itself opened, even after the
stock leaves the focus list. It never touches holdings it didn't open, such as a
manual SNDK position while SNDK is switched off.

## Live Market Pulse (Stocklake + Stocktwits)

Every weekday morning the pulse agent reads two live sources: Stocklake's market
pulse (SPY change and RSI, VIX, fear & greed, breadth) and Stocktwits sentiment
for the focus stocks. It writes the numbers to a JSON file and runs:

```bash
python3 run_study.py pulse ingest readings.json     # format: see pulse.py
```

Each reading votes bullish, bearish or not at all, using the thresholds in
`rules.json` → `pulse_rules`. The bias needs at least ±2 net votes; otherwise it
is **neutral**, and neutral never blocks a trade. For example, on Oct 2 the
readings were SPY +0.62% (bull), fear & greed 31 (bear) and Stocktwits 72
(bull). That nets +1, so the bias was neutral. These thresholds are starting
defaults. Change them to match how you read the market. `pulse set` still
works for a manual override.

## Google Sheets mirror

After every `sync` and `review`, the desk can replace three tabs in a Google
Sheet: **Trades**, **Closed trades** and **Daily reviews**. One-time setup:

1. Create a sheet, then go to **Extensions → Apps Script** and paste:

   ```js
   const TOKEN = 'choose-a-long-random-string';
   function doPost(e) {
     const body = JSON.parse(e.postData.contents);
     if (body.token !== TOKEN) return ContentService.createTextOutput('forbidden');
     const ss = SpreadsheetApp.getActiveSpreadsheet();
     for (const [name, table] of Object.entries(body.tabs)) {
       const sh = ss.getSheetByName(name) || ss.insertSheet(name);
       sh.clearContents();
       const rows = [table.header].concat(table.rows);
       sh.getRange(1, 1, rows.length, table.header.length).setValues(rows);
       sh.setFrozenRows(1);
     }
     return ContentService.createTextOutput('ok');
   }
   ```
2. Go to **Deploy → New deployment → Web app**. Execute as *Me*, with access
   *Anyone with the link*, then copy the URL.
3. Add both values to `.env.alpaca`:
   ```bash
   export SHEETS_WEBHOOK_URL='https://script.google.com/macros/s/…/exec'
   export SHEETS_WEBHOOK_TOKEN='the same long random string'
   ```
4. Run `python3 sheets_sync.py` to push now.

The sheet is a mirror only. A failed push is logged and never blocks trading.

## Running it on your own computer instead

### Where the console runs: on your computer

The console is a local page at `http://127.0.0.1:8765`, locked behind a PIN.
It does not run on the website, for these reasons:

- The desk's data (ledger, journal, account snapshot) and the Alpaca keys live
  in this folder on the machine that runs the trader. A website would need that
  data copied to a server.
- A 4-digit PIN is a screen lock. On the public internet anyone can try all
  10,000 PINs. On `127.0.0.1` only you can reach it, and the console refuses
  to listen anywhere else.
- It stays off every website and app on purpose. Nothing is published.

If you later want the desk online, give it proper sign-in (accounts, not a
PIN) and a server that receives the desk state.

## Setup

```bash
git clone https://github.com/pabwear/spy-paper-desk.git && cd spy-paper-desk
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env.alpaca` (gitignored, never commit it) with the **Paper 1000** keys:

```bash
export ALPACA_API_KEY='paper key for Paper 1000'
export ALPACA_SECRET_KEY='paper secret'
export APCA_API_BASE_URL='https://paper-api.alpaca.markets'
```

Check the account (prints equity, cash, buying power; never prints keys):

```bash
python3 alpaca_client.py          # look only
python3 alpaca_client.py --save   # also write account.json
```

If the keys belong to another account (for example the older $500 paper
account), it prints a warning and the order gate refuses every order.

## Open the console

```bash
python3 console.py                 # then open http://127.0.0.1:8765
```

Enter the PIN on the keypad. Five wrong PINs lock the keypad for 5 minutes.
A session ends after 30 minutes idle, on **Lock**, or when the console stops.
To change the PIN: `python3 console.py set-pin` (it is stored as a salted
PBKDF2 hash in `console_pin.json`, never as plain text).

The console only reads. It cannot place orders. It refreshes every 15 seconds.

| Tab | What it shows |
| --- | --- |
| Overview | Paper equity vs the $1,000 start, cash, buying power, realized P&L (all time and today), unrealized and net P&L, equity curve, daily realized P&L, performance (closed trades, wins, losses, win rate, gross profit/loss, profit factor, expectancy, average and largest win/loss, max drawdown, streak), open position (contract, premium, SPY at entry, stop level, flatten time), today at a glance, latest evaluation with P(loss) |
| Today · AOI & gate | Today's zones (color, low, high, mid, side, distance from price, Ops tags), whether the override is tradable and fresh, every order-gate check with pass/fail, the market read (price, RSI, VWAP, volume), Market Pulse bias, risk and sizing, how each zone scored |
| Trades | Every paper fill (shares and option contracts) with average-cost position, realized P&L and exit reason (filters: closes, wins, losses, buys, sells); every entry, exit, rejection and failed exit |
| Passes & evals | Evaluated, passes (skips), would-enter and orders, all time and today; why the desk passed; 4:15 PM reviews |
| Learning | Learner mode, trades learned from, walk-forward accuracy vs the base rate, lessons, mistake catalog, operational failures, per-signal win rate and multiplier, recent trades with their mistakes, what the loss model weighs |
| Journal | Every logged event: evals, passes, orders, fills, closes, failures, AOI, pulse, account snapshots, reviews, learning |
| Setup | Paper-only safety checks, instrument switches (SPY options / SPY shares / SNDK), account and venue, rules, confluence weights, file health |

## The daily run

| Time (ET, weekdays) | Who | Command |
| --- | --- | --- |
| before 09:30 | Pulse agent | `python3 run_study.py pulse ingest readings.json` (Stocklake + Stocktwits) |
| 09:30–09:59 | Trader (watch only) | `python3 run_study.py eval` (every focus stock) |
| 09:39 → before 10:00 | Scout, from the boxes Ops read | `python3 run_study.py aoi set [--symbol NVDA] --zone red:LOW:HIGH --zone green:LOW:HIGH [--tag 1:CHoCH]` |
| by 09:55 if unreadable | Scout | `python3 run_study.py aoi clear --reason "Mxwll boxes not readable"` |
| morning | Market Pulse agents | `python3 run_study.py pulse set bullish\|bearish\|neutral --note "..."` |
| 10:00–15:39 | Trader | `python3 run_study.py paper` (exits first, then at most one gated entry) |
| every minute 10:00–15:59 | Trader | `python3 run_study.py manage` (0.35% stop, flatten from 15:40) |
| every few minutes | Trader | `python3 run_study.py sync` (fills → ledger, account snapshot) |
| 16:15 | Trader | `python3 run_study.py review` (review + learning) |

`aoi set` refuses to write zones outside 09:30–09:59 ET on a weekday. Ops tags
(`BOS`, `CHoCH`, `HH`, `LH`, `LL`, `HL`, `order_block`, `session`) count as
structure, order-block or session confluence.

Example cron (machine clock in New York time):

```cron
*/5 9 * * 1-5      cd ~/spy-paper-desk && .venv/bin/python run_study.py eval
*/5 10-15 * * 1-5  cd ~/spy-paper-desk && .venv/bin/python run_study.py paper
* 10-15 * * 1-5    cd ~/spy-paper-desk && .venv/bin/python run_study.py manage
15 16 * * 1-5      cd ~/spy-paper-desk && .venv/bin/python run_study.py sync && .venv/bin/python run_study.py review
```

The stop and the flatten only work while `manage` (or `paper`) is running. If
the machine is off at 15:40, nothing flattens the position. The console then
shows a red **Overnight hold** banner, and the review prints a warning.

## How a decision is made

1. Outside 09:30–16:00, or on a weekend: logged as a pass, nothing else happens.
2. 09:30–09:59: the zones are scored and logged. No entries.
3. From 10:00 until 15:40 the trader picks the zone that price is inside, or
   within 0.15% of its midpoint. If several qualify, the highest confluence
   score wins. At least **2** of these must agree:
   - RSI (14, 1-minute) under 50 in red or over 50 in green
   - price at or under session VWAP in red, or at or over it in green
   - last bar's volume above its 20-bar average
   - structure, order-block or session tags Ops sent with the box

   Scores are weight × learned multiplier (see Learning). Red is a buy signal
   and green is a sell signal.
4. Options: a buy signal buys 1 call and a sell signal buys 1 put. Both use the
   nearest listed expiry and the strike nearest the dollar to SPY.
   Shares (when on): $800 long on a buy. On a sell, a whole-share short inside
   $800, because fractional shares cannot be shorted.
5. Market Pulse against the signal: options skip, because a contract cannot be
   cut in half. Shares trade at half size. The reason is logged.
6. Max 2 entries a day. One position at a time, and no entry while an order is open.
7. Exits: the loser exits when SPY moves 0.35% against the SPY price at entry.
   Leaving the zone is **not** an exit. Otherwise the position is flattened from
   15:40, so it is flat by 16:00. No overnight holds. Exits are allowed even
   after an instrument is switched off, because closing only removes risk.
8. `paper` checks exits first. Then it asks the paper account for its account
   number, clock, positions, open orders and the listed SPY contracts. Then it
   runs the gate, and submits a DAY market order only if every check passes.

### The entry gate (`gate.py`)

Every one of these must pass:

- the client is paper (`paper-api.alpaca.markets`, built with `paper=True`)
- `live_unlocked` is false and the mode is paper in `alpaca_config.json` and `rules.json`
- the keys belong to Paper 1000 (`PA36VOEO5PHB`)
- the active instrument's switch is on, and the underlying is SPY (SNDK is refused)
- it is a weekday, 10:00 ≤ time < 15:40 ET, and Alpaca's clock says the market is open
- `aoi_override.json` is tradable, has zones, was written today between 09:30 and 09:59 ET, and is not approximate. The October 2 stand-in zones fail this.
- the zone is in that override, price is at the zone, and the zone color matches the signal
- at least 2 confluence signals
- fewer than 2 entries today; flat with no open orders
- options: call on buy, put on sell, exactly 1 contract bought to open, nearest listed expiry, strike nearest $1
- shares: notional ≤ $800

The exit gate (`check_exit`) requires the paper client, live locked, Paper
1000, a weekday, the market open, and a SPY or SPY-option position.

`python3 run_study.py eval` never builds a trading client, so it cannot send
an order on any day.

If anything sets `live_unlocked` to true without Roy saying so in chat, stop.
The gate and `alpaca_client.py` both refuse to run.

## The desk's own Mxwll read (`studies/`)

`studies/mxwll.py` is a Python port of the Mxwll Suite by Mxwll Capital (Mozilla Public
License 2.0; the license header stays on the file). It follows the Pine code line by line:

- **Areas of Interest:** red = [highest candle body of the last 50 candles, + ATR(14)], above price;
  green = [lowest body − ATR(14), lowest body], below price. Both roll with every candle.
- **Structure:** internal (sensitivity 3) and external (25) pivots; I-BoS / I-CHoCH and BoS / CHoCH
  breaks; HH / LH / HL / LL swing labels.
- **Swing order blocks:** removed once price closes through them; the last 10 are kept.
- Fair value gaps, Fibonacci levels and the session table are not ported yet.

How the desk uses it (`rules.json` → `studies.mxwll`):

- **Auto zones.** After 09:39 ET, any focus stock with no zones for today gets the study's boxes as
  of 09:39, on `timeframe_minutes` candles (5 by default; regular hours only). Zones Ops publishes
  through the form are never replaced. Auto zones are marked `approximate`, so the gate refuses
  them, until Roy checks them against his TradingView chart and sets `trusted` to `true`.
- **Confluence.** `structure` counts when the latest internal break points the trade's way;
  `order_blocks` counts when a live swing order block overlaps or sits near the zone.
- **Chart.** `charts.json` carries the study's current read, so the dashboard can show both boxes.

The boxes depend on the chart's timeframe and on whether extended hours are shown. Roy's
TradingView chart is 5-minute candles with extended hours on (04:00–20:00 ET), so that is the
default. To switch extended hours, run **Actions → Desk settings → Run workflow** and pick on or
off; the choice is saved on `desk-state` (`settings.json`) and every later run uses it. The
dashboard can show either view at any time; the chart also carries order blocks, every structure
break, swing labels and a pattern projection (display only, never used for orders).

## The projection learns every day (`studies/forecast.py`, `projection_log.py`)

Each timeframe's projection blends six forecasters: what followed the most similar past
stretches (10, 20 and 40 candles), momentum, a drift back to the 20-candle average, and "no
move". It learns in two ways:

- **From history, before it says anything:** it replays itself at many past points using only
  what was known then, and each forecaster's weight shrinks with its error (multiplicative
  weights). That gives the starting blend and an honest out-of-sample record.
- **From each day's results:** every projection shown is logged (`projections.jsonl`, once per
  candle), scored after its last candle closes, and folded into `projection_model.json`. After
  10 scored projections a timeframe uses its live weights; after 15, its live band widths, so the
  50% and 80% ranges hold price about half and 8 times in 10. The 16:10 review logs the day's
  record per timeframe.

The dashboard shows the live record next to the history record. Display only: the gate never
reads the projection.

## Learning from mistakes (`learning.py`)

The desk learns from its own closed paper trades and its journal. It retrains
at every 4:15 review, or on demand with `python3 run_study.py learn`. It has
three parts.

1. **Mistake catalog.** Every closed trade is tagged with what went wrong or
   was weak:
   - hit the stop, or flattened at 16:00 for a loss
   - entered against Market Pulse
   - only the minimum 2 signals
   - near the box but not inside it
   - entered after 15:00
   - RSI within 5 of 50
   - a second entry right after a loss

   Each tag gets a count, a loss rate and P&L. When a setup tag (not an outcome)
   loses at least 15 points more often than overall, across 3 or more trades, it
   becomes a **lesson**. Operational failures are counted too: rejected orders,
   failed exits, missing data, no contract.
2. **Signal multipliers.** Each confluence signal's win rate becomes a
   multiplier on its weight, clipped to 0.5–1.5. It applies only once that signal
   has 10 or more closed trades. It changes which zone ranks first. It never
   changes the 2-signal minimum or any gate check.
3. **Loss model.** A small logistic regression estimates P(loss) from the setup
   at entry. Its inputs are which signals agreed, RSI and VWAP distance, distance
   from the zone, time of day, Market Pulse and the entry number. It is scored
   walk-forward (train on earlier trades, predict the next one) against simply
   guessing the base loss rate. The console shows whether it beats that.

`learning_weights.json` → `ml.mode` decides what the model may do:

- `"shadow"` (default): it predicts and logs P(loss) on every evaluation and
  order. It never changes anything.
- `"veto"`: it may **skip** an entry. That takes all three: P(loss) ≥ 65%,
  at least 30 closed trades, and walk-forward results that beat the base rate.

The learner can never add an order, add contracts, resize anything, or bypass
the gate. With about 2 trades a day it needs several weeks before its numbers
mean much, and the console says so.

## Files

| File | Purpose |
| --- | --- |
| `alpaca_config.json`, `rules.json`, `risk.json`, `study.json` | Venue, rules, risk and study settings |
| `aoi_override.json` | Today's zones. Empty until a same-day read |
| `market_pulse.json` | Today's bias, with the live readings and votes behind it |
| `watchlist.json` | Focus list (priority order) and per-stock instrument and switch |
| `aoi_override.<SYMBOL>.json` | Today's zones for a non-SPY focus stock |
| `pulse.py` | Readings → bias rule |
| `sheets_sync.py` | Google Sheets mirror |
| `learning_weights.json` | Confluence weights, learned multipliers, learner settings |
| `ml_model.json`, `learning_report.json` | Trained loss model and the latest learning report (gitignored, local) |
| `account.json` | Last paper account snapshot. Starts at $1,000 |
| `trades.csv` | Ledger. Rows come only from paper fills. Option P&L uses the ×100 multiplier |
| `journal.jsonl` | Event log (gitignored, local) |
| `console_pin.json` | PIN hash for the console |
| `alpaca_client.py` | Paper snapshot and paper client |
| `run_study.py` | eval / paper / manage / sync / review / learn / aoi / pulse / focus |
| `instruments.py` | Instrument switches, option symbols, contract choice, stop levels |
| `gate.py`, `signals.py`, `journal.py`, `common.py` | Entry and exit gates, indicators, ledger and journal, shared helpers |
| `learning.py` | Mistake catalog, signal multipliers, loss model |
| `rebuild_dashboard.py` | Builds the console state (`dashboard_state.json`) |
| `console.py`, `lock.html`, `dashboard.html` | The PIN-locked local console |

## Later

- **Reddit API access (free for personal use).** Apply through Reddit's support form
  (https://support.reddithelp.com/hc/en-us/requests/new) under its Responsible Builder Policy; reviews take
  about a week and are not guaranteed. If approved: a "script" app at reddit.com/prefs/apps, its id and secret
  as GitHub secrets, and the desk reads r/wallstreetbets, r/stocks, r/investing and r/options itself.
- **More stocks to watch** (watch-only next to SPY): about three fit inside the free GitHub minutes.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

They cover:
- **Gate:** weekend, before 10:00, after 15:40, prior-day or stand-in override,
  live host, live unlock, wrong account, color, 2-signal confluence, 2 entries
  a day, one position.
- **Instruments:** one call on a buy and one put on a sell (1 contract, nearest
  expiry, nearest-dollar strike). With shares off, no share order. With SNDK
  off (or on), no SNDK order. Shares come back on by config alone, at $800.
- **Exits:** the 0.35% stop for calls and puts, no exit on leaving the zone,
  the flatten from 15:40 even with options switched off, no exit on a live host.
- **Ledger:** option P&L uses the ×100 multiplier.
- **Learner:** it finds a planted losing pattern, beats the base rate
  walk-forward, never acts in shadow mode, and in veto mode can only skip.
- **Console lock:** wrong PIN, lockout, host check, loopback only.

- **Focus list:** a new stock is watch-only; it trades once switched on (one NVDA
  call or put); only one entry when two stocks qualify; an off-list stock never
  trades; zones must match the stock; the NVDA stop uses NVDA's price; manual
  holdings are left alone; the console can change focus but not trading.
- **Pulse and Sheets:** readings become a bias, a bearish pulse blocks a call,
  and the Sheets push never raises.

No Alpaca keys or network are needed.
