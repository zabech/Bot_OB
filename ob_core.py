"""
Modul inti: semua fungsi murni untuk fetch data OKX dan deteksi order block.
Dipakai bersama oleh main.py (bot live) dan backtest.py (simulasi historis).

Kompatibel dengan dan tanpa pandas:
- main.py di Railway: pandas tersedia, fungsi fetch return DataFrame
- backtest.py di Termux: pandas tidak tersedia, fungsi fetch return list of dict
"""
import time
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

# Coba import pandas — tidak wajib (backtest.py jalan tanpa pandas)
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

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
    """Ambil n pair USDT-margined perpetual swap dengan volume 24h tertinggi."""
    data = okx_get("/api/v5/market/tickers", {"instType": "SWAP"})
    tickers = data.get("data", [])
    filtered = [
        t for t in tickers
        if t["instId"].endswith(f"-{quote}-SWAP") and float(t.get("volCcy24h", 0)) >= min_volume_usd
    ]
    filtered.sort(key=lambda t: float(t.get("volCcy24h", 0)), reverse=True)
    return [t["instId"] for t in filtered[:n]]


def _rows_to_records(rows: list) -> list:
    """Konversi raw rows OKX ke list of dict."""
    result = []
    for r in rows:
        result.append({
            "ts": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "vol": float(r[5]),
        })
    return result


def fetch_klines_df(symbol: str, interval: str, limit: int):
    """Ambil kline terbaru. Return DataFrame jika pandas tersedia, list of dict jika tidak."""
    data = okx_get("/api/v5/market/candles", {"instId": symbol, "bar": interval, "limit": limit})
    rows = list(reversed(data.get("data", [])))
    records = _rows_to_records(rows)

    if HAS_PANDAS:
        df = pd.DataFrame(records)
        return df
    return records


def fetch_klines_history_df(symbol: str, interval: str, after_ts: Optional[str] = None, limit: int = 300):
    """Ambil kline historis via /history-candles. Return DataFrame atau list of dict."""
    params = {"instId": symbol, "bar": interval, "limit": limit}
    if after_ts:
        params["after"] = after_ts
    data = okx_get("/api/v5/market/history-candles", params)
    rows = list(reversed(data.get("data", [])))
    records = _rows_to_records(rows)

    if HAS_PANDAS:
        df = pd.DataFrame(records)
        return df
    return records


def fetch_full_history(symbol: str, interval: str, start_ts_ms: int, end_ts_ms: int):
    """Ambil seluruh data historis dengan paging otomatis. Return DataFrame atau list of dict."""
    all_records = []
    cursor_after = str(end_ts_ms)

    while True:
        page = fetch_klines_history_df(symbol, interval, after_ts=cursor_after, limit=300)
        # Normalkan ke list of dict untuk paging logic
        if HAS_PANDAS and hasattr(page, 'empty'):
            if page.empty:
                break
            page_list = page.to_dict("records")
        else:
            if not page:
                break
            page_list = page

        all_records = page_list + all_records
        oldest_ts = page_list[0]["ts"]
        if oldest_ts <= start_ts_ms:
            break
        cursor_after = str(oldest_ts)
        time.sleep(0.2)

    if not all_records:
        if HAS_PANDAS:
            return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "vol"])
        return []

    # Deduplikasi dan filter
    seen = set()
    result = []
    for row in sorted(all_records, key=lambda x: x["ts"]):
        if row["ts"] in seen or row["ts"] < start_ts_ms:
            continue
        seen.add(row["ts"])
        result.append(row)

    if HAS_PANDAS:
        return pd.DataFrame(result)
    return result


def _get_val(row, key):
    """Ambil nilai dari row (dict atau pandas Series)."""
    if isinstance(row, dict):
        return row[key]
    return row[key]  # pandas Series juga support [key]


def detect_order_blocks(data, max_zones: int, impulse_min_percent: float, volume_multiplier: float) -> list:
    """
    Deteksi order block. Menerima DataFrame (pandas) atau list of dict.
    Filter kualitas:
    1. Volume: candle OB >= volume_multiplier x rata-rata
    2. Unmitigated: buang OB yang sudah ditembus harga setelah terbentuk
    """
    # Normalisasi ke list of dict
    if HAS_PANDAS and hasattr(data, 'iterrows'):
        candles = data.to_dict("records")
    else:
        candles = data

    zones = []
    n = len(candles)
    if n == 0:
        return []

    avg_vol = sum(c["vol"] for c in candles) / n

    for i in range(n - 3):
        c = candles[i]
        is_bearish = c["close"] < c["open"]
        is_bullish = c["close"] > c["open"]

        future = candles[i + 1:i + 4]
        if not future:
            continue

        if c["vol"] < avg_vol * volume_multiplier:
            continue

        zone_top = c["high"]
        zone_bottom = c["low"]
        after = candles[i + 1:]

        if is_bearish:
            max_high_future = max(f["high"] for f in future)
            move_pct = (max_high_future - c["close"]) / c["close"] * 100
            if move_pct >= impulse_min_percent:
                mitigated = any(a["close"] < zone_bottom for a in after)
                if not mitigated:
                    zones.append({
                        "type": "bullish", "top": zone_top, "bottom": zone_bottom,
                        "index": i, "mitigated": False,
                    })

        if is_bullish:
            min_low_future = min(f["low"] for f in future)
            move_pct = (c["close"] - min_low_future) / c["close"] * 100
            if move_pct >= impulse_min_percent:
                mitigated = any(a["close"] > zone_top for a in after)
                if not mitigated:
                    zones.append({
                        "type": "bearish", "top": zone_top, "bottom": zone_bottom,
                        "index": i, "mitigated": False,
                    })

    zones.sort(key=lambda z: z["index"], reverse=True)
    return zones[:max_zones]


def ltf_shows_reaction(ltf_data, zone: dict) -> bool:
    """Cek reaksi LTF. Menerima DataFrame atau list of dict."""
    if HAS_PANDAS and hasattr(ltf_data, 'tail'):
        recent = ltf_data.tail(3).to_dict("records")
    else:
        recent = ltf_data[-3:]

    for c in recent:
        in_zone = (zone["bottom"] <= c["close"] <= zone["top"] or
                   zone["bottom"] <= c["open"] <= zone["top"])
        if not in_zone:
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
