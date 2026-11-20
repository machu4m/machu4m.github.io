"""Module 1 - session clock. Killzones are defined in NY time (config) and shown in Dubai time.
Blocks dead hours, the NY-open ban, daily rollover and the weekend."""

import datetime as dt

from core import UTC, hm, in_window, local_tz, ny_tz


def killzone_at(ny_minute, cfg):
    for name, (s, e) in cfg["sessions"]["killzones"].items():
        if in_window(ny_minute, s, e):
            return name
    return None


def blocks_at(ny_naive, cfg):
    """Reasons the clock forbids a new trade at this NY wall time. Empty list = allowed."""
    s = cfg["sessions"]
    m = ny_naive.hour * 60 + ny_naive.minute
    wd = ny_naive.weekday()  # Mon=0 .. Sun=6
    out = []
    if wd == 5 or (wd == 4 and m >= hm(s["friday_block_from"])) or (wd == 6 and m < hm(s["sunday_open_block_until"])):
        out.append("weekend: market closed or thin around the weekly close/open")
    if in_window(m, *s["rollover_block"]):
        out.append("daily rollover: spreads widen")
    if s.get("ny_open_ban") and in_window(m, *s["ny_open_ban"]):
        out.append("NY-open ban (your rule 7)")
    if killzone_at(m, cfg) is None and not s.get("allow_outside_killzones"):
        out.append("dead hours: outside the Asia / London / NY killzones")
    return out


def _to_local(ny_date, hhmm, cfg):
    """A NY wall time on ny_date -> the same instant in the local zone."""
    h, m = divmod(hm(hhmm), 60)
    return dt.datetime(ny_date.year, ny_date.month, ny_date.day, h, m, tzinfo=ny_tz(cfg)).astimezone(local_tz(cfg))


def status(now_utc, cfg):
    """The clock document: what time it is, which killzone, allowed or blocked and why, today's windows in Dubai time."""
    ny = now_utc.astimezone(ny_tz(cfg)).replace(tzinfo=None)
    loc = now_utc.astimezone(local_tz(cfg))
    kz = killzone_at(ny.hour * 60 + ny.minute, cfg)
    reasons = blocks_at(ny, cfg)
    windows = []
    for name, (s, e) in cfg["sessions"]["killzones"].items():
        a = _to_local(ny.date(), s, cfg)
        b = _to_local(ny.date(), e, cfg)
        windows.append({"killzone": name, "start_local": a.strftime("%H:%M"), "end_local": b.strftime("%H:%M")})
    ban = cfg["sessions"].get("ny_open_ban")
    nxt = None
    probe = now_utc.replace(second=0, microsecond=0)
    step = dt.timedelta(minutes=1)
    for _ in range(7 * 24 * 60):  # first allowed minute within a week
        probe += step
        pny = probe.astimezone(ny_tz(cfg)).replace(tzinfo=None)
        if not blocks_at(pny, cfg):
            nxt = {"local": probe.astimezone(local_tz(cfg)).strftime("%a %Y-%m-%d %H:%M"),
                   "killzone": killzone_at(pny.hour * 60 + pny.minute, cfg)}
            break
    return {
        "now_local": loc.strftime("%a %Y-%m-%d %H:%M %Z"),
        "now_ny": ny.strftime("%a %H:%M"),
        "killzone": kz,
        "allowed": not reasons,
        "blocked_by": reasons,
        "killzones_local_today": windows,
        "ny_open_ban_local": [_to_local(ny.date(), x, cfg).strftime("%H:%M") for x in ban] if ban else None,
        "next_allowed": None if not reasons else nxt,
    }


def report(doc):
    lines = ["Clock  %s  (NY %s)" % (doc["now_local"], doc["now_ny"]),
             "Killzone: %s" % (doc["killzone"] or "none"),
             "Trading: %s" % ("ALLOWED" if doc["allowed"] else "BLOCKED - " + "; ".join(doc["blocked_by"]))]
    if doc["next_allowed"]:
        lines.append("Next allowed: %s (%s)" % (doc["next_allowed"]["local"], doc["next_allowed"]["killzone"]))
    lines.append("Killzones today (local): " + ", ".join(
        "%s %s-%s" % (w["killzone"], w["start_local"], w["end_local"]) for w in doc["killzones_local_today"]))
    if doc["ny_open_ban_local"]:
        lines.append("NY-open ban (local): %s-%s" % tuple(doc["ny_open_ban_local"]))
    return "\n".join(lines)


def now_utc():
    return dt.datetime.now(UTC)
