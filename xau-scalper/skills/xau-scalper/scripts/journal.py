"""Module 9 - journal and weekly review. trader/journal.csv holds every trade; the review flags repeated
mistakes and maps them to the rules in trader/docs/Trade_History_Diagnosis.md when that file is there."""

import csv
import datetime as dt
import os
import re
from collections import Counter, defaultdict

from clock import blocks_at
from core import UTC, XauError, local_tz, ny_tz

FIELDS = ["id", "open_time", "close_time", "side", "setup", "killzone", "entry", "sl", "tp", "lot",
          "exit", "pnl_usd", "r", "spread", "mistakes", "notes"]


def path(tdir):
    return os.path.join(tdir, "journal.csv")


def read(tdir):
    p = path(tdir)
    if not os.path.exists(p):
        return []
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


def write(tdir, rows):
    p = path(tdir)
    tmp = p + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    os.replace(tmp, p)


def _t(text, cfg):
    t = dt.datetime.fromisoformat(text)
    return t if t.tzinfo else t.replace(tzinfo=local_tz(cfg))


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def result(row, cfg):
    """(pnl_usd, r) for a closed row."""
    e, x, sl, lot = _f(row["entry"]), _f(row["exit"]), _f(row["sl"]), _f(row["lot"])
    if None in (e, x, lot):
        return None, None
    sgn = 1 if row["side"] == "long" else -1
    pnl = sgn * (x - e) * lot * cfg["contract_oz_per_lot"]
    r = None
    if sl is not None and abs(e - sl) > 0:
        r = sgn * (x - e) / abs(e - sl)
    return round(pnl, 2), (round(r, 2) if r is not None else None)


def add(tdir, cfg, side, entry, sl, tp, lot, open_time=None, setup="", killzone="", spread="", mistakes="", notes="",
        exit=None, close_time=None):
    rows = read(tdir)
    if side not in ("long", "short"):
        raise XauError("side must be long or short")
    now = dt.datetime.now(UTC).astimezone(local_tz(cfg))
    row = {"id": str(1 + max([int(r["id"]) for r in rows] or [0])),
           "open_time": (_t(open_time, cfg) if open_time else now).isoformat(timespec="minutes"),
           "close_time": "", "side": side, "setup": setup, "killzone": killzone, "entry": entry, "sl": sl, "tp": tp,
           "lot": lot, "exit": "", "pnl_usd": "", "r": "", "spread": spread, "mistakes": mistakes, "notes": notes}
    rows.append(row)
    write(tdir, rows)
    if exit is not None:
        return close(tdir, cfg, row["id"], exit, close_time)
    return row


def close(tdir, cfg, trade_id, exit, close_time=None):
    rows = read(tdir)
    for r in rows:
        if r["id"] == str(trade_id):
            r["exit"] = exit
            now = dt.datetime.now(UTC).astimezone(local_tz(cfg))
            r["close_time"] = (_t(close_time, cfg) if close_time else now).isoformat(timespec="minutes")
            pnl, rr = result(r, cfg)
            r["pnl_usd"], r["r"] = pnl, ("" if rr is None else rr)
            write(tdir, rows)
            return r
    raise XauError("no trade with id %s in %s" % (trade_id, path(tdir)))


# ---------------------------------------------------------------- day state for the risk gate

def day_state(rows, cfg, now_utc):
    """Today's trades (local date), realised P/L, and the losing streak that ends the day so far."""
    today = now_utc.astimezone(local_tz(cfg)).date()
    mine = sorted([r for r in rows if _t(r["open_time"], cfg).astimezone(local_tz(cfg)).date() == today],
                  key=lambda r: r["open_time"])
    closed = [r for r in mine if _f(r.get("pnl_usd")) is not None]
    realised = sum(_f(r["pnl_usd"]) for r in closed)
    streak = 0
    for r in sorted(closed, key=lambda r: r["close_time"] or r["open_time"]):
        streak = streak + 1 if _f(r["pnl_usd"]) < 0 else 0
    return {"date": today.isoformat(), "trades": len(mine), "open": len(mine) - len(closed),
            "realised_usd": round(realised, 2), "loss_streak": streak}


# ---------------------------------------------------------------- review

