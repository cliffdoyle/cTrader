"""Tests for the read-only cTrader Open API client and the dashboard's OAuth/login routes (stdlib unittest only).
Run from the scanner folder:  python -m unittest discover -s tests -v"""
import base64
import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from ictscan import openapi
from ictscan.mcp import MCPError
from ictscan.openapi import Credentials, OpenAPIClient, OpenAPIError, convert_trendbar


def tb(minute, low, d_open=0, d_high=0, d_close=0):
    return {"utcTimestampInMinutes": minute, "low": low, "deltaOpen": d_open, "deltaHigh": d_high, "deltaClose": d_close, "volume": 7}


def creds(tmp):
    c = Credentials(env_file=tmp / "openapi.env", token_file=tmp / "tokens.json")
    c.client_id, c.client_secret = "id", "secret"
    return c


def fake_urlopen(reply):
    fake = mock.MagicMock()
    fake.__enter__.return_value.read.return_value = json.dumps(reply).encode()
    return mock.patch.object(openapi.urllib.request, "urlopen", return_value=fake)


class ConversionTests(unittest.TestCase):
    def test_trendbar_prices_are_low_plus_deltas_over_100000(self):
        bar = convert_trendbar(tb(29_000_000, 412_345_600, d_open=100_000, d_high=250_000, d_close=50_000), 2)
        self.assertEqual(bar["timestamp"], datetime.fromtimestamp(29_000_000 * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual((bar["open"], bar["high"], bar["low"], bar["close"]), (4124.46, 4125.96, 4123.46, 4123.96))

    def test_string_encoded_int64_fields_are_accepted(self):
        bar = convert_trendbar({"utcTimestampInMinutes": "60", "low": "112097", "deltaHigh": "15", "volume": "3"}, 5)
        self.assertEqual((bar["low"], bar["high"], bar["volume"]), (1.12097, 1.12112, 3))


class FakeClient(OpenAPIClient):
    """Replaces the network: answers trendbar requests from a list of minute timestamps."""

    def __init__(self, minutes, chunk=None, reject_above_ms=None):
        super().__init__(creds=mock.Mock(), timeout=5)
        self.minutes, self.chunk, self.reject_above_ms = minutes, chunk, reject_above_ms
        self.symbols, self.account_id, self.requests = {"EURUSD": 1}, 99, []

    def _hist(self, payload):
        self.requests.append(payload)
        span = payload["toTimestamp"] - payload["fromTimestamp"]
        if self.reject_above_ms and span > self.reject_above_ms:
            raise OpenAPIError("INCORRECT_BOUNDARIES", "range too large")
        hits = [m for m in self.minutes if payload["fromTimestamp"] <= m * 60000 <= payload["toTimestamp"]]
        more = self.chunk is not None and len(hits) > self.chunk
        return {"trendbar": [tb(m, 112_000) for m in (hits[-self.chunk:] if more else hits)], "hasMore": more}


class PagingTests(unittest.TestCase):
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def minutes(self, n, step=5):
        m0 = int(self.start.timestamp() // 60)
        return [m0 + i * step for i in range(n)]

    def test_pages_backwards_and_returns_every_bar_once_in_order(self):
        mins = self.minutes(2500)
        c = FakeClient(mins)
        bars = c.trendbars("EURUSD", "m5", self.start, self.start + timedelta(minutes=5 * 2500))
        expect = [datetime.fromtimestamp(m * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") for m in mins]
        self.assertEqual([b["timestamp"] for b in bars], expect)
        self.assertGreaterEqual(len(c.requests), 3)
        self.assertTrue(all(r["period"] == 5 and r["symbolId"] == 1 and r["ctidTraderAccountId"] == 99 for r in c.requests))

    def test_has_more_halves_the_window_without_losing_bars(self):
        c = FakeClient(self.minutes(1500), chunk=300)
        self.assertEqual(len(c.trendbars("EURUSD", "m5", self.start, self.start + timedelta(minutes=5 * 1500))), 1500)

    def test_rejected_range_is_retried_with_a_smaller_window(self):
        c = FakeClient(self.minutes(800), reject_above_ms=200 * 5 * 60000)
        self.assertEqual(len(c.trendbars("EURUSD", "m5", self.start, self.start + timedelta(minutes=5 * 800))), 800)

    def test_empty_weekend_windows_do_not_stop_paging(self):
        later = [m + 3 * 1440 * 5 for m in self.minutes(50)]
        c = FakeClient(sorted(set(self.minutes(50) + later)))
        self.assertEqual(len(c.trendbars("EURUSD", "m5", self.start, self.start + timedelta(days=20))), 100)

    def test_unknown_symbol_lists_close_matches(self):
        c = FakeClient([])
        c.symbols = {"US500": 1, "US30": 2}
        with self.assertRaises(MCPError) as e:
            c.symbol_id("US 500")
        self.assertIn("US500", str(e.exception))


class SafetyTests(unittest.TestCase):
    def test_trading_payloads_are_refused_before_sending(self):
        c = OpenAPIClient(creds=mock.Mock())
        c.ws = mock.Mock()
        for pt in (2106, 2107, 2108, 2109, 2110, 2111):  # order/position messages
            with self.assertRaises(MCPError):
                c._send(pt, {})
        c.ws.send.assert_not_called()

    def test_only_view_scope_is_requested(self):
        with tempfile.TemporaryDirectory() as d:
            url = creds(Path(d)).authorize_url()
        self.assertIn("scope=accounts", url)
        self.assertNotIn("trading", url)

    def test_call_refuses_other_tools(self):
        with self.assertRaises(MCPError):
            OpenAPIClient(creds=mock.Mock()).call("place_market_order", {})


class CredentialTests(unittest.TestCase):
    def test_env_file_and_token_file_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "openapi.env").write_text("# comment\nCTRADER_CLIENT_ID=abc\nCTRADER_CLIENT_SECRET='s3'\nCTRADER_ACCOUNT_ID=5360302\n")
            c = Credentials(env_file=d / "openapi.env", token_file=d / "tokens.json")
            self.assertEqual((c.client_id, c.client_secret, c.account_login), ("abc", "s3", "5360302"))
            c.save("AT", "RT", 3600)
            c2 = Credentials(env_file=d / "openapi.env", token_file=d / "tokens.json")
            self.assertEqual((c2.access_token, c2.refresh_token), ("AT", "RT"))
            self.assertTrue(c2.needs_refresh())  # expires within 3 days

    def test_refresh_stores_the_rotated_refresh_token(self):
        with tempfile.TemporaryDirectory() as d:
            c = creds(Path(d))
            c.save("old-at", "old-rt", 100)
            with fake_urlopen({"accessToken": "new-at", "refreshToken": "new-rt", "expiresIn": 2628000, "errorCode": None}) as op, \
                    mock.patch.object(c, "reload"):
                c.refresh()
            req = op.call_args[0][0]
            self.assertEqual(req.get_method(), "POST")
            self.assertIn("refresh_token=old-rt", req.full_url)
            stored = json.loads((Path(d) / "tokens.json").read_text())
            self.assertEqual((stored["access_token"], stored["refresh_token"]), ("new-at", "new-rt"))
            self.assertFalse(c.needs_refresh())

    def test_token_endpoint_error_is_raised(self):
        with tempfile.TemporaryDirectory() as d:
            c = creds(Path(d))
            with fake_urlopen({"errorCode": "ACCESS_DENIED", "description": "bad code"}):
                with self.assertRaises(OpenAPIError):
                    c.exchange_code("x")

    def test_server_time_needs_no_connection(self):
        t = OpenAPIClient(creds=mock.Mock()).call("get_server_time")["utcTime"]
        self.assertLess(abs((datetime.fromisoformat(t) - datetime.now(timezone.utc)).total_seconds()), 5)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class WebTests(unittest.TestCase):
    """Starts the real dashboard on a free port with a password set."""

    @classmethod
    def setUpClass(cls):
        import uvicorn
        from ictscan.store import Store
        from ictscan.web import app as webapp
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        (tmp / "openapi.env").write_text("CTRADER_CLIENT_ID=cid\nCTRADER_CLIENT_SECRET=sec\n")
        cls.patches = [mock.patch.object(webapp, "DASH_PASSWORD", "pw"), mock.patch.object(webapp, "DASH_USER", "me"),
                       mock.patch.object(openapi, "ENV_FILE", tmp / "openapi.env"),
                       mock.patch.object(openapi, "TOKEN_FILE", tmp / "tokens.json")]
        for p in cls.patches:
            p.start()
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        cls.port = s.getsockname()[1]
        s.close()
        app = webapp.create_app(Store(tmp / "t.db"))
        cls.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=cls.port, log_level="error"))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        for _ in range(100):
            if cls.server.started:
                break
            time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(5)
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def get(self, path, auth=None, follow=True):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if auth:
            req.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
        opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(req, timeout=10) as r:
                return r.status, r.headers, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read().decode()

    def test_dashboard_requires_the_password(self):
        self.assertEqual(self.get("/api/health")[0], 401)
        self.assertEqual(self.get("/api/health", "me:wrong")[0], 401)
        self.assertEqual(self.get("/api/health", "me:pw")[0], 200)

    def test_healthz_is_open_and_reveals_nothing(self):
        status, _, body = self.get("/healthz")
        self.assertEqual((status, body), (200, "ok"))

    def test_oauth_start_redirects_to_ctrader_with_view_scope(self):
        status, headers, _ = self.get("/oauth/start", "me:pw", follow=False)
        self.assertEqual(status, 307)
        loc = headers["location"]
        self.assertTrue(loc.startswith(openapi.AUTH_URL))
        self.assertIn("client_id=cid", loc)
        self.assertIn("scope=accounts", loc)

    def test_oauth_callback_saves_tokens(self):
        with fake_urlopen({"accessToken": "AT", "refreshToken": "RT", "expiresIn": 2628000, "errorCode": None}) as op:
            status, _, body = self.get("/oauth/callback?code=abc", "me:pw")
        self.assertEqual(status, 200)
        self.assertIn("Connected to cTrader", body)
        self.assertIn("code=abc", op.call_args[0][0].full_url)
        self.assertEqual(json.loads((Path(self.tmp.name) / "tokens.json").read_text())["refresh_token"], "RT")

    def test_oauth_callback_without_code_explains(self):
        status, _, body = self.get("/oauth/callback?error=access_denied", "me:pw")
        self.assertEqual(status, 200)
        self.assertIn("Access not granted", body)

class FakeWS:
    """Socket double: replies to each request after a short delay and lets tests push spot events."""

    def __init__(self, reply):
        import queue as _q
        self.inbox, self.reply, self.sent = _q.Queue(), reply, []

    def send(self, raw):
        msg = json.loads(raw)
        self.sent.append(msg)
        out = self.reply(msg)
        if out is not None:
            self.inbox.put(json.dumps({**out, "clientMsgId": msg["clientMsgId"]}))

    def push(self, msg):
        self.inbox.put(json.dumps(msg))

    def recv(self, timeout=None):
        item = self.inbox.get()
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.inbox.put(ConnectionError("closed"))


class ReaderTests(unittest.TestCase):
    def client(self, reply):
        c = OpenAPIClient(creds=mock.Mock(), timeout=3)
        c.ws = FakeWS(reply)
        threading.Thread(target=c._reader, args=(c.ws,), daemon=True).start()
        return c

    def test_replies_are_routed_to_the_waiting_request_while_spots_flow(self):
        c = self.client(lambda m: {"payloadType": openapi.SYMBOLS_RES, "payload": {"symbol": []}} if m["payloadType"] == openapi.SYMBOLS_REQ else None)
        c.names = {7: "NAS100"}
        c.ws.push({"payloadType": openapi.SPOT_EVENT, "payload": {"symbolId": 7, "bid": 2899770000, "ask": 2899880000, "timestamp": 1792692000000}})
        self.assertEqual(c._rpc(openapi.SYMBOLS_REQ, {}, openapi.SYMBOLS_RES), {"symbol": []})
        for _ in range(50):
            if c.live().get("NAS100"):
                break
            time.sleep(0.02)
        q = c.live()["NAS100"]
        self.assertEqual((q["bid"], q["ask"]), (28997.7, 28998.8))
        self.assertEqual(q["bar"]["time"] % 300, 0)

    def test_forming_bar_tracks_high_low_close_and_rolls_every_5_minutes(self):
        c = OpenAPIClient(creds=mock.Mock())
        c.names = {7: "NAS100"}
        t0 = 1792692000000  # a 5-minute boundary
        for ms, bid in ((0, 100.0), (60_000, 103.0), (120_000, 98.0), (200_000, 101.0), (300_000, 99.0)):
            c._on_spot({"symbolId": 7, "bid": int(bid * 100000), "timestamp": t0 + ms})
            if ms == 200_000:
                self.assertEqual({k: c.live_bars["NAS100"][k] for k in ("open", "high", "low", "close")},
                                 {"open": 100.0, "high": 103.0, "low": 98.0, "close": 101.0})
        self.assertEqual(c.live_bars["NAS100"]["time"], t0 // 1000 + 300)
        self.assertEqual(c.live_bars["NAS100"]["open"], 99.0)

    def test_lost_connection_fails_waiting_requests_fast(self):
        c = self.client(lambda m: None)
        c.ws.inbox.put(ConnectionError("reset"))
        t = time.time()
        with self.assertRaises(MCPError):
            c._rpc(openapi.SYMBOLS_REQ, {}, openapi.SYMBOLS_RES)
        self.assertLess(time.time() - t, 2)
        self.assertFalse(c.alive)

    def test_spot_subscription_is_a_permitted_read_only_request(self):
        self.assertIn(openapi.SUB_SPOTS, openapi.ALLOWED_REQUESTS)
        self.assertFalse({2106, 2107, 2108, 2109, 2110, 2111} & openapi.ALLOWED_REQUESTS)


if __name__ == "__main__":
    unittest.main()
