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
        self.assertEqual(out[2], "T|g|g2|1793012400|7|||FFFFFFFF|0|1|solid|0|a/b")


class FeedEndpointTests(unittest.TestCase):
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
            self.assertEqual(asgi(app, "GET", "/api/feed/GER40", {"X-Feed-Token": "k3y"})["status"], 404)
            self.assertEqual(asgi(app, "GET", "/api/overview", {"X-Feed-Token": "k3y"})["status"], 401)


if __name__ == "__main__":
    unittest.main()