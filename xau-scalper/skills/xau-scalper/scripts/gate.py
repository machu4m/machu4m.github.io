"""Module 7 - risk gate. PASS only when every check passes; otherwise BLOCK with every reason."""

from budget import risk_dollars
from clock import blocks_at
from core import ny_tz


def check(trade, cfg, day, now_utc, spread=None, filter_result=None):
    """trade: {"side","entry","stop","tp","lot"}; day: journal.day_state(); filter_result: filters.check() or None."""
    side, e, sl, tp, lot = trade["side"], trade["entry"], trade["stop"], trade["tp"], trade["lot"]
    long = side == "long"
    oz = cfg["contract_oz_per_lot"]
    allowed = float(risk_dollars(cfg))
    limit = cfg["account_size"] * cfg["daily_loss_limit_pct"] / 100.0
    spread = cfg["typical_spread"] if spread is None else spread
    risk_usd = abs(e - sl) * lot * oz if sl is not None and lot else 0.0
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    sides_ok = sl is not None and tp is not None and ((sl < e < tp) if long else (tp < e < sl))
    add("stop_and_target", sides_ok, "stop %s and target %s on the right sides of entry %s" % (sl, tp, e))
    blocks = blocks_at(now_utc.astimezone(ny_tz(cfg)).replace(tzinfo=None), cfg)
    add("session", not blocks, "; ".join(blocks) or "inside a killzone")
    add("spread", spread <= cfg["max_spread"], "spread %.2f vs max %.2f" % (spread, cfg["max_spread"]))
    if filter_result is not None:
        add("filters", filter_result["pass"], "; ".join(filter_result["reasons"]) or "no spike / fakeout")
    add("risk_per_trade", lot > 0 and risk_usd <= allowed + 1e-9,
        "lot %.2f risks $%.2f vs $%.2f allowed" % (lot, risk_usd, allowed))
    rr = abs(tp - e) / abs(e - sl) if sides_ok else 0.0
    add("min_rr", rr >= cfg["min_rr"], "RR %.2f vs min %.2f" % (rr, cfg["min_rr"]))
    add("max_trades", day["trades"] < cfg["max_trades_per_day"],
        "%d of %d trades used today" % (day["trades"], cfg["max_trades_per_day"]))
    worst = -day["realised_usd"] + risk_usd
    add("daily_loss_limit", day["realised_usd"] > -limit and worst <= limit + 1e-9,
        "today $%.2f; if this one stops out the day is -$%.2f vs limit $%.2f" % (day["realised_usd"], worst, limit))
    add("consecutive_losses", day["loss_streak"] < cfg["stop_after_consecutive_losses"],
        "%d losses in a row today (stop at %d)" % (day["loss_streak"], cfg["stop_after_consecutive_losses"]))
    failed = [c for c in checks if not c["ok"]]
    return {"verdict": "PASS" if not failed else "BLOCK", "checks": checks, "risk_usd": round(risk_usd, 2),
            "rr": round(rr, 2), "blocked_by": [c["check"] + ": " + c["detail"] for c in failed]}


def report(g):
    L = ["RISK GATE: %s" % g["verdict"]]
    for c in g["checks"]:
        L.append("  [%s] %-18s %s" % ("ok" if c["ok"] else "XX", c["check"], c["detail"]))
    return "\n".join(L)
