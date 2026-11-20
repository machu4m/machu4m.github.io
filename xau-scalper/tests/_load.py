"""Load xau-scalper modules and the bundled config; build synthetic MT5 M1 CSVs for tests."""

import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "xau-scalper", "scripts")
sys.path.insert(0, SCRIPTS)
CONFIG = os.path.join(ROOT, "skills", "xau-scalper", "xau_config.json")


def cfg(**over):
    c = json.load(open(CONFIG))
    c.update(over)
    return c


def write_mt5(path, rows, start, spread_pts=20):
    """rows: [(o,h,l,c)] one per minute from start, or [(datetime,o,h,l,c)] (naive server time). MT5 tab-separated."""
    with open(path, "w") as f:
        f.write("<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n")
        t = start
        for row in rows:
            if len(row) == 5:
                t, row = row[0], row[1:]
            o, h, l, c = row
            f.write("%s\t%s\t%.2f\t%.2f\t%.2f\t%.2f\t10\t0\t%d\n" % (t.strftime("%Y.%m.%d"), t.strftime("%H:%M:%S"), o, h, l, c, spread_pts))
            t += dt.timedelta(minutes=1)


class B:
    """Minimal bar for filter tests."""
    def __init__(self, o, h, l, c):
        self.o, self.h, self.l, self.c = o, h, l, c

    @property
    def rng(self):
        return self.h - self.l

    @property
    def body(self):
        return abs(self.c - self.o)


def flat(n, price=2650.0, half=0.5):
    return [B(price, price + half, price - half, price) for _ in range(n)]