def auto_mistakes(rows, cfg):
    """{trade id: [mistake codes]} worked out from the journal itself."""
    from budget import risk_dollars
    allowed = float(risk_dollars(cfg))
    limit = cfg["account_size"] * cfg["daily_loss_limit_pct"] / 100.0
    out = defaultdict(list)
    rows = sorted(rows, key=lambda r: r["open_time"])
    by_day = defaultdict(list)
    for r in rows:
        t = _t(r["open_time"], cfg)
        day = t.astimezone(local_tz(cfg)).date()
        prior = by_day[day]
        ny = t.astimezone(ny_tz(cfg)).replace(tzinfo=None)
        blocks = blocks_at(ny, cfg)
        if any(b.startswith("dead hours") for b in blocks):
            out[r["id"]].append("outside_killzone")
        if any(not b.startswith("dead hours") for b in blocks):
            out[r["id"]].append("blocked_window")
        e, sl, tp, lot = _f(r["entry"]), _f(r["sl"]), _f(r["tp"]), _f(r["lot"])
        if sl is None:
            out[r["id"]].append("no_stop")
        else:
            if tp is not None and abs(e - sl) > 0 and abs(tp - e) / abs(e - sl) < cfg["min_rr"]:
                out[r["id"]].append("low_rr")
            if lot and abs(e - sl) * lot * cfg["contract_oz_per_lot"] > allowed + 0.005:
                out[r["id"]].append("oversize")
        if len(prior) >= cfg["max_trades_per_day"]:
            out[r["id"]].append("over_max_trades")
        done = [p for p in prior if p.get("close_time") and _t(p["close_time"], cfg) <= t and _f(p.get("pnl_usd")) is not None]
        if sum(_f(p["pnl_usd"]) for p in done) <= -limit:
            out[r["id"]].append("after_daily_stop")
        streak = 0
        for p in sorted(done, key=lambda p: p["close_time"]):
            streak = streak + 1 if _f(p["pnl_usd"]) < 0 else 0
        if streak >= cfg["stop_after_consecutive_losses"]:
            out[r["id"]].append("after_loss_streak")
        if done:
            last = max(done, key=lambda p: p["close_time"])
            gap = (t - _t(last["close_time"], cfg)).total_seconds() / 60
            if _f(last["pnl_usd"]) < 0 and gap <= cfg["journal"]["revenge_minutes"]:
                out[r["id"]].append("revenge")
        prior.append(r)
    return out


def diagnosis_rules(tdir, cfg):
    p = os.path.join(tdir, cfg["journal"]["diagnosis_doc"])
    if not os.path.exists(p):
        return None
    rules = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\s*(?:#+\s*)?(?:\*\*)?(?:rule\s*)?(\d{1,2})[.):]\s*(.+)", line, re.I)
            if m:
                rules.append((int(m.group(1)), m.group(2).strip().strip("*").strip()))
    return rules


def match_rule(code, rules, cfg):
    for kw in cfg["journal"]["diagnosis_keywords"].get(code, []):
        for n, text in rules:
            if kw.lower() in text.lower():
                return n, text
    return None


def review(tdir, cfg, now_utc, days=None):
    days = days or cfg["journal"]["review_days"]
    rows = read(tdir)
    since = now_utc - dt.timedelta(days=days)
    week = [r for r in rows if _t(r["open_time"], cfg) >= since]
    flags = auto_mistakes(rows, cfg)
    closed = [r for r in week if _f(r.get("pnl_usd")) is not None]
    wins = [r for r in closed if _f(r["pnl_usd"]) > 0]
    rs = [_f(r["r"]) for r in closed if _f(r.get("r")) is not None]
    counts = Counter()
    for r in week:
        for c in flags.get(r["id"], []):
            counts[c] += 1
        for c in filter(None, (x.strip() for x in (r.get("mistakes") or "").split(";"))):
            counts[c] += 1
    groups = {}
    for key in ("setup", "killzone"):
        g = defaultdict(list)
        for r in closed:
            g[r.get(key) or "?"].append(_f(r["pnl_usd"]))
        groups[key] = {k: {"trades": len(v), "net_usd": round(sum(v), 2)} for k, v in g.items()}
    wd = defaultdict(list)
    for r in closed:
        wd[_t(r["open_time"], cfg).astimezone(local_tz(cfg)).strftime("%a")].append(_f(r["pnl_usd"]))
    groups["weekday"] = {k: {"trades": len(v), "net_usd": round(sum(v), 2)} for k, v in wd.items()}
    rules = diagnosis_rules(tdir, cfg)
    thr = cfg["journal"]["repeat_threshold"]
    repeated = []
    for code, n in counts.most_common():
        if n >= thr:
            rule = match_rule(code, rules, cfg) if rules else None
            repeated.append({"mistake": code, "times": n, "rule": ("Rule %d: %s" % rule) if rule else None})
    return {
        "from": since.astimezone(local_tz(cfg)).date().isoformat(), "to": now_utc.astimezone(local_tz(cfg)).date().isoformat(),
        "trades": len(week), "closed": len(closed), "win_rate": round(len(wins) / len(closed), 3) if closed else None,
        "net_usd": round(sum(_f(r["pnl_usd"]) for r in closed), 2), "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
        "by": groups, "mistakes": dict(counts), "repeated": repeated,
        "diagnosis_doc": "found" if rules else "missing: put Trade_History_Diagnosis.md in trader/%s" % os.path.dirname(cfg["journal"]["diagnosis_doc"]),
    }


def review_md(doc):
    L = ["# Weekly review %s to %s" % (doc["from"], doc["to"]), "",
         "Trades %d (closed %d) | win rate %s | net $%.2f | avg R %s" % (
             doc["trades"], doc["closed"], "-" if doc["win_rate"] is None else "%.0f%%" % (100 * doc["win_rate"]),
             doc["net_usd"], "-" if doc["avg_r"] is None else doc["avg_r"]), ""]
    L.append("## Repeated mistakes")
    if doc["repeated"]:
        for m in doc["repeated"]:
            L.append("- **%s** x%d%s" % (m["mistake"], m["times"], (" -> " + m["rule"]) if m["rule"] else ""))
    else:
        L.append("- none repeated this week")
    for key in ("killzone", "setup", "weekday"):
        L += ["", "## By %s" % key] + ["- %s: %d trades, $%.2f" % (k, v["trades"], v["net_usd"]) for k, v in doc["by"][key].items()]
    L += ["", "Diagnosis doc: %s" % doc["diagnosis_doc"]]
    return "\n".join(L) + "\n"
