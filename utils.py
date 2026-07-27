import time
import math
from datetime import datetime, timezone

import ob_core
from config import *

def interval_to_seconds(interval: str) -> int:
    """Konversi string interval OKX ke detik."""
    mapping = {
        "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
        "1H": 3600, "2H": 7200, "4H": 14400, "6H": 21600, "12H": 43200,
        "1D": 86400, "1W": 604800,
    }
    return mapping.get(interval, 3600)

def candle_is_closed(candles, interval: str) -> bool:
    """
    Cek apakah candle LTF TERAKHIR (paling baru di array) sudah close.
    PENTING: OKX API biasanya mengembalikan candle yang sedang berjalan (live, belum close)
    sebagai elemen terakhir. Untuk cek apakah ADA candle yang sudah close dan siap dipakai,
    kita cek candle kedua dari belakang (index -2), karena itu yang dijamin sudah selesai.
    """
    if not candles or len(candles) < 2:
        return False
    try:
        if isinstance(candles, list):
            second_last_ts_ms = int(candles[-2]["ts"])
        elif hasattr(candles, 'iloc'):
            second_last_ts_ms = int(candles.iloc[-2]["ts"])
        else:
            return True
        interval_ms = interval_to_seconds(interval) * 1000
        candle_close_time_ms = second_last_ts_ms + interval_ms
        now_ms = int(time.time() * 1000)
        return now_ms >= candle_close_time_ms
    except Exception:
        return True
