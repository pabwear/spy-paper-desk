# Research notes

What the desk has learned, newest first. Paper only. Roy's decision (Oct 9 2026): keep trading as now and
keep learning from it; these notes are the record.

## Daily log

### Fri Oct 9 2026 — +$30.80 (equity $1,056.42; +$56.42 since the $1,000 start)
- SPY put spread 772/767: sold 10:00 for $15 (mid $0.18, natural $0.15), bought back 15:40 for $1 → about +$14.
  All legs closed in one order. Real price 0.46 × VIX (assumed 0.5): in line.
- SPY options (learning), first trades, both closed on the time limit: Oct 19 777 call $525 → $500 (−$25);
  Oct 16 777 call $463 → $505 (+$42). Net +$17.
- Shares books: no trades.
- Price forecasts: 1m direction 53.7% of 322, 3m 57.4% of 298, 5m 49.8% of 297; "flat" still leads most
  timeframes (little short-term predictability).
- Loss model: shadow; 2 closed option trades of the 30 it needs before it may act.

### Week of Oct 5–9 (first live week of the spread book)
| Book | Trades | Won | Net | Backtest expectation (real prices) |
|---|---|---|---|---|
| SPY put spread | 3 | 3 | ≈ +$40 | wins 83% of days, ~$17 avg win, but loses ~$18/day on average (rare ~$490 days) |
| SPY options (learning) | 2 | 1 | +$17 | ~49% won, about −$3.50 a trade |
| SPY / SNDK shares | 0 | — | $0 | near break-even |

Three wins in a row is what the spread does 57% of the time (0.83³); it says nothing yet about the losing
days that decide its result. No new rule tested this week: 5 live trades is far too few to learn from
without fooling ourselves. Next check: the spread's first losing day (size vs the ~$490 worst case) and the
options book once it has 10+ trades.

## Oct 10 2026: crypto LEARNING book (Roy said yes in chat; rules written before any run or test)

No busier crypto rule passed (below), so this book is here to learn, not to earn: it is expected to lose
a little, like the SPY options learning book. It sits in the crypto paper account next to trend200 and never
touches BTC or ETH, so the two books can't sell each other's coins.

- Coin: SOL/USD only (Alpaca paper, free data back to 2021).
- Rule (mom7d, from the active-crypto test): hold while the last complete UTC daily close is above the close
  7 days earlier; cash otherwise. Checked every hour (crypto.yml), but the call can change only once a day.
- Size: $45 a buy (never more than 98% of the cash there is, never under $10). One position at a time.
- Stop: sell if the price is 6% or more under the buy price at an hourly check. After a stop, no new buy
  until the next UTC day.
- Worst case: about $2.70 a trade plus fees at the stop; it can be more if the price jumps past the stop
  between hourly checks, and it can never be more than the $45 in the trade (no leverage, long only).
- At most one buy and one sell a day (dated order ids).
- Kept as is: no tuning after it starts. Its trades and results go in the journal and on the Crypto page.
  It can be switched off with "learning": {"enabled": false} in crypto_config.json.
- One expectation check on past SOL prices (2021 to now), run once after this was written, so
  the result can be compared with what it does live. It does not change the rule.
- Expectation check (`python3 crypto_learning.py`, run once, Oct 10): SOL/USD 2021-01-01 to 2026-10-10,
  $45 a trade, 0.30% each side: 180 trades (31 a year), 53 won (29%), 58 stops, total +$441.76.
  By year: 2021 +$171, 2022 −$39, 2023 +$19, 2024 +$304, 2025 −$19, 2026 +$5. Almost all of it is two big
  SOL rallies (best trade +$290); most trades lose a little. Worst trade −$11.18: a price jump past the stop
  between hourly bars, so the stop is not a hard floor. Read: small, frequent losses, rare big wins. Not a pass
  (one coin, no train/check/exam split); it stays a learning book.

## Oct 10 2026: active crypto rules (Roy wants crypto to trade regularly)

Five more active rules on hourly, 4-hour and daily bars, set before the run (`crypto_active.py`), 0.30% a trade:
$1,000 compounding, 0.30% a trade. Rules set before the run. Passes = makes money in train, check AND exam, and trades 30+ times a year.

## BTC/USD (2021-01-01 to 2026-10-10)

| Rule | Train | Check | Exam | All | Worst drop | Trades a year |
|---|---|---|---|---|---|---|
| **hold** | +34.2% | +146.5% | -25.6% | +149.1% | -76.6% | 0.2 |
| **trend200** | +17.7% | +50.9% | +7.3% | +92.9% | -36.8% | 8.5 |
| **trend4h** | -74.2% | -22.3% | -58.3% | -91.6% | -93.4% | 162.5 |
| **cross1h** | -81.0% | -43.7% | -54.9% | -95.1% | -97.1% | 166.7 |
| **donchian4h** | -60.5% | -1.5% | -35.4% | -74.8% | -83.0% | 99.1 |
| **dip_uptrend** | -48.5% | -29.3% | -27.1% | -73.5% | -74.8% | 56.5 |
| **mom7d** | -47.0% | -8.5% | -30.1% | -65.9% | -79.0% | 68.3 |

## ETH/USD (2021-01-01 to 2026-10-10)

| Rule | Train | Check | Exam | All | Worst drop | Trades a year |
|---|---|---|---|---|---|---|
| **hold** | +192.6% | +8.4% | -1.2% | +217.3% | -79.2% | 0.2 |
| **trend200** | +100.6% | +21.9% | +22.6% | +201.7% | -40.1% | 5.0 |
| **trend4h** | -62.8% | -44.8% | -58.3% | -91.4% | -95.6% | 179.5 |
| **cross1h** | -20.2% | -57.7% | -60.7% | -86.7% | -95.8% | 164.3 |
| **donchian4h** | -65.7% | -44.9% | -12.8% | -83.4% | -90.6% | 102.9 |
| **dip_uptrend** | +2.1% | -46.0% | -18.9% | -55.3% | -70.7% | 50.9 |
| **mom7d** | +73.1% | -9.7% | -3.0% | +52.6% | -58.0% | 66.6 |

