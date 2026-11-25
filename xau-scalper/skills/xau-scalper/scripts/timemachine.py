"""Module 3 - Time Machine for gold. Replays every past hit of the same setup in your MT5 M1 history:
target first / stop first / neither, average R after spread, and the most recent hits. Then compares
against a no-edge baseline (random entries, same killzone, same side, same stop sizes, same R) and says
plainly whether the setup did better than chance. Stop wins when both are hit inside one bar."""

import random
from collections import Counter, defaultdict

import exits as exit_mod
from clock import killzone_at
from setups import scan


def walk(bars, start, side, entry, stop, target, max_hold):
    """Walk bars[start:] for a filled trade. Returns (outcome, exit_price, bars_held). Stop wins ties."""
    long = side == "long"
    end = min(len(bars), start + max_hold)
    for i in range(start, end):
        b = bars[i]
        if long:
            if b.l <= stop:
                return "stop", stop, i - start + 1
            if b.h >= target:
                return "target", target, i - start + 1
        else:
            if b.h >= stop:
                return "stop", stop, i - start + 1
            if b.l <= target:
                return "target", target, i - start + 1
    last = bars[end - 1].c if end > start else entry
    return "neither", last, end - start


def fill(bars, start, entry, cfg, etype):
    """Index of the bar that fills the order, or None. Market orders fill on the next bar."""
    if etype == "market":
        return start if start < len(bars) else None
    for i in range(start, min(len(bars), start + cfg["strategy"]["limit_fill_max_bars"])):
        if bars[i].l <= entry <= bars[i].h:
            return i
    return None


def replay_one(bars, s, target_r, cfg):
    sp = bars[s["bar_index"]].sp
    p = exit_mod.plan(s, cfg, spread=sp)
    if not p["ok"]:
        return None
    risk = p["risk_per_oz"]
    sgn = 1 if s["side"] == "long" else -1
    target = s["entry"] + sgn * target_r * risk
    f = fill(bars, s["bar_index"] + 1, s["entry"], cfg, s["entry_type"])
    if f is None:
        return {"outcome": "unfilled", "time_ny": s["time_ny"], "killzone": s["killzone"]}
    out, px, held = walk(bars, f, s["side"], s["entry"], p["stop"], target,
                         cfg["timemachine"]["max_hold_bars"])
    r = sgn * (px - s["entry"]) / risk - sp / risk
    return {"outcome": out, "r": round(r, 3), "bars": held, "risk": risk, "time_ny": s["time_ny"],
            "weekday": s["weekday"], "killzone": s["killzone"], "entry": s["entry"], "stop": p["stop"],
            "target": round(target, 2)}


def baseline(bars, side, killzone, risks, target_r, cfg):
    """No-edge reference: random bars in the same killzone, same side, stop sizes drawn from the real hits."""
    tm = cfg["timemachine"]
    pool = [i for i in range(len(bars) - 1) if killzone_at(bars[i].minute, cfg) == killzone]
    if not pool or not risks:
        return None
    rng = random.Random(tm["seed"])
    sgn = 1 if side == "long" else -1
    cnt = Counter()
    rs = []
    for _ in range(tm["baseline_samples"]):
        i = rng.choice(pool)
        risk = rng.choice(risks)
        e = bars[i].c
        stop, tgt = e - sgn * risk, e + sgn * target_r * risk
        out, px, _ = walk(bars, i + 1, side, e, stop, tgt, tm["max_hold_bars"])
        cnt[out] += 1
        rs.append(sgn * (px - e) / risk - bars[i].sp / risk)
    n = tm["baseline_samples"]
    return {"samples": n, "p_target": round(cnt["target"] / n, 4), "p_stop": round(cnt["stop"] / n, 4),
            "p_neither": round(cnt["neither"] / n, 4), "avg_r": round(sum(rs) / n, 3)}


def summarize(results, target_r):
    filled = [r for r in results if r and r["outcome"] != "unfilled"]
    n = len(filled)
    c = Counter(r["outcome"] for r in filled)
    doc = {"hits": len(results), "filled": n, "unfilled": len(results) - n, "target_r": target_r}
    if n:
        doc.update(p_target=round(c["target"] / n, 4), p_stop=round(c["stop"] / n, 4),
                   p_neither=round(c["neither"] / n, 4), avg_r=round(sum(r["r"] for r in filled) / n, 3))
    return doc


def verdict(s, base, cfg):
    tm = cfg["timemachine"]
    if not s.get("filled"):
        return "No past hits of this setup in the history you gave me. Unproven: trade it small or not at all."
    if s["filled"] < tm["min_samples"]:
        return ("Only %d past hits (need %d): too few to say anything. Treat it as unproven."
                % (s["filled"], tm["min_samples"]))
    pt, ps = s["p_target"], s["p_stop"]
    even = 1.0 / (1.0 + s["target_r"])
    share = pt / (pt + ps) if pt + ps else 0.0
    gap = share - even
    if abs(gap) < tm["coin_flip_band"]:
        head = "Close to break-even"
    elif abs(gap) < tm["strong_band"]:
        head = "Leans toward the target" if gap > 0 else "Leans toward the stop"
    else:
        head = "Strongly toward the target" if gap > 0 else "Strongly toward the stop"
    txt = ("%s: at %gR the target must come first in %.0f%% of decided trades to break even; it did in %.0f%% "
           "(target %.1f%% / stop %.1f%% / neither %.1f%%, avg %+.3fR after spread)."
           % (head, s["target_r"], 100 * even, 100 * share, 100 * pt, 100 * ps, 100 * s["p_neither"], s["avg_r"]))
    if base:
        edge = s["avg_r"] - base["avg_r"]
        txt += (" Random entries in the same killzone made %+.3fR, so the setup is %s chance by %+.3fR."
                % (base["avg_r"], "better than" if edge > tm["coin_flip_band"] else "no better than" if edge > -tm["coin_flip_band"] else "worse than", edge))
    return txt


