"""Tests for the MMXM detector (engine/mmxm.py) on a synthetic textbook market-maker buy model and its mirror image.
Run: python -m unittest discover -s tests -v"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from ictscan.engine import mmxm
from ictscan.engine.core import Bar

T0 = datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)
STEP = timedelta(minutes=5)
CFG = {**mmxm.MMXM_DEFAULTS, "intraday": {**mmxm.MMXM_DEFAULTS["intraday"], "curve_atr": 0.5}, "min_stop_pips": 0}


def candles(spec):
    """spec: list of (open, close) or (open, close, high, low)."""
    out = []
    for n, s in enumerate(spec):
        o, c = s[0], s[1]
        h = s[2] if len(s) > 2 else max(o, c) + 0.05
        lo = s[3] if len(s) > 3 else min(o, c) - 0.05
        out.append(Bar(T0 + n * STEP, T0 + (n + 1) * STEP, o, h, lo, c))
    return out


def walk(a, b, step):
    pts, x = [], a
    while (step < 0 and x + step > b) or (step > 0 and x + step < b):
        pts.append((x, x + step))
        x += step
    pts.append((x, b))
    return pts


def textbook(floor=None):
    """Consolidation ~110 → curve down (bearish FVG, retrace into it = H ≈ 101) → low 91.9 in the array → bullish
    displacement through H (FVG 92.2–101.45) → retrace into the FVG → rally to 111. `floor` keeps lows after H above it."""
    spec = [(110.0, 110.05) if n % 2 else (110.05, 110.0) for n in range(40)]
    spec += walk(110.0, 104.0, -0.3)
    spec += [(104.0, 103.9, 104.05, 103.85), (103.9, 100.0, 103.95, 99.95), (100.0, 99.8, 100.05, 99.75)]
    spec += [(99.8, 100.1), (100.1, 100.4), (100.4, 100.7), (100.7, 100.95, 101.05, 100.65)]
    after_h = walk(100.95, 92.0, -0.3) + [(92.0, 91.95, 92.05, 91.9)]
    if floor is not None:
        after_h = [(max(o, floor), max(c, floor)) for o, c, *_ in after_h]
    spec += after_h
    base = spec[-1][1]
    spec += [(base, base + 0.2, base + 0.25 if floor is None else base + 0.25, min(base, 92.0) if floor is None else base - 0.05)]
    spec += [(spec[-1][1], 101.5, 101.55, spec[-1][1] - 0.02), (101.5, 101.8, 101.85, 101.45)]
    spec += [(101.8, 101.6, 101.82, 101.55), (101.6, 101.4, 101.62, 101.3), (101.4, 101.6, 101.65, 101.35)]
    spec += walk(101.6, 111.0, 0.3)
    spec += [(111.0, 110.9)] * 30
    return candles(spec)


def aggregate(m5, minutes):
    out, cur = [], None
    for b in m5:
        start = b.t - timedelta(minutes=(b.t.hour * 60 + b.t.minute) % minutes)
        if cur is None or cur[0] != start:
            if cur:
                out.append(Bar(cur[0], cur[0] + timedelta(minutes=minutes), cur[1], cur[2], cur[3], cur[4]))
            cur = [start, b.o, b.h, b.l, b.c]
        else:
            cur[2], cur[3], cur[4] = max(cur[2], b.h), min(cur[3], b.l), b.c
    out.append(Bar(cur[0], cur[0] + timedelta(minutes=minutes), cur[1], cur[2], cur[3], cur[4]))
    return out


def tfs(m5):
    return {"m5": m5, "m15": aggregate(m5, 15), "h1": aggregate(m5, 60), "h4": aggregate(m5, 240), "d1": aggregate(m5, 1440)}


def fake_arrays(self):
    # the bullish array the reversal low trades into exists only in the un-mirrored (positive-price) world
    return [(85.0, 93.0, T0, None, "TEST FVG")] if self.ex[0].o > 0 else []


BUF = {"NAS100": 0.5, "US30": 0.5, "US500": 0.5}
COST = {"NAS100": 0.1, "US30": 0.1, "US500": 0.1}


class MMXMTests(unittest.TestCase):
    def run_model(self, data, cfg=CFG, pips=None):
        with mock.patch.object(mmxm.Series, "_arrays", fake_arrays):
            return mmxm.run(data, cfg, modes=("intraday",), buffers=BUF, costs=COST, pips=pips)

    def test_min_stop_distance_widens_a_tight_stop(self):
        data = {"NAS100": tfs(textbook()), "US30": tfs(textbook()), "US500": tfs(textbook(floor=100.5))}
        cfg = {**CFG, "min_stop_pips": {"NAS100": 15, "US30": 20, "US500": 20}}
        res, _ = self.run_model(data, cfg, pips={"NAS100": 1.0, "US30": 1.0, "US500": 1.0})
        r = [r for r in res if r["symbol"] == "NAS100" and r["dir"] == "buy"][0]
        self.assertAlmostEqual(r["stop"], 101.45 - 15)
        self.assertEqual(r["outcome"], "target")
        self.assertAlmostEqual(r["R"], (110.1 - 101.45) / 15 - 0.1 / 15)
        mirrored = {s: {tf: mmxm.mirror(b) for tf, b in d.items()} for s, d in data.items()}
        res, _ = self.run_model(mirrored, {**cfg, "min_stop_pips": 15})
        r = [r for r in res if r["symbol"] == "NAS100" and r["dir"] == "sell"][0]
        self.assertAlmostEqual(r["stop"], -101.45 + 15)

    def test_stop_for_keeps_a_wider_structural_stop(self):
        self.assertEqual(mmxm.stop_for(100.0, 70.0, 15.0), 70.0)
        self.assertEqual(mmxm.stop_for(100.0, 98.0, 15.0), 85.0)
        self.assertEqual(mmxm.stop_for(100.0, 98.0, 0.0), 98.0)

    def test_textbook_buy_model_with_smt(self):
        data = {"NAS100": tfs(textbook()), "US30": tfs(textbook()), "US500": tfs(textbook(floor=100.5))}
        res, fun = self.run_model(data)
        buys = [r for r in res if r["symbol"] == "NAS100" and r["dir"] == "buy"]
        self.assertEqual(len(buys), 1, fun[("intraday", "buy", "NAS100")])
        r = buys[0]
        self.assertAlmostEqual(r["H"], 101.05)
        self.assertAlmostEqual(r["L"], 91.9)
        self.assertAlmostEqual(r["T"], 110.1)
        self.assertEqual(r["fvg"], (92.2, 101.45))
        self.assertAlmostEqual(r["entry"], 101.45)
        self.assertAlmostEqual(r["stop"], 92.2 - 0.5)
        self.assertTrue(r["smt"])
        self.assertEqual(r["took"], {"NAS100": True, "US30": True, "US500": False})
        self.assertEqual(r["outcome"], "target")
        risk = 101.45 - 91.7
        self.assertAlmostEqual(r["R"], (110.1 - 101.45) / risk - 0.1 / risk)

    def test_no_setup_without_smt_when_required(self):
        data = {s: tfs(textbook()) for s in ("NAS100", "US30", "US500")}
        res, fun = self.run_model(data)
        self.assertEqual([r for r in res if r["dir"] == "buy"], [])
        self.assertEqual(fun[("intraday", "buy", "NAS100")]["SMT"], 0)
        self.assertGreater(fun[("intraday", "buy", "NAS100")]["curve from terminus"], 0)

    def test_one_trade_per_mss_fvg(self):
        data = {"NAS100": tfs(textbook()), "US30": tfs(textbook()), "US500": tfs(textbook(floor=100.5))}
        res, _ = self.run_model(data)
        keys = [(r["symbol"], r["dir"], r["fvg"]) for r in res]
        self.assertEqual(len(keys), len(set(keys)))

    def test_sell_model_is_the_mirror(self):
        data = {"NAS100": tfs(textbook()), "US30": tfs(textbook()), "US500": tfs(textbook(floor=100.5))}
        mirrored = {s: {tf: mmxm.mirror(b) for tf, b in d.items()} for s, d in data.items()}
        res, _ = self.run_model(mirrored)
        sells = [r for r in res if r["symbol"] == "NAS100" and r["dir"] == "sell"]
        self.assertEqual(len(sells), 1)
        r = sells[0]
        self.assertAlmostEqual(r["entry"], -101.45)
        self.assertAlmostEqual(r["stop"], -91.7)
        self.assertAlmostEqual(r["T"], -110.1)
        self.assertEqual(r["fvg"], (-101.45, -92.2))
        self.assertEqual(r["outcome"], "target")
        self.assertEqual([e[0] for e in r["events"]], ["filled"])
        self.assertAlmostEqual(r["events"][0][2], -101.45)

    def test_live_record_for_the_dashboard(self):
        from ictscan.engine import mmxm_live
        data = {"NAS100": tfs(textbook()), "US30": tfs(textbook()), "US500": tfs(textbook(floor=100.5))}
        with mock.patch.object(mmxm.Series, "_arrays", fake_arrays), mock.patch.object(mmxm, "MMXM_DEFAULTS", CFG):
            recs = [s for s in mmxm_live.live_setups(data, T0 + timedelta(days=1)) if s["symbol"] == "NAS100"]
        self.assertEqual(len(recs), 1)
        s = recs[0]
        self.assertTrue(s["id"].startswith("NAS100-MMXM-intraday-bull-"))
        self.assertEqual((s["setup"], s["dir"], s["grade"], s["entry"], s["tp1"]), ("MMXM", "bullish", "A", 101.5, 110.1))
        self.assertEqual([e["stage"] for e in s["timeline"]], ["limit", "triggered", "target"])
        self.assertEqual(s["stage"], "target")
        self.assertIsNotNone(s["result_r"])
        self.assertEqual(len(s["checklist"]), 7)
        self.assertTrue(s["checklist"][4]["ok"])  # SMT
        self.assertIn("US500 held", s["checklist"][4]["detail"])


if __name__ == "__main__":
    unittest.main()
