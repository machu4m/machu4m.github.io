"""Module 5 - exit planner. Stop beyond the structure (the sweep extreme) plus a buffer plus the spread;
targets in R; take the next liquidity when it pays at least min_rr."""


def plan(setup, cfg, spread=None):
    x = cfg["exits"]
    spread = cfg["typical_spread"] if spread is None else spread
    long = setup["side"] == "long"
    entry = setup["entry"]
    buf = x["structure_buffer"] + spread
    stop = setup["sweep_extreme"] - buf if long else setup["sweep_extreme"] + buf
    risk = (entry - stop) if long else (stop - entry)
    if risk <= 0:
        return {"ok": False, "reason": "stop is not beyond the entry (entry %.2f, stop %.2f)" % (entry, stop)}
    sgn = 1 if long else -1
    ladder = [{"r": r, "price": round(entry + sgn * r * risk, 2)} for r in x["targets_r"]]
    tp, basis = None, None
    liq = setup.get("target_liquidity")
    if x["use_liquidity_target"] and liq:
        tp, basis = liq["price"], "next liquidity: %s" % liq["name"]
    else:
        tp, basis = entry + sgn * x["default_target_r"] * risk, "default %gR (no liquidity target)" % x["default_target_r"]
    rr = sgn * (tp - entry) / risk
    return {
        "ok": True, "side": setup["side"], "entry": round(entry, 2), "stop": round(stop, 2), "tp": round(tp, 2),
        "tp_basis": basis, "rr": round(rr, 2), "risk_per_oz": round(risk, 2), "stop_buffer": round(buf, 2),
        "breakeven_at": round(entry + sgn * x["breakeven_at_r"] * risk, 2), "ladder": ladder,
        "note": "At %gR move the stop to breakeven. Never tighten the stop to fit a bigger lot." % x["breakeven_at_r"],
    }
