"""Tests for time cycles and PXH/PXL reads (engine/cycles.py). Run: python -m unittest discover -s tests -v"""
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ictscan.engine.core import Bar
from ictscan.engine.cycles import build_cycles, cycle_of, cycle_summary, read

NY = ZoneInfo("America/New_York")


def ny(y, mo, d, hh, mm=0):
    return datetime(y, mo, d, hh, mm, tzinfo=NY).astimezone(timezone.utc)


def bar(t, o, h, lo, c, minutes=5):
    return Bar(t, t + timedelta(minutes=minutes), o, h, lo, c)


class CycleOfTests(unittest.TestCase):
    def test_90m_windows_in_summer_and_winter_time(self):
        for d in ((2026, 10, 21), (2026, 11, 4)):  # EDT and EST
            key, start, end = cycle_of("90m", ny(*d, 8, 45))
            self.assertEqual(key[1], 1)
            self.assertEqual((start, end), (ny(*d, 8, 30), ny(*d, 10, 0)))
            self.assertEqual(cycle_of("90m", ny(*d, 15, 55))[0][1], 5)
            self.assertIsNone(cycle_of("90m", ny(*d, 6, 55)))
            self.assertIsNone(cycle_of("90m", ny(*d, 16, 0)))

    def test_day_week_month_boundaries(self):
        key, start, end = cycle_of("day", ny(2026, 10, 26, 3, 0))  # Monday
        self.assertEqual((start, end), (ny(2026, 10, 23, 18, 0), ny(2026, 10, 26, 17, 0)))
        self.assertEqual(cycle_of("day", ny(2026, 10, 21, 18, 5))[0].isoformat(), "2026-10-22")
        wk = cycle_of("week", ny(2026, 10, 25, 19, 0))  # Sunday evening belongs to the new week
        self.assertEqual((wk[1], wk[2]), (ny(2026, 10, 25, 18, 0), ny(2026, 10, 30, 17, 0)))
        self.assertEqual(cycle_of("month", ny(2026, 10, 30, 12, 0))[0], (2026, 10))
        self.assertEqual(cycle_of("month", ny(2026, 10, 30, 18, 30))[0], (2026, 11))  # Friday evening → Monday 2 Nov


class BuildAndReadTests(unittest.TestCase):
    def series(self, legs):
        """legs: list of (start_ny_dt, [(o,h,l,c), ...]) 5-minute bars."""
        out = []
        for t0, rows in legs:
            for i, r in enumerate(rows):
                out.append(bar(t0 + timedelta(minutes=5 * i), *r))
        return out

    def test_cycle_high_low_eq_and_zones(self):
        bars = self.series([(ny(2026, 10, 21, 8, 30), [(10, 11, 9, 10), (10, 14, 12, 13), (13, 13, 8, 9)])])
        c = build_cycles(bars, "90m")[0]
        self.assertEqual((c["high"], c["low"], c["eq"]), (14, 8, 11))
        self.assertEqual(c["hi_zone"], (12, 14))
        self.assertEqual(c["lo_zone"], (8, 13))

    def test_reads(self):
        prev = {"high": 14, "low": 8, "eq": 11, "hi_zone": (12, 14), "lo_zone": (8, 9)}
        t = ny(2026, 10, 21, 10, 0)
        mk = lambda rows: [bar(t + timedelta(minutes=5 * i), *r) for i, r in enumerate(rows)]  # noqa: E731
        self.assertEqual(read(prev, mk([(13, 15, 12.5, 14.5), (14.5, 14.8, 12.2, 12.6)]), 12.6)["state"], "holding_above_pxh")
        self.assertEqual(read(prev, mk([(13, 15, 12.5, 14.5), (14.5, 14.6, 11, 11.5)]), 11.5)["state"], "pxh_failed")
        self.assertEqual(read(prev, mk([(9, 9.5, 7, 7.5)]), 7.5)["state"], "holding_below_pxl")
        self.assertEqual(read(prev, mk([(9, 9.5, 7, 9.8)]), 9.8)["state"], "pxl_failed")
        r = read(prev, mk([(10, 12, 9, 12)]), 12)
        self.assertEqual((r["state"], r["pos"]), ("inside", 67))
        self.assertIn("premium", r["note"])

    def test_summary_current_vs_previous_90m(self):
        prev = [(10, 11, 9, 10)] * 17 + [(10, 14, 12, 13)]   # 08:30–10:00: high 14 made by a 12–14 candle
        cur = [(13, 15, 12.5, 14.6), (14.6, 15.2, 13, 14.9)]  # 10:00–: breaks above and holds
        bars = self.series([(ny(2026, 10, 21, 8, 30), prev), (ny(2026, 10, 21, 10, 0), cur)])
        now = bars[-1].end
        s = cycle_summary({"m5": bars}, now, 14.9, lambda v: v)["90m"]
        self.assertTrue(s["live"])
        self.assertEqual((s["prev"]["high"], s["prev"]["hi_zone"]), (14, [12, 14]))
        self.assertEqual(s["read"]["state"], "holding_above_pxh")
        self.assertEqual(s["read"]["flow"], "bullish")


if __name__ == "__main__":
    unittest.main()
