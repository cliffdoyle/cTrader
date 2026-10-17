---
name: ctrader-mcp
description: 'Use when working with the local cTrader / Pepperstone MCP server (127.0.0.1:9876): fetching candles or trendbars, symbols, opening charts, drawing chart objects (rectangles, lines, FVG, BOS/CHoCH), reading indicators, ICT/smart-money analysis, the prompt.md stages, or backtesting. Holds the verified tool catalog, safety rules, quirks, a PowerShell client and a findings log to update.'
argument-hint: 'What to do with cTrader (e.g. "fetch H4 GBPJPY", "continue Stage 1")'
---

# cTrader MCP (local)

Quick access to everything verified about the user's local cTrader MCP server, plus the rules for using it. The analysis workflow itself lives in [prompt.md](../../../prompt.md). The scanner strategy being built is **top-down market structure (W/D levels → 4H/1H structure, BOS/CHoCH, OBs, FVGs, liquidity → 15m/5m confirmation)**, from two Smart Risk videos summarised in [strategy-top-down.md](./references/strategy-top-down.md) and [strategy-3-setups.md](./references/strategy-3-setups.md). SMT divergence was dropped by the user (2026-10-17) — do not use it. The exact rules live in [strategy-spec.md](./references/strategy-spec.md) (v0.2, approved) — it overrides the video notes where they differ.

**The live scanner** is built in [scanner/](../../../scanner/README.md): Python engine + FastAPI dashboard on http://127.0.0.1:8765, data from this MCP server, SQLite in `%LOCALAPPDATA%\ictscan`, auto-started by the Task Scheduler task "ICT Scanner". Change rules in `scanner/ictscan/engine/`, parameters in `scanner/ictscan/config.py`; restart with `Stop-ScheduledTask "ICT Scanner"; Start-ScheduledTask "ICT Scanner"`.

## Hard safety rules
1. READ AND DRAW ONLY. Never call tools marked **BLOCKED** in [tools.md](./references/tools.md) (orders, positions, cBots, `clear_chart_objects`). If a task needs one, stop and tell the user.
2. Never delete or modify objects the agent did not create. Agent-created objects are prefixed `MCP_` by the server (user-approved substitute for `AI_`).
3. Never invent prices or candles; compute from fetched data and print index, NY time and price.
4. Report all times in America/New_York (DST-aware). Server data is UTC; the platform UI shows UTC+2.

## How to call the server
The tools are not native chat tools here; call them over HTTP:

```powershell
. '<workspace>\.github\skills\ctrader-mcp\scripts\ctrader.ps1'
$bars = Invoke-CTrader 'get_trendbars' @{ symbolName = 'EURUSD'; timeframe = 'h4'; from = '2026-01-01T00:00:00Z'; to = '2026-10-17T00:00:00Z'; limit = 150 }
ConvertTo-NewYorkTime $bars.bars[0].timestamp
```

[ctrader.ps1](./scripts/ctrader.ps1) handles session init, refuses blocked tools, and parses results with `-DateKind String`. For heavy computation (swings, FVGs, backtests), save fetched JSON to the session `files/` folder and process it in Python or PowerShell.

[ict_analysis.py](./scripts/ict_analysis.py) implements the prompt.md definitions (N=2 swings, trend, BOS/CHoCH, displacement FVGs with fill status, sweeps, dealing range, ATR, Stage 1 scoring): `python ict_analysis.py <bars.json> <out_dir>`. Reuse it so every stage applies identical rules.

[stage2_setups.py](./scripts/stage2_setups.py) finds Stage 2 entry setups on H1/M15/M5 and reports status (pending/triggered/stopped/target hit/missed): `python stage2_setups.py [--htf-sweep] <stage1_bars.json> <stage2_bars.json> <out_dir> US30:bearish GBPJPY:bullish` (run from the scripts folder).

## Key tools (full list in [tools.md](./references/tools.md))
| Need | Tool |
|---|---|
| Candles (≤1000/request, newest kept when truncated) | `get_trendbars` |
| Symbol lookup / pip size | `get_symbols`, `get_symbol_details` |
| Quote / sessions / time | `get_spot_prices`, `get_symbol_sessions`, `get_server_time` |
| Charts | `open_chart`, `list_charts`, `focus_chart`, `get_active_chart` |
| Draw / list objects | `add_chart_object`, `get_chart_objects`, `delete_chart_object` (own objects only) |
| View | `get_chart_viewport`, `zoom_chart`, `scroll_chart` |
| Indicators | `listAvailableIndicators`, `listChartIndicators`, `getIndicatorValues` |

## Drawing procedure
1. `open_chart` or `focus_chart` the target symbol/timeframe.
2. `get_active_chart` immediately before drawing; abort if it is not the expected chart.
3. `get_chart_objects` to count objects (max 15 per chart per prompt.md).
4. `add_chart_object` with ISO UTC times; two-point objects need price+time for both points. Use a `text` object next to shapes to carry the `AI_<type>_<tf>` label.
5. `get_chart_objects` again and confirm the new id. Geometry cannot be read back; record submitted coordinates and ask the user to check visually.

## Keep this skill current (mandatory)
Whenever you discover something new about this server—a new tool, a parameter quirk, an error, a symbol name, a limit, or a completed stage—append a dated line to [findings.md](./references/findings.md) under the right heading before finishing the turn. If `tools/list` changes, regenerate [tools.md](./references/tools.md) and [tools-schema.json](./references/tools-schema.json). Never store session ids, tokens or account credentials.

## Current state
See **Progress checkpoints** in [findings.md](./references/findings.md). As of 2026-10-17 the live scanner and dashboard are running locally (alerts unvalidated); next: backtest, then phone access via the droplet.
