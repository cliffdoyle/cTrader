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
}

TIMEFRAMES = ("w1", "d1", "h4", "h1", "m15", "m5")
# Bars kept in memory per scan (the cache in SQLite can hold more).
WINDOW = {"w1": 200, "d1": 400, "h4": 600, "h1": 1000, "m15": 1000, "m5": 2000}
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
    "enabled_setups": ["S1", "S2", "S3", "S4"],
}

DATA_DIR = Path(os.environ.get("ICTSCAN_DATA", Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ictscan"))
DB_PATH = DATA_DIR / "ictscan.db"
LOG_PATH = DATA_DIR / "ictscan.log"
MCP_ENDPOINT = os.environ.get("ICTSCAN_MCP", "http://127.0.0.1:9876/mcp/")
WEB_HOST = os.environ.get("ICTSCAN_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("ICTSCAN_PORT", "8765"))
SCAN_DELAY_SECONDS = 20  # wait after each M5 close so the broker has the closed candle
