# Call/put area backtest

1554 trading days with areas, 2020-07-28 to 2026-10-06. Train ['2020-07-28', '2024-11-19'], test ['2024-11-20', '2026-10-06']. Option priced with Black-Scholes at that day's VIX (time decay in), after $5 a trade.

| | Settings | Train trades | Train won | Train net | Test trades | Test won | Test net | Test per trade | Test worst drop |
|---|---|---|---|---|---|---|---|---|---|
| Desk now (red, ~30d affordable, 0.25%, 30 min, 10-min checks) | touch · red · 0 signals · stop 0.25% · no target · max 30m · auto option · checks every 10m | 1128 | 44.0% | $-4,283 | 501 | 49.1% | $-1,735 | $-3.46 | $-2,247 |
| Same, checked every minute | touch · red · 0 signals · stop 0.25% · no target · max 30m · auto option | 1189 | 44.3% | $-3,626 | 510 | 50.4% | $-892 | $-1.75 | $-1,554 |
| Benchmark: same call at 10:00 daily | always_call · both · 0 signals · stop 0.25% · no target · max 30m · auto option · checks every 10m | 1069 | 46.4% | $-7,202 | 441 | 46.9% | $-1,661 | $-3.77 | $-2,151 |
| Original rules (before Oct 7) | touch · both · 2 signals · stop 0.35% · no target · to 15:40 · 0d option | 1249 | 20.3% | $-61,640 | 537 | 17.5% | $-46,411 | $-86.43 | $-46,411 |
| Picked on train | touch · red · 0 signals · stop 0.25% · no target · max 30m · auto option | 1189 | 44.3% | $-3,626 | 510 | 50.4% | $-892 | $-1.75 | $-1,554 |

Top 10 on the train days, and how they did on the test days:

| Settings | Train net | Test trades | Test won | Test net |
|---|---|---|---|---|
| touch · red · 0 signals · stop 0.25% · no target · max 30m · auto option | $-3,626 | 510 | 50.4% | $-892 |
| touch · red · 0 signals · stop 0.25% · no target · max 30m · auto option · checks every 10m | $-4,283 | 501 | 49.1% | $-1,735 |
| touch · both · 2 signals · stop 0.35% · no target · to 15:40 · 0d option | $-61,640 | 537 | 17.5% | $-46,411 |

Net by year (option priced, after costs):

| | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|
| Desk now | $-892 (125) | $-226 (283) | $-1,211 (202) | $-337 (253) | $-1,598 (293) | $-2,055 (279) | $301 (194) |
| Call at 10:00, same rules | $-153 (103) | $-2,068 (247) | $-2,334 (251) | $-1,722 (248) | $-1,470 (247) | $-15 (237) | $-1,101 (177) |
| Original rules | $-6,266 (114) | $-16,671 (270) | $-10,659 (317) | $-11,882 (291) | $-18,245 (281) | $-22,074 (293) | $-22,254 (220) |
| Picked | $-827 (127) | $-273 (294) | $-655 (221) | $-248 (270) | $-1,477 (305) | $-1,228 (284) | $189 (198) |
