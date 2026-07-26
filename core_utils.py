import time
import logging
import ob_core
from config import *

logger = logging.getLogger(__name__)

top_pairs_cache = {
    "symbols": [],
    "last_refresh": 0,
}

def get_top_volume_pairs(n: int, quote: str) -> list:
    return ob_core.get_top_volume_pairs(n, quote, MIN_VOLUME_USD)

def get_active_symbols() -> list:
    """Refresh daftar top pair tiap SYMBOL_REFRESH_HOURS, selain itu pakai cache."""
    now = time.time()
    if not top_pairs_cache["symbols"] or (now - top_pairs_cache["last_refresh"]) > SYMBOL_REFRESH_HOURS * 3600:
        try:
            symbols = get_top_volume_pairs(TOP_N_PAIRS, PAIR_QUOTE)
            top_pairs_cache["symbols"] = symbols
            top_pairs_cache["last_refresh"] = now
            logger.info(f"Daftar top {len(symbols)} pair di-refresh: {symbols[:5]}...")
        except Exception as e:
            logger.error(f"Gagal refresh daftar pair: {e}")
    return top_pairs_cache["symbols"]

def fetch_klines_df(symbol: str, interval: str, limit: int):
    """Fetch klines dan return sebagai list of dict (bukan DataFrame) untuk konsistensi."""
    data = ob_core.fetch_klines_df(symbol, interval, limit)
    # Selalu konversi ke list of dict agar tidak ada ambiguitas pandas Series
    if hasattr(data, 'to_dict'):
        return data.to_dict("records")
    return data

def detect_order_blocks(candles, max_zones: int) -> list:
    """Wrapper untuk ob_core.detect_order_blocks dengan parameter dari konfigurasi."""
    # Pastikan input selalu list of dict
    if hasattr(candles, 'to_dict'):
        candles = candles.to_dict("records")
    return ob_core.detect_order_blocks(
        candles, max_zones, IMPULSE_MIN_PERCENT, VOLUME_MULTIPLIER,
        require_bos=REQUIRE_BOS,
        require_fvg=REQUIRE_FVG,
        mitigation_50pct=MITIGATION_50PCT,
        swing_lookback=SWING_LOOKBACK,
        use_atr_impulse=USE_ATR_IMPULSE,
        impulse_atr_multiplier=IMPULSE_ATR_MULTIPLIER
    )

def ltf_shows_reaction(ltf_data, zone: dict) -> bool:
    if hasattr(ltf_data, 'to_dict'):
        ltf_data = ltf_data.to_dict("records")
    return ob_core.ltf_shows_reaction_advanced(ltf_data, zone)  # <-- PAKAI YANG ADVANCED
