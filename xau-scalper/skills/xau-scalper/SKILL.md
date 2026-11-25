---
name: xau-scalper
description: XAUUSD (gold) scalping and day-trading desk for MT5, paper/analysis only. Use when the user asks whether they can trade gold now, for a gold setup, entry, stop, take-profit or lot size, to replay or backtest a gold setup on MT5 M1 history, to check a trade against their risk rules, for an MT5 order ticket, to log a gold trade, or for a weekly trading review. Never places or sends orders.
---

# xau-scalper

One skill, nine modules, one config (`xau_config.json`). Everything reads and writes the `trader/` folder in the
current directory. **Paper / analysis only: no module can connect to MT5 or send an order.** The user types the
ticket into MT5 themselves.

Run everything through one script:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/xau-scalper/scripts/xau.py" <command> ...
```

(If `CLAUDE_PLUGIN_ROOT` is empty, use the folder this SKILL.md is in.) First time: `xau.py init` copies the
config to `trader/xau_config.json`; the user edits that copy. Rules from the user's docs go in `trader/docs/`.

## Modules and commands

| # | Module | Command | Writes |
|---|---|---|---|
| 1 | Session clock (killzones in Dubai time; dead hours, NY-open ban, rollover, weekend) | `clock [--now "YYYY-MM-DD HH:MM"]` | `trader/clock.json` |
| 2 | Setup engine: liquidity sweep -> close back inside -> displacement, long and short | `setups --csv M1.csv` | `trader/setups.json` |
| 3 | Time Machine: past hits of the same setup in MT5 M1 history, vs a random-entry baseline | `replay --csv HIST.csv [--setup-id asia_low_sweep_long --killzone ny] [--r 2]`, `stats --csv HIST.csv` | `trader/timemachine.json`, `trader/stats.json` |
| 4 | Spread / spike / stop-hunt-wick filter | inside `plan` | |
| 5 | Exit planner: stop past the sweep + buffer + spread; TP = next liquidity (or default R) | inside `plan` | |
| 6 | Budget: lot = risk$ / (stop distance x 100 oz), floored, refused when too wide | `size --entry E --stop S` | |
| 7 | Risk gate PASS/BLOCK | inside `plan` and `check` | `trader/gate_log.jsonl` |
| 8 | MT5 order ticket (only on PASS) | inside `plan` and `check` | `trader/tickets/*.txt` |
| 9 | Journal + weekly review | `journal add/close/list`, `review [--days 7]` | `trader/journal.csv`, `trader/reviews/` |

Full pipeline on the latest MT5 export: `plan --csv M1.csv [--spread 0.25] [--price P] [--replay HIST.csv]`.
A manual idea: `check --side long --entry E --stop S --tp T [--spread S]`.

## Workflow

1. **Clock first.** If BLOCKED, say so plainly and give the next allowed window in Dubai time. Do not build a plan for a blocked time unless the user asks for study only.
2. **Data.** Ask for an MT5 M1 export (MT5: View > Symbols > Bars, or a chart's Save As CSV). The live CSV needs the last ~3 days; the Time Machine wants as much M1 history as possible. Check `data.csv_server_tz` matches the broker's server clock.
3. **Plan.** Run `plan`. Report: setup and side, filter result, stop / TP / RR / breakeven, lot, every gate line, then the ticket only if PASS.
4. **Odds.** If a history CSV exists, run `replay` (or `plan --replay`). Quote the verdict as written, including the baseline comparison and sample size. Fewer than `min_samples` hits = unproven.
5. **After the trade.** `journal add ... --exit X` (or `add` then `close`). Weekly: `review`; lead with the repeated mistakes and the matching rule.

## Hard rules

- This skill never places or sends an order. Tickets are text for the user to type. The separate MT5 EA in `mt5/` trades automatically only when the user installs and runs it in MT5 (demo by default).
- Never hand over a ticket when the gate says BLOCK. Explain each block instead.
- Never tighten a stop to fit a bigger lot: a smaller lot or no trade. The budget never rounds a lot up.
- Every number comes from `xau_config.json`. To change a rule, edit the config, not the code.
- Be honest: after a sweep, direction is about 50/50 in the user's data and no setup has a proven edge on its own. A past hit rate is history, not a forecast. Say "not financial advice" once per chat.
- Strategy findings and their sources are in `references/strategy_rules.md`. Items marked UNVERIFIED there must be confirmed by the user before relying on them.
