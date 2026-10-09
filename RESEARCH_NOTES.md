# Research notes

What the desk has learned, newest first. Paper only. Roy's decision (Oct 9 2026): keep trading as now and
keep learning from it; these notes are the record.

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
