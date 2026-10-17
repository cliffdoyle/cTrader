# cTrader MCP findings log

Append new verified facts here (newest at the bottom of each section). Include the date. Do not record session ids or credentials.

## Environment
- 2026-10-17: Pepperstone cTrader 5.10.20, Demo account, leverage 1:400. Platform clock shows UTC+2.
- 2026-10-17: Local MCP server `ctrader-desktop` 2.0.0 at `http://127.0.0.1:9876/mcp/` (Streamable HTTP, JSON responses, protocol `2025-06-18`). Settings: Enable MCP server ✔, Allow trading via MCP ✔, Require confirmation for trading ✔.
- 2026-10-17: The cTrader tools are NOT exposed as native chat tools in this VS Code session; call the endpoint over HTTP via [ctrader.ps1](../scripts/ctrader.ps1).
- 2026-10-17: Capabilities: `tools`, `resources` (subscribe). `resources/list` returns empty; `resources/templates/list` is not implemented (-32601).

## Data
- 2026-10-17: `get_trendbars` timestamps are ISO UTC with `Z`, bar open time. Response: `{bars:[{timestamp,open,high,low,close,volume}], truncated}`. `truncated:true` appears when `limit` cuts the range; observed behaviour keeps the NEWEST bars (3-day range, limit 5 → last five H1 bars before Friday close), returned in ascending time order. To get older history, page backwards by moving `to`.
- 2026-10-17: Cap 1000 bars per request, default limit 500.
- 2026-10-17: `get_symbol_details` EURUSD: digits 5, pipSize 0.0001, lotSize 100000. `bid`/`ask` were null while the market was closed (weekend).
- 2026-10-17: Broker pipSize: NAS100/US30 1 (digits 1), GBPUSD 0.0001, GBPJPY 0.01, XAUUSD **0.1** (digits 2). prompt.md says gold pip = 0.01, so analysis uses 0.01 (gold "pips" look 10x larger than the broker's).
- 2026-10-17: Bar alignment: D1 bars open 21:00Z = 17:00 NY (EDT); trading date = NY open + 7h. H4 bars open 21/01/05/09/13/17Z (17:00, 21:00, 01:00, 05:00, 09:00, 13:00 NY in EDT). Last weekly bar before weekend: H4 17:00Z Friday.
- 2026-10-17: 150 D1 bars need ~400 calendar days in `from`; 150 H4 bars need ~75 days. With `limit` the newest bars are kept.
- 2026-10-17: Python `zoneinfo` on this machine needs `pip install --user tzdata` (installed).
- 2026-10-17: `m1` timeframe works; NAS100 M1 history reaches at least 2025-10-01. `from`/`to` are inclusive of bar open times (13:25–13:32 returned 8 M1 bars).
- 2026-10-17: YouTube transcripts can be read with `youtube-transcript-api` (installed `--user`); the page HTML itself gives only the title. Titles/channel come from `https://www.youtube.com/oembed?url=<video>&format=json`. Raw transcripts are kept in session `files/video/`.
- 2026-10-17: Scanner detour started. Video sources (all Smart Risk): TiDSqsOTHc8 (3 setups), dnnFb9V9uLI (top-down). pCZ1I1EG_Zw (SMT/session model) was reviewed but REMOVED from the strategy at the user's request. User wants a morning-alert scanner; brainstorming, nothing built yet.
- 2026-10-17: DECISIONS — data via **cTrader Open API** (not the desktop MCP), hosted on the user's **DigitalOcean** account, with a **web dashboard**. Open API facts (help.ctrader.com): OAuth2; scope `accounts` = view-only (trading impossible) — use it; access token ~30 days, refresh token never expires; endpoints `demo.ctraderapi.com` / `live.ctraderapi.com`, port 5035 Protobuf, 5036 JSON (TCP or WebSocket); demo and live need separate connections. Rate limits ~50 req/s general, ~5 req/s historical (verify); max bars per trendbar request undocumented — measure in the connection spike. The local MCP stays useful for drawing detections on charts for visual validation.
- 2026-10-17: Scanner decisions (user form): **shared** existing DO droplet; **demo** account; Open API app **not registered yet**; order block = **video 3 literal** ("the candle that created the inefficiency" — the displacement candle); sweep = **wick OR two-candle close-back**; setups **S1–S4** all in v1 (backtested first); morning digest **07:00 UTC+3**; watchlist unchanged (6 symbols).
- 2026-10-17: Next steps: user registers the Open API app (openapi.ctrader.com → Add new app; status "submitted" until Spotware emails approval; then add redirect URI; the default Playground URI only works in the Playground). Strategy spec draft v0.1 written in [strategy-spec.md](./strategy-spec.md). When deploying to the shared droplet, load the user-level `add-wordpress-site` skill for that droplet's conventions (Docker Compose, Caddy, Cloudflare, backups).
- 2026-10-17: `get_server_time` returns `{utcTime, localTime, unixMs}`.
- 2026-10-17: PowerShell 7 `ConvertFrom-Json` turns ISO strings into DateTime by default; always use `-DateKind String`.

## Charts and drawing
- 2026-10-17: `open_chart` returns `{chartId, symbolName, timeframe, status:"opened"}` and makes it active. Timeframe strings are lowercase (`h1`), but existing charts may report `D1`.
- 2026-10-17: `add_chart_object` has NO name parameter. Server names objects `MCP_<Type>_<guid>` (e.g. `MCP_Rectangle_3e30...`). The user approved this in place of the `AI_` prefix: treat `MCP_` objects as agent-created. Text labels can carry the `AI_` name.
- 2026-10-17: `get_chart_objects` returns only `{objectId,type,name}` — no prices or times. Placement cannot be verified via MCP; record submitted coordinates and ask the user to verify visually.
- 2026-10-17: Drawing acts on the ACTIVE chart only. Confirm with `get_active_chart` immediately before drawing.
- 2026-10-17: `get_chart_viewport` returns `{startIndex, visibleBarsCount, maxStartIndex}`; `zoom_chart {visibleBars}` works.
- 2026-10-17: `text` objects are listed by `get_chart_objects` with type `interactive_text`. `horizontal_line` needs only `price1`. Rectangles accept a `time2` in the future (used Mon 13:00Z while market closed) without error.
- 2026-10-17: Drawing 14 objects with an active-chart check before each took ~1 min per chart; no errors.

## Watchlist symbol names
- 2026-10-17: All six watchlist names exist exactly: NAS100, US30, XAUUSD ("Default Category"), EURUSD, GBPUSD (FX Majors), GBPJPY (FX Crosses). Variants `NAS100-F`, `US30-F` (Indices) and `XAUUSD-F` (Metals) also exist — NOT used; ask before switching.
- 2026-10-17: Other symbols that exist on the broker (not on the watchlist; ask before using): `US500` (also `US500-F`), `US2000`, `GER40`, `XAGUSD`, `EURJPY`, `USDX` (dollar index; no `DXY`).

## Live scanner (built 2026-10-17)
- 2026-10-17: User dropped the Open API for now ("start ASAP"): the scanner reads from this desktop MCP server on this PC. Data source is isolated in `scanner/ictscan/data.py` + `mcp.py` so the Open API can replace it later. User wants notifications **on the dashboard** (no Telegram) and a dashboard showing the top-down analysis and the reason for each setup.
- 2026-10-17: Auto-start: Task Scheduler task "ICT Scanner" (at logon + 10-min watchdog, `pythonw -m ictscan`, venv `%LOCALAPPDATA%\ictscan\venv`); `Pepperstone cTrader.lnk` copied to the user's Startup folder. Restart test passed (resumes from SQLite, no duplicate notifications).
- 2026-10-17: Performance via MCP: first full sync ~37k candles in ~43 s; later scans 8–23 s for 6 symbols × 6 TFs. W1 bars open Sunday 21:00Z, so the current week counts as "forming" until Sunday; D1 bars open 21:00Z.
- 2026-10-17 23:17: cTrader's MCP server stopped answering (HTTP 404 from Windows HTTP.sys on port 9876, owner PID 4) right after the heavy 1-year history download (~500 large get_trendbars calls back to back). cTrader itself kept running. Fix is in cTrader: untick/re-tick "Enable MCP server" or restart cTrader. Pace big downloads. The scanner now reports this as "offline" with that instruction and recovers automatically.
- 2026-10-17: Validated by hand against raw candles: GBPJPY S3 short (H1 10:00 NY candle swept 210.876/210.899, M5 FVG 210.738–210.788, limit filled 12:25) and EURUSD S1 long (entry = M5 CHoCH candle close 1.12282). In-sample last 7 days (not a backtest): S3 50 closed, 21 wins, +10.7R; S1 8 closed, 0 wins; S2 2 closed, 0 wins — S1's stop (M5 leg extreme) looks tight; check in the backtest.

## Progress checkpoints
- 2026-10-17: Step 0 complete. Five EURUSD H1 candles fetched (16 Oct 2026, 12:00–16:00 NY); one cyan rectangle `MCP_Rectangle_3e302c036b3c4f098f8d1cdc6eb07985` drawn on chart `077739a8-3fdd-4a11-bb00-d4fa73e9cce3` (EURUSD H1), price 1.12126–1.12404, 12:00–17:00 NY. User confirmed placement visually. Stage 1 not started.
- 2026-10-17: Stage 1 complete (data to Fri 16 Oct close). Scores: US30 3 (bearish), GBPJPY 3 (bullish), EURUSD 2, GBPUSD 2, NAS100 1, XAUUSD 0. Only US30 and GBPJPY reach the ≥3 cutoff. Computed with [ict_analysis.py](../scripts/ict_analysis.py). Awaiting user go-ahead and interpretation choices before Stage 2.
- 2026-10-17: User said "continue" with Stage 1 rules unchanged. Stage 2 done for US30 (bearish) and GBPJPY (bullish) with [stage2_setups.py](../scripts/stage2_setups.py). Fetch: H1/M15/M5 200 bars each (H1 from −21 d, M15 −7 d, M5 −4 d). 09:30 NY M5 candles fetched individually (`from`=13:30Z, `to`=13:34Z, limit 1) — works exactly.
- 2026-10-17: Result: NO live strict setups on either symbol (all recent ones target-hit, stopped or missed). With `--htf-sweep` (H4 candle sweep counts) one US30 short is open: entry 52021, stop 52624.8, target 51868.1, R:R 0.25 — not drawn.
- 2026-10-17: Drawn (14 objects each, context only): US30 H1 chart `9c215278-5732-415a-b8af-2f413dcc5bcf`, GBPJPY H1 chart `af6293c7-d434-41d9-b256-b8ce4025900f`. Plan/record: session `files/stage2/draw_plan.json`, `draw_record.json`. Stage 3 summary delivered; Stage 4 only after user says "go to Stage 4".

## Analysis interpretation choices (Stage 1, pending user confirmation)
- Trend = last two confirmed swing highs/lows (HH+HL / LH+LL, else range). Bias for criteria 2–5 = H4 trend, falling back to D1 if H4 is range.
- BOS/CHoCH via a structure state machine (closes beyond the latest confirmed swing; CHoCH flips state). This can disagree with the swing-sequence trend after a fresh CHoCH.
- Criterion 3 counts only fully "unfilled" FVGs (partly filled excluded); H4 and D1 FVGs both considered. Displacement = candle-2 body ≥ 1.5× mean body of the 20 candles before it.
- Criterion 4 = sweep (wick beyond intact swing/equal/PDH/PDL/PWH/PWL, close back inside) in last 20 H4, with price now beyond the sweep candle close in the bias direction. Equal levels tolerance 0.1×ATR.
- Dealing range = last confirmed H4 swing high/low, extended to any later extreme. ATR = Wilder 14 on H4.
- Stage 2 setup: entry-TF BOS/CHoCH in bias direction; leg origin = extreme between broken swing and break candle; origin must tap an HTF FVG that was unfilled before the tap, OR sweep (wick beyond intact level, close back inside, same entry-TF candle or 2 before). Entry = 50% of the entry-TF FVG formed in the breaking leg; stop = origin extreme; target = nearest intact opposing liquidity. Same-candle stop/target ambiguity resolved as stop. Optional `--htf-sweep` also accepts the containing H4 candle's sweep.

## Backtest (2026-10-17)
- `python -m ictscan.backtest fetch --months 6` pulled 6 months + warm-up via MCP in ~5 min (M5 ≈ 39k bars/symbol from 2026-04-06; NAS100/US30 have a normal 1h daily break). Stored in `%LOCALAPPDATA%\ictscan\backtest.db` (separate from the live DB). Replay ≈ 1 s per simulated day per symbol (6 processes in parallel).
- Split: in-sample 2026-04-17 → 2026-09-11 (80%); out-of-sample 2026-09-11 → 2026-10-17 is SEALED — run once only after the rules are final (`run --part out-of-sample` refuses a second run).
- In-sample baseline (spec v0.2, costs in backtest.py): 1410 trades, 36.7% wins, gross −0.05R/trade, net −0.26R/trade. S1 net ≈ 0 (cost<0.2R subset +0.006R, 141 trades); S2 12.8% wins (stop at M5 sweep wick, median 8 pips, TP far → structural, not a bug); S3 −0.12R net (921 trades); S4 0/12. 95 trades had costs ≥ 0.5R (stops of a few pips). Checklist score does NOT separate winners (score 7 = 6.8% wins); H4-bias filter did not help S3. By NY hour: 00–05 slightly positive, 05–08 and 14–18 worst (small samples, risk of overfitting).
- Also changed: limit fills now require price to trade beyond the limit (spec §7), in both live and backtest.
- 2026-10-17: User chose variants B (S2 stop at H1 protected level), C (S1 stop beyond OB far edge), E (fixed 2R TP1), F (switch off negatives); declined A (min stop size) and D (killzones). In-sample avg net R: baseline −0.264 · V1 B+C −0.247 (S1 58% wins but −0.06R; S2 −0.49→−0.13) · V2 B+C+E −0.214 · V3 E −0.194 (S3 −0.27→−0.19). EVERY setup is negative in EVERY variant → by rule F nothing qualifies; out-of-sample NOT run (still sealed). Variant switches live in config.PARAMS (s1_stop, s2_stop, tp_mode, enabled_setups); CLI `run --label X --no-publish --set key=value`.

## Strategy research (2026-10-17, "measure first")
- User goal: ONE mechanical, profitable strategy. Pass bar (set before testing): ≥ +0.15R/trade net, PF ≥ 1.3, max losing run < 15R, ≥ 4/6 symbols profitable, 200+ trades. Setup pop-ups muted (store meta `alerts_muted`).
- History: 1 year via MCP (`backtest fetch --months 12`, ~8 min; M5 ≈ 75k bars/symbol from 2025-10-05). Fixed splits in `ictscan/research.py`: research → 2026-06-17, validation → 2026-09-11, sealed → 2026-10-17 (sealed never looked at).
- `python -m ictscan.research events` (86 s) writes `%LOCALAPPDATA%\ictscan\events.jsonl`: every M15/M5 BOS/CHoCH (43k) with causal context (H4/H1 alignment, H1/H4 premium-discount, H1 opposite sweep in prior 12 h, H1/H4 FVG/OB tap in prior 12 h, session) and net R at 1/1.5/2/3R for stops leg / h1prot and a retracement limit at 50% of the leg. `analyze` aggregates the research period only (cost ≤ 0.1R).
- Result (research period): base rate of all breaks ≈ −0.04R/trade at every target. Best of ~3000 filter combos only +0.05…+0.12R (n 170–1070, PF ≤ 1.26) — below the bar and within multiple-testing noise. Most consistent theme: H1 opposite-side sweep + killzone (London 02–05 / NY AM 08:30–11 NY) + 4H/1H alignment, M15 break, leg stop, 3R (≈ +0.05…+0.09R, 4–5/6 symbols). Retracement entries did not fix it.
- 2026-10-17: DECISION — user chose to STOP searching for a mechanical strategy. The scanner stays as a top-down analysis dashboard; setup pop-ups stay muted (meta `alerts_muted`), setups remain listed for study. Unused next ideas if resumed: (1) validate the sweep+killzone+alignment M15 pattern once on validation data, (2) Asian-range sweep at London/NY open + M15 CHoCH, (3) H4/H1 swing version. Validation and sealed periods are still untouched.
