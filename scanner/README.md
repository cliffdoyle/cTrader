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

## Data and restarts
Candles, setups, and notifications are stored in `%LOCALAPPDATA%\ictscan\ictscan.db` (outside OneDrive); the log is
`ictscan.log` in the same folder. After a restart the scanner fills the candles it missed, replays them, and records what
happened to each setup while it was offline (those notifications are marked "while the scanner was offline").
The first ever scan backfills about 7 days of history and marks those notifications as read.

## Layout
- `ictscan/engine/` — structure, FVGs, order blocks, liquidity, sweeps (`core.py`), W/D key levels (`levels.py`),
  setup detectors (`setups.py`), top-down summary (`topdown.py`).
- `ictscan/data.py`, `mcp.py`, `store.py`, `runner.py` — data sync, cTrader client, SQLite, scan loop.
- `ictscan/web/` — FastAPI app and static pages (Lightweight Charts by TradingView).
- Tunable parameters: `ictscan/config.py`.
