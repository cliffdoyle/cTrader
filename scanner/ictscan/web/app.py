"""Dashboard web app (FastAPI). Read-only views over the scanner store plus notification state."""
from __future__ import annotations

import base64
import html
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from ..config import (ALERT_SETUPS, DASH_PASSWORD, DASH_USER, DASHBOARD_SETUPS, DASHBOARD_SYMBOLS, DATA_SOURCE, DRAW_BRIDGE,
                      DRAW_ORIGINS, FEED_TOKEN, SHOW_GRADES, SYMBOLS, TIMEFRAMES, WINDOW)
from ..engine.core import TF_SECONDS, make_bars
from ..engine.cycles import KIND_TF, chart_cycles

STATIC = Path(__file__).parent / "static"
ACTIVE = ("watch", "armed", "limit", "triggered")
DASH_SYMS = tuple(s for s in (DASHBOARD_SYMBOLS or SYMBOLS) if s in SYMBOLS)
FILT = {"grades": SHOW_GRADES, "symbols": DASHBOARD_SYMBOLS, "kinds": DASHBOARD_SETUPS}


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

    @app.middleware("http")
    async def static_revalidate(request: Request, call_next):
        resp = await call_next(request)
        if request.url.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"  # always revalidate (ETag) so updates show up at once
        return resp

    if DASH_PASSWORD:
        @app.middleware("http")
        async def basic_auth(request: Request, call_next):
            if (request.url.path == "/healthz" or (DRAW_BRIDGE and request.url.path.startswith("/api/draw"))
                    or request.url.path.startswith("/api/feed/")  # checks its own read-only key (feed_ok)
                    or _authorised(request.headers.get("authorization", ""))):
                return await call_next(request)
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="ICT Scanner"'})

    if DRAW_BRIDGE:
        @app.middleware("http")
        async def draw_cors(request: Request, call_next):
            """Only the dashboard origins may call /api/draw (CORS + Chrome/Edge private-network preflight)."""
            if not request.url.path.startswith("/api/draw"):
                return await call_next(request)
            origin = request.headers.get("origin")
            if origin and origin not in DRAW_ORIGINS:
                return Response(status_code=403, content="origin not allowed")
            hdrs = {"Access-Control-Allow-Origin": origin, "Vary": "Origin"} if origin else {}
            if request.method == "OPTIONS":
                return Response(status_code=204, headers={**hdrs, "Access-Control-Allow-Methods": "GET, POST",
                                                          "Access-Control-Allow-Headers": "content-type",
                                                          "Access-Control-Allow-Private-Network": "true",
                                                          "Access-Control-Max-Age": "600"})
            resp = await call_next(request)
            resp.headers.update(hdrs)
            return resp

        @app.get("/api/draw/status")
        def draw_status():
            from ..drawing import status
            from ..mcp import MCPError
            try:
                return status()
            except MCPError as e:
                return {"ok": False, "error": str(e)}

        @app.post("/api/draw")
        def draw_now(body: dict = Body(...)):
            from ..drawing import DrawError, draw
            from ..mcp import MCPError
            try:
                return draw(str(body.get("symbol", "")), str(body.get("what", "")),
                            kinds=[str(k) for k in body.get("kinds") or []] or None,
                            untouched_only=bool(body.get("untouched_only")), timeframe=body.get("timeframe") or None,
                            store=store)
            except (DrawError, MCPError) as e:
                return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

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
        # Versioned asset URLs: a browser holding an old common.js/style.css would otherwise break a newer page.
        ver = int(max(p.stat().st_mtime for p in STATIC.iterdir() if p.suffix in (".js", ".css")))
        text = (STATIC / name).read_text(encoding="utf-8")
        for asset in ("common.js", "chart.js", "style.css"):
            text = text.replace(f"/static/{asset}\"", f"/static/{asset}?v={ver}\"")
        return HTMLResponse(text, headers={"Cache-Control": "no-store"})

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

    @app.get("/cycles")
    def cycles_page():
        return page("cycles.html")

    @app.get("/api/cycles/{sym}")
    def cycles(sym: str, kind: str = "90m", n: int = 8):
        if sym not in SYMBOLS or kind not in KIND_TF:
            raise HTTPException(404, "unknown symbol/cycle")
        tf = KIND_TF[kind]
        bars = make_bars(store.load_bars(sym, tf, WINDOW[tf]), tf)
        if not bars:
            return {"tf": tf, "bars": [], "cycles": []}
        digits = SYMBOLS[sym]["digits"]
        now = bars[-1].end
        cyc = chart_cycles(bars, kind, now, max(1, min(n, 30)), lambda v: round(v, digits))
        t0 = datetime.fromisoformat(cyc[0]["px"]["start"] if cyc and "px" in cyc[0] else cyc[0]["start"]) if cyc else bars[0].t
        shown = [b for b in bars if b.t >= t0]
        return {"tf": tf, "kind": kind, "digits": digits, "cycles": cyc,
                "bars": [{"time": int(b.t.timestamp()), "open": b.o, "high": b.h, "low": b.l, "close": b.c} for b in shown]}

    @app.get("/api/live")
    def live():
        """Latest quotes (pushed by the Open API every tick) and the forming M5/M15/H1 candle for the dashboard symbols.
        Without a stream (desktop MCP source, weekend) the last closed M5 close is returned with live=false."""
        client = getattr(scanner, "client", None)
        quotes = client.live() if client is not None and hasattr(client, "live") else {}
        now = datetime.now(timezone.utc)
        out = {}
        for sym in DASH_SYMS:
            m5 = store.load_bars(sym, "m5", 13)
            q = quotes.get(sym) or {}
            age = (now - datetime.fromisoformat(q["t"])).total_seconds() if q.get("t") else None
            price = q.get("bid") if q.get("bid") is not None else (m5[-1]["c"] if m5 else None)
            lb = q.get("bar")
            bars = {}
            fresh = age is not None and age < 120
            for tf, sec in (("m5", 300), ("m15", 900), ("h1", 3600)):
                if q.get("bid") is None or not fresh:
                    break
                start = int(now.timestamp()) // sec * sec
                seq = [(b["o"], b["h"], b["l"], b["c"]) for b in m5 if b["t"] >= start]
                if lb and lb["time"] >= start:
                    seq.append((lb["open"], lb["high"], lb["low"], lb["close"]))
                else:
                    seq.append((price, price, price, price))
                bars[tf] = {"time": start, "open": seq[0][0], "high": max(s[1] for s in seq), "low": min(s[2] for s in seq),
                            "close": seq[-1][3]}
            out[sym] = {"price": price, "ask": q.get("ask"), "t": q.get("t"), "age": round(age, 1) if age is not None else None,
                        "live": fresh, "bars": bars}
        return {"server_time": now.isoformat(), "symbols": out}

    @app.get("/api/overview")
    def overview():
        rows = []
        for sym in DASH_SYMS:
            snap = store.snapshot(sym)
            if not snap:
                rows.append({"symbol": sym, "missing": True})
                continue
            tfs = snap["timeframes"]
            ctx = (snap.get("session") or {}).get("context") or {}
            rows.append({
                "symbol": sym, "price": snap["price"], "as_of_ny": snap["as_of_ny"], "bias": snap["bias"],
                "aligned": snap["aligned"], "states": {tf: tfs[tf]["state"] for tf in TIMEFRAMES},
                "h4_position": tfs["h4"]["position"], "narrative": snap["narrative"],
                "lean": ctx.get("lean"), "draw": ctx.get("draw"), "cycles": snap.get("cycles"), "nyplan": snap.get("nyplan"),
            })
        active = store.setups(active_only=True, limit=200, **FILT)
        closed = [s for s in store.setups(limit=1000, **FILT) if s["stage"] not in ACTIVE]
        return {"health": store.get_meta("health"), "unread": store.unread_count(**FILT), "symbols": rows,
                "muted": store.get_meta("alerts_muted", False), "show_grades": list(SHOW_GRADES),
                "dashboard": {"symbols": list(DASH_SYMS), "setups": list(DASHBOARD_SETUPS)},
                "stats": performance(closed, days=30), "smt": store.get_meta("smt"),
                "active": [brief(s) for s in active], "recent": [brief(s) for s in closed[:30]]}

    @app.get("/api/smt")
    def smt():
        return store.get_meta("smt") or {}

    @app.get("/api/feed/{sym}")
    def feed(sym: str, request: Request, token: str = "", groups: str = "boxes,cycles,levels,smt", kinds: str = ""):
        """Drawing instructions for the chart add-ons (see ictscan/feed.py). Read-only."""
        from ..feed import GROUPS, build_feed, resolve
        given = token or request.headers.get("x-feed-token", "")
        if not ((FEED_TOKEN and secrets.compare_digest(given.encode(), FEED_TOKEN.encode())) or not DASH_PASSWORD
                or _authorised(request.headers.get("authorization", ""))):
            return PlainTextResponse("#ERR|auth|wrong or missing feed token\n", status_code=401)
        target = resolve(sym)
        if not target:
            return PlainTextResponse(f"#ERR|symbol|{sym} is not a scanner symbol\n", status_code=404)
        gs = [g for g in groups.split(",") if g in GROUPS] or list(GROUPS)
        ks = [k for k in kinds.split(",") if k in ("month", "week", "day", "90m")] or None
        try:
            return PlainTextResponse(build_feed(store, target, gs, ks), headers={"Cache-Control": "no-store"})
        except ValueError as e:
            return PlainTextResponse(f"#ERR|data|{e}\n", status_code=503)

    @app.get("/api/symbol/{sym}")
    def symbol(sym: str):
        if sym not in SYMBOLS:
            raise HTTPException(404, "unknown symbol")
        return {"snapshot": store.snapshot(sym), "show_grades": list(SHOW_GRADES),
                "setups": [brief(s) for s in store.setups(symbol=sym, limit=80, **FILT)]}

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
        items = store.notifications(after_id, limit, unread, **FILT)
        for n in items:  # setup ids look like SYMBOL-KIND-…
            kind = (n["setup_id"] or "").split("-")[1] if (n["setup_id"] or "").count("-") else ""
            n["popup"] = (not muted) or kind in ALERT_SETUPS
        return {"items": items, "unread": store.unread_count(**FILT), "muted": muted, "alert_setups": list(ALERT_SETUPS)}

    @app.post("/api/notifications/read")
    def mark_read(ids: list[int] | None = Body(default=None, embed=True)):
        store.mark_read(ids)
        return {"unread": store.unread_count(**FILT)}

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


