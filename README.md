# SPY paper desk

A weekday SPY desk on the **Alpaca paper** account *Paper 1000* (`PA3R32D8LP4Q`),
a $1,000 book. It fades the Mxwll Price Action Suite areas of interest:
buy only in red, sell only in green, and only after the 09:30–09:59 ET open has
produced real zones from Roy's TradingView chart.

**Active instrument: SPY options.** One long contract per signal: a call on a
buy (red), a put on a sell (green), nearest listed expiry, strike nearest the
dollar to SPY. SPY shares stay in the code, switched off. SNDK has a switch
that is off. No SNDK strategy exists in this codebase.

This is a simulated study, not financial advice. It never places a live order.

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

It is separate from the HomeStack app in this repo: nothing here is imported by
the Next.js site, and the site does not serve it.

## Where the console runs: on your computer

The console is a local page at `http://127.0.0.1:8765`, locked behind a PIN.
It does not run on the website, for these reasons:

- The desk's data (ledger, journal, account snapshot) and the Alpaca keys live
  in this folder on the machine that runs the trader. A website would need that
  data copied to a server.
- A 4-digit PIN is a screen lock. On the public internet anyone can try all
  10,000 PINs. On `127.0.0.1` only you can reach it, and the console refuses
  to listen anywhere else.
- The website is HomeStack, a homebuyer product. A trading desk does not belong
  on it.

If you later want the desk online, give it proper sign-in (accounts, not a
PIN) and a server that receives the desk state.

## Setup

```bash
cd paper-trading
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
| 09:30–09:59 | Trader (watch only) | `python3 run_study.py eval` |
| 09:39 → before 10:00 | Scout, from the boxes Ops read | `python3 run_study.py aoi set --zone red:LOW:HIGH --zone green:LOW:HIGH [--tag 1:CHoCH]` |
| by 09:55 if unreadable | Scout | `python3 run_study.py aoi clear --reason "Mxwll boxes not readable"` |
| morning | Market Pulse agents | `python3 run_study.py pulse set bullish\|bearish\|neutral --note "..."` |
| 10:00–15:54 | Trader | `python3 run_study.py paper` (exits first, then at most one gated entry) |
| every minute 10:00–15:59 | Trader | `python3 run_study.py manage` (0.35% stop, flatten from 15:55) |
| every few minutes | Trader | `python3 run_study.py sync` (fills → ledger, account snapshot) |
| 16:15 | Trader | `python3 run_study.py review` (review + learning) |

`aoi set` refuses to write zones outside 09:30–09:59 ET on a weekday. Ops tags
(`BOS`, `CHoCH`, `HH`, `LH`, `LL`, `HL`, `order_block`, `session`) count as
structure, order-block or session confluence.

Example cron (machine clock in New York time):

```cron
*/5 9 * * 1-5      cd ~/DPA/paper-trading && .venv/bin/python run_study.py eval
*/5 10-15 * * 1-5  cd ~/DPA/paper-trading && .venv/bin/python run_study.py paper
* 10-15 * * 1-5    cd ~/DPA/paper-trading && .venv/bin/python run_study.py manage
15 16 * * 1-5      cd ~/DPA/paper-trading && .venv/bin/python run_study.py sync && .venv/bin/python run_study.py review
```

The stop and the flatten only work while `manage` (or `paper`) is running. If
the machine is off at 15:55, nothing flattens the position. The console then
shows a red **Overnight hold** banner, and the review prints a warning.

## How a decision is made

1. Outside 09:30–16:00, or on a weekend: logged as a pass, nothing else happens.
2. 09:30–09:59: the zones are scored and logged. No entries.
3. From 10:00 until 15:55 the trader picks the zone that price is inside, or
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
   15:55, so it is flat by 16:00. No overnight holds. Exits are allowed even
   after an instrument is switched off, because closing only removes risk.
8. `paper` checks exits first. Then it asks the paper account for its account
   number, clock, positions, open orders and the listed SPY contracts. Then it
   runs the gate, and submits a DAY market order only if every check passes.

### The entry gate (`gate.py`)

Every one of these must pass:

- the client is paper (`paper-api.alpaca.markets`, built with `paper=True`)
- `live_unlocked` is false and the mode is paper in `alpaca_config.json` and `rules.json`
- the keys belong to Paper 1000 (`PA3R32D8LP4Q`)
- the active instrument's switch is on, and the underlying is SPY (SNDK is refused)
- it is a weekday, 10:00 ≤ time < 15:55 ET, and Alpaca's clock says the market is open
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
| `market_pulse.json` | Today's bias from the pulse agents |
| `learning_weights.json` | Confluence weights, learned multipliers, learner settings |
| `ml_model.json`, `learning_report.json` | Trained loss model and the latest learning report (gitignored, local) |
| `account.json` | Last paper account snapshot. Starts at $1,000 |
| `trades.csv` | Ledger. Rows come only from paper fills. Option P&L uses the ×100 multiplier |
| `journal.jsonl` | Event log (gitignored, local) |
| `console_pin.json` | PIN hash for the console |
| `alpaca_client.py` | Paper snapshot and paper client |
| `run_study.py` | eval / paper / manage / sync / review / learn / aoi / pulse |
| `instruments.py` | Instrument switches, option symbols, contract choice, stop levels |
| `gate.py`, `signals.py`, `journal.py`, `common.py` | Entry and exit gates, indicators, ledger and journal, shared helpers |
| `learning.py` | Mistake catalog, signal multipliers, loss model |
| `rebuild_dashboard.py` | Builds the console state (`dashboard_state.json`) |
| `console.py`, `lock.html`, `dashboard.html` | The PIN-locked local console |

## Tests

```bash
python3 -m unittest discover -s tests -v
```

They cover:
- **Gate:** weekend, before 10:00, after 15:55, prior-day or stand-in override,
  live host, live unlock, wrong account, color, 2-signal confluence, 2 entries
  a day, one position.
- **Instruments:** one call on a buy and one put on a sell (1 contract, nearest
  expiry, nearest-dollar strike). With shares off, no share order. With SNDK
  off (or on), no SNDK order. Shares come back on by config alone, at $800.
- **Exits:** the 0.35% stop for calls and puts, no exit on leaving the zone,
  the flatten from 15:55 even with options switched off, no exit on a live host.
- **Ledger:** option P&L uses the ×100 multiplier.
- **Learner:** it finds a planted losing pattern, beats the base rate
  walk-forward, never acts in shadow mode, and in veto mode can only skip.
- **Console lock:** wrong PIN, lockout, host check, loopback only.

No Alpaca keys or network are needed.
