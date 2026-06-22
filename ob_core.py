"""
Modul inti: semua fungsi murni untuk fetch data OKX dan deteksi order block.
Dipakai bersama oleh main.py (bot live) dan backtest.py (simulasi historis),
supaya logika deteksi di backtest BENAR-BENAR identik dengan yang dipakai live.
"""
import time
import logging
import requests
import pandas as pd
from typing import Optional

logger = logging.getLogger(__name__)

OKX_BASE_URL = "https://www.okx.com"
REQUEST_TIMEOUT = 10

# Default retry config, bisa di-override dari luar (misal main.py set ini dari env var)
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 2


def okx_get(path: str, params: dict, max_retries: Optional[int] = None, backoff_seconds: Optional[float] = None) -> dict:
    """Request ke OKX API dengan retry otomatis (exponential backoff)."""
    max_retries = max_retries if max_retries is not None else DEFAULT_MAX_RETRIES
    backoff_seconds = backoff_seconds if backoff_seconds is not None else DEFAULT_BACKOFF_SECONDS
    last_error = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(f"{OKX_BASE_URL}{path}", params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0":
                raise RuntimeError(f"OKX API error: {data.get('msg')}")
            return data
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = backoff_seconds * (2 ** attempt)
                logger.warning(f"Request gagal ({e}), retry dalam {wait:.0f}s [percobaan {attempt + 1}/{max_retries}]")
                time.sleep(wait)
    raise last_error


def get_top_volume_pairs(n: int, quote: str, min_volume_usd: float) -> list:
    """Ambil n pair USDT-margined perpetual swap dengan volume 24h tertinggi,
    skip pair dengan volume di bawah min_volume_usd."""
    data = okx_get("/api/v5/market/tickers", {"instType": "SWAP"})
    tickers = data.get("data", [])
    filtered = [
        t for t in tickers
        if t["instId"].endswith(f"-{quote}-SWAP") and float(t.get("volCcy24h", 0)) >= min_volume_usd
    ]
    filtered.sort(key=lambda t: float(t.get("volCcy24h", 0)), reverse=True)
    return [t["instId"] for t in filtered[:n]]


def fetch_klines_df(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    """Ambil kline/candle terbaru (maks 300 per request, batasan OKX)."""
    data = okx_get("/api/v5/market/candles", {"instId": symbol, "bar": interval, "limit": limit})
    rows = data.get("data", [])
    rows = list(reversed(rows))  # OKX kembalikan terbaru dulu -> balik jadi kronologis
    df = pd.DataFrame(rows, columns=[
        "ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"
    ])
    for col in ["open", "high", "low", "close", "vol"]:
        df[col] = df[col].astype(float)
    if not df.empty:
        df["ts"] = df["ts"].astype("int64")
    return df


def fetch_klines_history_df(symbol: str, interval: str, after_ts: Optional[str] = None, limit: int = 300) -> pd.DataFrame:
    """Ambil kline historis (lebih jauh ke belakang) lewat endpoint /history-candles OKX,
    yang mendukung paging via parameter 'after' (timestamp ms, exclusive upper bound)."""
    params = {"instId": symbol, "bar": interval, "limit": limit}
    if after_ts:
        params["after"] = after_ts
    data = okx_get("/api/v5/market/history-candles", params)
    rows = data.get("data", [])
    rows = list(reversed(rows))
    df = pd.DataFrame(rows, columns=[
        "ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"
    ])
    for col in ["open", "high", "low", "close", "vol"]:
        df[col] = df[col].astype(float)
    if not df.empty:
        df["ts"] = df["ts"].astype("int64")
    return df


def fetch_full_history(symbol: str, interval: str, start_ts_ms: int, end_ts_ms: int) -> pd.DataFrame:
    """Ambil seluruh data historis dari start_ts_ms sampai end_ts_ms (ms epoch),
    dengan paging otomatis karena OKX membatasi maksimal 300 candle per request."""
    all_rows = []
    cursor_after = str(end_ts_ms)

    while True:
        df_page = fetch_klines_history_df(symbol, interval, after_ts=cursor_after, limit=300)
        if df_page.empty:
            break
        all_rows.append(df_page)
        oldest_ts = int(df_page.iloc[0]["ts"])
        if oldest_ts <= start_ts_ms:
            break
        cursor_after = str(oldest_ts)
        time.sleep(0.2)  # jaga-jaga rate limit saat paging banyak halaman

    if not all_rows:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "vol"])

    full_df = pd.concat(all_rows, ignore_index=True)
    full_df = full_df.drop_duplicates(subset="ts").sort_values("ts").reset_index(drop=True)
    full_df = full_df[full_df["ts"] >= start_ts_ms].reset_index(drop=True)
    return full_df


