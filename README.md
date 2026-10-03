# SPY paper desk

A weekday SPY desk on the **Alpaca paper** account *Paper 1000* (`PA3R32D8LP4Q`),
sized as a $1,000 book. It fades the Mxwll Price Action Suite areas of interest:
buy only in red, sell only in green, and only after the 09:30–09:59 ET open has
produced real zones from Roy's TradingView chart.

This is a simulated study, not financial advice. It never places a live order.

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
| Overview | Paper equity vs the $1,000 start, cash, buying power, realized P&L (all time and today), unrealized and net P&L, equity curve, daily realized P&L, performance (closed trades, wins, losses, win rate, gross profit/loss, profit factor, expectancy, average and largest win/loss, max drawdown, streak), open position, today at a glance, latest evaluation |
| Today · AOI & gate | Today's zones (color, low, high, mid, side, distance from price, Ops tags), whether the override is tradable and fresh, every order-gate check with pass/fail, the market read (price, RSI, VWAP, volume), Market Pulse bias, risk and sizing, how each zone scored |
| Trades | Every paper fill with average-cost position and realized P&L, zone, confluence, score and pulse bias (filters: closes, wins, losses, buys, sells); every order sent with its setup |
| Passes & evals | Evaluated, passes (skips), would-enter and orders, all time and today; why the desk passed; 4:15 PM reviews |
| Journal | Every logged event: evals, passes, orders, fills, closes, AOI, pulse, account snapshots, reviews |
| Setup | Paper-only safety checks, account and venue, rules, confluence weights, file health |

## The daily run

| Time (ET, weekdays) | Who | Command |
| --- | --- | --- |
| 09:30–09:59 | Trader (watch only) | `python3 run_study.py eval` |
| 09:39 → before 10:00 | Scout, from the boxes Ops read | `python3 run_study.py aoi set --zone red:LOW:HIGH --zone green:LOW:HIGH [--tag 1:CHoCH]` |
| by 09:55 if unreadable | Scout | `python3 run_study.py aoi clear --reason "Mxwll boxes not readable"` |
| morning | Market Pulse agents | `python3 run_study.py pulse set bullish\|bearish\|neutral --note "..."` |
| 10:00–15:59 | Trader | `python3 run_study.py paper` (eval + gated paper order) |
| every few minutes | Trader | `python3 run_study.py sync` (fills → ledger, account snapshot) |
| 16:15 | Trader | `python3 run_study.py review` |

`aoi set` refuses to write zones outside 09:30–09:59 ET on a weekday. Ops tags
(`BOS`, `CHoCH`, `HH`, `LH`, `LL`, `HL`, `order_block`, `session`) count as
structure, order-block or session confluence.

Example cron (machine clock in New York time):

```cron
*/5 9 * * 1-5      cd ~/DPA/paper-trading && .venv/bin/python run_study.py eval
*/5 10-15 * * 1-5  cd ~/DPA/paper-trading && .venv/bin/python run_study.py paper && .venv/bin/python run_study.py sync
15 16 * * 1-5      cd ~/DPA/paper-trading && .venv/bin/python run_study.py sync && .venv/bin/python run_study.py review
```

## How a decision is made

1. Outside 09:30–16:00, or on a weekend: logged as a pass, nothing else happens.
2. 09:30–09:59: the zones are scored and logged. No orders.
3. From 10:00 the trader takes the zone price is inside, or within 0.15% of
   its midpoint, with the highest confluence score:
   - RSI (14, 1-minute) under 50 in red or over 50 in green
   - price at or under session VWAP in red, or at or over it in green
   - last bar's volume above its 20-bar average
   - structure, order-block or session tags Ops sent with the box

   Scores use `learning_weights.json`. Red means buy, green means sell.
4. Market Pulse bias for today against the side → size × 0.5 (or skip, if
   `risk.json` → `pulse_contradiction.action` is `"skip"`). The reason is logged.
5. Size: `round((1000 × 0.25) / price, 4)` SPY, from `risk.json`. A green sell
   closes an open long (take-profit). With no long open it would be a short,
   and fractional shares cannot be sold short, so it needs a whole share inside
   the $250 cap. With SPY above $250 that is never possible, so the desk passes.
   No second buy while long; no order while another is open.
6. `paper` asks the paper account for its account number, clock, position and
   open orders. Then it runs the gate. It submits a DAY market order only if
   every check passes.

### The order gate (`gate.py`)

Every one of these must pass:

- the client is paper (`paper-api.alpaca.markets`, built with `paper=True`)
- `live_unlocked` is false and the mode is paper in `alpaca_config.json` and `rules.json`
- the keys belong to Paper 1000 (`PA3R32D8LP4Q`)
- the symbol is SPY
- it is a weekday, 10:00 ≤ time < 16:00 ET, and Alpaca's clock says the market is open
- `aoi_override.json` is tradable, has zones, was written today between 09:30 and 09:59 ET, and is not approximate
- the zone is in that override, price is at the zone, and the zone color matches the side
- at least one confluence signal
- notional ≤ 25% of $1,000

`python3 run_study.py eval` never builds a trading client, so it cannot send
an order on any day.

If anything sets `live_unlocked` to true without Roy saying so in chat, stop.
The gate and `alpaca_client.py` both refuse to run.

## Files

| File | Purpose |
| --- | --- |
| `alpaca_config.json`, `rules.json`, `risk.json`, `study.json` | Venue, rules, risk and study settings |
| `aoi_override.json` | Today's zones. Empty until a same-day read |
| `market_pulse.json` | Today's bias from the pulse agents |
| `learning_weights.json` | Confluence weights and indicator settings |
| `account.json` | Last paper account snapshot. Starts at $1,000 |
| `trades.csv` | Ledger. Rows come only from paper fills |
| `journal.jsonl` | Event log (gitignored, local) |
| `console_pin.json` | PIN hash for the console |
| `alpaca_client.py` | Paper snapshot and paper client |
| `run_study.py` | eval / paper / sync / review / aoi / pulse |
| `gate.py`, `signals.py`, `journal.py`, `common.py` | Gate, indicators, ledger and journal, shared helpers |
| `rebuild_dashboard.py` | Builds the console state (`dashboard_state.json`) |
| `console.py`, `lock.html`, `dashboard.html` | The PIN-locked local console |

## Tests

```bash
python3 -m unittest discover -s tests -v
```

They cover the gate (weekend, before 10:00, prior-day or approximate override,
live host, live unlock, wrong account, color, confluence, size), eval never
ordering, one paper order sized off $1,000, the ledger math, and the console
lock (wrong PIN, lockout, host check, loopback only). No Alpaca keys or network
are needed.
