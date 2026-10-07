# Call/put area backtest

1554 trading days with areas, 2020-07-28 to 2026-10-06. Train ['2020-07-28', '2024-11-19'], test ['2024-11-20', '2026-10-06']. Option priced with Black-Scholes at that day's VIX (time decay in), after $5 a trade.

| | Settings | Train trades | Train won | Train net | Test trades | Test won | Test net | Test per trade | Test worst drop |
|---|---|---|---|---|---|---|---|---|---|
| Desk today | touch · both · 2 signals · stop 0.35% · hold | 1249 | 20.3% | $-61,640 | 537 | 17.5% | $-46,411 | $-86.43 | $-46,411 |
| Option 3 | confirm · both · 2 signals · stop 0.35% · hold | 1111 | 19.8% | $-47,038 | 470 | 16.0% | $-37,997 | $-80.84 | $-37,997 |
| Picked on train | confirm · green · 2 signals · stop 0.25% · tp 1x | 721 | 39.1% | $-17,001 | 296 | 37.5% | $-12,021 | $-40.61 | $-12,243 |
| Benchmark: call at 10:00 daily | always_call · both · 0 signals · stop 0.35% · hold | 1087 | 19.2% | $-55,526 | 467 | 17.1% | $-44,801 | $-95.93 | $-44,801 |
| Benchmark: put at 10:00 daily | always_put · both · 0 signals · stop 0.35% · hold | 1087 | 17.7% | $-53,782 | 467 | 15.4% | $-40,511 | $-86.75 | $-40,511 |

Top 10 on the train days, and how they did on the test days:

| Settings | Train net | Test trades | Test won | Test net |
|---|---|---|---|---|
| confirm · green · 2 signals · stop 0.25% · tp 1x | $-17,001 | 296 | 37.5% | $-12,021 |
| touch · green · 0 signals · stop 0.25% · tp 1x | $-17,475 | 385 | 43.4% | $-12,393 |
| confirm · green · 2 signals · stop 0.35% · tp 1x | $-18,222 | 267 | 32.6% | $-14,817 |
| confirm · green · 0 signals · stop 0.25% · tp 1x | $-18,241 | 336 | 38.4% | $-13,057 |
| confirm · red · 2 signals · stop 0.25% · tp 1x | $-18,488 | 315 | 31.7% | $-18,666 |
| confirm · green · 2 signals · stop 0.25% · tp 2x | $-18,590 | 288 | 19.4% | $-16,296 |
| touch · green · 2 signals · stop 0.25% · tp 1x | $-18,730 | 347 | 43.5% | $-10,929 |
| confirm · red · 0 signals · stop 0.25% · tp 1x | $-19,446 | 373 | 34.9% | $-19,596 |
| confirm · green · 2 signals · stop 0.35% · tp 2x | $-20,960 | 260 | 19.6% | $-17,240 |
| confirm · green · 2 signals · stop 0.35% · hold | $-21,312 | 255 | 16.5% | $-17,088 |

Net by year (option priced, after costs):

| | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|
| Desk today | $-6,266 (114) | $-16,671 (270) | $-10,659 (317) | $-11,882 (291) | $-18,245 (281) | $-22,074 (293) | $-22,254 (220) |
| Option 3 | $-4,924 (101) | $-14,394 (224) | $-6,829 (325) | $-10,449 (242) | $-12,235 (244) | $-17,159 (244) | $-19,044 (201) |
| Picked | $-768 (53) | $-5,050 (136) | $-3,515 (237) | $-3,381 (170) | $-5,069 (140) | $-5,136 (151) | $-6,103 (130) |
| Call at 10:00 | $-7,670 (110) | $-15,310 (252) | $-8,590 (251) | $-9,221 (250) | $-16,437 (251) | $-21,524 (249) | $-21,575 (191) |
| Put at 10:00 | $-6,107 (110) | $-18,939 (252) | $-3,693 (251) | $-12,148 (250) | $-14,086 (251) | $-18,783 (249) | $-20,536 (191) |
