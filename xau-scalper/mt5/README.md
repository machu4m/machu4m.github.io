# XAU_Scalper_EA (MT5)

Automatic version of the skill: same killzones, sweep setup, filters, exits, sizing and risk gate.
**It refuses to run on a real account unless you set `AllowLiveAccount = true`.** Test on demo first.

1. MT5: File > Open Data Folder > `MQL5/Experts/`, copy `XAU_Scalper_EA.mq5` there.
2. Open it in MetaEditor, press F7 (Compile). Send me any compile errors.
3. Strategy Tester: XAUUSD, M1, "Every tick based on real ticks", 1+ year. Judge it there first.
4. Demo: drag onto an XAUUSD M1 chart, tick "Allow Algo Trading", press Algo Trading in the toolbar.

Inputs mirror `xau_config.json`. Set `NyOpenBan` to your real rule 7 window. Every decision is logged to
`MQL5/Files/xau_scalper_log.csv` and the Experts tab.
