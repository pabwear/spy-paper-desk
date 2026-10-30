# Research notes

What the desk has learned, newest first. Paper only. Roy's decision (Oct 9 2026): keep trading as now and
keep learning from it; these notes are the record.

## Daily log

### Fri Oct 30 2026 — −$273.10 (equity $484.01; −$515.99 since the $1,000 start; week −$159.86)
- SPY put spread 757/752: sold 10:00 for $34 (mid $0.37, natural $0.34; 0.56 × VIX, in line with 0.5) with SPY at
  $761.19. SPY fell below the short strike; bought back 15:40 as one order → −$273 (short −$291, long +$18). Nothing
  left open. Spread book (expired legs included): 17 trades, 13 won, ≈ −$527.
- Options book, shares, SNDK: no trades.
- **Account size is now critical.** $484 barely covers one spread's ≈$466 worst case. The book will keep trading
  (its rules allow it while the worst case fits the cash); one more full loss would leave ≈$20 and stop every book.
  Rules unchanged: changing them needs Roy's say in chat. Asked him again.
- Price forecasts: direction 59% (1m), 50% (3m), 54% (5m), 61% (15m, 36 scored), 60% (30m); "flat" leads except 15m.
  Loss model: shadow, 37 closed trades, 9 learnable of 30.
- ML shadow: 34 predictions, p 0.40–0.48, no would-be entry. Forward: 462 scored, Brier 0.250 vs ≈0.249 base rate.
- Crypto: no orders. Last check 15:45 UTC, ≈4½ h before this review. Account $916.40 (−$83.60 vs its $1,000 start).
  BTC $439 and ETH $419 held, 6% and 4% above their 200-day averages. SOL learning book in cash ($95.42 vs $103.54
  seven days earlier).

### Week of Oct 26–30
| Book | Trades | Won | Net (week) | Total since start | Backtest expectation (real prices) |
|---|---|---|---|---|---|
| SPY put spread | 5 | 4 | ≈ −$159 | 17 trades, 13 won, ≈ −$527 | wins 83% of days, ~$17 wins, ~1 day in 6 losing up to ~$490; ≈ −$18/day |
| SPY options (learning) | 0 | — | $0 | 7 trades, 3 won, ≈ +$19 | ~49% won, about −$3.50 a trade |
| SPY / SNDK shares | 0 | — | $0 | 2 trades, ≈ $0 | near break-even |
| Crypto trend200 (BTC, ETH) | 0 | — | ≈ −$57 | held since Oct 10, ≈ −$102 open before fees | ~8 trades a year; holds above the 200-day average, gives back gains in drops |
| Crypto learning (SOL mom7d) | 0 | — | $0 | 1 trade, 0 won, ≈ −$0.21 | ~31 a year, 29% won, small frequent losses, rare big wins |

- Spread: four wins this week (+$125 after expired legs), then one −$273. Three weeks in: won 76% of days (backtest
  83%), but ≈ −$31 a day against −$18 expected. The live record now matches the real-price backtest's warning:
  three losses (−$386, −$102, −$273) have outweighed thirteen wins.
- The other stock books could not trade (account too small); the crypto desk has drifted down with the market
  while its trend rule keeps holding.
- Rule worth testing: none new. The spread's fixes were already tested on Oct 9 and all lost. The decision left is
  Roy's (turn the spread book off, and/or reset to $10,000), not a new rule.

### Thu Oct 29 2026 — +$25.90 (equity $757.22; −$242.78 since the $1,000 start; week +$113.35)
(Review written Fri Oct 30 morning: the scheduled Thursday review reached this session late.)
- SPY put spread 764/759: sold 10:00 for $33 (mid $0.34, natural $0.33; 0.59 × VIX, a little above 0.5) with SPY at
  $768.71. SPY stayed above; bought back 15:40 as one order → +$26 (short +$34, long −$8). Nothing left open.
  Spread book (expired legs included): 16 trades, 13 won, ≈ −$254.
