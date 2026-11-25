"""Module 8 - MT5 order ticket for you to type or paste into MT5 yourself.
This module never connects to MT5 or any broker and never sends an order."""

import datetime as dt
import os


def order_type(side, entry, price, cfg):
    if price is None or abs(entry - price) <= cfg["strategy"]["market_entry_tolerance"]:
        return "Buy (market)" if side == "long" else "Sell (market)"
    if side == "long":
        return "Buy Limit" if entry < price else "Buy Stop"
    return "Sell Limit" if entry > price else "Sell Stop"


def make(trade, gate, cfg, price=None, setup_id="", now_utc=None):
    """Returns (text, None) on PASS or (None, reasons) on BLOCK. Never produces a ticket past a BLOCK."""
    if gate["verdict"] != "PASS":
        return None, gate["blocked_by"]
    pct = 100.0 * gate["risk_usd"] / cfg["account_size"]
    lines = [
        "MT5 ORDER TICKET - MANUAL ENTRY ONLY (nothing was sent to MT5)",
        "Symbol : %s" % cfg["symbol"],
        "Type   : %s" % order_type(trade["side"], trade["entry"], price, cfg),
        "Volume : %.2f" % trade["lot"],
        "Price  : %.2f" % trade["entry"],
        "S/L    : %.2f" % trade["stop"],
        "T/P    : %.2f" % trade["tp"],
        "Comment: %s RR%.2f" % (setup_id or "xau-scalper", gate["rr"]),
        "Risk   : %s %.2f (%.2f%% of %s)" % (cfg["currency"], gate["risk_usd"], pct, cfg["account_size"]),
    ]
    if trade.get("breakeven_at") is not None:
        lines.append("Manage : stop to breakeven at %.2f" % trade["breakeven_at"])
    if now_utc:
        lines.append("Made   : %s UTC" % now_utc.strftime("%Y-%m-%d %H:%M"))
    return "\n".join(lines), None


def save(text, tdir, now_utc):
    d = os.path.join(tdir, "tickets")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "ticket-%s.txt" % now_utc.strftime("%Y%m%d-%H%M%S"))
    with open(p, "w") as f:
        f.write(text + "\n")
    return p
