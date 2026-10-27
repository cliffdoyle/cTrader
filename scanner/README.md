# ICT Scanner

A read-only market scanner for the top-down market-structure strategy in
[strategy-spec.md](../.github/skills/ctrader-mcp/references/strategy-spec.md). It reads candles from the cTrader
desktop app's local MCP server, detects setups S1–S4, and shows the analysis, setups, and notifications on a local dashboard.
It never places orders.

## Run
- Dashboard: <http://127.0.0.1:8765>
- Starts automatically at logon (Task Scheduler task **ICT Scanner**, with a 10-minute watchdog). cTrader must be open and
  logged in, with Settings → MCP server → *Enable MCP server* ticked. Recommended: untick *Allow trading via MCP*.
- Install / reinstall: `powershell -ExecutionPolicy Bypass -File install-autostart.ps1`
- Run manually: `%LOCALAPPDATA%\ictscan\venv\Scripts\python.exe -m ictscan` from this folder.
- Stop: `Stop-ScheduledTask "ICT Scanner"`; remove: `Unregister-ScheduledTask "ICT Scanner"`.

## Run on a server (cTrader Open API, no desktop app)
Set `ICTSCAN_SOURCE=openapi` and the scanner reads candles from the cTrader Open API (JSON over WebSocket,
`ictscan/openapi.py`) instead of the desktop app, so it can run 24/7 on a Linux server. It is read-only: only the
view scope (`accounts`) is requested and only read messages can be sent.
1. Register an app at <https://openapi.ctrader.com/apps> (view permission only) and add the redirect URL
   `http://localhost:8765/oauth/callback` (and/or `https://<your-domain>/oauth/callback`).
2. Put `CTRADER_CLIENT_ID`, `CTRADER_CLIENT_SECRET` and `CTRADER_ACCOUNT_ID` in `openapi.env` in the data folder (or the
   environment), open the dashboard at `/oauth/start` and allow access. Tokens are saved to `openapi-tokens.json` and renew
   automatically (the refresh token rotates, so only **one** scanner may use a token file at a time).
3. Check: `python -m ictscan.openapi` (prints the account and compares recent H1 bars with the desktop app if it is open).
4. Docker: `cd deploy && cp .env.example .env` → fill in → `docker compose up -d --build`. Data lives in the
   `ictscan-data` volume; set `ICTSCAN_PASSWORD` whenever the dashboard is reachable from the internet.

Tests: `python -m unittest discover -s tests -v` (from this folder).

**Live deployment (2026-10-22):** <https://scanner.zuristay.com> (login user `cliff`; password in
`%LOCALAPPDATA%\ictscan\server-login.txt` on the PC). Droplet 143.110.219.173, code in `/root/ictscan`, container `ictscan`
(`cd /root/ictscan/deploy && docker compose -f docker-compose.yml -f docker-compose.zuristay.yml up -d --build` to update),
served through the ZuriStay Caddy. The server owns the rotating Open API tokens (in the `deploy_ictscan-data` volume); do not
run a second Open API scanner with the same tokens. Logs: `docker exec ictscan tail -f /data/ictscan.log`.

## Data and restarts
Candles, setups, and notifications are stored in `%LOCALAPPDATA%\ictscan\ictscan.db` (outside OneDrive); the log is
`ictscan.log` in the same folder. After a restart the scanner fills the candles it missed, replays them, and records what
happened to each setup while it was offline (those notifications are marked "while the scanner was offline").
The first ever scan backfills about 7 days of history and marks those notifications as read.

## Layout
- `ictscan/engine/` — structure, FVGs, order blocks, liquidity, sweeps (`core.py`), W/D key levels (`levels.py`),
  setup detectors (`setups.py`), top-down summary (`topdown.py`).
- `ictscan/data.py`, `mcp.py`, `openapi.py`, `source.py`, `store.py`, `runner.py` — data sync, cTrader clients (desktop MCP /
  Open API), SQLite, scan loop.
- `ictscan/web/` — FastAPI app and static pages (Lightweight Charts by TradingView).
- Tunable parameters: `ictscan/config.py`.

## Symbols and grades
Scans NAS100, US30, US500 (S&P 500 CFD), EURUSD, GBPUSD, AUDUSD, USDCAD, USDJPY, XAUUSD and GBPJPY (`SYMBOLS` in
`config.py`; add the digits in `web/static/common.js` and a cost in `backtest.py` when adding one). Every setup gets a
grade (`grade()` in `engine/setups.py`):
- **A** — with the 4H and 1H trend, daily trend not against it, POI at a weekly/daily key level, and no other check failed
  (pending checks such as R:R before the entry is known do not count against it).
