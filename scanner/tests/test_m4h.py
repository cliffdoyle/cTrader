"""Tests for the 4H manipulation candle engine (engine/m4h.py) on hand-built M30 candles and their mirror image.
Run: python -m unittest discover -s tests -v"""
import unittest
from datetime import datetime, timedelta, timezone

from ictscan.engine import m4h
from ictscan.engine.core import Bar

T0 = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)  # Monday, on a 4-hour boundary
STEP = timedelta(minutes=30)
CFG = {"spread": 0.0, "cost": 0.0}
# P: bullish 100 → 104 (wicks 99 / 105). C: up to 106, then closes 99.5 — below P's body (100) but above its wick (99).
P = [(100, 100.5, 99, 100.4), (100.4, 101.2, 100.3, 101), (101, 101.8, 100.9, 101.6), (101.6, 102.4, 101.5, 102.3),
     (102.3, 103.2, 102.2, 103), (103, 104, 102.9, 103.8), (103.8, 105, 103.7, 104.2), (104.2, 104.3, 103.9, 104)]
C = [(104, 105, 103.5, 104.5), (104.5, 106, 104.2, 105.8), (105.8, 105.9, 104, 104.2), (104.2, 104.3, 102, 102.2),
     (102.2, 102.4, 100.5, 100.6), (100.6, 100.8, 99.6, 99.8), (99.8, 100, 99.5, 99.7), (99.7, 99.8, 99.5, 99.5)]
# After C: back up through 50 % (fills E2 and the FVG entry), then down through both 2R targets; OTE never fills.
AFTER = [(99.5, 101, 99.4, 100.8), (100.8, 103, 100.7, 102.9), (102.9, 102.95, 99, 99.2), (99.2, 99.3, 96, 96.1),
         (96.1, 96.2, 94, 94.5)]
# After C: a 30m candle above the previous high closing below its body, inside C's range = duplicate.
DUP = [(99.5, 100, 99.3, 99.9), (99.9, 101, 99.8, 100.9), (100.9, 101.5, 99.6, 99.7), (99.7, 99.8, 98, 98.2)]


def series(rows, mirror=False):
    m30 = []
    for n, (o, h, lo, c) in enumerate(rows):
        if mirror:
            o, h, lo, c = -o, -lo, -h, -c
        m30.append(Bar(T0 + n * STEP, T0 + (n + 1) * STEP, o, h, lo, c))
    return {"d1": m4h.aggregate(m30, 86400), "h4": m4h.aggregate(m30, 14400), "m30": m30}


def the_setup(rows, mirror=False, **params):
    c_end = T0 + 16 * STEP
    out = [s for s in m4h.run(series(rows, mirror), CFG, params) if s["conf_t"] == c_end]
    return out


def plan(s, pid):
    return next((p for p in s["plans"] if p["id"] == pid), None)


def trade(pl, tid):
    return next(t for t in pl["trades"] if t["target"] == tid)


class Detection(unittest.TestCase):
    def test_close_below_body_is_a_setup(self):
        (s,) = the_setup(P + C + AFTER)
        self.assertEqual(s["dir"], "sell")
        self.assertEqual(s["C"], (104, 106, 99.5, 99.5))

    def test_close_inside_body_is_not(self):
        c = C[:-1] + [(99.7, 99.8, 99.5, 100.2)]  # closes above P's body low (100)
        self.assertEqual(the_setup(P + c + AFTER), [])

    def test_needs_the_previous_high_taken(self):
        c = [(o, min(h, 104.9), lo, cl) for o, h, lo, cl in C]  # never above P's high 105
        self.assertEqual(the_setup(P + c + AFTER), [])


