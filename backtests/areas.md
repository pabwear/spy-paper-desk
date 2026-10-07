# Call/put area backtest

1554 trading days with areas, 2020-07-28 to 2026-10-06. Train ['2020-07-28', '2024-11-19'], test ['2024-11-20', '2026-10-06']. Option priced with Black-Scholes at that day's VIX (time decay in), after $5 a trade.

| | Settings | Train trades | Train won | Train net | Test trades | Test won | Test net | Test per trade | Test worst drop |
|---|---|---|---|---|---|---|---|---|---|
| Desk today | touch · both · 2 signals · stop 0.35% · no target · to 15:40 · 0d option | 1249 | 20.3% | $-61,640 | 537 | 17.5% | $-46,411 | $-86.43 | $-46,411 |
| Option 3 | confirm · both · 2 signals · stop 0.35% · no target · to 15:40 · 0d option | 1111 | 19.8% | $-47,038 | 470 | 16.0% | $-37,997 | $-80.84 | $-37,997 |
| Picked on train | confirm · red · 0 signals · stop 0.25% · no target · to 15:40 · 30d option | 800 | 35.1% | $-2,503 | 355 | 34.4% | $-1,309 | $-3.69 | $-5,185 |
| Benchmark: call at 10:00 daily | always_call · both · 0 signals · stop 0.35% · no target · to 15:40 · 0d option | 1087 | 19.2% | $-55,526 | 467 | 17.1% | $-44,801 | $-95.93 | $-44,801 |
| Benchmark: put at 10:00 daily | always_put · both · 0 signals · stop 0.35% · no target · to 15:40 · 0d option | 1087 | 17.7% | $-53,782 | 467 | 15.4% | $-40,511 | $-86.75 | $-40,511 |
| Benchmark: 30-day call at 10:00 daily | always_call · both · 0 signals · stop 0.35% · no target · to 15:40 · 30d option | 1087 | 36.6% | $-10,738 | 467 | 36.6% | $-9,928 | $-21.26 | $-10,127 |

Top 10 on the train days, and how they did on the test days:

| Settings | Train net | Test trades | Test won | Test net |
|---|---|---|---|---|
| confirm · red · 0 signals · stop 0.25% · no target · to 15:40 · 30d option | $-2,503 | 355 | 34.4% | $-1,309 |
| touch · red · 0 signals · stop 0.25% · no target · to 15:40 · 30d option | $-2,923 | 356 | 41.0% | $-1,968 |
| touch · red · 0 signals · stop 0.35% · no target · to 15:40 · 30d option | $-3,204 | 327 | 45.3% | $-2,036 |
| confirm · red · 0 signals · stop 0.25% · tp 1x · to 15:40 · 30d option | $-3,236 | 373 | 45.3% | $-4,909 |
| touch · red · 0 signals · stop 0.25% · no target · max 30m · 30d option | $-3,509 | 525 | 51.2% | $-632 |
| touch · red · 0 signals · stop 0.25% · tp 2x · to 15:40 · 30d option | $-3,513 | 367 | 42.5% | $-1,988 |
| touch · red · 0 signals · stop 0.25% · tp 2x · max 30m · 30d option | $-3,575 | 525 | 51.2% | $-555 |
| confirm · red · 0 signals · stop 0.25% · no target · max 30m · 30d option | $-3,580 | 458 | 42.8% | $-3,657 |
| touch · red · 0 signals · stop 0.5% · tp 2x · max 30m · 30d option | $-3,629 | 524 | 52.3% | $-1,228 |
| touch · red · 0 signals · stop 0.5% · no target · max 30m · 30d option | $-3,657 | 524 | 52.3% | $-1,258 |

Net by year (option priced, after costs):

| | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|
| Desk today | $-6,266 (114) | $-16,671 (270) | $-10,659 (317) | $-11,882 (291) | $-18,245 (281) | $-22,074 (293) | $-22,254 (220) |
| Option 3 | $-4,924 (101) | $-14,394 (224) | $-6,829 (325) | $-10,449 (242) | $-12,235 (244) | $-17,159 (244) | $-19,044 (201) |
| Picked | $-842 (85) | $-1,694 (159) | $-102 (206) | $452 (182) | $-36 (186) | $-1,025 (192) | $-565 (145) |
| Call at 10:00 | $-7,670 (110) | $-15,310 (252) | $-8,590 (251) | $-9,221 (250) | $-16,437 (251) | $-21,524 (249) | $-21,575 (191) |
| Put at 10:00 | $-6,107 (110) | $-18,939 (252) | $-3,693 (251) | $-12,148 (250) | $-14,086 (251) | $-18,783 (249) | $-20,536 (191) |
| 30-day call at 10:00 | $-2,132 (110) | $-2,769 (252) | $-2,344 (251) | $-933 (250) | $-2,943 (251) | $-4,764 (249) | $-4,780 (191) |
