# Call/put area backtest

1554 trading days with areas, 2020-07-28 to 2026-10-06. Train ['2020-07-28', '2024-11-19'], test ['2024-11-20', '2026-10-06']. Net of $5 a trade; time decay left out.

| | Settings | Train trades | Train won | Train net | Test trades | Test won | Test net | Test per trade | Test worst drop |
|---|---|---|---|---|---|---|---|---|---|
| Desk today | touch · both · 2 signals · stop 0.35% · hold | 1249 | 40.5% | $-3,091 | 537 | 44.3% | $1,005 | $1.87 | $-2,233 |
| Option 3 | confirm · both · 2 signals · stop 0.35% · hold | 1111 | 40.0% | $-870 | 470 | 41.5% | $-994 | $-2.11 | $-2,772 |
| Picked on train | touch · red · 0 signals · stop 0.35% · hold | 769 | 45.3% | $3,149 | 327 | 49.5% | $2,450 | $7.49 | $-1,217 |

Top 10 on the train days, and how they did on the test days:

| Settings | Train net | Test trades | Test won | Test net |
|---|---|---|---|---|
| touch · red · 0 signals · stop 0.35% · hold | $3,149 | 327 | 49.5% | $2,450 |
| touch · red · 0 signals · stop 0.25% · hold | $2,968 | 356 | 44.7% | $2,224 |
| confirm · red · 0 signals · stop 0.25% · hold | $2,644 | 355 | 38.9% | $2,017 |
| touch · red · 0 signals · stop 0.5% · hold | $2,104 | 304 | 52.0% | $2,334 |
| touch · both · 0 signals · stop 0.25% · hold | $1,674 | 617 | 41.5% | $2,259 |
| touch · red · 0 signals · stop 0.5% · tp 2x | $1,459 | 305 | 52.1% | $2,683 |
| touch · both · 0 signals · stop 0.35% · hold | $1,427 | 563 | 45.1% | $2,498 |
| touch · red · 0 signals · stop 0.35% · tp 2x | $1,401 | 330 | 49.4% | $2,025 |
| touch · red · 2 signals · stop 0.25% · hold | $1,299 | 352 | 43.8% | $805 |
| touch · red · 0 signals · stop 0.25% · tp 2x | $1,273 | 367 | 46.0% | $1,598 |
