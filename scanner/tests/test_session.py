"""Tests for today's session key levels and the daily lean (engine/session.py). Run: python -m unittest discover -s tests -v"""
import unittest
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from ictscan.engine.core import Bar
from ictscan.engine.session import daily_context, key_levels, trading_day

NY = ZoneInfo("America/New_York")


def ny(y, mo, d, hh, mm=0):
    return datetime(y, mo, d, hh, mm, tzinfo=NY).astimezone(timezone.utc)


def m5_series(t0, t1, price_fn):
    out, t = [], t0
    while t < t1:
        p = price_fn(t)
        out.append(Bar(t, t + timedelta(minutes=5), p, p + 1, p - 1, p))
        t += timedelta(minutes=5)
    return out


class TradingDayTests(unittest.TestCase):
    def test_day_rolls_at_17_ny_and_weekend_goes_to_monday(self):
        self.assertEqual(trading_day(ny(2026, 10, 21, 16, 59)).isoformat(), "2026-10-21")
        self.assertEqual(trading_day(ny(2026, 10, 21, 18, 0)).isoformat(), "2026-10-22")
        self.assertEqual(trading_day(ny(2026, 10, 23, 18, 0)).isoformat(), "2026-10-26")  # Friday evening -> Monday


class KeyLevelTests(unittest.TestCase):
    def build(self, now, y=2026, mo=10, d=21):
        start = datetime.combine(datetime(y, mo, d).date() - timedelta(days=1), time(18), NY).astimezone(timezone.utc)

        def price(t):
            h = t.astimezone(NY)
            if h.hour >= 20 or h.hour < 0:          # Asia (evening before): 100–110
                return 100 + (h.minute % 10)
            if 2 <= h.hour < 5:                    # London: 120–130
                return 120 + (h.minute % 10)
            if h.hour == 9 and h.minute >= 30 or 10 <= h.hour < 12:
                return 140                          # New York AM
            return 125                              # between sessions: above the London low, below the London high
        m5 = m5_series(start, now, price)
        d1 = [Bar(ny(y, mo, d - 1, 17) - timedelta(days=1), ny(y, mo, d - 1, 17), 100, 150, 90, 120)]
        w1 = [Bar(ny(y, mo, d - 1, 17) - timedelta(days=7), ny(y, mo, d - 1, 17) - timedelta(days=2), 100, 200, 50, 120)]
        return key_levels(m5, d1, w1, now)

    def names(self, kl):
        return {lv["name"]: lv for lv in kl["levels"]}

    def test_session_ranges_and_status_after_ny_open(self):
        kl = self.build(ny(2026, 10, 21, 10, 0))
        lv = self.names(kl)
        self.assertEqual((lv["Asia H"]["price"], lv["Asia L"]["price"]), (106, 99))
        self.assertEqual((lv["London H"]["price"], lv["London L"]["price"]), (126, 119))
        self.assertFalse(lv["Asia H"]["untouched"])        # London traded above 106 after Asia closed
        self.assertEqual(lv["Asia H"]["taken_t"], ny(2026, 10, 21, 0, 0))  # price 125 right after Asia closed
        self.assertFalse(lv["London H"]["untouched"])      # NY at 140 took London high
        self.assertEqual(lv["London H"]["taken_t"], ny(2026, 10, 21, 9, 30))
        self.assertTrue(lv["London L"]["untouched"])
        self.assertTrue(lv["PDH"]["untouched"] and lv["PDL"]["untouched"])
        self.assertIn("forming", lv["NY AM H"]["window"])
        self.assertEqual(kl["opens"]["midnight"], 125)
        self.assertEqual(kl["opens"]["ny_open"], 140)

    def test_windows_not_started_are_absent(self):
        kl = self.build(ny(2026, 10, 21, 1, 0))
        lv = self.names(kl)
        self.assertIn("Asia H", lv)
        self.assertNotIn("London H", lv)
        self.assertNotIn("NY AM H", lv)
        self.assertIsNone(kl["opens"]["ny_open"])

    def test_asia_window_respects_daylight_saving(self):
        # 3 Nov 2026 is after the US switch back to EST, so 20:00 NY is 01:00 UTC (it was 00:00 UTC in October)
        kl = self.build(ny(2026, 11, 3, 10, 0), mo=11, d=3)
        formed = self.names(kl)["Asia H"]["formed_t"]
        self.assertGreaterEqual(formed, ny(2026, 11, 2, 20, 0))
        self.assertLess(formed, ny(2026, 11, 3, 0, 0))


class ContextTests(unittest.TestCase):
    def ctx(self, states, price, now):
        bars = {tf: [None] for tf in ("d1", "h4", "h1")}
        st = {tf: SimpleNamespace(state=[s], prot=[(1.0, 0)]) for tf, s in zip(("d1", "h4", "h1"), states)}
        return SimpleNamespace(price=price, now=now, bars=bars, st=st, fmt=lambda v: None if v is None else round(v, 1))

    def test_lean_needs_two_vote_margin_and_draw_uses_random_walk_odds(self):
        now = ny(2026, 10, 21, 10, 0)
        kl = {"levels": [{"name": "PDH", "side": "high", "price": 110.0, "untouched": True},
                         {"name": "PDL", "side": "low", "price": 70.0, "untouched": True}],
              "opens": {"midnight": 95.0}}
        c = daily_context(self.ctx(("bullish", "bullish", "bearish"), 100.0, now), kl)
        self.assertEqual((c["bulls"], c["bears"], c["lean"]), (3, 1, "bullish"))  # D1, H4, midnight vs H1
        self.assertEqual(c["draw"]["above"], "PDH")
        self.assertEqual(c["draw"]["p_above_first"], 75)  # 30 below vs 10 above
        c2 = daily_context(self.ctx(("bullish", "bearish", "bearish"), 100.0, now), kl)
        self.assertEqual(c2["lean"], "mixed")


if __name__ == "__main__":
    unittest.main()
