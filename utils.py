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

def drop_unclosed_last_candle(candles):
    """
    Buang candle TERAKHIR dari hasil fetch_klines_df/fetch_klines_history_df.

    OKX selalu menaruh candle yang sedang berjalan (belum close) sebagai
    elemen paling akhir di response endpoint /market/candles. Kalau candle
    itu ikut dipakai untuk deteksi order block, hasilnya bisa membentuk
    OB "hantu" yang cuma valid selama candle-nya belum selesai — begitu
    close beneran, bentuknya sering berubah dan OB itu tidak akan pernah
    kelihatan lagi di data historis/backtest.

    Pakai fungsi ini SEBELUM data candle HTF/LTF dipakai untuk deteksi
    pola (detect_order_blocks, dsb), supaya konsisten dengan backtest.py
    yang selalu bekerja di atas candle yang sudah pasti final.
    """
    if candles is None:
        return candles
    if hasattr(candles, 'iloc'):
        return candles.iloc[:-1] if len(candles) > 1 else candles
    return candles[:-1] if len(candles) > 1 else candles

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

def merge_zone_state(old_zones: list, new_zones: list) -> list:
    return ob_core.merge_zone_state(old_zones, new_zones)

def format_duration(entry_time_str: str) -> str:
    """Hitung durasi dari entry_time sampai sekarang dan format menjadi string."""
    try:
        from datetime import datetime, timezone
        
        # Parse entry_time
        entry_time = datetime.fromisoformat(entry_time_str)
        now = datetime.now(timezone.utc)
        
        # Jika entry_time naive (tanpa timezone), asumsikan UTC
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)
        
        delta = now - entry_time
        
        total_seconds = delta.total_seconds()
        days = int(total_seconds // 86400)
        hours = int((total_seconds % 86400) // 3600)
        minutes = int((total_seconds % 3600) // 60)
        
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        elif hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"
    except Exception:
        return "N/A"
