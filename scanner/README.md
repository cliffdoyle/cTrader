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
