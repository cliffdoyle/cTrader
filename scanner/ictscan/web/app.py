"""Dashboard web app (FastAPI). Read-only views over the scanner store plus notification state."""
from __future__ import annotations

import base64
import html
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from ..config import ALERT_SETUPS, DASH_PASSWORD, DASH_USER, DATA_SOURCE, SHOW_GRADES, SYMBOLS, TIMEFRAMES
from ..engine.core import TF_SECONDS

STATIC = Path(__file__).parent / "static"
ACTIVE = ("watch", "armed", "limit", "triggered")


def _authorised(header):
    if not header.startswith("Basic "):
        return False
    try:
        user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except Exception:  # noqa: BLE001 — malformed header = not authorised
        return False
    return secrets.compare_digest(user, DASH_USER) and secrets.compare_digest(pw, DASH_PASSWORD)


def _page(title, body):
    return HTMLResponse(f"<!doctype html><meta charset='utf-8'><title>{html.escape(title)}</title>"
                        f"<link rel='stylesheet' href='/static/style.css'><main><h1>{html.escape(title)}</h1>{body}"
                        f"<p><a href='/'>Back to the dashboard</a></p></main>")


def create_app(store, scanner=None):
    app = FastAPI(title="ICT Scanner", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    if DASH_PASSWORD:
        @app.middleware("http")
        async def basic_auth(request: Request, call_next):
            if request.url.path == "/healthz" or _authorised(request.headers.get("authorization", "")):
                return await call_next(request)
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="ICT Scanner"'})

    @app.get("/healthz")
    def healthz():
        return PlainTextResponse("ok")

    @app.get("/oauth/start")
    def oauth_start():
        from ..openapi import Credentials
        creds = Credentials()
        if not creds.app_ready:
            return _page("cTrader Open API not configured", f"<p>Put CTRADER_CLIENT_ID and CTRADER_CLIENT_SECRET in "
                         f"<code>{html.escape(str(creds.env_file))}</code> (or the server's environment), then reload this page.</p>")
        return RedirectResponse(creds.authorize_url())

    @app.get("/oauth/callback")
    def oauth_callback(code: str = "", error: str = "", error_description: str = ""):
        from ..openapi import Credentials
        if error or not code:
            return _page("Access not granted", f"<p>{html.escape(error_description or error or 'No authorisation code received.')}</p>"
                         "<p><a href='/oauth/start'>Try again</a></p>")
        creds = Credentials()
        try:
            creds.exchange_code(code)
        except Exception as e:  # noqa: BLE001 — show the reason to the user
            return _page("Could not complete the connection", f"<p>{html.escape(str(e))}</p><p><a href='/oauth/start'>Try again</a></p>")
        client = getattr(scanner, "client", None)
        if client is not None and hasattr(client, "request_reconnect"):
            client.request_reconnect()
            scanner.scan_now.set()
        note = "" if DATA_SOURCE == "openapi" else "<p>This scanner still reads candles from the cTrader app (ICTSCAN_SOURCE=mcp); the tokens are saved for the server version.</p>"
        until = datetime.fromtimestamp(creds.expires_at, timezone.utc).strftime("%d %b %Y")
        return _page("Connected to cTrader", f"<p>Read-only access granted. The token is valid until {until} and renews automatically.</p>{note}")

    def page(name):
        return FileResponse(STATIC / name, headers={"Cache-Control": "no-store"})

    @app.get("/")
    def index():
        return page("index.html")

    @app.get("/symbol/{sym}")
    def symbol_page(sym: str):
        return page("symbol.html")

    @app.get("/setup/{sid}")
    def setup_page(sid: str):
        return page("setup.html")

    @app.get("/notifications")
    def notifications_page():
        return page("notifications.html")

    @app.get("/api/overview")
    def overview():
        rows = []
        for sym in SYMBOLS:
            snap = store.snapshot(sym)
            if not snap:
                rows.append({"symbol": sym, "missing": True})
                continue
            tfs = snap["timeframes"]
            rows.append({
                "symbol": sym, "price": snap["price"], "as_of_ny": snap["as_of_ny"], "bias": snap["bias"],
                "aligned": snap["aligned"], "states": {tf: tfs[tf]["state"] for tf in TIMEFRAMES},
                "h4_position": tfs["h4"]["position"], "narrative": snap["narrative"],
            })
        active = store.setups(active_only=True, limit=200, grades=SHOW_GRADES)
        recent = [s for s in store.setups(limit=200, grades=SHOW_GRADES) if s["stage"] not in ACTIVE][:30]
        return {"health": store.get_meta("health"), "unread": store.unread_count(SHOW_GRADES), "symbols": rows,
                "muted": store.get_meta("alerts_muted", False), "show_grades": list(SHOW_GRADES),
                "active": [brief(s) for s in active], "recent": [brief(s) for s in recent]}

    @app.get("/api/symbol/{sym}")
    def symbol(sym: str):
        if sym not in SYMBOLS:
            raise HTTPException(404, "unknown symbol")
        return {"snapshot": store.snapshot(sym), "show_grades": list(SHOW_GRADES),
                "setups": [brief(s) for s in store.setups(symbol=sym, limit=80, grades=SHOW_GRADES)]}

    @app.get("/api/bars/{sym}/{tf}")
    def bars(sym: str, tf: str, n: int = 300, end: int | None = None):
        if sym not in SYMBOLS or tf not in TF_SECONDS:
            raise HTTPException(404, "unknown symbol/timeframe")
        rows = store.load_bars(sym, tf, 5000 if end else min(n, 3000))
        if end:
            rows = [r for r in rows if r["t"] <= end][-n:]
        return [{"time": r["t"], "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"]} for r in rows]

    @app.get("/backtest")
    def backtest_page():
        return page("backtest.html")

    @app.get("/api/backtest")
    def backtest(full: bool = False):
        out = {}
        for part in ("in-sample", "out-of-sample"):
            rep = store.get_meta(f"backtest:{part}")
            if rep and not full:
                rep = {k: v for k, v in rep.items() if k != "trades"}
            out[part] = rep
        out["variants"] = store.get_meta("backtest_variants:in-sample", {})
        return out

    @app.get("/api/setup/{sid}")
    def setup(sid: str):
        s = store.setup(sid)
        if not s:
            raise HTTPException(404, "unknown setup")
        rep = store.get_meta("backtest:in-sample")
        if s["setup"] == "MMXM":
            mm = store.get_meta("backtest:mmxm")
            mode = s["id"].split("-")[2] if s["id"].count("-") >= 4 else None
            if mm and mode:
                s["backtest"] = {"setup": mm["by_mode"].get(mode), "setup_symbol": mm["by_mode_symbol"].get(f"{mode} {s['symbol']}"),
                                 "period": mm["period"], "label": f"MMXM {mode}"}
        elif rep:
            s["backtest"] = {"setup": rep["by_setup"].get(s["setup"]),
                             "setup_symbol": rep["by_setup_symbol"].get(f"{s['setup']} {s['symbol']}"),
                             "period": f"{rep['from'][:10]} → {rep['to'][:10]} (in-sample)"}
        return s

    @app.get("/api/notifications")
    def notifications(after_id: int = 0, limit: int = 100, unread: bool = False):
        muted = store.get_meta("alerts_muted", False)
        items = store.notifications(after_id, limit, unread, SHOW_GRADES)
        for n in items:  # setup ids look like SYMBOL-KIND-…
            kind = (n["setup_id"] or "").split("-")[1] if (n["setup_id"] or "").count("-") else ""
            n["popup"] = (not muted) or kind in ALERT_SETUPS
        return {"items": items, "unread": store.unread_count(SHOW_GRADES), "muted": muted, "alert_setups": list(ALERT_SETUPS)}

    @app.post("/api/notifications/read")
    def mark_read(ids: list[int] | None = Body(default=None, embed=True)):
        store.mark_read(ids)
        return {"unread": store.unread_count(SHOW_GRADES)}

    @app.get("/api/health")
    def health():
        client = getattr(scanner, "client", None)
        return {"health": store.get_meta("health"), "server_time": datetime.now(timezone.utc).isoformat(),
                "source": DATA_SOURCE, "account": getattr(client, "info", None) or None}

    @app.post("/api/scan")
    def scan_now():
        if scanner is None:
            raise HTTPException(503, "scanner not running")
        scanner.scan_now.set()
        return {"ok": True}

    return app


def brief(s):
    keys = ("id", "symbol", "setup", "name", "dir", "poi_tf", "confirm_tf", "stage", "stage_t", "key_t", "entry", "stop",
            "tp1", "tp1_name", "rr", "result_r", "score", "grade", "grade_note")
    out = {k: s.get(k) for k in keys}
    out["poi"] = {k: s["poi"].get(k) for k in ("kind", "low", "high")}
    out["last_note"] = s["timeline"][-1]["note"] if s["timeline"] else ""
    out["last_ny"] = s["timeline"][-1]["ny"] if s["timeline"] else ""
    return out