def detect_order_blocks(df: pd.DataFrame, max_zones: int, impulse_min_percent: float, volume_multiplier: float) -> list:
    """
    Deteksi order block dengan 2 filter kualitas:
    1. Filter volume: candle OB harus punya volume >= volume_multiplier x rata-rata volume window.
    2. Filter unmitigated: OB yang sudah pernah ditembus penuh oleh harga setelah terbentuk dibuang.
    """
    zones = []
    n = len(df)
    avg_volume = df["vol"].mean()

    for i in range(n - 3):
        candle = df.iloc[i]
        is_bearish_candle = candle["close"] < candle["open"]
        is_bullish_candle = candle["close"] > candle["open"]

        future = df.iloc[i + 1:i + 4]
        if future.empty:
            continue

        if candle["vol"] < avg_volume * volume_multiplier:
            continue

        zone_top = float(candle["high"])
        zone_bottom = float(candle["low"])
        after_candle = df.iloc[i + 1:]

        if is_bearish_candle:
            move_pct = (future["high"].max() - candle["close"]) / candle["close"] * 100
            if move_pct >= impulse_min_percent:
                already_mitigated = (after_candle["close"] < zone_bottom).any()
                if not already_mitigated:
                    zones.append({
                        "type": "bullish", "top": zone_top, "bottom": zone_bottom,
                        "index": i, "mitigated": False,
                    })

        if is_bullish_candle:
            move_pct = (candle["close"] - future["low"].min()) / candle["close"] * 100
            if move_pct >= impulse_min_percent:
                already_mitigated = (after_candle["close"] > zone_top).any()
                if not already_mitigated:
                    zones.append({
                        "type": "bearish", "top": zone_top, "bottom": zone_bottom,
                        "index": i, "mitigated": False,
                    })

    zones.sort(key=lambda z: z["index"], reverse=True)
    return zones[:max_zones]


def ltf_shows_reaction(ltf_df: pd.DataFrame, zone: dict) -> bool:
    recent = ltf_df.tail(3)
    for _, c in recent.iterrows():
        price_in_zone = zone["bottom"] <= c["close"] <= zone["top"] or zone["bottom"] <= c["open"] <= zone["top"]
        if not price_in_zone:
            continue
        if zone["type"] == "bullish" and c["close"] > c["open"]:
            return True
        if zone["type"] == "bearish" and c["close"] < c["open"]:
            return True
    return False


def merge_zone_state(old_zones: list, new_zones: list) -> list:
    for new_zone in new_zones:
        for old_zone in old_zones:
            if (old_zone["type"] == new_zone["type"]
                    and abs(old_zone["top"] - new_zone["top"]) < 1e-6
                    and abs(old_zone["bottom"] - new_zone["bottom"]) < 1e-6):
                new_zone["mitigated"] = old_zone["mitigated"]
    return new_zones


def calculate_invalidation(zone: dict) -> float:
    if zone["type"] == "bullish":
        return zone["bottom"]
    return zone["top"]


def find_nearest_opposite_target(zone: dict, current_price: float, all_zones_for_symbol: dict) -> Optional[float]:
    opposite_type = "bearish" if zone["type"] == "bullish" else "bullish"
    candidates = []

    for tf_zones in all_zones_for_symbol.values():
        for z in tf_zones:
            if z["type"] != opposite_type:
                continue
            if zone["type"] == "bullish" and z["bottom"] > current_price:
                candidates.append(z["bottom"])
            elif zone["type"] == "bearish" and z["top"] < current_price:
                candidates.append(z["top"])

    if not candidates:
        return None
    if zone["type"] == "bullish":
        return min(candidates)
    return max(candidates)


def calculate_risk_reward(zone: dict, current_price: float, target: Optional[float]) -> str:
    invalidation = calculate_invalidation(zone)
    risk = abs(current_price - invalidation)
    if risk == 0:
        return "N/A"
    if target is None:
        return "N/A (target tidak tersedia)"
    reward = abs(target - current_price)
    ratio = reward / risk
    return f"1:{ratio:.1f}"