- Options book, shares, SNDK: no trades.
- Price forecasts: direction 53% (1m), 40% (3m), 40% (5m), 40% (30m); "flat" leads. Loss model: shadow, 35 closed
  trades, 9 learnable of 30.
- ML shadow: 34 predictions, p 0.41–0.50, no would-be entry. Forward: 428 scored, Brier 0.251 vs ≈0.249 base rate.
- Crypto (as of Fri 08:25 UTC): no orders. At Thursday's review time the last check was 16:49 UTC, ≈3½ h old.
  Account $920.99 (−$79.01 vs its $1,000 start). BTC $443 and ETH $420 still held, but only 6% and 4% above their
  200-day averages (13–14% a week earlier): a close below the average would trigger the trend rule's sell. SOL
  learning book in cash ($95.42 vs $103.54 seven days earlier); 1 trade so far, about −$0.21 before fees.

### Wed Oct 28 2026 — +$53.90 (equity $731.43; −$268.57 since the $1,000 start; week +$87.56)
- SPY put spread 766/761: sold 10:00 for $65 (mid $0.66, natural $0.65) with SPY at $770.53, VIX 17.5. Priced at
  0.75 × the VIX, the first day well above the 0.5 assumed (richer credit, so more risk was being priced in that
  morning). SPY stayed above; bought back 15:40 as one order → +$54 (short +$77, long −$23). Nothing left open.
  Spread book (expired legs included): 15 trades, 12 won, ≈ −$280.
- Options book, shares, SNDK: no trades.
- Price forecasts: direction 55% (1m), 67% (3m), 67% (5m), 73% (30m); "flat" leads. Loss model: shadow, 33 closed
  trades, 9 learnable of 30.
- ML shadow: 34 predictions, p 0.41–0.50, no would-be entry. Forward: 394 scored, Brier 0.252 vs ≈0.250 base rate.
- Crypto: no orders; last check 18:44 UTC (gaps up to 7½ h overnight). Account $960.03 (−$39.97 vs its $1,000
  start). BTC $461 and ETH $441 held, 11% and 9% above their 200-day averages. SOL learning book in cash ($101.21 vs
  $106.44 seven days earlier); 1 trade so far, about −$0.21 before fees.

### Tue Oct 27 2026 — +$14.92 (equity $677.62; −$322.38 since the $1,000 start; week +$33.75)
- SPY put spread 761/756: sold 10:00 for $16 (mid $0.19, natural $0.16; 0.48 × VIX, in line with 0.5) with SPY at
  $766.19. Short leg bought back 15:40 for $0.01 (+$19); the long 756 put had no bid (−$4, expires worthless; no
  further risk). Spread book (expired legs included): 14 trades, 11 won, ≈ −$334.
- Options book, shares, SNDK: no trades.
- Price forecasts: direction 49% (1m), 53% (3m), 54% (5m), 53% (30m); "flat" leads. Loss model: shadow, 31 closed
  trades, 9 learnable of 30.
- ML shadow: 33 predictions, p 0.40–0.49, no would-be entry. Forward: 360 scored, Brier 0.251 vs ≈0.250 base rate.
- Crypto: no orders; checks every 3½–6½ h, last 19:59 UTC. Account $958.00 (−$42.00 vs its $1,000 start). BTC $458
  and ETH $442 held, 10% and 8% above their 200-day averages. SOL learning book in cash ($100.76 vs $104.94 seven
  days earlier); 1 trade so far, about −$0.21 before fees.

### Mon Oct 26 2026 — +$18.92 (equity $662.79; −$337.21 since the $1,000 start)
- SPY put spread 758/753: sold 10:00 for $21 (mid $0.22) with SPY at $762.81, VIX 19. Short leg bought back 15:40
  for $0.02 (+$26); the long 753 put had no bid (−$7, expires worthless; no further risk). Spread book (true total,
  expired legs included): 13 trades, 10 won, ≈ −$349.
- Options book: no trades (budget_too_small). Shares, SNDK: no trades (125 checks outside the zones).
- Price forecasts: direction 50% (1m), 32% (3m), 46% (5m), 47% (30m); "flat" leads. Loss model: shadow, 30 closed
  trades, 9 learnable (needs 30 learnable).
