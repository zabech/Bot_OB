import time
import logging
import ob_core
from config import *

from market_utils import get_macro_regime

logger = logging.getLogger(__name__)

# top_pairs_cache hidup di config.py (satu sumber state).
# Jangan definisikan ulang di sini — akan men-shadow import dari config.

macro_regime_cache = {
    "regime": None,
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

def get_current_macro_regime() -> str | None:
    """
    Ambil regime market makro saat ini (cached, refresh tiap MACRO_REFRESH_MINUTES).
    Return "bullish" / "bearish" / None (kalau USE_MACRO_FILTER mati atau data kurang).
    """
    if not USE_MACRO_FILTER:
        return None

    now = time.time()
    if macro_regime_cache["regime"] is not None and \
       (now - macro_regime_cache["last_refresh"]) < MACRO_REFRESH_MINUTES * 60:
        return macro_regime_cache["regime"]

    try:
        # Ambil cukup candle untuk MA_PERIOD + buffer
        candles = fetch_klines_df(MACRO_SYMBOL, MACRO_TIMEFRAME, MACRO_MA_PERIOD + 10)
        regime = get_macro_regime(candles, MACRO_MA_PERIOD)
        if regime is not None:
            macro_regime_cache["regime"] = regime
            macro_regime_cache["last_refresh"] = now
            logger.info(f"[MACRO] Regime market ({MACRO_SYMBOL} {MACRO_TIMEFRAME} MA{MACRO_MA_PERIOD}): {regime}")
        else:
            logger.warning("[MACRO] Data candle kurang untuk hitung regime, filter dilewati sementara")
        return regime
    except Exception as e:
        logger.error(f"[MACRO] Gagal ambil regime market: {e}")
        return macro_regime_cache["regime"]  # fallback ke cache lama kalau ada

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
        require_liquidity_sweep=REQUIRE_LIQUIDITY_SWEEP,
        mitigation_50pct=MITIGATION_50PCT,
        swing_lookback=SWING_LOOKBACK,
        use_atr_impulse=USE_ATR_IMPULSE,
        impulse_atr_multiplier=IMPULSE_ATR_MULTIPLIER,
        direction_filter=DIRECTION_FILTER
    )

def ltf_shows_reaction(ltf_data, zone: dict) -> bool:
    if hasattr(ltf_data, 'to_dict'):
        ltf_data = ltf_data.to_dict("records")
    return ob_core.ltf_shows_reaction_advanced(ltf_data, zone)  # <-- PAKAI YANG ADVANCED
    
def calculate_sl_with_atr(zone: dict, current_price: float,
                           htf_candles: list) -> tuple:
    """
    Hitung Stop Loss berdasarkan ATR pair di HTF:
    - SL = invalidasi ± (ATR × ATR_MULTIPLIER)
    - Kalau ATR tidak tersedia (data kurang), fallback ke buffer flat SL_BUFFER_PERCENT
    - Kalau ATR-based SL lebih kecil dari flat buffer, pakai yang lebih besar (lebih aman)

    Return: (sl_price, sl_method) — sl_method = "ATR" atau "buffer"
    """
    invalidation = ob_core.calculate_invalidation(zone)
    
    # ⬇️ PERBAIKAN DI SINI ⬇️
    # Pastikan menggunakan ob_core.calculate_atr
    try:
        atr = ob_core.calculate_atr(htf_candles, ATR_PERIOD)
    except AttributeError:
        # Fallback jika fungsi tidak tersedia
        atr = None
        logger.warning("ob_core.calculate_atr tidak tersedia, gunakan fallback buffer")
    # ⬆️ PERBAIKAN DI SINI ⬆️

    # SL flat buffer (fallback)
    if zone["type"] == "bullish":
        sl_flat = invalidation * (1 - SL_BUFFER_PERCENT / 100)
    else:
        sl_flat = invalidation * (1 + SL_BUFFER_PERCENT / 100)

    if atr is None:
        return (sl_flat, "buffer")

    # SL berbasis ATR
    if zone["type"] == "bullish":
        sl_atr = invalidation - atr * ATR_MULTIPLIER
        sl = min(sl_atr, sl_flat)
    else:
        sl_atr = invalidation + atr * ATR_MULTIPLIER
        sl = max(sl_atr, sl_flat)

    return (sl, "ATR")