class Entries(unittest.TestCase):
    def setUp(self):
        (self.s,) = the_setup(P + C + AFTER)

    def test_levels(self):
        self.assertAlmostEqual(plan(self.s, "E1")["entry"], 99.5)
        self.assertAlmostEqual(plan(self.s, "E2")["entry"], 102.75)
        self.assertAlmostEqual(plan(self.s, "E5")["entry"], 99.5 + 0.705 * 6.5)
        self.assertAlmostEqual(plan(self.s, "E3")["entry"], 102.4)  # the imbalance nearest to 50 %
        for pid in ("E1", "E2", "E3", "E5"):
            self.assertAlmostEqual(plan(self.s, pid)["stop"], 106)
        self.assertIsNone(plan(self.s, "E4"))

    def test_outcomes(self):
        e2 = trade(plan(self.s, "E2"), "T2R")
        self.assertEqual((e2["outcome"], e2["R"]), ("target", 2.0))
        self.assertEqual(trade(plan(self.s, "E3"), "T2R")["outcome"], "target")
        self.assertEqual(trade(plan(self.s, "E5"), "T2R")["outcome"], "missed")  # target traded before the fill
        self.assertEqual(trade(plan(self.s, "E1"), "T2R")["outcome"], "open")
        self.assertEqual(trade(plan(self.s, "E2"), "TLIQ")["outcome"], "no_target")

    def test_costs_are_charged_in_r(self):
        (s,) = [x for x in m4h.run(series(P + C + AFTER), {"spread": 0.0, "cost": 0.325}) if x["conf_t"] == T0 + 16 * STEP]
        self.assertAlmostEqual(trade(plan(s, "E2"), "T2R")["R"], 1.9)

    def test_stop_and_target_in_one_bar_is_a_loss(self):
        wide = AFTER[:2] + [(102.9, 106.5, 96, 99)]
        (s,) = the_setup(P + C + wide)
        self.assertEqual(trade(plan(s, "E2"), "T2R")["outcome"], "stopped")

    def test_limit_expires(self):
        flat = [(99.5, 99.9, 99.3, 99.6)] * 30
        (s,) = the_setup(P + C + flat, expiry_h4=1)
        self.assertEqual(trade(plan(s, "E2"), "T2R")["outcome"], "expired")

    def test_duplicate(self):
        (s,) = the_setup(P + C + DUP)
        e4 = plan(s, "E4")
        self.assertAlmostEqual(e4["entry"], 99.7)
        self.assertAlmostEqual(e4["stop"], 101.5)


class Mirror(unittest.TestCase):
    def test_buy_is_the_mirror_image(self):
        (s,) = the_setup(P + C + AFTER, mirror=True)
        self.assertEqual(s["dir"], "buy")
        self.assertEqual(s["C"], (-104, -99.5, -106, -99.5))
        e2 = plan(s, "E2")
        self.assertAlmostEqual(e2["entry"], -102.75)
        self.assertAlmostEqual(e2["stop"], -106)
        self.assertEqual(trade(e2, "T2R")["outcome"], "target")
        self.assertEqual(plan(s, "E3")["fvg"]["high"], -102.4)


class LiveRecord(unittest.TestCase):
    def test_record_stays_armed_while_a_trade_is_open(self):
        from ictscan.engine import m4h_live
        (s,) = the_setup(P + C + AFTER)
        rec = m4h_live.to_record("EURUSD", s)
        self.assertEqual(rec["id"], f"EURUSD-M4H-bear-{int((T0 + 8 * STEP).timestamp())}")
        self.assertEqual(rec["stage"], "armed")  # E1 at 2R is still open
        self.assertEqual(rec["timeline"][0]["stage"], "armed")
        self.assertEqual([p["id"] for p in rec["m4h"]["plans"]], ["E1", "E2", "E3", "E5"])


class Context(unittest.TestCase):
    def test_session_and_day(self):
        self.assertEqual(m4h.session_of(datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)), "London")  # 03:00 NY
        self.assertEqual(m4h.session_of(datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)), "NY AM")  # 10:00 NY
        self.assertEqual(m4h.session_of(datetime(2026, 11, 2, 1, 0, tzinfo=timezone.utc)), "Asia")    # 20:00 NY
        start, end = m4h.day_bounds(datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc))
        self.assertEqual(start, datetime(2026, 11, 1, 22, 0, tzinfo=timezone.utc))  # 17:00 NY (EST)
        self.assertEqual(end - start, timedelta(days=1))

    def test_tom_candles_open_at_18_ny(self):
        t = datetime(2026, 11, 2, 21, 0, tzinfo=timezone.utc)  # 16:00 NY (EST)
        m30 = [Bar(t + n * STEP, t + (n + 1) * STEP, 1, 2, 0, 1) for n in range(20)]
        starts = [b.t.astimezone(m4h.NY).hour for b in m4h.aggregate_ny(m30)]
        self.assertEqual(starts, [14, 18, 22])


if __name__ == "__main__":
    unittest.main()
