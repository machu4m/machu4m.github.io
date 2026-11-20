"""Module 2 - setup engine (ICT/SMC). Replaces the opening-range breakout.

Model: liquidity sweep -> close back inside -> displacement, long and short.
Liquidity levels: Asia H/L, London H/L (killzone ranges), PDH/PDL, EQH/EQL (equal swing highs/lows).
A setup id is '<level>_sweep_<long|short>', e.g. asia_low_sweep_long; the Time Machine replays
past hits of the same id in the same killzone. The detector reads bars strictly in order: no lookahead.
"""

from collections import deque

from clock import killzone_at
from core import session_date

HIGH_LEVELS = ("asia_high", "london_high", "pdh", "eqh")
LOW_LEVELS = ("asia_low", "london_low", "pdl", "eql")


class Detector:
    def __init__(self, cfg):
        self.cfg = cfg
        st = cfg["strategy"]
        self.st = st
        self.day = None
        self.day_hi = self.day_lo = None
        self.prev_hi = self.prev_lo = None
        self.levels = {}          # name -> price (active, untaken)
        self.taken = set()
        self.sess = {}            # killzone name -> [hi, lo, seen, done]
        self.pending = {}         # level -> {"start", "extreme", "reclaim"}
        self.bodies = deque(maxlen=st["displacement_avg_bars"])
        self.win = deque(maxlen=2 * st["eq_swing_bars"] + 1)
        self.swings_h = deque()
        self.swings_l = deque()
        self.recent = deque(maxlen=3)  # last 3 bars for FVG
        self.i = -1

    # ------------------------------------------------ levels
    def _new_day(self, d):
        if self.day is not None:
            self.prev_hi, self.prev_lo = self.day_hi, self.day_lo
        self.day = d
        self.day_hi = self.day_lo = None
        self.levels, self.taken, self.pending = {}, set(), {}
        self.sess = {k: [None, None, False, False] for k in self.st["level_sessions"]}
        if self.prev_hi is not None:
            self.levels["pdh"], self.levels["pdl"] = self.prev_hi, self.prev_lo

    def _session_levels(self, b):
        kz = killzone_at(b.minute, self.cfg)
        for name, s in self.sess.items():
            if s[3]:
                continue
            if kz == name:
                s[0] = b.h if s[0] is None else max(s[0], b.h)
                s[1] = b.l if s[1] is None else min(s[1], b.l)
                s[2] = True
            elif s[2]:  # window just ended: the range becomes liquidity
                s[3] = True
                self.levels[name + "_high"], self.levels[name + "_low"] = s[0], s[1]

    def _equal_levels(self, b):
        n = self.st["eq_swing_bars"]
        self.win.append((self.i, b.h, b.l))
        if len(self.win) < 2 * n + 1:
            return
        mid = self.win[n]
        others = [w for k, w in enumerate(self.win) if k != n]
        horizon = self.i - self.st["eq_window_bars"]
        tol = self.st["eq_tolerance"]
        for side, val, better, store, name in (("h", mid[1], lambda a, c: a > c, self.swings_h, "eqh"),
                                               ("l", mid[2], lambda a, c: a < c, self.swings_l, "eql")):
            if all(better(val, w[1] if side == "h" else w[2]) for w in others):
                while store and store[0][0] < horizon:
                    store.popleft()
                match = [p for _, p in store if abs(p - val) <= tol]
                store.append((mid[0], val))
                if match and name not in self.taken:
                    self.levels[name] = max(match + [val]) if side == "h" else min(match + [val])

    # ------------------------------------------------ main step
    def step(self, b):
        """Feed one closed bar. Returns the setups completed on this bar (usually none)."""
        self.i += 1
        d = session_date(b.ny, self.cfg)
        if d != self.day:
            self._new_day(d)
        self.recent.append(b)
        found = []
        found += self._sweeps(b)
        self._session_levels(b)
        self._equal_levels(b)
        self.day_hi = b.h if self.day_hi is None else max(self.day_hi, b.h)
        self.day_lo = b.l if self.day_lo is None else min(self.day_lo, b.l)
        self.bodies.append(b.body)
        return found

    def _avg_body(self):
        return sum(self.bodies) / len(self.bodies) if len(self.bodies) == self.bodies.maxlen else None

    def _sweeps(self, b):
        st, out = self.st, []
        pierce = st["sweep_min_pierce"]
        for name, lvl in list(self.levels.items()):
            if name in self.taken:
                continue
            high = name in HIGH_LEVELS
            if (high and b.h >= lvl + pierce) or (not high and b.l <= lvl - pierce):
                self.taken.add(name)
                self.pending[name] = {"start": self.i, "extreme": b.h if high else b.l, "reclaim": None, "level": lvl}
        for name, p in list(self.pending.items()):
            high = name in HIGH_LEVELS
            p["extreme"] = max(p["extreme"], b.h) if high else min(p["extreme"], b.l)
            if p["reclaim"] is None:
                if (high and b.c < p["level"]) or (not high and b.c > p["level"]):
                    p["reclaim"] = self.i
                elif self.i - p["start"] >= st["reclaim_max_bars"]:
                    del self.pending[name]
                    continue
            if p["reclaim"] is not None:
                avg = self._avg_body()
                disp = (avg is not None and b.body >= st["displacement_body_mult"] * avg
                        and ((high and b.c < b.o) or (not high and b.c > b.o)))
                if disp:
                    out.append(self._setup(name, p, b))
                    del self.pending[name]
                elif self.i - p["reclaim"] >= st["displacement_max_bars"]:
                    del self.pending[name]
        return out

    def _setup(self, name, p, b):
        high = name in HIGH_LEVELS
        side = "short" if high else "long"
        entry, etype, fvg = b.c, "market", None
        if self.st["use_fvg_entry"] and len(self.recent) == 3:
            a = self.recent[0]
            if high and b.h < a.l:
                fvg = (b.h, a.l)
            elif not high and b.l > a.h:
                fvg = (a.h, b.l)
            if fvg:
                entry, etype = (fvg[0] + fvg[1]) / 2, "limit_fvg"
        tgt = nearest_liquidity(self.levels, self.taken, entry, side)
        return {
            "id": "%s_sweep_%s" % (name, side), "side": side, "level": name, "level_price": round(p["level"], 2),
            "sweep_extreme": round(p["extreme"], 2), "entry": round(entry, 2), "entry_type": etype,
            "fvg": [round(x, 2) for x in fvg] if fvg else None,
            "target_liquidity": tgt, "bar_index": self.i,
            "time_utc": b.t.isoformat(), "time_ny": b.ny.isoformat(), "weekday": b.ny.strftime("%a"),
            "killzone": killzone_at(b.minute, self.cfg),
        }


