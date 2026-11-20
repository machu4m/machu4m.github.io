"""Module 4 - spread / slippage / fakeout filter. Skip when the spread is too wide, right after a spike,
or when the last candles show a stop-hunt wick against the trade (or both sides hunted = chop)."""

from core import atr


def check(bars, side, spread, cfg):
    """bars: M1 bars up to now (last = latest closed). side 'long'/'short'. Returns {"pass", "reasons", "atr"}."""
    f = cfg["filters"]
    reasons = []
    if spread is None:
        spread = cfg["typical_spread"]
    if spread > cfg["max_spread"]:
        reasons.append("spread %.2f > max_spread %.2f" % (spread, cfg["max_spread"]))
    n = len(bars)
    a = atr(bars, max(1, n - f["spike_cooldown_bars"]), f["atr_bars"])
    if a is None:
        reasons.append("not enough bars for ATR(%d): need at least %d" % (f["atr_bars"], f["atr_bars"] + f["spike_cooldown_bars"] + 1))
        return {"pass": False, "reasons": reasons, "atr": None, "spread": spread}
    for b in bars[-f["spike_cooldown_bars"]:]:
        if b.rng > f["spike_atr_mult"] * a:
            reasons.append("spike: a %.2f candle (> %.1fx ATR %.2f) in the last %d bars"
                           % (b.rng, f["spike_atr_mult"], a, f["spike_cooldown_bars"]))
            break
    for b in bars[-f["fakeout_lookback_bars"]:]:
        if b.rng <= 0 or b.rng < f["fakeout_min_range_atr"] * a:
            continue
        against = (b.h - max(b.o, b.c)) if side == "long" else (min(b.o, b.c) - b.l)
        if against / b.rng >= f["fakeout_wick_ratio"]:
            reasons.append("stop-hunt wick against the %s: %.0f%% of a %.2f candle"
                           % (side, 100 * against / b.rng, b.rng))
            break
    L = f["double_sweep_lookback_bars"]
    if n >= 2 * L:
        prior, last = bars[-2 * L:-L], bars[-L:]
        if max(b.h for b in last) > max(b.h for b in prior) and min(b.l for b in last) < min(b.l for b in prior):
            reasons.append("both sides hunted in the last %d bars: chop / fakeout risk" % L)
    return {"pass": not reasons, "reasons": reasons, "atr": round(a, 3), "spread": spread}
