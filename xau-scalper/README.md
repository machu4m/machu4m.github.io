# xau-scalper

One Claude skill for scalping and day-trading XAUUSD on MT5. Built from the 11-skill
[day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) pack (MIT), adapted to gold, killzones in
Dubai time and an ICT/SMC sweep model. **Paper/analysis only: it never places orders.**

- Settings: `skills/xau-scalper/xau_config.json` (`xau.py init` copies it to `trader/` for editing).
- Data: MT5 M1 CSV exports. Output: the `trader/` folder.
- Rules and sources: `skills/xau-scalper/references/strategy_rules.md`.

```bash
claude plugin marketplace add ./xau-scalper
```

```bash
claude plugin install xau-scalper@xau-scalper
```

```bash
python3 -m pytest xau-scalper/tests -q
```