def nearest_liquidity(levels, taken, price, side):
    """The closest untaken level in the trade's direction: buy-side liquidity above for a long, sell-side below for a short."""
    pool = HIGH_LEVELS if side == "long" else LOW_LEVELS
    best = None
    for n in pool:
        if n in levels and n not in taken:
            v = levels[n]
            if (side == "long" and v > price) or (side == "short" and v < price):
                if best is None or abs(v - price) < abs(best[1] - price):
                    best = (n, v)
    return {"name": best[0], "price": round(best[1], 2)} if best else None


def scan(bars, cfg):
    """Run the detector over all bars. Returns (all setups, detector) so callers can read the live level map."""
    det = Detector(cfg)
    found = []
    for b in bars:
        found += det.step(b)
    return found, det


def live(bars, cfg):
    """Setups completed in the last scan_bars bars, plus the level map and the closer-side read."""
    found, det = scan(bars, cfg)
    last = det.i
    recent = [s for s in found if last - s["bar_index"] < cfg["strategy"]["scan_bars"]]
    price = bars[-1].c
    up = nearest_liquidity(det.levels, det.taken, price, "long")
    dn = nearest_liquidity(det.levels, det.taken, price, "short")
    closer = None
    if up and dn:
        closer = "above" if up["price"] - price < price - dn["price"] else "below"
    lo, hi = cfg["strategy"]["closer_side_first_pct"]
    return {
        "last_bar_local_utc": bars[-1].t.isoformat(), "price": price,
        "levels": {k: round(v, 2) for k, v in det.levels.items()}, "taken": sorted(det.taken),
        "nearest_above": up, "nearest_below": dn, "closer_side": closer,
        "closer_side_note": ("Closer liquidity is %s; in your data the closer side is taken first about %d-%d%% of the time."
                             % (closer, lo, hi)) if closer else None,
        "direction_note": "After a sweep, direction is about %d/%d in your data: no setup has a proven edge on its own."
                          % (cfg["strategy"]["sweep_direction_odds_pct"], 100 - cfg["strategy"]["sweep_direction_odds_pct"]),
        "setups": recent,
    }
