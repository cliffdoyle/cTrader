"""Read-only cTrader Open API client (JSON over WebSocket) so the scanner can run without the cTrader desktop app.

It answers the same call() interface as MCPClient for the two tools the scanner uses (get_server_time, get_trendbars).
Only the view-only OAuth scope ("accounts") is requested, and only the read requests listed in ALLOWED_REQUESTS can be
sent — there is no trading message anywhere in this module.

Credentials (environment variables, or KEY=VALUE lines in DATA_DIR/openapi.env):
  CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET   from https://openapi.ctrader.com/apps → Credentials
  CTRADER_ACCOUNT_ID                         the account number shown in cTrader (optional if only one account)
  CTRADER_REDIRECT_URI                       must be listed in the app's redirect URLs (default http://localhost:8765/oauth/callback)
Tokens are obtained through the dashboard (/oauth/start) and kept in DATA_DIR/openapi-tokens.json; the refresh token
rotates on every refresh, so only one running scanner may use a given token file.
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect as ws_connect

from .config import DATA_DIR, SYMBOLS
from .engine.core import TF_SECONDS
from .mcp import MCPError, MCPUnavailable

log = logging.getLogger("ictscan.openapi")

HOSTS = {False: "demo.ctraderapi.com", True: "live.ctraderapi.com"}
PORT = 5036  # JSON
TOKEN_URL = "https://openapi.ctrader.com/apps/token"
AUTH_URL = "https://id.ctrader.com/my/settings/openapi/grantingaccess/"
SCOPE = "accounts"  # view-only; never "trading"
ENV_FILE = DATA_DIR / "openapi.env"
TOKEN_FILE = DATA_DIR / "openapi-tokens.json"

PERIOD = {"m1": 1, "m5": 5, "m15": 7, "m30": 8, "h1": 9, "h4": 10, "d1": 12, "w1": 13}
APP_AUTH, APP_AUTH_RES = 2100, 2101
ACC_AUTH, ACC_AUTH_RES = 2102, 2103
SYMBOLS_REQ, SYMBOLS_RES = 2114, 2115
TRENDBARS, TRENDBARS_RES = 2137, 2138
ERROR_RES, COMMON_ERROR = 2142, 50
TOKEN_INVALIDATED, CLIENT_DISCONNECT = 2147, 2148
ACCOUNTS, ACCOUNTS_RES = 2149, 2150
SUB_SPOTS, SUB_SPOTS_RES, SPOT_EVENT = 2127, 2128, 2131
HEARTBEAT = 51
ALLOWED_REQUESTS = {APP_AUTH, ACC_AUTH, SYMBOLS_REQ, TRENDBARS, ACCOUNTS, SUB_SPOTS, HEARTBEAT}
HIST_SPACING = 0.25  # historical-data requests are rate limited (5/s); stay under it
TOKEN_ERRORS = ("TOKEN", "OA_AUTH", "ACCOUNT_NOT_AUTHORIZED")


class OpenAPIError(MCPError):
    def __init__(self, code, description="", retry_after=None):
        super().__init__(f"{code}: {description}" if description else str(code))
        self.code, self.retry_after = str(code or ""), retry_after

    @property
    def token_problem(self):
        return any(k in self.code for k in TOKEN_ERRORS)


def read_env_file(path):
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


class Credentials:
    def __init__(self, env_file=None, token_file=None):
        self.env_file, self.token_file = env_file or ENV_FILE, token_file or TOKEN_FILE
        self.lock = threading.Lock()
        self.reload()

    def reload(self):
        f = read_env_file(self.env_file)
        get = lambda k: os.environ.get(k) or f.get(k) or ""  # noqa: E731
        self.client_id, self.client_secret = get("CTRADER_CLIENT_ID"), get("CTRADER_CLIENT_SECRET")
        self.account_login = get("CTRADER_ACCOUNT_ID")
        self.redirect_uri = get("CTRADER_REDIRECT_URI") or "http://localhost:8765/oauth/callback"
        tok = json.loads(self.token_file.read_text(encoding="utf-8")) if self.token_file.exists() else {}
        self.access_token = tok.get("access_token") or get("CTRADER_ACCESS_TOKEN")
        self.refresh_token = tok.get("refresh_token") or get("CTRADER_REFRESH_TOKEN")
        self.expires_at = int(tok.get("expires_at") or 0)  # unix seconds; 0 = unknown

    @property
    def app_ready(self):
        return bool(self.client_id and self.client_secret)

    def authorize_url(self):
        q = urllib.parse.urlencode({"client_id": self.client_id, "redirect_uri": self.redirect_uri, "scope": SCOPE, "product": "web"})
        return f"{AUTH_URL}?{q}"

    def needs_refresh(self, margin_s=3 * 86400):
        return bool(self.refresh_token) and self.expires_at > 0 and self.expires_at - time.time() < margin_s

    def save(self, access, refresh, expires_in):
        self.access_token, self.refresh_token = access, refresh
        self.expires_at = int(time.time()) + int(expires_in or 2628000)
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.token_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"access_token": access, "refresh_token": refresh, "expires_at": self.expires_at,
                                   "saved": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.token_file)

    def _token_request(self, params, method):
        q = urllib.parse.urlencode({**params, "client_id": self.client_id, "client_secret": self.client_secret})
        req = urllib.request.Request(f"{TOKEN_URL}?{q}", method=method, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise MCPError(f"cTrader token endpoint HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from e
        except (urllib.error.URLError, OSError) as e:
            raise MCPUnavailable(f"cTrader token endpoint not reachable: {e}") from e
        if data.get("errorCode") or not data.get("accessToken"):
            raise OpenAPIError(data.get("errorCode") or "TOKEN_REQUEST_FAILED", data.get("description") or "")
        self.save(data["accessToken"], data.get("refreshToken") or self.refresh_token, data.get("expiresIn"))

    def exchange_code(self, code):
        with self.lock:
            self._token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri}, "GET")

    def refresh(self):
        with self.lock:
            self.reload()  # another process/thread may already have rotated the refresh token
            if not self.refresh_token:
                raise MCPUnavailable("No refresh token: open the dashboard at /oauth/start and allow access again")
            self._token_request({"grant_type": "refresh_token", "refresh_token": self.refresh_token}, "POST")
            log.info("Open API access token refreshed (valid until %s)", datetime.fromtimestamp(self.expires_at, timezone.utc).date())


def _price(v, digits):
    return round(int(v) / 100000, digits)


def convert_trendbar(tb, digits):
    low = int(tb.get("low", 0))
    t = datetime.fromtimestamp(int(tb["utcTimestampInMinutes"]) * 60, timezone.utc)
    return {"timestamp": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "open": _price(low + int(tb.get("deltaOpen", 0)), digits),
            "high": _price(low + int(tb.get("deltaHigh", 0)), digits), "low": _price(low, digits),
            "close": _price(low + int(tb.get("deltaClose", 0)), digits), "volume": int(tb.get("volume", 0))}


class OpenAPIClient:
    def __init__(self, creds=None, timeout=30):
        self.creds = creds or Credentials()
        self.timeout = timeout
        self.ws = None
        self.account_id = None
        self.is_live = None
        self.symbols = {}
        self.names = {}
        self.info = {}
        self.send_lock = threading.Lock()
        self.conn_lock = threading.RLock()
        self.hb_stop = threading.Event()
        self.last_hist = 0.0
        self.reconnect_requested = False
        self.pending = {}
        self.pending_lock = threading.Lock()
        self.broken = None
        self.quotes = {}          # symbol → {"bid", "ask", "t"} from live spot events
        self.live_bars = {}       # symbol → forming M5 bar built from spot bids
        self.quote_lock = threading.Lock()
        self.spot_symbols = set()
        self.subscribed = set()

    # --- transport ---
    def _open(self, live):
        url = f"wss://{HOSTS[live]}:{PORT}"
        try:
            # Long-lived connection: entering the context directly is the supported non-`with` form (websockets ≥ 17.1).
            self.ws = ws_connect(url, open_timeout=self.timeout, close_timeout=5, max_size=2 ** 24).__enter__()
        except (OSError, WebSocketException, TimeoutError) as e:
            self.ws = None
            raise MCPUnavailable(f"cTrader Open API not reachable at {url}: {e}") from e
        self.is_live = live
        self.broken = None
        threading.Thread(target=self._reader, args=(self.ws,), name="openapi-reader", daemon=True).start()

    def close(self):
        self.hb_stop.set()
        ws, self.ws = self.ws, None
        self.subscribed = set()
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001 — closing a dead socket must never raise
                pass

    def request_reconnect(self):
        """Called from another thread (e.g. after new tokens were saved): reconnect before the next request."""
        self.reconnect_requested = True

    @property
    def alive(self):
        return self.ws is not None and self.broken is None

    def _fail_pending(self, err):
        with self.pending_lock:
            waiting, self.pending = self.pending, {}
        for q in waiting.values():
            q.put(err)

    def _reader(self, ws):
        """Only reader of the socket: routes replies to the waiting request and keeps live quotes."""
        while True:
            try:
                raw = ws.recv()
            except Exception as e:  # noqa: BLE001 — any socket failure ends this connection
                if ws is self.ws:
                    self.broken = MCPUnavailable(f"Open API connection lost: {e}")
                    self._fail_pending(self.broken)
                return
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            t, p = msg.get("payloadType"), msg.get("payload") or {}
            if t == HEARTBEAT:
                continue
            if t == SPOT_EVENT:
                self._on_spot(p)
                continue
            if t in (CLIENT_DISCONNECT, TOKEN_INVALIDATED):
                err = (MCPUnavailable(f"Open API closed the connection: {p.get('reason', '')}") if t == CLIENT_DISCONNECT
                       else OpenAPIError("ACCESS_TOKEN_INVALIDATED", p.get("reason", "")))
                self.broken = err
                self._fail_pending(err)
                continue
            with self.pending_lock:
                q = self.pending.pop(msg.get("clientMsgId"), None)
            if q is not None:
                q.put(msg)

    def _on_spot(self, p):
        name = self.names.get(int(p.get("symbolId", 0)))
        if not name:
            return
        digits = SYMBOLS.get(name, {}).get("digits", 5)
        ts = p.get("timestamp")
        now = datetime.fromtimestamp(int(ts) / 1000, timezone.utc) if ts else datetime.now(timezone.utc)
        with self.quote_lock:
            q = self.quotes.setdefault(name, {"bid": None, "ask": None, "t": None})
            if p.get("bid") is not None:
                q["bid"] = _price(p["bid"], digits)
            if p.get("ask") is not None:
                q["ask"] = _price(p["ask"], digits)
            q["t"] = now.isoformat()
            if q["bid"] is None:
                return
            start = int(now.timestamp()) // 300 * 300
            bar = self.live_bars.get(name)
            if bar is None or bar["time"] != start:
                self.live_bars[name] = {"time": start, "open": q["bid"], "high": q["bid"], "low": q["bid"], "close": q["bid"]}
            else:
                bar["high"], bar["low"], bar["close"] = max(bar["high"], q["bid"]), min(bar["low"], q["bid"]), q["bid"]

    def live(self):
        """Copy of the latest quotes and forming M5 bars (empty when no spot data has arrived)."""
        with self.quote_lock:
            return {s: {**q, "bar": dict(self.live_bars[s]) if s in self.live_bars else None} for s, q in self.quotes.items()}

    def _send(self, pt, payload, mid=None):
        if pt not in ALLOWED_REQUESTS:
            raise MCPError(f"Refused: payload type {pt} is not a read-only request")
        mid = mid or uuid.uuid4().hex[:16]
        try:
            with self.send_lock:
                self.ws.send(json.dumps({"clientMsgId": mid, "payloadType": pt, "payload": payload}))
        except (OSError, WebSocketException, AttributeError) as e:
            raise MCPUnavailable(f"Open API connection lost: {e}") from e
        return mid

    def _rpc(self, pt, payload, expect):
        if self.broken is not None:
            raise self.broken
        mid, q = uuid.uuid4().hex[:16], queue.Queue(maxsize=1)
        with self.pending_lock:
            self.pending[mid] = q
        try:
            self._send(pt, payload, mid)
            try:
                msg = q.get(timeout=self.timeout)
            except queue.Empty as e:
                raise MCPUnavailable(f"Open API: no response to payload {pt} within {self.timeout}s") from e
        finally:
            with self.pending_lock:
                self.pending.pop(mid, None)
        if isinstance(msg, Exception):
            raise msg
        t, p = msg.get("payloadType"), msg.get("payload") or {}
        if t in (ERROR_RES, COMMON_ERROR):
            raise OpenAPIError(p.get("errorCode"), p.get("description", ""), p.get("retryAfter"))
        if t != expect:
            raise MCPError(f"Open API: expected payload {expect}, got {t}")
        return p

    def _heartbeat(self, stop):
        while not stop.wait(10):
            try:
                self._send(HEARTBEAT, {})
            except MCPError:
                return

    # --- session ---
    def _authorise_app(self):
        self._rpc(APP_AUTH, {"clientId": self.creds.client_id, "clientSecret": self.creds.client_secret}, APP_AUTH_RES)

    def _accounts(self):
        try:
            return self._rpc(ACCOUNTS, {"accessToken": self.creds.access_token}, ACCOUNTS_RES)
        except OpenAPIError as e:
            if not e.token_problem:
                raise
            self.creds.refresh()
            return self._rpc(ACCOUNTS, {"accessToken": self.creds.access_token}, ACCOUNTS_RES)

    def connect(self):
        with self.conn_lock:
            self._connect()

    def _connect(self):
        self.close()
        self.reconnect_requested = False
        c = self.creds
        c.reload()
        if not c.app_ready:
            raise MCPUnavailable(f"cTrader Open API credentials missing: set CTRADER_CLIENT_ID and CTRADER_CLIENT_SECRET in {c.env_file}")
        if not c.access_token:
            raise MCPUnavailable("Not authorised yet: open the dashboard at /oauth/start and allow access to your account")
        if c.needs_refresh():
            c.refresh()
        live = bool(self.is_live)
        try:
            self._open(live)
            self._authorise_app()
            res = self._accounts()
            accs = res.get("ctidTraderAccount") or []
            if not accs:
                raise MCPUnavailable("The access token has no trading accounts; allow access to your account again at /oauth/start")
            want = str(c.account_login).strip()
            acc = next((a for a in accs if want and str(a.get("traderLogin")) == want), None) or (accs[0] if not want else None)
            if acc is None:
                raise MCPError(f"Account {want} not among the authorised accounts {[a.get('traderLogin') for a in accs]}")
            if bool(acc.get("isLive")) != live:
                self.close()
                self._open(bool(acc.get("isLive")))
                self._authorise_app()
            self.account_id = int(acc["ctidTraderAccountId"])
            self._rpc(ACC_AUTH, {"ctidTraderAccountId": self.account_id, "accessToken": c.access_token}, ACC_AUTH_RES)
            syms = self._rpc(SYMBOLS_REQ, {"ctidTraderAccountId": self.account_id}, SYMBOLS_RES)
        except BaseException:
            self.close()
            raise
        self.symbols = {s["symbolName"]: int(s["symbolId"]) for s in syms.get("symbol", []) if s.get("symbolName")}
        self.names = {v: k for k, v in self.symbols.items()}
        self.info = {"account": acc.get("traderLogin"), "live": bool(acc.get("isLive")), "broker": acc.get("brokerTitleShort"),
                     "scope": res.get("permissionScope"), "symbols": len(self.symbols)}
        log.info("Open API connected: %s", self.info)
        self.hb_stop = threading.Event()
        threading.Thread(target=self._heartbeat, args=(self.hb_stop,), name="openapi-heartbeat", daemon=True).start()
        if self.spot_symbols:
            self._subscribe(self.spot_symbols)

    # --- live quotes ---
    def _subscribe(self, symbols):
        todo = [s for s in symbols if s in self.symbols and s not in self.subscribed]
        if not todo:
            return
        try:
            self._rpc(SUB_SPOTS, {"ctidTraderAccountId": self.account_id, "symbolId": [self.symbols[s] for s in todo],
                                  "subscribeToSpotTimestamp": True}, SUB_SPOTS_RES)
        except OpenAPIError as e:
            if "ALREADY" not in e.code:
                raise
        self.subscribed.update(todo)
        log.info("Live quotes subscribed: %s", ", ".join(todo))

    def ensure_connected(self):
        """Connect if needed. Double-checked under the lock so concurrent threads open only one connection."""
        if self.alive and not self.reconnect_requested:
            return
        with self.conn_lock:
            if not self.alive or self.reconnect_requested:
                self._connect()

    def ensure_live(self, symbols):
        """Keep a connection with spot subscriptions for `symbols` (called every few seconds by the live keeper)."""
        with self.conn_lock:
            self.spot_symbols = set(symbols)
            if not self.alive or self.reconnect_requested:
                self._connect()
            else:
                self._subscribe(self.spot_symbols)

    # --- data ---
    def _hist(self, payload):
        while True:
            wait = self.last_hist + HIST_SPACING - time.time()
            if wait > 0:
                time.sleep(wait)
            self.last_hist = time.time()
            try:
                return self._rpc(TRENDBARS, payload, TRENDBARS_RES)
            except OpenAPIError as e:
                if e.code == "BLOCKED_PAYLOAD_TYPE":
                    time.sleep(min(float(e.retry_after or 1), 60))
                    continue
                raise

    def symbol_id(self, symbol):
        if symbol in self.symbols:
            return self.symbols[symbol]
        close = difflib.get_close_matches(symbol, list(self.symbols), n=5, cutoff=0.5)
        raise MCPError(f"Symbol {symbol} is not available on this account (close matches: {close})")

    def trendbars(self, symbol, tf, start, end):
        """All bars with open time in [start, end], oldest first. Pages backwards in windows of about 1000 bars and
        halves the window if the server reports more data or rejects the range."""
        sid, sec = self.symbol_id(symbol), TF_SECONDS[tf]
        digits = SYMBOLS.get(symbol, {}).get("digits", 5)
        start_ms, to_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        window, min_window = 1000 * sec * 1000, 10 * sec * 1000
        out = {}
        while to_ms >= start_ms:
            frm = max(start_ms, to_ms - window)
            payload = {"ctidTraderAccountId": self.account_id, "symbolId": sid, "period": PERIOD[tf],
                       "fromTimestamp": frm, "toTimestamp": to_ms}
            try:
                res = self._hist(payload)
            except OpenAPIError as e:
                if e.token_problem or window <= min_window:
                    raise
                window //= 2
                continue
            if res.get("hasMore") and window > min_window:
                window //= 2
                continue
            for tb in res.get("trendbar") or []:
                bar = convert_trendbar(tb, digits)
                out[bar["timestamp"]] = bar
            if frm <= start_ms:
                break
            to_ms = frm - 1
        return [out[k] for k in sorted(out)]

    # --- MCPClient-compatible interface ---
    def call(self, tool, args=None):
        args = args or {}
        if tool == "get_server_time":
            return {"utcTime": datetime.now(timezone.utc).isoformat()}
        if tool != "get_trendbars":
            raise MCPError(f"{tool} is not available through the read-only Open API client")
        start = datetime.fromisoformat(args["from"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(args["to"].replace("Z", "+00:00"))
        for attempt in (1, 2):
            try:
                self.ensure_connected()
                return {"bars": self.trendbars(args["symbolName"], args["timeframe"], start, end), "truncated": False}
            except OpenAPIError as e:
                if not e.token_problem or attempt == 2:
                    raise
                self.close()
                self.creds.refresh()
            except MCPUnavailable:
                self.close()
                if attempt == 2:
                    raise


def check(compare=True):
    """Connect, print the account, fetch recent H1 bars for every scanner symbol and (optionally) compare the last
    closed bars with the cTrader desktop app over MCP. Usage: python -m ictscan.openapi [--no-compare]"""
    from datetime import timedelta
    from .config import MCP_ENDPOINT
    from .mcp import MCPClient
    c = OpenAPIClient()
    c.connect()
    print("Connected:", json.dumps(c.info))
    mcp = MCPClient(MCP_ENDPOINT, timeout=30) if compare else None
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=4)
    ok = True
    for sym in SYMBOLS:
        bars = [b for b in c.trendbars(sym, "h1", start, end) if b["timestamp"] < end.strftime("%Y-%m-%dT%H:%M:%SZ")]
        line = f"{sym:8s} {len(bars):3d} H1 bars, last {bars[-1]['timestamp']} close {bars[-1]['close']}" if bars else f"{sym:8s} no bars"
        if mcp is not None and bars:
            try:
                ref = mcp.call("get_trendbars", {"symbolName": sym, "timeframe": "h1", "from": bars[0]["timestamp"],
                                                 "to": bars[-1]["timestamp"], "limit": 1000})["bars"]
                ref = {b["timestamp"]: b for b in ref}
                diff = [b["timestamp"] for b in bars if b["timestamp"] in ref and any(
                    abs(b[k] - ref[b["timestamp"]][k]) > 1e-9 for k in ("open", "high", "low", "close"))]
                common = sum(b["timestamp"] in ref for b in bars)
                line += f" | vs cTrader app: {common} common bars, {len(diff)} differ"
                ok &= not diff and common > 0
            except MCPError as e:
                line += f" | compare skipped: {e}"
        print(line)
    c.close()
    return ok


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(0 if check(compare="--no-compare" not in sys.argv) else 1)
