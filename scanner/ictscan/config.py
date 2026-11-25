"""Scanner configuration. Values marked ⚙ in strategy-spec.md live here so the backtest can tune them."""
import os
from pathlib import Path

SYMBOLS = {
    # pip = analysis unit (prompt.md); buffer = stop buffer ≈ typical spread (refreshed from live quotes when available)
    "NAS100": {"pip": 1.0, "unit": "pts", "digits": 1, "buffer": 1.5},
    "US30": {"pip": 1.0, "unit": "pts", "digits": 1, "buffer": 2.5},
    "EURUSD": {"pip": 0.0001, "unit": "pips", "digits": 5, "buffer": 0.0001},
    "GBPUSD": {"pip": 0.0001, "unit": "pips", "digits": 5, "buffer": 0.00015},
    "XAUUSD": {"pip": 0.01, "unit": "pips", "digits": 2, "buffer": 0.25},
    "GBPJPY": {"pip": 0.01, "unit": "pips", "digits": 3, "buffer": 0.02},
    "US500": {"pip": 1.0, "unit": "pts", "digits": 1, "buffer": 0.6},
    "AUDUSD": {"pip": 0.0001, "unit": "pips", "digits": 5, "buffer": 0.0001},
    "USDCAD": {"pip": 0.0001, "unit": "pips", "digits": 5, "buffer": 0.00015},
    "USDJPY": {"pip": 0.01, "unit": "pips", "digits": 3, "buffer": 0.015},
}

# Setup grades listed on the dashboard and in notifications (see engine/setups.py `grade`).
# A = with the 4H and 1H trend, daily not against it, POI at a W/D key level, and every other check passes.
# Use ("A", "B", "C") to list everything again.
SHOW_GRADES = ("A",)
# Setups whose notifications pop up even while the old S1–S4 alerts are muted (store meta "alerts_muted").
ALERT_SETUPS = ("MMXM", "M4H")
# What the dashboard shows (all symbols are still scanned and stored). Empty tuple = no restriction.
DASHBOARD_SYMBOLS = ("NAS100", "US30", "US500")
DASHBOARD_SETUPS = ("MMXM",)

TIMEFRAMES = ("w1", "d1", "h4", "h1", "m15", "m5")
# Bars kept in memory per scan (the cache in SQLite can hold more).
WINDOW = {"w1": 200, "d1": 400, "h4": 600, "h1": 1000, "m30": 1500, "m15": 1000, "m5": 2000}
# How often each timeframe is re-fetched (seconds); lower timeframes every scan.
REFRESH = {"w1": 3600, "d1": 900, "h4": 300, "h1": 300, "m15": 300, "m5": 0}

PARAMS = {
    "swing_n": 2,
    "fvg_mult": 1.5,
    "fvg_lookback": 20,
    "equal_tol_atr": 0.1,
    "zone_tol_atr": 0.25,
    "zone_move_atr": 3.0,
    "zone_turn_atr": 2.0,
    "zone_move_bars": 10,
    "zone_recent": {"w1": 26, "d1": 60},
    "zone_min_strength": 2,
    "zone_near_atr_d1": 5.0,
    "room_r": 2.0,
    "min_rr": 2.0,
    "s1_lookback_days": {"h4": 10, "h1": 7},
    "s1_expiry_hours": 72,
    "s1_watch_atr": 1.0,
    "s1_leg_tolerance_atr": 0.5,
    "s3_window_h1_bars": 12,
    "s3_fill_m5_bars": 24,
    "s4_max_gap_atr": 1.0,
    "s4_expiry_hours": 120,
    "setup_lookback_days": 7,
    # Variants under test (backtest); defaults = spec v0.2
    "s1_stop": "leg",          # leg = confirmation-TF leg extreme | ob_far = beyond the order block's far edge
    "s2_stop": "sweep",        # sweep = beyond the M5 sweep extreme | protected = beyond the H1 protected level
    "tp_mode": "liquidity",    # liquidity = nearest opposing liquidity | fixed_2r = 2 × risk
    # Stop placement (stop study 2026-10-20): stop goes beyond the structural level by stop_buffer_atr × H1 ATR
    # (never less than the spread buffer) and at least stop_min_atr × H1 ATR from the entry. 0 / 0 = old tight stops.
    "stop_buffer_atr": 0.25,
    "stop_min_atr": 1.0,
    "be_at_r": 1.0,            # move the stop to the entry once price has gone this many R in favour (0 = off)
    "enabled_setups": ["S1", "S2", "S3", "S4"],
}

