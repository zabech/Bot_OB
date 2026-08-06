from datetime import datetime, timezone
from typing import Optional
import ob_core

from config import *
from ob_core import calculate_atr as ob_calculate_atr

def get_session_info() -> tuple:
    """
    Tentukan sesi trading saat ini berdasarkan jam UTC.
    Return: (nama_sesi, label_kualitas, emoji_bintang)

    Sesi dan kualitasnya:
    - London-NY Overlap (13:00-16:00 UTC) → Premium ⭐⭐⭐
    - London (07:00-13:00 UTC)            → Aktif   ⭐⭐
    - New York (16:00-22:00 UTC)          → Aktif   ⭐⭐
    - Asia (22:00-07:00 UTC)              → Rendah  ⭐
    """

    hour_utc = datetime.now(timezone.utc).hour

    in_london = SESSION_LONDON_START <= hour_utc < SESSION_LONDON_END
    in_ny = SESSION_NY_START <= hour_utc < SESSION_NY_END
    in_overlap = in_london and in_ny

    if in_overlap:
        return ("London-NY Overlap", "Premium", "⭐⭐⭐")
    elif in_london:
        return ("London", "Aktif", "⭐⭐")
    elif in_ny:
        return ("New York", "Aktif", "⭐⭐")
    else:
        return ("Asia", "Rendah", "⭐")


def calculate_atr(candles, period: int) -> Optional[float]:
    """Hitung ATR dari list of dict candle."""
    if hasattr(candles, 'to_dict'):
        candles = candles.to_dict("records")    
    return ob_calculate_atr(candles, period)

def calculate_ma(candles, period: int) -> Optional[float]:
    """Hitung Moving Average dari close price N candle terakhir."""
    if hasattr(candles, 'to_dict'):
        candles = candles.to_dict("records")
    if len(candles) < period:
        return None
    closes = [c["close"] if isinstance(c, dict) else float(c["close"]) for c in candles[-period:]]
    return sum(closes) / len(closes)

def get_current_price(symbol: str) -> Optional[float]:
    """Ambil harga terakhir pair dari endpoint ticker OKX."""
    try:
        data = ob_core.okx_get("/api/v5/market/ticker", {"instId": symbol})
        return float(data["data"][0]["last"])
    except Exception:
        return None

def trend_allows_zone(zone: dict, current_price: float, htf_candles) -> bool:
    """
    Filter trend: cek apakah arah zona OB searah dengan trend MA50 HTF.
    - Bullish OB valid hanya kalau harga di atas MA50 (uptrend / area demand)
    - Bearish OB valid hanya kalau harga di bawah MA50 (downtrend / area supply)
    Kalau USE_TREND_FILTER=false atau MA tidak bisa dihitung, lewatkan filter ini.
    """
    if not USE_TREND_FILTER:
        return True

    if isinstance(htf_candles, list):
        candles_list = htf_candles
    else:
        candles_list = htf_candles.to_dict("records") if hasattr(htf_candles, 'to_dict') else list(htf_candles)

    ma = calculate_ma(candles_list, MA_PERIOD)
    if ma is None:
        return True  # tidak cukup data, jangan blokir

    return (
    (zone["type"] == "bullish" and current_price > ma)
    or
    (zone["type"] == "bearish" and current_price < ma)
    )