def run(bars, setup, cfg, target_r=None, same_killzone=True):
    """Replay past hits of setup['id'] (only those completed before the setup's own bar)."""
    found, _ = scan(bars, cfg)
    cut = setup.get("bar_index", len(bars))
    past = [s for s in found if s["id"] == setup["id"] and s["bar_index"] < cut
            and (not same_killzone or s["killzone"] == setup["killzone"])]
    if target_r is None:
        p = exit_mod.plan(setup, cfg)
        target_r = p["rr"] if p["ok"] else cfg["exits"]["default_target_r"]
    results = [replay_one(bars, s, target_r, cfg) for s in past]
    results = [r for r in results if r]
    s = summarize(results, round(target_r, 2))
    risks = [r["risk"] for r in results if r.get("risk")]
    base = baseline(bars, setup["side"], setup["killzone"], risks, target_r, cfg) if risks else None
    recent = [r for r in results if r["outcome"] != "unfilled"][-cfg["timemachine"]["recent_hits_shown"]:]
    by_wd = defaultdict(Counter)
    for r in results:
        if r["outcome"] != "unfilled":
            by_wd[r["weekday"]][r["outcome"]] += 1
    return {"setup_id": setup["id"], "killzone": setup["killzone"] if same_killzone else "any", "summary": s,
            "baseline": base, "by_weekday": {k: dict(v) for k, v in by_wd.items()}, "recent_hits": recent,
            "verdict": verdict(s, base, cfg),
            "honesty": "Past hits are history, not a forecast. After a sweep, direction is about %d/%d in your data."
                       % (cfg["strategy"]["sweep_direction_odds_pct"], 100 - cfg["strategy"]["sweep_direction_odds_pct"])}


def stats(bars, cfg):
    """Weekday behaviour and big days from the CSV itself: average daily range %, big-day share, killzone ranges."""
    from core import session_date
    days = {}
    for b in bars:
        d = session_date(b.ny, cfg)
        x = days.setdefault(d, {"o": b.o, "h": b.h, "l": b.l, "c": b.c, "kz": {}})
        x["h"], x["l"], x["c"] = max(x["h"], b.h), min(x["l"], b.l), b.c
        kz = killzone_at(b.minute, cfg)
        if kz:
            k = x["kz"].setdefault(kz, [b.h, b.l])
            k[0], k[1] = max(k[0], b.h), min(k[1], b.l)
    big = cfg["timemachine"]["big_day_pct"]
    wd = defaultdict(list)
    kzr = defaultdict(list)
    for d, x in days.items():
        rng_pct = 100.0 * (x["h"] - x["l"]) / x["o"]
        wd[d.strftime("%a")].append((rng_pct, abs(100.0 * (x["c"] - x["o"]) / x["o"]) >= big))
        for k, (h, l) in x["kz"].items():
            kzr[k].append(h - l)
    return {
        "days": len(days), "big_day_pct": big,
        "weekday": {k: {"days": len(v), "avg_range_pct": round(sum(a for a, _ in v) / len(v), 3),
                        "big_days": sum(1 for _, g in v if g)} for k, v in wd.items()},
        "killzone_avg_range_usd": {k: round(sum(v) / len(v), 2) for k, v in kzr.items()},
    }


def report(doc):
    s = doc["summary"]
    L = ["TIME MACHINE  %s  (killzone: %s)" % (doc["setup_id"], doc["killzone"]),
         "Past hits %d, filled %d, unfilled %d, scored at %gR" % (s["hits"], s["filled"], s["unfilled"], s["target_r"])]
    if s.get("filled"):
        L.append("Target first %.1f%% | stop first %.1f%% | neither %.1f%% | avg %+.3fR"
                 % (100 * s["p_target"], 100 * s["p_stop"], 100 * s["p_neither"], s["avg_r"]))
    if doc["baseline"]:
        b = doc["baseline"]
        L.append("Baseline (random, same killzone): target %.1f%% | stop %.1f%% | avg %+.3fR"
                 % (100 * b["p_target"], 100 * b["p_stop"], b["avg_r"]))
    L += ["VERDICT: " + doc["verdict"], doc["honesty"]]
    if doc["recent_hits"]:
        L.append("Recent hits:")
        L += ["  %s %s %s %+.2fR" % (r["time_ny"][:16], r["weekday"], r["outcome"], r["r"]) for r in doc["recent_hits"]]
    return "\n".join(L)
