"""
Runtime state bot — satu sumber untuk semua state yang berubah saat jalan.

Pisah dari config.py supaya:
- config = konstanta / env saja (immutable setelah load)
- state  = data yang berubah (zones, trades, cache, cooldown)

Modul lain boleh:
  from state import active_trades, active_zones, ...
atau tetap lewat config (re-export) demi kompatibilitas lama.
"""

# Zona aktif per (symbol, timeframe):
# { "BTC-USDT-SWAP": {"1D": [...], "4H": [...]}, ... }
active_zones: dict = {}

# Trade aktif per pair — pair tidak boleh kirim sinyal baru sampai TP/SL tercapai
# Format: {
#   "BTC-USDT-SWAP": {
#       "entry": 65000, "sl": 64200, "tp": 66600,
#       "zone_type": "bullish", "htf": "4H",
#       "entry_time": "...", "breakeven_triggered": False,
#   }
# }
active_trades: dict = {}

# Cache daftar top pair, di-refresh berkala
# { "symbols": [...], "last_refresh": unix_ts }
top_pairs_cache: dict = {
    "symbols": [],
    "last_refresh": 0,
}

# Timestamp alert terakhir per pair (unix seconds) — enforce ALERT_COOLDOWN_MINUTES
# { "BTC-USDT-SWAP": 1712345678.0, ... }
last_alert_times: dict = {}

# Timestamp health alert terakhir — hindari spam notifikasi "bot bermasalah"
last_health_alert_time: dict = {"ts": 0}

# Cache regime market makro (BTC vs MA)
# { "regime": "bullish"|"bearish"|None, "last_refresh": unix_ts }
macro_regime_cache: dict = {
    "regime": None,
    "last_refresh": 0,
}
