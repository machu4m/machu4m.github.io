import unittest

from _load import cfg
import budget


class Sizing(unittest.TestCase):
    def test_formula(self):
        # $1000 x 2% = $20; stop 5.00 away -> 20 / (5 x 100) = 0.04
        s = budget.size(2650.00, 2645.00, cfg())
        self.assertTrue(s["ok"])
        self.assertEqual(s["lot"], 0.04)
        self.assertAlmostEqual(s["risk_usd"], 20.0)

    def test_never_rounds_up(self):
        # 20 / (3.00 x 100) = 0.0666 -> 0.06, not 0.07
        s = budget.size(2650.00, 2647.00, cfg())
        self.assertEqual(s["lot"], 0.06)
        self.assertLessEqual(s["risk_usd"], 20.0)

    def test_exact_boundary_not_lost_to_float(self):
        # 20 / (2.00 x 100) = exactly 0.10
        self.assertEqual(budget.size(2650.10, 2648.10, cfg())["lot"], 0.10)

    def test_short_side(self):
        self.assertEqual(budget.size(2650.00, 2654.00, cfg())["lot"], 0.05)

    def test_refuses_stop_too_wide(self):
        # 20 / (25 x 100) = 0.008 < 0.01 min lot
        s = budget.size(2650.00, 2625.00, cfg())
        self.assertFalse(s["ok"])
        self.assertEqual(s["lot"], 0.0)
        self.assertIn("stop too wide", s["reason"])

    def test_refuses_zero_stop(self):
        self.assertFalse(budget.size(2650.0, 2650.0, cfg())["ok"])

    def test_follows_config(self):
        s = budget.size(2650.00, 2645.00, cfg(account_size=5000, risk_per_trade_pct=1))
        self.assertEqual(s["lot"], 0.10)

    def test_default_lot_warning(self):
        # 0.02 lot on a 15.00 stop = $30 > $20
        s = budget.size(2650.00, 2635.00, cfg())
        self.assertIn("warning", s)


if __name__ == "__main__":
    unittest.main()
