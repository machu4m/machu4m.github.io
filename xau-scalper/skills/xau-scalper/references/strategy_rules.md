# Strategy rules and where they came from

| Rule | Value (config key) | Source |
|---|---|---|
| Killzones, NY time | Asia 20:00-00:00, London 02:00-05:00, NY 07:00-10:00 (`sessions.killzones`) | Killzones_XAUUSD via xau-chart-map |
| Dubai offset | NY+9h US winter, NY+8h US summer (computed from time zones) | xau-chart-map |
| NY-open ban | 09:30-10:00 NY (`sessions.ny_open_ban`) | **UNVERIFIED window.** Rule 7 of Trade_History_Diagnosis names a NY-open ban; exact times not available here |
| Entry model | sweep -> close back inside -> displacement; entry after the hunt | xau-chart-map, Liquidity_timing |
| Stop | past the sweep wick + $1.50 (`exits.structure_buffer`) + spread | xau-chart-map |
| Target | next liquidity; doc says at least 1:2, config min_rr is 1.5 (`min_rr`, `strategy.doc_min_rr`) | xau-chart-map / user config |
| Breakeven | at 1R (`exits.breakeven_at_r`) | xau-chart-map |
| Closer side taken first | 73-76% (`strategy.closer_side_first_pct`) | Liquidity_timing via xau-chart-map |
| Direction after a sweep | ~50/50 (`strategy.sweep_direction_odds_pct`) | Setup_probabilities via xau-chart-map |
| Old straddle/ladder logic | no edge: -0.010R / +0.010R vs a +0.034R random-walk baseline, 1 of 6 walk-forward folds positive | HANDOFF_SUMMARY.md (10y M15 backtest) |
| Small-sample subgroups | a London-vs-overlap edge on 3 years reversed on 10 years | HANDOFF_SUMMARY.md |
| MT5 CSV time | server time, EET (GMT+2/+3) (`data.csv_server_tz`) | HANDOFF_SUMMARY.md |

Not available when this skill was built (the docs were not in Drive or the repo): the full text of
Weekday_behaviour, Big_days_2pct, ICT backtests and Trade_History_Diagnosis. Instead:
- `xau.py stats --csv HIST.csv` recomputes weekday ranges, big days (>= `timemachine.big_day_pct`) and killzone ranges from your own data.
- The weekly review reads `trader/docs/Trade_History_Diagnosis.md` when you put it there and maps repeated mistakes to its numbered rules.
