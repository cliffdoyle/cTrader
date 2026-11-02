import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ictscan.feed import resolve, to_lines
from test_smt import asgi


class FeedFormatTests(unittest.TestCase):
    def test_broker_symbols_map_to_scanner_symbols(self):
        self.assertEqual([resolve(s) for s in ("USTEC", "us30.cash", "SPX500m", "NAS100", "XAUUSD.r", "GER40", "ESTX50")],
                         ["NAS100", "US30", "US500", "NAS100", "XAUUSD", None, None])

    def test_lines(self):
        objs = [({"object_type": "rectangle", "price1": 10.04, "time1": "2026-10-26T11:00:00Z", "price2": 9.0,
                  "time2": "2026-10-26T12:30:00Z", "color": "#26F5B84A", "fill": True}, None, None),
                ({"object_type": "trend_line", "price1": 5, "time1": "2026-10-26T11:00:00Z", "price2": 5,
                  "time2": "2026-10-26T12:00:00Z", "color": "#5AA9FF", "extend": True}, 2, "dots"),
                ({"object_type": "text", "price1": 7, "time1": "2026-10-26T11:00:00Z", "color": "#FFFFFF", "text": "a|b"}, None, None)]
        out = to_lines("g", objs, lambda v: round(v, 1))
        self.assertEqual(out[0], "R|g|g0|1793012400|10.0|1793017800|9.0|26F5B84A|1|1|solid|0|")
        self.assertEqual(out[1], "L|g|g1|1793012400|5|1793016000|5|FF5AA9FF|0|2|dot|1|")
        self.assertEqual(out[2], "T|g|g2|1793012400|7|||FFFFFFFF|0|1|above|0|a/b")
        more = to_lines("s", [({"object_type": "panel", "color": "#FF5C6C", "text": "▼ BEARISH SMT"}, None, None),
                              ({"object_type": "text", "price1": 9, "time1": "2026-10-26T11:00:00Z", "color": "#FFFFFF",
                                "anchor": "middle", "right": True, "text": "D PXH 9"}, None, None)], lambda v: v)
        self.assertEqual(more[0], "P|s|s0|0|0|||FFFF5C6C|0|1|above|0|▼ BEARISH SMT")
        self.assertEqual(more[1], "T|s|s1|1793012400|9|||FFFFFFFF|0|1|middle|1|D PXH 9")


class FeedEndpointTests(unittest.TestCase):
    def test_fit_to_timeframe(self):
        from ictscan.feed import fit
        g = ["sessions", "boxes", "cycles", "levels", "smt"]
        all_kinds = ["month", "week", "day", "90m"]
        self.assertEqual(fit(0, g, None), (g, ["week", "day", "90m"]))
        self.assertEqual(fit(1, g, all_kinds), (g, all_kinds))
        self.assertEqual(fit(3, g, all_kinds), (["sessions", "cycles", "levels", "smt"], ["month", "week", "day"]))
        self.assertEqual(fit(4, g, all_kinds), (["cycles", "levels", "smt"], ["month", "week", "day"]))
        self.assertEqual(fit(5, g, all_kinds), (["cycles", "levels", "smt"], ["month", "week"]))

    def test_session_boxes_for_several_days(self):
        from datetime import datetime, timedelta, timezone
        from types import SimpleNamespace
        from zoneinfo import ZoneInfo
        from ictscan.drawing import plan_sessions
        from ictscan.engine.core import Bar
        t0 = datetime(2026, 10, 28, 20, 0, tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
        bars = [Bar(t0 + i * timedelta(minutes=5), t0 + (i + 1) * timedelta(minutes=5), 100 + i % 7, 101 + i % 7, 99 + i % 7,
                    100 + i % 5) for i in range(12 * 24 * 5)]
        now = datetime(2026, 11, 2, 12, 0, tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
        ctx = SimpleNamespace(bars={"m5": [b for b in bars if b.end <= now]}, now=now, atr={"h1": [10.0]}, fmt=lambda v: round(v, 1))
        objs, _ = plan_sessions(ctx, "kz", ("Asia", "London", "NY AM"), 3)
        texts = [o for o, _, _ in objs if o["object_type"] == "text"]
        self.assertEqual(sum(t["text"].startswith("ASIA") for t in texts), 3)    # Mon, Fri, Thu (weekend skipped)
        self.assertEqual(sum(t["text"].startswith("NEW YORK") for t in texts), 3)
        latest = [t for t in texts if t["color"].startswith("#4F")]               # full-colour labels = latest day only
        self.assertEqual(len(latest), 2)
        self.assertTrue(all(t.get("bold") for t in texts if t["anchor"] == "above"))
        self.assertEqual({t["row"] for t in texts if "LONDON" in t["text"]}, {1})
        ticks = [o for o, w, _ in objs if o["object_type"] == "trend_line" and w == 3]
        self.assertEqual(len(ticks), 6)                                            # open/close ticks: latest day only
        compact, _ = plan_sessions(ctx, "kz", ("Asia", "London", "NY AM"), 3, compact=True)
        self.assertFalse([o for o, w, _ in compact if o["object_type"] == "trend_line" and w == 3])

    def test_key_required_when_dashboard_has_password(self):
        from ictscan.store import Store
        from ictscan.web import app as appmod
        with tempfile.TemporaryDirectory() as d, mock.patch.object(appmod, "DASH_PASSWORD", "pw"), \
                mock.patch.object(appmod, "FEED_TOKEN", "k3y"), mock.patch("ictscan.feed.build_feed", return_value="#ICTSCAN|1|NAS100\n") as bf:
            app = appmod.create_app(Store(Path(d) / "t.db"))
            self.assertEqual(asgi(app, "GET", "/api/feed/USTEC")["status"], 401)
            r = asgi(app, "GET", "/api/feed/USTEC", {"X-Feed-Token": "k3y"})
            self.assertEqual((r["status"], r["body"]), (200, b"#ICTSCAN|1|NAS100\n"))
            self.assertEqual(bf.call_args.args[1], "NAS100")
            self.assertEqual(bf.call_args.args[5], "all")
            asgi(app, "GET", "/api/feed/USTEC?smt=nq-es", {"X-Feed-Token": "k3y"})
            self.assertEqual(bf.call_args.args[5], "nq-es")
            self.assertEqual(bf.call_args.args[6:], (0, ("Asia", "London"), 1))   # first add-on versions: unchanged
            asgi(app, "GET", "/api/feed/USTEC?tier=9&sn=asia,ny,bogus&days=0", {"X-Feed-Token": "k3y"})
            self.assertEqual(bf.call_args.args[6:], (5, ("Asia", "NY AM"), 1))
            self.assertEqual(asgi(app, "GET", "/api/feed/GER40", {"X-Feed-Token": "k3y"})["status"], 404)
            self.assertEqual(asgi(app, "GET", "/api/overview", {"X-Feed-Token": "k3y"})["status"], 401)


if __name__ == "__main__":
    unittest.main()