- **B** — with the 4H trend and daily not against it, but a check failed. **C** — against the 4H or the daily trend.

The dashboard lists and notifies only the grades in `SHOW_GRADES` (default `("A",)`); all setups are still stored.
News is not checked — look at the economic calendar before acting.

## MMXM — the main strategy (2026-10-24)
The user's chosen model: ICT market maker buy/sell models with SMT across NAS100/US30/US500, rules in
[strategy-mmxm.md](../.github/skills/ctrader-mcp/references/strategy-mmxm.md). Detector `engine/mmxm.py` (the sell model
is the buy logic on price-mirrored series), dashboard records `engine/mmxm_live.py`, backtest
`python -m ictscan.mmxm_backtest [--db hist-indices.db]`. Built as specified (both timeframes, stop below the MSS
FVG) except that every stop is at least `min_stop_pips` from its entry (NAS100 15, US30 20, US500 20 points, in
`engine/mmxm.py`); MMXM alerts pop up while S1–S4 stay muted (`ALERT_SETUPS`). Backtest (Oct 2025 – Oct 2026, costs
included) with that minimum stop: +83R over 541 trades (max drawdown −60R; the old tight stop: −3R, max drawdown −111R).
Before the minimum stop:
as specified swing +0.93R/trade (113 trades, but only +0.2R without the 3 best) and intraday −0.25R (428 trades); the
swing version with the stop beyond the reversal extreme was the only one positive in both halves after capping outliers.
The dashboard shows only NAS100/US30/US500 and only MMXM signals (`DASHBOARD_SYMBOLS` / `DASHBOARD_SETUPS` in
`config.py`; set them to `()` to show everything again — all symbols are still scanned).

