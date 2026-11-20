import datetime as dt
import unittest

from _load import cfg
import gate

UTC = dt.timezone.utc
# Thu 2026-11-19 08:00 NY (EST, UTC-5) = 13:00 UTC = 17:00 Dubai: inside the NY killzone, before the 09:30 ban
IN_KZ = dt.datetime(2026, 11, 19, 13, 0, tzinfo=UTC)
DEAD = dt.datetime(2026, 11, 19, 17, 0, tzinfo=UTC)      # 12:00 NY: dead hours
BAN = dt.datetime(2026, 11, 19, 14, 40, tzinfo=UTC)      # 09:40 NY: NY-open ban
SAT = dt.datetime(2026, 11, 21, 13, 0, tzinfo=UTC)

GOOD = {"side": "long", "entry": 2650.0, "stop": 2645.0, "tp": 2660.0, "lot": 0.04}
FRESH = {"trades": 0, "open": 0, "realised_usd": 0.0, "loss_streak": 0}


def failed(g):
    return {c["check"] for c in g["checks"] if not c["ok"]}


class Gate(unittest.TestCase):
    def test_pass(self):
        g = gate.check(GOOD, cfg(), FRESH, IN_KZ, spread=0.20)
        self.assertEqual(g["verdict"], "PASS", g["blocked_by"])

    def test_risk_per_trade(self):
        g = gate.check(dict(GOOD, lot=0.05), cfg(), FRESH, IN_KZ, spread=0.20)  # $25 > $20
        self.assertEqual(failed(g), {"risk_per_trade"})

    def test_zero_lot_blocks(self):
        self.assertIn("risk_per_trade", failed(gate.check(dict(GOOD, lot=0.0), cfg(), FRESH, IN_KZ)))

    def test_min_rr(self):
        g = gate.check(dict(GOOD, tp=2657.0), cfg(), FRESH, IN_KZ, spread=0.20)  # 1.4R
        self.assertEqual(failed(g), {"min_rr"})

    def test_rr_exactly_min_passes(self):
        self.assertEqual(gate.check(dict(GOOD, tp=2657.5), cfg(), FRESH, IN_KZ)["verdict"], "PASS")

    def test_spread(self):
        self.assertEqual(failed(gate.check(GOOD, cfg(), FRESH, IN_KZ, spread=0.45)), {"spread"})

    def test_max_trades(self):
        self.assertEqual(failed(gate.check(GOOD, cfg(), dict(FRESH, trades=10), IN_KZ)), {"max_trades"})

    def test_daily_loss_hit(self):
        self.assertIn("daily_loss_limit", failed(gate.check(GOOD, cfg(), dict(FRESH, realised_usd=-50.0), IN_KZ)))

    def test_daily_loss_would_exceed(self):
        # -$40 today + $20 at risk = -$60 > $50 limit
        self.assertEqual(failed(gate.check(GOOD, cfg(), dict(FRESH, realised_usd=-40.0), IN_KZ)), {"daily_loss_limit"})

    def test_daily_loss_room_left(self):
        self.assertEqual(gate.check(GOOD, cfg(), dict(FRESH, realised_usd=-30.0), IN_KZ)["verdict"], "PASS")

    def test_consecutive_losses(self):
        self.assertEqual(failed(gate.check(GOOD, cfg(), dict(FRESH, loss_streak=3), IN_KZ)), {"consecutive_losses"})

    def test_sessions(self):
        for t in (DEAD, BAN, SAT):
            self.assertEqual(failed(gate.check(GOOD, cfg(), FRESH, t)), {"session"}, t)

    def test_wrong_side_stop(self):
        self.assertIn("stop_and_target", failed(gate.check(dict(GOOD, stop=2655.0), cfg(), FRESH, IN_KZ)))

    def test_short(self):
        t = {"side": "short", "entry": 2650.0, "stop": 2654.0, "tp": 2642.0, "lot": 0.05}
        self.assertEqual(gate.check(t, cfg(), FRESH, IN_KZ)["verdict"], "PASS")

    def test_filter_result_blocks(self):
        g = gate.check(GOOD, cfg(), FRESH, IN_KZ, filter_result={"pass": False, "reasons": ["spike"]})
        self.assertEqual(failed(g), {"filters"})

    def test_every_failure_listed(self):
        g = gate.check(dict(GOOD, lot=0.1, tp=2651.0), cfg(), dict(FRESH, trades=10, loss_streak=3), DEAD, spread=1.0)
        self.assertEqual(g["verdict"], "BLOCK")
        self.assertTrue({"risk_per_trade", "min_rr", "max_trades", "consecutive_losses", "session", "spread"} <= failed(g))


if __name__ == "__main__":
    unittest.main()