None passed; none even made money overall on both coins. At 0.25% fees each way, a rule that trades 100–180
times a year pays 50–100% a year in costs, more than any of these rules earned. (mom7d first showed 0 trades
because of a bug in the test code, not the rule; fixed and re-run with the rule unchanged.) The daily 200-day
trend rule stays the crypto desk's main rule.

## Oct 10 2026: crypto rules (before any crypto trading)

Roy asked to add crypto. Five daily long-or-cash rules on BTC and ETH, set before the run (`crypto_research.py`),
Jan 2021 – Oct 2026, 0.30% a trade:
$1,000 compounding, 0.30% a trade. Rules set before the run. Passes = makes money in train, check AND exam, with a smaller worst drop than holding.

## BTC/USD (2021-01-01 to 2026-10-09)

| Rule | Train | Check | Exam | All | Worst drop | Trades |
|---|---|---|---|---|---|---|
| **hold** | +35.6% | +147.8% | -32.1% | +130.9% ($2,309) | -76.6% | 1 |
| **trend50** ✓ | +19.8% | +78.1% | +1.8% | +119.8% ($2,198) | -60.7% | 123 |
| **trend200** ✓ | +19.4% | +51.6% | +7.2% | +96.4% ($1,964) | -36.8% | 49 |
| **mom28** | +199.0% | +24.9% | -4.0% | +262.9% ($3,629) | -48.9% | 181 |
| **donchian** | +25.9% | +79.6% | -8.4% | +107.0% ($2,070) | -57.9% | 90 |

## ETH/USD (2021-01-01 to 2026-10-09)

| Rule | Train | Check | Exam | All | Worst drop | Trades |
|---|---|---|---|---|---|---|
| **hold** | +192.6% | +8.9% | -12.0% | +183.9% ($2,839) | -79.2% | 1 |
| **trend50** | -4.3% | +41.9% | +84.5% | +151.9% ($2,519) | -59.4% | 110 |
| **trend200** ✓ | +100.6% | +24.1% | +22.2% | +205.9% ($3,059) | -40.1% | 29 |
| **mom28** ✓ | +77.7% | +15.5% | +11.7% | +130.6% ($2,306) | -56.7% | 177 |
| **donchian** | +143.1% | +4.8% | -16.7% | +113.5% ($2,135) | -53.0% | 88 |

Only the **200-day trend** rule passed on both coins: it made money in train, check and exam and halved the
worst drop (BTC −37% vs −77% holding; ETH −40% vs −79%). On BTC it made less than holding overall
(+96% vs +131%) but held up in the exam period when holding lost 32%. It trades rarely (about 8 times a year),
so it is not a daily strategy. Six years of crypto is a short history with one big boom and bust in it.

## Oct 9 2026: no daily strategy has passed

**The put spread at real prices.** The live spreads on Oct 7–8 were priced at about 0.5 × the VIX
(mids 0.47–0.63 × VIX), a fifth of what the VIX-priced backtest assumed. That error is why the spread book
was picked. Re-run at real prices (`spreads.py`, `real_` variants), Jul 2020 – Oct 2026, 1,315 days:

| Rule | Days won | Total |
|---|---|---|
| Live rule (½ move below, bought back 15:40) | 83% | −$23,563 |
| Stop at 2× credit | 56% | −$12,813 |
| Take 50% / stop 3× | 66% | −$12,801 |
| Uptrend and no down open (best filter) | 86% | −$7,318 |

All six day filters (set before running) lost in train, check and exam. Pattern: ~$17 wins, ~1 day in 6
losing up to ~$490.

**Intraday SPY shares** (`intraday.py`, $4,000 a trade, $0.02/share costs, rules set before running):

| Strategy | Train | Check | Exam | Total |
|---|---|---|---|---|
| Noise-area breakout, long and short | +$1,312 | +$168 | −$207 | +$1,274 |
| Noise-area breakout, buys only | +$943 | +$67 | −$113 | +$897 |
| Last half hour, long and short | −$656 | +$11 | −$57 | −$702 |
| Last half hour, buys only | −$610 | +$32 | −$91 | −$668 |
| Buy and hold SPY (yardstick) | +$1,825 | +$1,090 | +$550 | +$3,465 |

None passed (each must make money in all three periods). The noise breakout came closest but is fading
(2026 negative). Holding SPY won 54% of days; no rule wins every day.

**Lessons**
1. Check model prices against real quotes before trusting a backtest of options. The daily review now does
   this every day (`spread_pricing` in the review, lesson in Learning).
2. A high win rate with a capped-but-large loss is not an edge; check the money, not the win rate.
3. Every extra idea tested on the same history raises the chance of a lucky pass. Write rules down first,
   use train / check / exam, and let new paper days be the real test.

**Still open**
- Live spread results vs the backtest's expectation, as paper days build up.
- Whether the measured price level (≈0.5 × VIX) holds across calm and stressed days.

## Earlier (Oct 2026)
- 6-year backtest and dress rehearsal of the original desk (options at call/put areas): lost money.
- Four ideas and the 5-round research loop with a locked final exam: both finalists failed the exam;
  nothing beat buy-and-hold SPY.
- Spread close-out bug (Oct 7: long leg sold before the short's buy-back filled; no quote at the close)
  fixed: short first, wait for its fill, $0.01 limit when unquoted.
