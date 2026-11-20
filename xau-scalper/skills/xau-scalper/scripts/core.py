"""Shared plumbing: config, the trader/ folder, time zones and the MT5 M1 CSV loader.

No trading number lives here. Every threshold is read from xau_config.json.
"""

import csv
import datetime as dt
import json
import os
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLED_CONFIG = os.path.join(os.path.dirname(HERE), "xau_config.json")
UTC = dt.timezone.utc


class XauError(Exception):
    """A problem the user must fix (bad input, missing file). Printed, never a traceback."""


# ---------------------------------------------------------------- config and trader/

def trader_dir(cfg=None, override=None):
    """The trader/ folder every module reads and writes. --trader-dir > $XAU_TRADER_DIR > ./<trader_dir>."""
    name = (cfg or {}).get("trader_dir", "trader")
    path = override or os.environ.get("XAU_TRADER_DIR") or os.path.join(os.getcwd(), name)
    os.makedirs(path, exist_ok=True)
    return path


def config_path(override=None, tdir=None):
    """--config > $XAU_CONFIG > trader/xau_config.json (your editable copy) > the bundled default."""
    for p in (override, os.environ.get("XAU_CONFIG")):
        if p:
            return p
    if tdir:
        mine = os.path.join(tdir, "xau_config.json")
        if os.path.exists(mine):
            return mine
    return BUNDLED_CONFIG


def load_config(path=None):
    path = path or BUNDLED_CONFIG
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        raise XauError("config not found: %s" % path)
    except json.JSONDecodeError as e:
        raise XauError("config %s is not valid JSON: %s" % (path, e))


def write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=2, default=str)
    os.replace(tmp, path)


# ---------------------------------------------------------------- time

def tz(name):
    return ZoneInfo(name)


def local_tz(cfg):
    return tz(cfg["timezone"])


def ny_tz(cfg):
    return tz(cfg["sessions"]["defined_in_tz"])


def hm(text):
    """'20:00' -> 1200 minutes after midnight."""
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def in_window(minute, start, end):
    """Minute-of-day inside [start, end). Handles windows that wrap midnight; '00:00' as an end means midnight."""
    s, e = hm(start), hm(end)
    if s == e:
        return False
    if s < e:
        return s <= minute < e
    return minute >= s or minute < e


def parse_now(text, cfg):
    """'2026-11-20 16:05' in the local (Dubai) zone, or an ISO time with an offset -> aware UTC. None -> now."""
    if not text:
        return dt.datetime.now(UTC)
    t = dt.datetime.fromisoformat(text.replace("T", " ").strip())
    if t.tzinfo is None:
        t = t.replace(tzinfo=local_tz(cfg))
    return t.astimezone(UTC)


def fmt_local(t_utc, cfg):
    return t_utc.astimezone(local_tz(cfg)).strftime("%a %Y-%m-%d %H:%M")


# ---------------------------------------------------------------- bars

class Bar:
    """One M1 candle. t = aware UTC open time; ny = naive New York wall time; sp = spread in price units."""
    __slots__ = ("t", "ny", "o", "h", "l", "c", "sp")

    def __init__(self, t, ny, o, h, l, c, sp):
        self.t, self.ny, self.o, self.h, self.l, self.c, self.sp = t, ny, o, h, l, c, sp

    @property
    def minute(self):
        return self.ny.hour * 60 + self.ny.minute

    @property
    def body(self):
        return abs(self.c - self.o)

    @property
    def rng(self):
        return self.h - self.l


def session_date(ny_naive, cfg):
    """The trading day a bar belongs to. The day starts at the rollover end (18:00 NY by default),
    so the Asia killzone (20:00-00:00 NY) belongs to the next calendar day, like on the chart."""
    start = hm(cfg["sessions"]["rollover_block"][1])
    shift = dt.timedelta(minutes=24 * 60 - start)
    return (ny_naive + shift).date()


def _norm(name):
    return name.strip().strip("<>").strip().lower()


def load_m1_csv(path, cfg, limit=None):
    """Read an MT5 'Bars' export (tab separated <DATE> <TIME> <OPEN> <HIGH> <LOW> <CLOSE> <TICKVOL> <VOL> <SPREAD>,
    dates YYYY.MM.DD, server time) or a plain comma CSV with the same columns. limit = keep only the last N bars."""
    if not os.path.exists(path):
        raise XauError("CSV not found: %s" % path)
    server = tz(cfg["data"]["csv_server_tz"])
    ny = ny_tz(cfg)
    point = cfg["data"]["point_size"]
    typical = cfg["typical_spread"]
    with open(path, newline="", encoding="utf-8-sig") as f:
        head = f.readline()
        delim = "\t" if "\t" in head else ";" if head.count(";") > head.count(",") else ","
        cols = [_norm(c) for c in head.rstrip("\r\n").split(delim)]
        idx = {c: i for i, c in enumerate(cols)}
        need = ("open", "high", "low", "close")
        if not all(c in idx for c in need) or not ("date" in idx or "datetime" in idx or "time" in idx):
            raise XauError("CSV %s: expected MT5 columns <DATE> <TIME> <OPEN> <HIGH> <LOW> <CLOSE> [<SPREAD>], got %s"
                           % (path, cols))
        rows = list(csv.reader(f, delimiter=delim))
    if limit:
        rows = rows[-limit:]
    cache = {}
    bars = []
    for r in rows:
        if not r or len(r) < len(need):
            continue
        if "date" in idx and "time" in idx:
            stamp = r[idx["date"]] + " " + r[idx["time"]]
        else:
            stamp = r[idx.get("datetime", idx.get("date", idx.get("time")))]
        stamp = stamp.replace(".", "-").replace("T", " ").strip()
        if stamp.count(":") == 1:
            stamp += ":00"
        try:
            naive = dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        key = (naive.date(), naive.hour)
        if key not in cache:
            aware = naive.replace(tzinfo=server)
            u = aware.astimezone(UTC)
            cache[key] = (u - naive.replace(tzinfo=UTC), u.astimezone(ny).replace(tzinfo=None) - u.replace(tzinfo=None))
        to_utc, utc_to_ny = cache[key]
        t = naive.replace(tzinfo=UTC) + to_utc
        sp = typical
        if "spread" in idx and idx["spread"] < len(r) and r[idx["spread"]].strip():
            sp = float(r[idx["spread"]]) * point
        bars.append(Bar(t, t.replace(tzinfo=None) + utc_to_ny, float(r[idx["open"]]), float(r[idx["high"]]),
                        float(r[idx["low"]]), float(r[idx["close"]]), sp))
    if not bars:
        raise XauError("CSV %s has no readable bars" % path)
    return bars


def atr(bars, end, n):
    """Average true range of the n bars before index end (exclusive). None when there is not enough history."""
    if end - n < 1:
        return None
    tot = 0.0
    for i in range(end - n, end):
        b, p = bars[i], bars[i - 1]
        tot += max(b.h - b.l, abs(b.h - p.c), abs(b.l - p.c))
    return tot / n