def mode_of(s):
    """MMXM ids are SYMBOL-MMXM-<intraday|swing>-…; other setups are grouped by their kind."""
    parts = s["id"].split("-")
    return parts[2] if s.get("setup") == "MMXM" and len(parts) > 3 else s.get("setup")


def performance(closed, days=30):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    recent = [s for s in closed if (s.get("stage_t") or "") >= since]

    def stat(group):
        rs = [s["result_r"] for s in group if s.get("result_r") is not None]
        wins = sum(r > 0 for r in rs)
        return {"signals": len(group), "trades": len(rs), "wins": wins,
                "win_rate": round(wins / len(rs) * 100) if rs else None,
                "total_r": round(sum(rs), 1) if rs else 0.0, "avg_r": round(sum(rs) / len(rs), 2) if rs else None}
    out = {"days": days, "all": stat(recent)}
    for m in sorted({mode_of(s) for s in recent}):
        out[m] = stat([s for s in recent if mode_of(s) == m])
    return out


def brief(s):
    keys = ("id", "symbol", "setup", "name", "dir", "poi_tf", "confirm_tf", "stage", "stage_t", "key_t", "entry", "stop",
            "tp1", "tp1_name", "rr", "result_r", "score", "grade", "grade_note")
    out = {k: s.get(k) for k in keys}
    out["poi"] = {k: s["poi"].get(k) for k in ("kind", "low", "high")}
    out["last_note"] = s["timeline"][-1]["note"] if s["timeline"] else ""
    out["last_ny"] = s["timeline"][-1]["ny"] if s["timeline"] else ""
    out["first_ny"] = s["timeline"][0]["ny"] if s["timeline"] else ""
    out["mode"] = mode_of(s)
    out["checks"] = len(s.get("checklist") or [])
    out["smt"] = next((c["detail"] for c in s.get("checklist") or [] if c["name"].startswith("SMT")), None)
    return out