## Time cycles (PXH / PXL)
`engine/cycles.py` (after Frank369's time-cycle lecture) builds New York-time cycles — month (D1), week (H4), trading day
(H1) and the six 90-minute windows 07:00–16:00 (M5) — and for the previous cycle gives PXH, PXL, EQ (50 %) and the full
range of the candle that made the high/low. The current cycle is read against it: **PXH held / PXH failed / PXL held /
PXL failed / inside** (holding = price stays beyond the level or inside its candle; failing = closes back through the
candle — a first warning only). Dashboard page **Time cycles** (`/cycles`): order-flow matrix for the three indices,
cycle cards with a PXL–EQ–PXH gauge, and a chart with shaded cycles and each previous cycle's levels drawn only across
the following cycle. On cTrader: `python -m ictscan.draw_cycles NAS100 [--kinds month,week,day,90m] [--dry-run]`
draws PXH/PXL (solid) and EQ (dotted) across the current cycle with short labels, one colour per cycle (week purple,
day blue, 90-min amber, month white), replacing only its own previous drawing. `--boxes` instead draws the latest day's
six 90-minute windows as TradingView-style session boxes — a ≈15 % translucent fill (`#AARRGGBB` colours work in
`add_chart_object`) plus a 70 % outline, one colour per window (07:00 violet, 08:30 amber, 10:00 teal, 11:30 grey, 13:00 blue,
14:30 pink; the forming window has a dashed outline and spans to its end), labelled "08:30 · 163" (start · range); up to 18
objects, own record file.

**New York session plan** (`engine/nyplan.py`, overview page): for each index, the next/current NY session (after 16:00
it plans the next trading day) — a lean from four votes (week and day cycle reads, D1 and 4H structure; needs a 2-vote
margin, else "two-sided"), the six 90-minute windows (done / live / next with each window's high/low, countdown), the
nearest untouched liquidity above and below (session levels + day/week PXH/PXL, equal prices merged), and two if/then
scenarios — "Sell the buy-side raid" and "Buy the sell-side raid" with trigger, MMXM entry, targets and invalidation —
ordered by the lean (primary / alternative). The lean only orders the scenarios; it is not a forecast.

**Draw from the dashboard**: the Time cycles page, each symbol page and each NY-plan card have a "Draw on cTrader" toolbar
(PXH/PXL levels with week/day/90-min/month ticks, 90-min boxes, session levels, SMT, clear). The browser sends the request
to the scanner running on this PC (`http://127.0.0.1:8765/api/draw`, also from the hosted dashboard — the PC endpoint only
accepts the dashboard origins in `DRAW_ORIGINS` and answers the Chrome/Edge private-network preflight; allow "local network
access" if the browser asks). `ictscan/drawing.py` picks the chart (active chart of the symbol, else its open chart brought to
the front, else a new chart), re-checks the active chart before every object and keeps per-chart records, so redrawing
replaces only the scanner's own `MCP_` objects. The server copy (Open API) has no drawing endpoint.

**Chart add-ons without MCP** ([../addons/README.md](../addons/README.md)): a cTrader indicator and an MT5 expert (draws
only) download `GET /api/feed/<symbol>?groups=boxes,cycles,levels,smt&kinds=…&token=<ICTSCAN_FEED_TOKEN>` every minute
and draw the same objects as the buttons above. `ictscan/feed.py` turns `drawing.plan_for` / `plan_smt` output into plain
pipe-separated lines (MQL5 has no JSON parser), maps broker names (USTEC → NAS100 …) and caches 20 s. The feed has its own
read-only key and is the only path that bypasses the dashboard login.

## SMT divergence (NAS100 · US30 · US500)
`engine/smt.py`, computed every scan (store meta `smt`, `/api/smt`, overview and Time cycles pages):
- **Cycle SMT** — for the 90-minute, day and week cycles: which indices took the previous cycle's PXH / PXL. Some took the
  high and some did not = bearish SMT (sell the weaker one that held); mirror at the lows = bullish SMT.
- **Swing SMT** on M5 / M15 / H1 — consecutive swing highs (lows) compared across the three indices around the same times
  (±2 bars; within 0.1 × ATR counts as equal, EQH/EQL, and is ignored). Status: *watch* (divergence, no shift yet),
  *confirmed* (an index closed through the swing between the two points — the market structure shift),
  *absorbed* (every index went on to a new extreme).
SMT is a warning that a reversal may be coming, not an entry; the MMXM rules still need the 5m shift + FVG.
**Verdict** (`smt_verdict`, top of the SMT section): every live SMT is ranked (Week 5 › Day 4 › 90-min = 1H 3 › 15m 2 › 5m 1).
Step 1 *direction* = the strongest SMT of weight ≥ 3; step 2 *timing* = a 5m/15m SMT the same way that formed after it;
step 3 *entry* = the MMXM signal on the index that refused (weak one at highs, strong one at lows). Opposite SMTs of weight
≥ 3 are shown as a caution.
`draw smt` draws only the direction SMT (label at this index's cycle high/low: "▼ Day SMT (took PXH)" / "(held PXH)") and
the timing SMT (line between this index's two swing points + "▼ 5m SMT LH").

**Live prices**: the Open API client streams spot quotes for the three indices; the dashboard polls `/api/live` every
second (price, forming M5/M15/H1 candle, signal distance, cycle countdowns). Quotes older than 120 s count as closed
market (no forming bar). Signals still come from closed 5-minute candles (scan starts ~5 s after each close).

## Today's key levels and daily lean
`engine/session.py` adds, for every symbol, the current New York trading day's (rolls at 17:00 NY) key levels — PWH/PWL,
PDH/PDL, Asia (20:00–00:00), London (02:00–05:00) and New York AM (09:30–11:30) highs/lows, midnight and 09:30 opens — with
"untouched" / "taken at …" status, plus a daily lean from six inputs (D1/4H/1H structure, price vs midnight open, London
direction, Asia-range raid; lean needs a 2-vote margin) and the nearest untouched liquidity above/below with random-walk
odds. Shown on each symbol page ("Today's key levels & bias", chart layer "Today's levels").
Draw them on the active cTrader chart: `python -m ictscan.draw_levels NAS100` (replaces only its own previous drawing;
best run after London, around 09:00 NY).
Evidence (2026-10-22, saved year, NAS100 and US30): none of the lean inputs predicted the 09:30–16:00 direction better than
40–57%, and "nearest level is hit first" (88–95%) matched random-walk odds (87–89%) — the lean is context, not a forecast.

## Stops and trade management
Stops go beyond the structural level by 0.25 × H1 ATR (never less than the spread buffer) and at least 1 × H1 ATR from the
entry; at +1R the scanner sends a "move stop to break-even" alert and tracks the trade at break-even (`stop_buffer_atr`,
`stop_min_atr`, `be_at_r` in `config.py`; set all three to 0 for the old tight stops). Chosen by a stop study on the
backtest trades (design Apr–Jun, validation Jul–Sep) and confirmed by a full engine backtest: −0.22R → −0.09R per trade,
worst trade −18R → −1.25R, and grade A became the best grade (+0.16R over 26 trades — too few to call an edge).

## Status and research tools
Backtests (`python -m ictscan.backtest fetch|run`) and an event study (`python -m ictscan.research events|analyze`) found no
mechanical edge in setups S1–S4 on the original six markets, so setup pop-ups are muted and the dashboard is used as a top-down
analysis tool. Results are on the dashboard's Backtest page and in `%LOCALAPPDATA%\ictscan` (`backtest-*.json`,
`research-analysis.txt`). The validation (2026-06-17 → 2026-09-11) and sealed (→ 2026-10-17) periods are still unused.