# 4H manipulation candle scanner (engine/m4h.py, strategy-m4h.md). Its own market list and timeframes; the existing
# analysis above is unchanged. (asset class, digits, pip, spread, cost): spread = stop buffer (typical spread),
# cost = round trip incl. commission (Razor: FX/metals ≈ $7 per lot), both in price; from live quotes 2026-11-25 × ~1.5.
_M4H = """
EURUSD fx 5 0.0001 0.00002 0.00017   GBPUSD fx 5 0.0001 0.00003 0.00022   AUDUSD fx 5 0.0001 0.00002 0.00017
NZDUSD fx 5 0.0001 0.00003 0.00012   USDJPY fx 3 0.01 0.003 0.017         USDCHF fx 5 0.0001 0.00003 0.00012
USDCAD fx 5 0.0001 0.00003 0.0002    EURGBP fx 5 0.0001 0.00002 0.0001    EURJPY fx 3 0.01 0.01 0.02
EURCHF fx 5 0.0001 0.00003 0.00012   EURAUD fx 5 0.0001 0.00007 0.00018   EURCAD fx 5 0.0001 0.00014 0.00028
EURNZD fx 5 0.0001 0.00019 0.00035   GBPJPY fx 3 0.01 0.006 0.031         GBPCHF fx 5 0.0001 0.00011 0.00024
GBPAUD fx 5 0.0001 0.00014 0.00028   GBPCAD fx 5 0.0001 0.00013 0.00027   GBPNZD fx 5 0.0001 0.00028 0.00049
AUDJPY fx 3 0.01 0.007 0.018         AUDCHF fx 5 0.0001 0.00005 0.00015  AUDCAD fx 5 0.0001 0.00007 0.00018
AUDNZD fx 5 0.0001 0.00009 0.0002    NZDJPY fx 3 0.01 0.01 0.022          NZDCHF fx 5 0.0001 0.00009 0.0002
NZDCAD fx 5 0.0001 0.0001 0.00022    CADJPY fx 3 0.01 0.01 0.022          CADCHF fx 5 0.0001 0.00008 0.00019
CHFJPY fx 3 0.01 0.016 0.031         XAUUSD metal 2 0.01 0.12 0.25        XAGUSD metal 3 0.01 0.03 0.05
NAS100 index 1 1 1.1 1.7             US30 index 1 1 2.1 3.2               US500 index 1 1 0.4 0.6
US2000 index 1 1 0.3 0.45            GER40 index 1 1 2.0 3.0             UK100 index 1 1 1.8 2.7
FRA40 index 1 1 6.0 9.0              EUSTX50 index 1 1 1.7 2.6           JPN225 index 1 1 7.0 10.5
AUS200 index 1 1 1.0 1.5             HK50 index 1 1 5.0 7.5              SpotCrude energy 3 0.01 0.028 0.042
SpotBrent energy 3 0.01 0.032 0.048  NatGas energy 3 0.001 0.004 0.006   BTCUSD crypto 2 1 15 23
ETHUSD crypto 2 0.1 2.0 3.0
"""
_w = _M4H.split()
M4H_SYMBOLS = {_w[k]: {"class": _w[k + 1], "digits": int(_w[k + 2]), "pip": float(_w[k + 3]), "spread": float(_w[k + 4]),
                       "cost": float(_w[k + 5])} for k in range(0, len(_w), 6)}
M4H_TIMEFRAMES = ("d1", "h4", "m30")
M4H_WINDOW = {"d1": 400, "h4": 600, "m30": 1500}
M4H_LOOKBACK_DAYS = 14   # candles re-simulated every scan (older records keep their last stored state)
# Pop-up filter for new M4H candles (user 2026-11-25: a manipulation candle in any session is valid); every candle is
# still stored and listed on /m4h. sessions=() = no session restriction.
M4H_ALERT = {"bias": True, "momentum": True, "sessions": ()}

DATA_DIR = Path(os.environ.get("ICTSCAN_DATA", Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ictscan"))
DB_PATH = DATA_DIR / "ictscan.db"
LOG_PATH = DATA_DIR / "ictscan.log"
MCP_ENDPOINT = os.environ.get("ICTSCAN_MCP", "http://127.0.0.1:9876/mcp/")
# Where candles come from: "mcp" = cTrader desktop app on this PC; "openapi" = cTrader Open API (no desktop app needed,
# e.g. on a server). Open API credentials go in DATA_DIR/openapi.env or environment variables (see openapi.py).
DATA_SOURCE = os.environ.get("ICTSCAN_SOURCE", "mcp").lower()
# 4H manipulation scanner: runs where candles come from the Open API (the droplet); the PC scanner (desktop MCP) skips it.
M4H_ENABLED = os.environ.get("ICTSCAN_M4H", "1" if DATA_SOURCE == "openapi" else "0") == "1"
WEB_HOST = os.environ.get("ICTSCAN_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("ICTSCAN_PORT", "8765"))
# Dashboard login (HTTP Basic). Required whenever the dashboard is reachable from the internet.
DASH_USER = os.environ.get("ICTSCAN_USER", "admin")
DASH_PASSWORD = os.environ.get("ICTSCAN_PASSWORD", "")
# Read-only key for the chart add-ons (cTrader indicator / MT5 expert) that fetch /api/feed/<symbol>. Empty = the feed
# needs the dashboard login instead (or is open when the dashboard has no password, e.g. on the PC).
FEED_TOKEN = os.environ.get("ICTSCAN_FEED_TOKEN", "")
# "Draw on cTrader" buttons: the PC scanner (desktop MCP source) accepts drawing requests on /api/draw from these
# dashboard origins only (the hosted dashboard calls http://127.0.0.1:<port> in the user's browser).
DRAW_BRIDGE = os.environ.get("ICTSCAN_DRAW_BRIDGE", "1" if DATA_SOURCE == "mcp" else "0") == "1"
DRAW_ORIGINS = {o.strip() for o in os.environ.get("ICTSCAN_DRAW_ORIGINS", "https://scanner.zuristay.com").split(",") if o.strip()} | {
    f"http://127.0.0.1:{WEB_PORT}", f"http://localhost:{WEB_PORT}"}
SCAN_DELAY_SECONDS = 5 if DATA_SOURCE == "openapi" else 20  # wait after each M5 close so the broker has the closed candle
