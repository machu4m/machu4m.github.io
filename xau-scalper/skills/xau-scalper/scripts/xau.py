#!/usr/bin/env python3
"""xau-scalper command line. Paper / analysis only: nothing here can place an order.

  xau.py init                      copy the default config to trader/xau_config.json (edit that one)
  xau.py clock [--now T]           killzone, allowed/blocked, next window (Dubai time)
  xau.py setups --csv M1.csv       levels, closer-side liquidity, setups in the last bars
  xau.py plan --csv M1.csv [--setup N] [--spread S] [--price P] [--now T]
                                   setup -> filter -> exits -> lot -> risk gate -> MT5 ticket
  xau.py check --side long --entry E --stop S --tp T [--spread S] [--now T]
                                   manual idea through size + gate (+ ticket on PASS)
  xau.py replay --csv HIST.csv [--setup-id ID --killzone KZ --side long|short] [--r R]
  xau.py stats --csv HIST.csv      weekday behaviour, big days, killzone ranges
  xau.py size --entry E --stop S
  xau.py journal add|close|list ...
  xau.py review [--days N]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import budget  # noqa: E402
import clock  # noqa: E402
import exits  # noqa: E402
import filters  # noqa: E402
import gate  # noqa: E402
import journal  # noqa: E402
import setups  # noqa: E402
import ticket  # noqa: E402
import timemachine  # noqa: E402
from core import (BUNDLED_CONFIG, XauError, config_path, load_config, load_m1_csv, parse_now,  # noqa: E402
                  trader_dir, write_json)

LIVE_BARS_PER_DAY = 1440  # minutes in a day: how many M1 bars make one day of history


def ctx(a):
    tdir = trader_dir(None, a.trader_dir)
    cfg = load_config(config_path(a.config, tdir))
    tdir = trader_dir(cfg, a.trader_dir)
    return cfg, tdir


def out(a, doc, text):
    print(json.dumps(doc, indent=2, default=str) if a.json else text)


def cmd_init(a):
    tdir = trader_dir(None, a.trader_dir)
    dst = os.path.join(tdir, "xau_config.json")
    if os.path.exists(dst):
        print("already there: %s (edit it; not overwritten)" % dst)
        return 0
    with open(BUNDLED_CONFIG) as f, open(dst, "w") as g:
        g.write(f.read())
    os.makedirs(os.path.join(tdir, "docs"), exist_ok=True)
    print("created %s\nput Trade_History_Diagnosis.md in %s" % (dst, os.path.join(tdir, "docs")))
    return 0


def cmd_clock(a):
    cfg, tdir = ctx(a)
    doc = clock.status(parse_now(a.now, cfg), cfg)
    write_json(os.path.join(tdir, "clock.json"), doc)
    out(a, doc, clock.report(doc))
    return 0


def live_bars(a, cfg):
    days = getattr(a, "days", None) or cfg["strategy"]["live_days"]
    return load_m1_csv(a.csv, cfg, limit=days * LIVE_BARS_PER_DAY)


def cmd_setups(a):
    cfg, tdir = ctx(a)
    doc = setups.live(live_bars(a, cfg), cfg)
    write_json(os.path.join(tdir, "setups.json"), doc)
    lines = ["Price %.2f | above: %s | below: %s" % (doc["price"], doc["nearest_above"], doc["nearest_below"])]
    if doc["closer_side_note"]:
        lines.append(doc["closer_side_note"])
    lines.append(doc["direction_note"])
    if not doc["setups"]:
        lines.append("No sweep + reclaim + displacement setup in the last %d bars." % cfg["strategy"]["scan_bars"])
    for n, s in enumerate(doc["setups"]):
        lines.append("[%d] %s %s at %s (%s) entry %.2f %s, swept %.2f, target %s"
                     % (n, s["id"], s["side"], s["time_ny"][11:16] + " NY", s["killzone"], s["entry"], s["entry_type"],
                        s["sweep_extreme"], s["target_liquidity"]))
    out(a, doc, "\n".join(lines))
    return 0


def gate_and_ticket(a, cfg, tdir, now, trade, spread, filt, price, setup_id):
    day = journal.day_state(journal.read(tdir), cfg, now)
    g = gate.check(trade, cfg, day, now, spread=spread, filter_result=filt)
    with open(os.path.join(tdir, "gate_log.jsonl"), "a") as f:
        f.write(json.dumps({"time": now.isoformat(), "trade": trade, "gate": g["verdict"], "blocked_by": g["blocked_by"]}) + "\n")
    text, why = ticket.make(trade, g, cfg, price=price, setup_id=setup_id, now_utc=now)
    path = ticket.save(text, tdir, now) if text else None
    return g, text, path


def cmd_plan(a):
    cfg, tdir = ctx(a)
    now = parse_now(a.now, cfg)
    bars = live_bars(a, cfg)
    clk = clock.status(now, cfg)
    live = setups.live(bars, cfg)
    if not live["setups"]:
        doc = {"clock": clk, "setups": live, "result": "no setup"}
        write_json(os.path.join(tdir, "plan.json"), doc)
        out(a, doc, clock.report(clk) + "\n\nNo setup in the last %d bars. Nothing to plan." % cfg["strategy"]["scan_bars"])
        return 0
    s = live["setups"][a.setup if a.setup is not None else -1]
    spread = a.spread if a.spread is not None else bars[-1].sp
    filt = filters.check(bars, s["side"], spread, cfg)
    ex = exits.plan(s, cfg, spread=spread)
    doc = {"clock": clk, "setup": s, "filters": filt, "exits": ex}
    lines = [clock.report(clk), "", "SETUP %s %s entry %.2f (%s)" % (s["id"], s["side"], s["entry"], s["entry_type"]),
             live["direction_note"], "FILTER: %s" % ("PASS" if filt["pass"] else "SKIP - " + "; ".join(filt["reasons"]))]
    if not ex["ok"]:
        lines.append("EXITS: " + ex["reason"])
    else:
        lines.append("EXITS: stop %.2f (structure + %.2f buffer) | TP %.2f (%s) RR %.2f | BE at %.2f"
                     % (ex["stop"], ex["stop_buffer"], ex["tp"], ex["tp_basis"], ex["rr"], ex["breakeven_at"]))
        sz = budget.size(ex["entry"], ex["stop"], cfg)
        doc["size"] = sz
        lines.append("SIZE: " + ("lot %.2f risks $%.2f of $%.2f" % (sz["lot"], sz["risk_usd"], sz["risk_allowed"])
                                 if sz["ok"] else "REFUSED - " + sz["reason"]) + (" | " + sz["warning"] if sz.get("warning") else ""))
        trade = {"side": s["side"], "entry": ex["entry"], "stop": ex["stop"], "tp": ex["tp"], "lot": sz["lot"],
                 "breakeven_at": ex["breakeven_at"]}
        g, text, path = gate_and_ticket(a, cfg, tdir, now, trade, spread, filt, a.price or bars[-1].c, s["id"])
        doc["gate"], doc["ticket_file"] = g, path
        lines += ["", gate.report(g), ""]
        lines.append(text + "\n(saved %s)" % path if text else "NO TICKET: the gate blocked this trade.")
    if a.replay:
        hist = load_m1_csv(a.replay, cfg)
        tm = timemachine.run(hist, dict(s, bar_index=len(hist)), cfg, target_r=ex.get("rr") if ex["ok"] else None)
        doc["timemachine"] = tm
        lines += ["", timemachine.report(tm)]
    write_json(os.path.join(tdir, "plan.json"), doc)
    out(a, doc, "\n".join(lines))
    return 0


def cmd_check(a):
    cfg, tdir = ctx(a)
    now = parse_now(a.now, cfg)
    sz = budget.size(a.entry, a.stop, cfg)
    lot = a.lot if a.lot is not None else sz.get("lot", 0.0)
    trade = {"side": a.side, "entry": a.entry, "stop": a.stop, "tp": a.tp, "lot": lot}
    g, text, path = gate_and_ticket(a, cfg, tdir, now, trade, a.spread, None, a.price, a.setup_id)
    lines = ["SIZE: " + ("lot %.2f risks $%.2f" % (sz["lot"], sz["risk_usd"]) if sz["ok"] else "REFUSED - " + sz["reason"]),
             gate.report(g), "", text + "\n(saved %s)" % path if text else "NO TICKET: the gate blocked this trade."]
    out(a, {"size": sz, "gate": g, "ticket_file": path}, "\n".join(lines))
    return 0


def cmd_size(a):
    cfg, _ = ctx(a)
    sz = budget.size(a.entry, a.stop, cfg)
    out(a, sz, ("lot %.2f (raw %.4f) risks $%.2f of $%.2f" % (sz["lot"], sz["raw_lot"], sz["risk_usd"], sz["risk_allowed"])
                if sz["ok"] else "REFUSED - " + sz["reason"]) + ("\n" + sz["warning"] if sz.get("warning") else ""))
    return 0


def cmd_replay(a):
    cfg, tdir = ctx(a)
    bars = load_m1_csv(a.csv, cfg)
    if a.setup_id:
        side = a.side or ("long" if a.setup_id.endswith("long") else "short")
        s = {"id": a.setup_id, "side": side, "killzone": a.killzone, "bar_index": len(bars)}
    else:
        cur = json.load(open(os.path.join(tdir, "setups.json")))["setups"]
        if not cur:
            raise XauError("no current setup in trader/setups.json; pass --setup-id")
        s = dict(cur[-1], bar_index=len(bars))
    doc = timemachine.run(bars, s, cfg, target_r=a.r or cfg["exits"]["default_target_r"], same_killzone=bool(s.get("killzone")))
    write_json(os.path.join(tdir, "timemachine.json"), doc)
    out(a, doc, timemachine.report(doc))
    return 0


def cmd_stats(a):
    cfg, tdir = ctx(a)
    doc = timemachine.stats(load_m1_csv(a.csv, cfg), cfg)
    write_json(os.path.join(tdir, "stats.json"), doc)
    lines = ["%d trading days. Big day = |close-open| >= %g%%" % (doc["days"], doc["big_day_pct"])]
    for k, v in doc["weekday"].items():
        lines.append("  %s: %d days, avg range %.2f%%, big days %d" % (k, v["days"], v["avg_range_pct"], v["big_days"]))
    lines.append("Killzone avg range ($): %s" % doc["killzone_avg_range_usd"])
    out(a, doc, "\n".join(lines))
    return 0


def cmd_journal(a):
    cfg, tdir = ctx(a)
    if a.action == "add":
        r = journal.add(tdir, cfg, a.side, a.entry, a.stop, a.tp, a.lot, a.open_time, a.setup or "", a.killzone or "",
                        a.spread if a.spread is not None else "", a.mistakes or "", a.notes or "", a.exit, a.close_time)
        out(a, r, "logged #%s %s" % (r["id"], ("P/L %s, R %s" % (r["pnl_usd"], r["r"])) if r.get("pnl_usd") != "" else "(open)"))
    elif a.action == "close":
        r = journal.close(tdir, cfg, a.id, a.exit, a.close_time)
        out(a, r, "closed #%s P/L %s R %s" % (r["id"], r["pnl_usd"], r["r"]))
    else:
        rows = journal.read(tdir)
        out(a, rows, "\n".join("#%s %s %s %s @%s sl %s tp %s lot %s -> %s (%s)" % (
            r["id"], r["open_time"], r["side"], r["setup"], r["entry"], r["sl"], r["tp"], r["lot"], r["exit"] or "open",
            r["pnl_usd"]) for r in rows) or "journal is empty")
    return 0


def cmd_review(a):
    cfg, tdir = ctx(a)
    now = parse_now(a.now, cfg)
    doc = journal.review(tdir, cfg, now, a.days)
    md = journal.review_md(doc)
    d = os.path.join(tdir, "reviews")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "review-%s.md" % doc["to"])
    with open(p, "w") as f:
        f.write(md)
    out(a, doc, md + "\n(saved %s)" % p)
    return 0


def parser():
    P = argparse.ArgumentParser(prog="xau.py", description="xau-scalper: paper/analysis only, never places orders")
    P.add_argument("--config")
    P.add_argument("--trader-dir")
    P.add_argument("--json", action="store_true")
    sub = P.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    c = sub.add_parser("clock")
    c.add_argument("--now")
    s = sub.add_parser("setups")
    s.add_argument("--csv", required=True)
    s.add_argument("--days", type=int)
    p = sub.add_parser("plan")
    p.add_argument("--csv", required=True)
    p.add_argument("--days", type=int)
    p.add_argument("--setup", type=int)
    p.add_argument("--spread", type=float)
    p.add_argument("--price", type=float)
    p.add_argument("--now")
    p.add_argument("--replay", help="long M1 history CSV for the Time Machine")
    k = sub.add_parser("check")
    for f in ("entry", "stop", "tp"):
        k.add_argument("--" + f, type=float, required=True)
    k.add_argument("--side", choices=("long", "short"), required=True)
    k.add_argument("--lot", type=float)
    k.add_argument("--spread", type=float)
    k.add_argument("--price", type=float)
    k.add_argument("--setup-id", default="manual")
    k.add_argument("--now")
    z = sub.add_parser("size")
    z.add_argument("--entry", type=float, required=True)
    z.add_argument("--stop", type=float, required=True)
    r = sub.add_parser("replay")
    r.add_argument("--csv", required=True)
    r.add_argument("--setup-id")
    r.add_argument("--killzone")
    r.add_argument("--side", choices=("long", "short"))
    r.add_argument("--r", type=float)
    t = sub.add_parser("stats")
    t.add_argument("--csv", required=True)
    j = sub.add_parser("journal")
    j.add_argument("action", choices=("add", "close", "list"))
    j.add_argument("--id")
    j.add_argument("--side", choices=("long", "short"))
    for f in ("entry", "stop", "tp", "lot", "exit", "spread"):
        j.add_argument("--" + f, type=float)
    for f in ("open-time", "close-time", "setup", "killzone", "mistakes", "notes"):
        j.add_argument("--" + f)
    v = sub.add_parser("review")
    v.add_argument("--days", type=int)
    v.add_argument("--now")
    return P


def main(argv=None):
    a = parser().parse_args(argv)
    try:
        return globals()["cmd_" + a.cmd](a)
    except XauError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
