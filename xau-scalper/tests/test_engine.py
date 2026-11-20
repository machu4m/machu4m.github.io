"""End to end on synthetic M1 gold: clock, setup engine, Time Machine, journal review, CLI."""

import datetime as dt
import json
import os
import tempfile
import unittest

from _load import cfg, write_mt5
import clock
import journal
import setups
import timemachine
import xau
from core import load_m1_csv

UTC = dt.timezone.utc


def day_rows(win, end_minute=None):
    """One session day in NY time from 18:15 to 12:00 next day. Asia low 2649.0; at 07:30 a sweep to 2648.5,
    close back inside, 07:32 displacement up with an FVG, 07:33 retrace fills the FVG, then win or loss."""
    rows = []
    for m in range(18 * 60 + 15, 24 * 60 + 12 * 60):
        nm = m % (24 * 60)
        if 20 * 60 <= m < 24 * 60:  # Asia
            rows.append((2650.0, 2651.0, 2649.0, 2650.2) if m % 2 else (2650.2, 2650.6, 2649.6, 2650.0))
        elif 2 * 60 <= nm < 5 * 60 and m >= 24 * 60:  # London
            rows.append((2650.0, 2650.4, 2649.7, 2650.1))
        elif m < 24 * 60 + 7 * 60 + 30:
            rows.append((2650.0, 2650.2, 2649.8, 2650.0))
        elif nm == 7 * 60 + 30:
            rows.append((2649.6, 2649.5, 2648.5, 2649.4))
        elif nm == 7 * 60 + 31:
            rows.append((2649.4, 2649.5, 2649.2, 2649.4))
        elif nm == 7 * 60 + 32:
            rows.append((2649.7, 2651.6, 2649.7, 2651.5))
        elif nm == 7 * 60 + 33:
            rows.append((2651.5, 2651.5, 2649.5, 2650.0))
        else:
            k = nm - (7 * 60 + 33)
            p = min(2659.0, 2650.0 + 0.5 * k) if win else max(2645.5, 2650.0 - 0.5 * k)
            rows.append((p, p + 0.1, p - 0.1, p))
        if end_minute is not None and m >= end_minute:
            break
    return [(o, max(o, h, c), min(o, l, c), c) for o, h, l, c in rows]


def history(path, days, end_minute=None):
    rows = []
    for d in range(days):
        t0 = dt.datetime(2026, 9, 1, 18, 15) + dt.timedelta(days=d)
        part = day_rows(win=(d % 3 != 2), end_minute=end_minute if d == days - 1 else None)
        rows += [(t0 + dt.timedelta(minutes=k),) + r for k, r in enumerate(part)]
    write_mt5(path, rows, None)
    return rows


def make_cfg():
    c = cfg()
    c["data"] = dict(c["data"], csv_server_tz="America/New_York")
    return c


class Clock(unittest.TestCase):
    def test_winter_dubai_times(self):
        d = clock.status(dt.datetime(2026, 11, 19, 13, 0, tzinfo=UTC), cfg())  # 08:00 NY
        self.assertEqual(d["killzone"], "ny")
        self.assertTrue(d["allowed"])
        w = {x["killzone"]: (x["start_local"], x["end_local"]) for x in d["killzones_local_today"]}
        self.assertEqual(w["ny"], ("16:00", "19:00"))
        self.assertEqual(w["london"], ("11:00", "14:00"))

    def test_summer_shifts_one_hour(self):
        d = clock.status(dt.datetime(2026, 7, 16, 12, 0, tzinfo=UTC), cfg())  # 08:00 NY (EDT)
        w = {x["killzone"]: x["start_local"] for x in d["killzones_local_today"]}
        self.assertEqual(w["ny"], "15:00")

    def test_dead_and_next(self):
        d = clock.status(dt.datetime(2026, 11, 19, 17, 0, tzinfo=UTC), cfg())  # 12:00 NY
        self.assertFalse(d["allowed"])
        self.assertTrue(d["next_allowed"]["local"].endswith("05:00"))  # Asia 20:00 NY = 05:00 Dubai
        self.assertEqual(d["next_allowed"]["killzone"], "asia")

    def test_weekend(self):
        d = clock.status(dt.datetime(2026, 11, 20, 21, 0, tzinfo=UTC), cfg())  # Fri 16:00 NY
        self.assertTrue(any("weekend" in r for r in d["blocked_by"]))


