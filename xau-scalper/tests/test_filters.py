import unittest

from _load import B, cfg, flat
import filters


class Spread(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(filters.check(flat(40), "long", 0.20, cfg())["pass"])

    def test_at_max_is_ok(self):
        self.assertTrue(filters.check(flat(40), "long", 0.40, cfg())["pass"])

    def test_too_wide(self):
        r = filters.check(flat(40), "long", 0.41, cfg())
        self.assertFalse(r["pass"])
        self.assertIn("spread", r["reasons"][0])

    def test_none_uses_typical(self):
        self.assertEqual(filters.check(flat(40), "long", None, cfg())["spread"], 0.20)

    def test_config_driven(self):
        self.assertFalse(filters.check(flat(40), "long", 0.30, cfg(max_spread=0.25))["pass"])


class SpikeAndFakeout(unittest.TestCase):
    def test_spike(self):
        bars = flat(40) + [B(2650, 2656, 2649, 2655)]  # 7.00 range vs ATR 1.00
        r = filters.check(bars, "long", 0.2, cfg())
        self.assertTrue(any(x.startswith("spike") for x in r["reasons"]))

    def test_wick_against_long(self):
        bars = flat(40) + [B(2650, 2652.0, 2649.8, 2650.1)]  # 1.9 upper wick of 2.2
        r = filters.check(bars, "long", 0.2, cfg())
        self.assertTrue(any("stop-hunt wick" in x for x in r["reasons"]))
        self.assertTrue(filters.check(bars, "short", 0.2, cfg())["pass"])  # same wick is fine for a short

    def test_both_sides_hunted(self):
        bars = flat(30) + flat(13) + [B(2650, 2650.9, 2649.9, 2650.5), B(2650, 2650.1, 2649.1, 2649.5)]
        r = filters.check(bars, "long", 0.2, cfg())
        self.assertTrue(any("both sides" in x for x in r["reasons"]))

    def test_not_enough_bars(self):
        self.assertFalse(filters.check(flat(5), "long", 0.2, cfg())["pass"])


if __name__ == "__main__":
    unittest.main()
