import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from ictscan.engine.core import Bar
from ictscan.engine.smt import cycle_smt, smt_summary, swing_smt

NY = ZoneInfo("America/New_York")


def path_bars(points, t0, step=timedelta(minutes=5)):
    """Bars whose closes follow straight lines through (index, price) points; high/low = close ± 0.5."""
    vals = []
    for (i0, p0), (i1, p1) in zip(points, points[1:]):
        for i in range(i0, i1):
            vals.append(p0 + (p1 - p0) * (i - i0) / (i1 - i0))
    vals.append(points[-1][1])
    return [Bar(t0 + k * step, t0 + (k + 1) * step, v, v + 0.5, v - 0.5, v) for k, v in enumerate(vals)]


class SwingSmtTests(unittest.TestCase):
    def test_bearish_smt_confirmed_by_shift(self):
        t0 = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
        hh = [(0, 100), (10, 110), (20, 100), (30, 115), (45, 95)]
        lh = [(0, 100), (10, 110), (20, 100), (30, 108), (45, 95)]
        bars = {"NAS100": {"m5": path_bars(hh, t0)}, "US500": {"m5": path_bars(hh, t0)}, "US30": {"m5": path_bars(lh, t0)}}
        now = bars["NAS100"]["m5"][-1].end
        ev = [e for e in swing_smt(bars, "m5", now, 8) if e["side"] == "bearish"]
        self.assertEqual(len(ev), 1)
        e = ev[0]
        self.assertEqual((e["leaders"], e["laggards"], e["status"]), (["NAS100", "US500"], ["US30"], "confirmed"))
        self.assertEqual(e["per"]["US30"]["label"], "LH")
        self.assertEqual(e["per"]["NAS100"]["label"], "HH")
        self.assertIn("sell the weaker US30", e["trade"])

    def test_no_smt_when_all_agree(self):
        t0 = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
        hh = [(0, 100), (10, 110), (20, 100), (30, 115), (45, 95)]
        bars = {s: {"m5": path_bars(hh, t0)} for s in ("NAS100", "US30", "US500")}
        self.assertEqual(swing_smt(bars, "m5", bars["US30"]["m5"][-1].end, 8), [])

    def test_watch_until_shift(self):
        t0 = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
        hh = [(0, 100), (10, 110), (20, 100), (30, 115), (36, 103)]
        lh = [(0, 100), (10, 110), (20, 100), (30, 108), (36, 103)]
        bars = {"NAS100": {"m5": path_bars(hh, t0)}, "US500": {"m5": path_bars(hh, t0)}, "US30": {"m5": path_bars(lh, t0)}}
        ev = swing_smt(bars, "m5", bars["US30"]["m5"][-1].end, 8)
        self.assertEqual([e["status"] for e in ev], ["watch"])


class CycleSmtTests(unittest.TestCase):
    def test_90m_cycle_smt_at_highs(self):
        t0 = datetime(2026, 10, 26, 7, 0, tzinfo=NY).astimezone(timezone.utc)  # Mon 07:00 NY
        first = [(0, 100), (9, 110), (18, 100)]                     # 07:00–08:30 window: high 110.5
        took = first + [(27, 112), (30, 108)]                       # next window takes it
        held = first + [(27, 109), (30, 106)]
        bars = {"NAS100": {"m5": path_bars(took, t0)}, "US500": {"m5": path_bars(took, t0)}, "US30": {"m5": path_bars(held, t0)}}
        now = bars["NAS100"]["m5"][-1].end
        c = cycle_smt(bars, now, kinds=("90m",))[0]
        self.assertTrue(c["live"])
        self.assertEqual(len(c["smt"]), 1)
        s = c["smt"][0]
        self.assertEqual((s["side"], s["leaders"], s["laggards"]), ("bearish", ["NAS100", "US500"], ["US30"]))
        self.assertTrue(c["per"]["NAS100"]["took_high"])
        self.assertFalse(c["per"]["US30"]["took_high"])
        summ = smt_summary({k: {"m5": v["m5"], "m15": [], "h1": [], "h4": []} for k, v in bars.items()}, now)
        self.assertEqual(summ["headline"]["side"], "bearish")


def asgi(app, method, path, headers=None, body=b""):
    """Minimal ASGI request (httpx is not installed)."""
    out = {"status": None, "headers": {}, "body": b""}
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "", "server": ("127.0.0.1", 8765),
             "client": ("127.0.0.1", 5000), "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]}
    sent = [False]

    async def receive():
        if sent[0]:
            return {"type": "http.disconnect"}
        sent[0] = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(m):
        if m["type"] == "http.response.start":
            out["status"] = m["status"]
            out["headers"] = {k.decode().lower(): v.decode() for k, v in m["headers"]}
        elif m["type"] == "http.response.body":
            out["body"] += m.get("body", b"")

    asyncio.run(app(scope, receive, send))
    return out


class DrawBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ictscan.store import Store
        from ictscan.web.app import create_app
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = create_app(Store(Path(cls.tmp.name) / "t.db"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_foreign_origin_refused(self):
        r = asgi(self.app, "OPTIONS", "/api/draw", {"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
        self.assertEqual(r["status"], 403)
        with mock.patch("ictscan.drawing.draw") as d:
            r = asgi(self.app, "POST", "/api/draw", {"Origin": "https://evil.example", "Content-Type": "application/json"},
                     json.dumps({"symbol": "NAS100", "what": "cycles"}).encode())
            self.assertEqual(r["status"], 403)
            d.assert_not_called()

    def test_dashboard_origin_preflight_and_draw(self):
        o = "https://scanner.zuristay.com"
        r = asgi(self.app, "OPTIONS", "/api/draw", {"Origin": o, "Access-Control-Request-Method": "POST",
                                                     "Access-Control-Request-Private-Network": "true"})
        self.assertEqual(r["status"], 204)
        self.assertEqual(r["headers"]["access-control-allow-origin"], o)
        self.assertEqual(r["headers"]["access-control-allow-private-network"], "true")
        with mock.patch("ictscan.drawing.draw", return_value={"ok": True, "lines": ["x"], "count": 3}) as d:
            r = asgi(self.app, "POST", "/api/draw", {"Origin": o, "Content-Type": "application/json"},
                     json.dumps({"symbol": "US30", "what": "cycles", "kinds": ["week", "90m"]}).encode())
        self.assertEqual(r["status"], 200)
        self.assertEqual(json.loads(r["body"])["count"], 3)
        self.assertEqual(d.call_args.args, ("US30", "cycles"))
        self.assertEqual(d.call_args.kwargs["kinds"], ["week", "90m"])


if __name__ == "__main__":
    unittest.main()