class Engine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.csv = os.path.join(cls.tmp, "hist.csv")
        history(cls.csv, 45)
        cls.cfg = make_cfg()
        cls.bars = load_m1_csv(cls.csv, cls.cfg)

    def test_detects_long_sweep_each_day(self):
        found, _ = setups.scan(self.bars, self.cfg)
        asia = [s for s in found if s["id"] == "asia_low_sweep_long"]
        self.assertEqual(len(asia), 45)
        s = asia[0]
        self.assertEqual(s["killzone"], "ny")
        self.assertEqual(s["entry_type"], "limit_fvg")
        self.assertAlmostEqual(s["entry"], 2649.65, places=2)
        self.assertEqual(s["sweep_extreme"], 2648.5)

    def test_no_lookahead_levels(self):
        found, _ = setups.scan(self.bars, self.cfg)
        for s in found:
            self.assertGreaterEqual(s["time_ny"][11:16], "00:00")

    def test_time_machine_counts(self):
        s = {"id": "asia_low_sweep_long", "side": "long", "killzone": "ny", "bar_index": len(self.bars)}
        doc = timemachine.run(self.bars, s, self.cfg, target_r=2.0)
        sm = doc["summary"]
        self.assertEqual(sm["filled"], 45)
        self.assertAlmostEqual(sm["p_target"], 30 / 45, places=3)
        self.assertAlmostEqual(sm["p_stop"], 15 / 45, places=3)
        self.assertIsNotNone(doc["baseline"])
        self.assertIn("break even", doc["verdict"])

    def test_few_samples_says_unproven(self):
        s = {"id": "asia_low_sweep_long", "side": "long", "killzone": "ny", "bar_index": len(self.bars)}
        doc = timemachine.run(self.bars[:1065 * 5], s, self.cfg, target_r=2.0)
        self.assertIn("unproven", doc["verdict"])

    def test_stats(self):
        st = timemachine.stats(self.bars, self.cfg)
        self.assertEqual(st["days"], 45)
        self.assertIn("ny", st["killzone_avg_range_usd"])


class JournalReview(unittest.TestCase):
    def test_flags_repeated_mistakes_and_maps_rules(self):
        tdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(tdir, "docs"))
        with open(os.path.join(tdir, "docs", "Trade_History_Diagnosis.md"), "w") as f:
            f.write("# Rules\n1. Only trade inside the killzone.\n2. No revenge trades after a loss.\n7. No trades at the NY open.\n")
        c = cfg()
        # 12:00 Dubai = 03:00 NY: London killzone. 21:00 Dubai = 12:00 NY: dead hours.
        journal.add(tdir, c, "long", 2650, 2645, 2660, 0.04, "2026-11-17 12:00", exit=2645, close_time="2026-11-17 12:20")
        journal.add(tdir, c, "long", 2650, 2645, 2660, 0.04, "2026-11-17 12:25", exit=2645, close_time="2026-11-17 12:40")
        journal.add(tdir, c, "short", 2650, 2655, 2640, 0.04, "2026-11-18 21:00", exit=2640, close_time="2026-11-18 21:30")
        journal.add(tdir, c, "short", 2650, 2655, 2640, 0.04, "2026-11-19 21:00", exit=2655, close_time="2026-11-19 21:30")
        doc = journal.review(tdir, c, dt.datetime(2026, 11, 20, 10, 0, tzinfo=UTC))
        rep = {m["mistake"]: m for m in doc["repeated"]}
        self.assertEqual(rep["outside_killzone"]["times"], 2)
        self.assertTrue(rep["outside_killzone"]["rule"].startswith("Rule 1"))
        self.assertNotIn("revenge", rep)  # only once
        self.assertEqual(doc["mistakes"]["revenge"], 1)
        self.assertEqual(doc["net_usd"], -20.0 - 20.0 + 40.0 - 20.0)
        st = journal.day_state(journal.read(tdir), c, dt.datetime(2026, 11, 17, 9, 0, tzinfo=UTC))
        self.assertEqual((st["trades"], st["realised_usd"], st["loss_streak"]), (2, -40.0, 2))


class Cli(unittest.TestCase):
    def setUp(self):
        self.tdir = tempfile.mkdtemp()
        self.cfgp = os.path.join(self.tdir, "cfg.json")
        json.dump(make_cfg(), open(self.cfgp, "w"))

    def run_cli(self, *args):
        return xau.main(["--config", self.cfgp, "--trader-dir", self.tdir] + list(args))

    def test_plan_writes_trader_files_and_never_tickets_a_block(self):
        csv = os.path.join(self.tdir, "live.csv")
        history(csv, 2, end_minute=24 * 60 + 7 * 60 + 40)
        rc = self.run_cli("plan", "--csv", csv, "--now", "2026-09-03T07:41:00-04:00", "--spread", "0.2")
        self.assertEqual(rc, 0)
        plan = json.load(open(os.path.join(self.tdir, "plan.json")))
        self.assertIn("_sweep_", plan["setup"]["id"])
        self.assertEqual(plan["gate"]["verdict"], "BLOCK")  # next liquidity is too close for min_rr
        self.assertIsNone(plan["ticket_file"])
        self.assertTrue(os.path.exists(os.path.join(self.tdir, "gate_log.jsonl")))

    def test_check_pass_writes_ticket(self):
        rc = self.run_cli("check", "--side", "long", "--entry", "2650", "--stop", "2645", "--tp", "2660",
                          "--now", "2026-11-19T08:00:00-05:00")
        self.assertEqual(rc, 0)
        files = os.listdir(os.path.join(self.tdir, "tickets"))
        self.assertEqual(len(files), 1)
        text = open(os.path.join(self.tdir, "tickets", files[0])).read()
        self.assertIn("Volume : 0.04", text)
        self.assertIn("nothing was sent", text)


if __name__ == "__main__":
    unittest.main()