- ML shadow: second would-be entry (14:20, p 0.515 ≥ 0.51) would have lost 14.1 bp after costs. Forward: 327 scored,
  2 would-be entries, both losers; Brier 0.250 vs ≈0.249 base rate (no better).
- Crypto (Fri → Mon, weekend included): no orders; 18 checks. Last check 17:14 UTC, about 3 h before this review
  (GitHub's timer skipped runs). Account $953.68 (−$46.32 vs its $1,000 start). BTC $457 and ETH $439 held, 10% and
  8% above their 200-day averages (gap narrowing). SOL learning book in cash ($101.25 vs $103.98 seven days earlier);
  1 trade so far, about −$0.21 before fees.
- Roy shared his own written strategy (Maxwell + RSI + daily moving averages + volume + VWAP; watch the open
  10–30 min; midday reversals; scale in on red, out on green; avoid negative-outlook days). Asked for feasibility only,
  no build. Waiting for his numbers before writing it as rules to test.

### Fri Oct 23 2026 — +$45.90 (equity $643.98; −$356.02 since the $1,000 start; week −$3.82)
- SPY put spread 756/751: sold 10:00 for $45 (mid $0.48, natural $0.45) with SPY at $760.94, VIX 19. SPY stayed
  above; bought back 15:40 as one order → +$46 (short +$63, long −$17). Nothing left open.
- Options book: no trades. Shares, SNDK: no trades.
- Price forecasts: direction 47% (1m), 55% (3m), 64% (5m), 61% (15m, 36 scored), 73% (30m, 15 scored); "flat" leads
  except 15m. Loss model: shadow, 29 closed trades, 9 learnable of 30.
- ML shadow: 34 predictions, p 0.39–0.48, no would-be entry. Forward: 294 scored, Brier 0.250 vs ≈0.249 base rate.
- Crypto learning book: **first trade closed.** SOL's 7-day change turned down, so it sold at 01:22 UTC at ≈$104.10
  (bought $104.60 Thursday) → about −$0.21 before fees. A rule exit, not the stop ($98.32, never reached). In cash
  ($103.54 vs $105.33 seven days earlier). BTC $463 and ETH $453 held, 12% and 11% above their 200-day averages.
  Account $973.64 (−$26.36 vs its $1,000 start). Checks every 3–6 h, last 18:12 UTC.
- Bookkeeping gap found: the spread book's running total counts only legs that were bought or sold, so the three
  long legs that expired worthless (Oct 19–21, −$23 together) are missing from it. Its true total is ≈ −$368, not
  the −$345 shown. The account equity is correct. Not fixed yet (reporting only).

### Week of Oct 19–23
| Book | Trades | Won | Net (week) | Total since start | Backtest expectation (real prices) |
|---|---|---|---|---|---|
| SPY put spread | 5 | 4 | ≈ −$4 | 12 trades, 9 won, ≈ −$368 | wins 83% of days, ~$17 wins, ~1 day in 6 losing up to ~$490; ≈ −$18/day |
| SPY options (learning) | 0 | — | $0 | 7 trades, 3 won, ≈ +$19 | ~49% won, about −$3.50 a trade |
| SPY / SNDK shares | 0 | — | $0 | 2 trades, ≈ $0 | near break-even |
| Crypto trend200 (BTC, ETH) | 0 | — | — | held since Oct 10, ≈ −$44 open before fees | ~8 trades a year; holds above the 200-day average |
| Crypto learning (SOL mom7d) | 1 | 0 | ≈ −$0.21 | 1 trade, 0 won | ~31 a year, 29% won, small frequent losses, rare big wins |

- Spread: four wins (+$122) and one loss (−$102) netted about zero, after Friday's −$386. Twelve days in, live
  average ≈ −$31 a day vs −$18 expected; too few days to tell apart, same direction.
- The options book could not trade all week: at ≈$600–700 one SPY call is over its budget. The account's size, not
  the market, now decides whether two of the four books trade.
- The crypto account's Alpaca cash error (Oct 14–22) corrected itself on Oct 22; its dollar results are readable again.
- Rule worth testing: none new. The spread's fixes were tested before running on Oct 9 and all lost; more variants
  on the same history would mostly find luck. Rules unchanged; still waiting for Roy on turning the spread book off
  and the $10k reset.

### Thu Oct 22 2026 — −$102.10 (equity $598.19; −$401.81 since the $1,000 start; week −$49.61)
- SPY put spread 763/758: sold 10:05 for $38 (mid $0.39, natural $0.38; 0.57 × VIX, a little above the 0.5 assumed)
  with SPY at $767.12. SPY fell to about $761.6 by 15:40, below the short strike; bought back at 15:40 as one order
  → −$102 (short −$96, long −$6). Closed cleanly, nothing left open. Spread book: 11 trades, 8 won, ≈ −$391.
- Options book: no trades (budget_too_small). Shares, SNDK: no trades.
- Account size: $598 still covers the spread's ~$470 worst case; a full loss would leave ≈$130.
- Price forecasts: direction 48% (1m), 64% (3m), 60% (5m), 33% (30m); "flat" leads. Loss model: shadow, 27 closed
  trades, 9 learnable of 30.
- ML shadow: 33 predictions, p 0.41–0.50, no would-be entry. Forward: 261 scored, Brier 0.252 vs ≈0.249 base rate.
- Crypto: **the account anomaly is resolved.** Between 07:24 and 13:40 UTC cash went from −$3,707.05 back to
  $58.59, its value before Oct 14; no desk order was involved (Alpaca-side correction). Equity now $966.22
  (−$33.78 vs the $1,000 start). The day's and week's crypto P&L figures are distorted by the correction.
- Crypto learning book: **first buy.** SOL turned up over 7 days ($106.86 vs $104.15), so it bought $45 of SOL at
  13:40 UTC (0.4208 SOL at ≈$104.60; stop $98.32, 6% below). SOL only, $45: within its rules. Now ≈$43.59,
  about −$0.42 before fees. BTC $461 and ETH $448 held, 16% and 15% above their 200-day averages. Last check
  18:27 UTC.
- Spread book this week: three small wins (≈ +$53) then −$102: again the backtest's shape. Still waiting for Roy
  on turning it off and the $10k reset.

### Wed Oct 21 2026 — +$22.92 (equity $700.38; −$299.62 since the $1,000 start; week +$52.58)
- SPY put spread 766/761: sold 10:00 for $25 (mid $0.26, natural $0.25; 0.53 × VIX, in line with 0.5) with SPY at
  $770.85. Short leg bought back 15:40 for $0.02 (+$31); the long 761 put had no bid (−$8, expires worthless; no
  further risk), same as Monday and Tuesday. Spread book: 10 trades, 8 won, ≈ −$289.
- Options book: no trades (budget_too_small). Shares, SNDK: no trades.
- Price forecasts: direction 60% (1m), 46% (3m), 42% (5m), 40% (30m); "flat" leads. Loss model: shadow, 25 closed
  trades, 9 learnable of 30.
- ML shadow: 34 predictions, p 0.41–0.50, no would-be entry. Forward: 228 scored, Brier 0.252 vs ≈0.249 base rate.
- Crypto: no orders; checks every 3–6 h, last 18:31 UTC. Anomaly unchanged (cash −$3,707.05). BTC $481 and ETH $474
  held, both 14% above their 200-day averages. SOL learning book in cash ($106.44 vs $106.73 seven days earlier).
- Three small spread wins this week (≈ +$53 together, after the expired long legs) have recovered about a seventh of Friday's −$386: the pattern the
  real-price backtest describes. Still waiting for Roy on turning the spread book off and the $10k reset.

### Tue Oct 20 2026 — +$14.92 (equity $677.55; −$322.45 since the $1,000 start; week +$29.75)
- SPY put spread: the 10:00 order (763/758 for $30) did not fill in 10 minutes and was cancelled; resent 10:20 as
  765/760 for $19 (natural $0.15; 0.47 × VIX; the day's two quotes averaged 0.51, in line with 0.5) with SPY at
  $769.80. Short leg bought back 15:40 for $0.04 (+$22); the long 760 put again had no bid (−$7, expires worthless;
  no further risk). Spread book: 9 trades, 7 won, ≈ −$320.
- Yesterday's fix worked as intended: the 15:50 retry was accepted (no "cash-secured put" refusal). With no bid at
  all, even a 1-cent sell cannot fill, so a worthless long leg simply expires. Nothing to fix.
- Options book: no trades (27 skips on budget_too_small). Shares, SNDK: no trades.
- Price forecasts: direction 45% (1m), 52% (3m), 62% (5m), 40% (30m); "flat" leads. Loss model: shadow, 24 closed
  trades, 9 learnable of 30.
- ML shadow: 34 predictions, p 0.43–0.51, no would-be entry. Forward: 194 scored, Brier 0.252 vs ≈0.248 base rate.
- Crypto: no orders; checks about every 4–5½ h, last 19:06 UTC. Anomaly unchanged (cash −$3,707.05). BTC $468 and
  ETH $458 held, 13% and 12% above their 200-day averages. SOL learning book in cash ($104.94 vs $105.59 seven days
  earlier; mom7d still no).
- Still waiting for Roy on turning the spread book off and the $10k reset. Rules unchanged.

### Mon Oct 19 2026 — +$14.92 (equity $662.72; −$337.28 since the $1,000 start)
- SPY put spread 761/756: sold 10:00 for $17 (mid $0.18, natural $0.17; 0.46 × VIX, in line with 0.5) with SPY at
  $766.58, VIX 20. Short leg bought back 15:40 for $0.02 (+$23); the long 756 put had no bid and stayed open
  (−$8, expires worthless today; a bought option, no further risk). Spread book: 8 trades, 6 won, ≈ −$342.
- Close-out bug: the long leg's market sell sat unfilled, and the 15:50 retry was refused by Alpaca as a new
  cash-secured put (the open sell already covered the position). Fixed in `_close_leg`: a stale close on the leg is
  cancelled first and a sell is resent as a 1-cent limit (sells at any bid); test added. Order handling only, no rule
  changed.
- Options book: no trades; 31 checks skipped on budget_too_small (≈$663 left, one SPY call costs more than its
  budget allows). Shares, SNDK: no trades.
- Price forecasts: direction 45% (1m), 40% (3m), 42% (5m), 47% (30m); "flat" leads. Loss model: shadow, 23 closed
  trades, 9 learnable of 30.
- ML shadow: 34 predictions, p 0.42–0.50, no would-be entry. Forward: 160 scored, Brier 0.254 vs ≈0.248 base rate.
- Crypto: no orders. Checks only at 12:47 and 19:11 UTC today (gaps ≈6½ h). Anomaly unchanged (cash −$3,707.05).
  BTC $465 and ETH $451 held, 13% and 12% above their 200-day averages. SOL learning book in cash (mom7d no).
- Roy asked "what happened?" about Friday's −$386. Recommended turning the spread book off (it lost at real prices
  in the Oct 9 backtest); waiting for his answer. Rules unchanged until he says so.

### Fri Oct 16 2026 — −$386.10 (equity $647.91; −$352.09 since the $1,000 start; week −$408.29)
(Review written Mon Oct 19: the scheduled Friday review reached this session late.)
- SPY put spread 767/762: sold 10:00 for $21 (mid $0.22, natural $0.21; 0.49 × VIX, in line with 0.5) with SPY at
  $771.41. SPY fell about 1% to ≈$762.5 by 15:40, below the short strike; bought back at 15:40 → −$386 (short leg
  −$432, long leg +$46). Inside its $479 worst case; the book has no stop by design. Spread book: 7 trades, 5 won,
  ≈ −$365. This is the day the real-price backtest warned about (Oct 9): one loss erased about 20 typical wins.
- Shares, SNDK, options: no trades; 0 orders refused. All positions closed at the end of the day.
- Account size now matters: $648 still covers the spread's ~$480 worst case, but one more such loss would leave
  ≈$170, and the spread and options books would then skip for lack of money. (The $10k reset, task 15, is still parked.)
- Price forecasts: direction 62% (1m), 73% (3m), 63% (5m), 33% (15m, 18 scored), 87% (30m, 15 scored); "flat"
  leads. Loss model: shadow, 22 closed trades, 9 learnable of 30.
- ML shadow (15-min TCN): 34 predictions, p 0.43–0.50, no would-be entry. Forward so far: 126 scored, Brier 0.255
  vs 0.249 for the base rate (still no better than it); 1 would-be entry, −7.5 bp.
- Crypto (Fri → Mon 05:56 UTC, weekend included): no orders; 23 checks, gaps up to ≈5½ h (GitHub's hourly timer
  skips runs). Account anomaly unchanged (cash −$3,707.05; reported equity −$2,780.55). Coins held: BTC $469 and
  ETH $457 (≈$926 vs $960 put in, about −$34 before fees); BTC 13% and ETH 12% above their 200-day averages (hold).
  SOL learning book in cash ($103.98 vs $111.26 seven days earlier; mom7d says no).

### Week of Oct 12–16
| Book | Trades | Won | Net | Backtest expectation (real prices) |
|---|---|---|---|---|
| SPY put spread | 4 (7 total) | 2 (5) | ≈ −$409 (−$365 total) | wins 83% of days, ~$17 wins, ~1 day in 6 losing up to ~$490; ≈ −$18/day on average |
| SPY options (learning) | 5 (7) | 2 (3) | ≈ +$2 (+$19) | ~49% won, about −$3.50 a trade |
| SPY shares | 2 (2) | 1 (1) | ≈ $0 | near break-even |
| SNDK shares | 0 | — | $0 | — |
| Crypto trend200 (BTC, ETH) | 0 (2 buys Oct 10) | — | ≈ −$34 open, before fees | ~8 trades a year; holds while above the 200-day average |
| Crypto learning (SOL mom7d) | 0 | — | $0 | ~31 trades a year, 29% won; 0 in 9 days happens about half the time |

- The spread book is behaving as the real-price backtest said: 5 wins of $7–$33, then losses of $63 and $386.
  Live average ≈ −$52 a day over 7 days, worse than the −$18 expected but from far too few days to separate.
- Options, shares and both crypto books: too few trades to compare with their backtests.
- The crypto account's cash is still wrong (Alpaca side); its dollar results are unreadable until Roy checks or
  resets it.
- Rule worth testing: none new. The spread's obvious fixes (stops at 2× and 3×, take-profit, six day filters) were
  already tested before running on Oct 9, and all lost in train, check and exam. Testing more variants on the same
  history would mostly find luck. Rules unchanged (Roy's call).

### Thu Oct 15 2026 — +$61.80 (equity $1,034.23; +$34.23 since the $1,000 start; week −$21.97)
- SPY put spread 761/756: sold 10:00 for $34 (mid $0.33, natural $0.30; 0.56 × VIX, in line with 0.5), bought back
  15:40 → about +$33 (short +$46, long −$13). Spread book: 6 trades, 5 won, ≈ +$17.
- SPY options (learning): Oct 22 770 call 12:50 → 13:30 (+$41); 14:10 → 14:50 (−$12); both on the time limit.
  Options book: 7 trades, 3 won, ≈ +$19.
- SPY shares: 17 buys refused by Alpaca for buying power (≈$506–547 free while the spread's collateral was held;
  1 share ≈ $770). Same cause as Monday; harmless but noisy. A buying-power pre-check would turn these into quiet
  skips (proposed to Roy, not changed).
- All positions closed at the end of the day.
- Price forecasts: direction 44% (1m), 25% (3m), 63% (5m), 8% (30m, 12 scored); "flat" leads. Loss model: shadow,
  20 closed trades, 9 learnable of 30.
- ML shadow (15-min TCN): 31 predictions, p 0.39–0.52; first would-be entry (10:09, p 0.52 ≥ 0.51) would have lost
  7.5 bp after costs. Forward so far: 82 scored, Brier 0.254 vs about 0.249 for the base rate: no better than it.
- Crypto: no trades; the account anomaly persists (cash −$3,707.05, reported equity −$2,779; BTC/ETH positions
  unchanged). BTC and ETH each 13% above their 200-day averages (hold). SOL learning book in cash ($104.15 vs
  $116.18 a week earlier). Last crypto check 18:09 UTC (≈2 h before this review).

### Wed Oct 14 2026 — −$63.10 (equity $972.54; −$27.46 since the $1,000 start; week −$83.66)
- SPY put spread 769/764: sold 10:10 for $19 (mid $0.18, natural $0.15; 0.48 × VIX, in line with the 0.5 assumed).
  SPY fell toward the short strike; bought back at 15:40 → −$63 (short leg −$60, long leg −$3). The book's first
  losing day: about 3× a typical win, far inside its ~$481 worst case. Spread book so far: 5 trades, 4 won, ≈ −$16.
  This is the shape the backtest warned about: frequent small wins, rarer bigger losses.
- Shares, SNDK and options: no trades (118 checks outside the zones). All positions closed at the end of the day.
- Price forecasts: direction 61% (1m), 62% (3m), 54% (5m), 20% (30m, 15 scored); "flat" still leads.
  Loss model: shadow, 16 closed trades, 7 learnable of 30.
- ML shadow (15-min TCN): 33 predictions, p 0.43–0.51, no would-be entries. Only 33% of windows made money after
  costs; Brier over two days 0.251 vs base rate 0.443 (about 0.247): still no better than the base rate.
- **Crypto account anomaly.** Cash went from $58.59 to −$3,707.05 between 07:24 and 13:32 UTC with NO order from
  the desk. A read-only audit (`crypto_desk.py audit`) shows no orders in 3 days, BTC/ETH positions unchanged
  (0.005681 BTC, 0.188344 ETH), and only zero-dollar coin-fee entries dated Oct 12. Reported equity −$2,788.
  Looks like an Alpaca paper-account accounting problem. The desk cannot buy with negative cash (fails closed);
  sells would still work. Roy to check the Alpaca dashboard / reset the paper account. Crypto P&L numbers since
  this point are not meaningful.
- BTC +15.2% and ETH +16.7% above their 200-day averages (hold). SOL learning book in cash ($106.73 vs $120.70).

### Tue Oct 13 2026 — −$2.02 (equity $1,035.76; +$35.76 since the $1,000 start; week −$20.44)
- SPY shares (first trades of this book): 10:00 buy 1 @ $777.93, out 10:30 on time (−$0.39); 14:40 buy 1 @ $777.76,
  out 15:20 on time (+$0.42). Net about $0. The buying-power refusals of Monday did not recur (no spread open).
- SPY options (learning): bought the Oct 30 778 call at 15:30 for $879, flattened at 15:40 (−$2). An entry 10 minutes
  before the flatten can only pay its spread: lesson logged, rule unchanged (options book now 5 trades, 2 won).
- Put spread: no trade. Skipped on credit_too_small (5 checks) and worst_case_affordable (3): the real credit stayed
  under the book's minimum all morning. Rule working as written.
- All positions closed at the end of the day. No rejected orders.
- Price forecasts: direction 36% (1m), 35% (3m), 45% (5m), 33% (30m); "flat" leads. Loss model: shadow, 14 closed
  trades, 7 learnable of 30.
- ML shadow model (15-min TCN, installed 10:50): 28 predictions, all scored. p stayed 0.43–0.46, under its 0.51 bar
  (no would-be entries). Brier 0.264 vs 0.245 for the day's base rate (57% of windows made money): worse than the
  base rate, as the walk-forward predicted.
- Crypto: no trades. BTC +13.5% and ETH +14.4% above their 200-day averages (hold); account $1,002.63 (+$2.63).
  Learning book (SOL) in cash: $105.59 vs $120.77 a week earlier. Last crypto check 17:29 UTC, 2 h 44 min before
  this review: GitHub's hourly schedule keeps skipping runs (6 checks since Monday).

### Mon Oct 12 2026 — −$18.20 (equity $1,038.00; +$38.00 since the $1,000 start)
- SPY put spread 775/770: sold 10:00 for $8 (mid $0.09, natural $0.08), bought back 15:40 → about +$7.
  Real price 0.36 × VIX (assumed 0.5): the second low reading of three; credits keep coming in under the model.
- SPY options (learning): Oct 22 779 call $543 → about $501 (−$42) and Oct 20 778 call $493 → about $510
  (+$17), both closed on the 30-minute time limit. Book so far: 4 trades, 2 won, about −$8.
- SPY shares: 5 buy attempts (11:20–12:40) refused by Alpaca for buying power ($522–539 free, 1 share ≈ $778)
  while the spread's collateral was held. No money lost; the desk's own budget check uses equity, not buying
  power. A $1,000 account cannot hold the spread and a share at once (the parked $10k reset, task 15).
- All positions closed at the end of the day.
- Price forecasts: direction 48.5% (1m, 5m), 42.7% (3m), 26.7% (30m, 15 scored); "flat" leads all
  timeframes. Still no short-term predictability.
- Loss model: shadow; 11 closed trades, 4 learnable of the 30 it needs.
- Crypto (Sat–Mon): no trades. BTC and ETH held, both above their 200-day averages (gap 13.5% and 14.4%).
  Account $1,005.21 (+$5.21). Learning book (SOL) stayed in cash: SOL $105.59 vs $120.77 seven days earlier.
  Only 10 crypto runs in ~60 hours: GitHub's hourly schedule is skipping most runs (no stop was at risk,
  since SOL was not held).

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

## Oct 13 2026: ML engine, first verdict — no edge in SPY minute bars after costs

Built the ML research engine (`ml/`, docs/ML.md) per Roy's spec. Pass rules (`ml/acceptance.json`) and the holdout
lock (May 2026 on, `ml/holdout.json`) were written before any model saw data.

Walk-forward on Alpaca IEX 1-minute SPY bars, 14 test windows (Nov 2022 – Apr 2026), out-of-sample, after costs
($0.02/share round trip + 0.5 bp slippage each side), 25 features, target "a long trade made money after costs":

| Horizon | Logistic | Trees (GBT) | TCN (neural) | Trade every decision |
|---|---|---|---|---|
| 15 min | −$44 (271 trades) | −$22 (332) | **+$18** (471; per trade −$0.09…+$0.17) | −$354 (3,330) |
| 30 min | −$20 (88) | −$31 (606) | −$81 (524) | −$281 |
| 60 min | −$1 (999) | −$29 (1,108) | −$51 (1,000) | −$167 |

- Nothing passed. Every model's probabilities scored worse than the plain base rate (Brier skill < 0); AUC 0.497–0.515
  (0.5 = a coin). The TCN's +$18 at 15 minutes is inside the noise (range includes 0, 4 of 14 windows positive,
  it abstained in 7 folds) and the same network lost at 30 and 60 minutes.
- The models did cut the losses of trading blindly (−$354 → about −$20 to +$18), mostly by abstaining.
- Controls behaved: on a pure random walk nothing passes; on a planted trend every model finds it. So the pipeline
  can see an edge when one exists; SPY's minute bars, with these features, after costs, do not show one.
- The holdout stays unused (only a passing candidate may use it, once). No LSTM: the TCN did not beat the baselines.
- Shadow mode: the 15-minute TCN (the closest candidate) predicts in the desk every 10 minutes, logs, never orders,
  and its forward record is scored on the dashboard. It stays unapproved.
- Next experiments worth writing down before running: real quotes for spread costs; longer horizons (end of day);
  cross-asset context (QQQ, VIX, sector ETFs); TSLA/SNDK (more movement per dollar of cost).

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
