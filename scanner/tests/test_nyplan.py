import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from ictscan.engine.nyplan import ny_plan


def ctx(now, price, d1="bearish", h4="bearish", m5=()):
    st = {"d1": NS(state=[d1]), "h4": NS(state=[h4])}
    return NS(fmt=lambda x: round(x, 1), price=price, now=now, st=st,
              bars={"m5": list(m5), "d1": [0], "h4": [0]})


def cyc(week="bearish", day="bearish"):
    def c(flow, lbl, hi, lo):
        return {"label": lbl, "read": {"flow": flow, "note": f"{flow} read — detail"}, "prev": {"high": hi, "low": lo}}
    return {"week": c(week, "Week", 120.0, 80.0), "day": c(day, "Day", 110.0, 90.0)}


SESS = {"levels": [{"name": "PDH", "price": 110.0, "untouched": True},
                   {"name": "Asia L", "price": 95.0, "untouched": True},
                   {"name": "London H", "price": 105.0, "untouched": False}]}


class NyPlanTests(unittest.TestCase):
    MON_6AM = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)   # 06:00 NY (EDT)

    def test_bearish_lean_puts_sell_first(self):
        p = ny_plan(ctx(self.MON_6AM, 100.0), SESS, cyc())
        self.assertEqual((p["lean"], p["score"], p["session"]), ("bearish", -4, "before"))
        self.assertEqual([s["side"] for s in p["scenarios"]], ["sell", "buy"])
        self.assertEqual([s["rank"] for s in p["scenarios"]], ["primary", "alternative"])
        self.assertEqual(p["focus"], "07:00")

    def test_mixed_votes_are_two_sided(self):
        p = ny_plan(ctx(self.MON_6AM, 100.0, d1="bullish", h4="bearish"), SESS, cyc(week="bullish", day="bearish"))
        self.assertEqual(p["lean"], "two-sided")
        self.assertEqual({s["rank"] for s in p["scenarios"]}, {"either"})

    def test_liquidity_merges_equal_prices_and_skips_touched(self):
        p = ny_plan(ctx(self.MON_6AM, 100.0), SESS, cyc())
        self.assertEqual(p["above"][0], {"name": "PDH / Day PXH", "price": 110.0, "dist": 10.0})
        self.assertNotIn("London H", [x["name"] for x in p["above"]])
        self.assertEqual([x["name"] for x in p["below"]], ["Asia L", "Day PXL", "Week PXL"])

    def test_window_status_and_ranges(self):
        now = datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)   # 09:00 NY: 07:00 done, 08:30 live
        start = datetime(2026, 10, 26, 11, 0, tzinfo=timezone.utc)
        m5 = [NS(t=start + timedelta(minutes=5 * i), end=start + timedelta(minutes=5 * i + 5), h=101 + i % 3, l=99 - i % 4)
              for i in range(24)]
        p = ny_plan(ctx(now, 100.0, m5=m5), SESS, cyc())
        self.assertEqual([w["status"] for w in p["windows"]], ["done", "now", "next", "next", "next", "next"])
        self.assertEqual((p["windows"][0]["high"], p["windows"][0]["low"]), (103, 96))
        self.assertEqual((p["session"], p["focus"]), ("live", "08:30"))

    def test_friday_evening_plans_monday(self):
        fri = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)   # 17:00 NY Friday
        p = ny_plan(ctx(fri, 100.0), SESS, cyc())
        self.assertEqual((p["day"], p["session"]), ("2026-10-26", "before"))
        self.assertTrue(all(w["status"] == "next" for w in p["windows"]))


if __name__ == "__main__":
    unittest.main()