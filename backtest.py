"""
Script backtest tanpa dependency pandas — kompatibel dengan Termux di Android.
Semua operasi data pakai Python murni (list/dict).

Cara pakai di Termux:
    pip install requests
    python backtest.py
    python backtest.py --symbol BTC-USDT-SWAP --months 1
    python backtest.py --months 3 --pairs 30
"""
import argparse
import logging
import time
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import ob_core

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ── Parameter default ────────────────────────────────────────
HTF_LIST_DEFAULT = ["1D", "4H"]
LTF_DEFAULT = "1H"
LOOKBACK_CANDLES = 50
IMPULSE_MIN_PERCENT = 1.5
VOLUME_MULTIPLIER = 1.2
MAX_ACTIVE_ZONES_PER_TF = 3
PAIR_QUOTE = "USDT"
MIN_VOLUME_USD = 5_000_000
MAX_LOOKFORWARD_CANDLES = 200


def get_backtest_pairs(n: int) -> list:
    logger.info(f"Mengambil top {n} pair by volume...")
    return ob_core.get_top_volume_pairs(n, PAIR_QUOTE, MIN_VOLUME_USD)


# ── Versi "no pandas" dari fungsi fetch ──────────────────────

def fetch_candles_raw(symbol: str, interval: str, limit: int) -> list:
    """Ambil kline terbaru, return list of dict."""
    data = ob_core.okx_get(
        "/api/v5/market/candles",
        {"instId": symbol, "bar": interval, "limit": limit}
    )
    rows = data.get("data", [])
    rows = list(reversed(rows))  # kronologis
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


def fetch_history_raw(symbol: str, interval: str, after_ts: str = None, limit: int = 300) -> list:
    """Ambil kline historis via /history-candles, return list of dict."""
    params = {"instId": symbol, "bar": interval, "limit": limit}
    if after_ts:
        params["after"] = after_ts
    data = ob_core.okx_get("/api/v5/market/history-candles", params)
    rows = data.get("data", [])
    rows = list(reversed(rows))
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


def fetch_full_history_raw(symbol: str, interval: str, start_ts_ms: int, end_ts_ms: int) -> list:
    """Ambil seluruh data historis dengan paging otomatis, return list of dict."""
    all_rows = []
    cursor_after = str(end_ts_ms)

    while True:
        page = fetch_history_raw(symbol, interval, after_ts=cursor_after, limit=300)
        if not page:
            break
        all_rows = page + all_rows  # prepend (page lebih lama di depan)
        oldest_ts = page[0]["ts"]
        if oldest_ts <= start_ts_ms:
            break
        cursor_after = str(oldest_ts)
        time.sleep(0.2)

    # Deduplikasi dan filter by start_ts
    seen = set()
    result = []
    for row in sorted(all_rows, key=lambda x: x["ts"]):
        if row["ts"] in seen:
            continue
        if row["ts"] >= start_ts_ms:
            seen.add(row["ts"])
            result.append(row)
    return result


# ── Deteksi OB tanpa pandas ───────────────────────────────────

def detect_order_blocks_raw(candles: list, max_zones: int) -> list:
    """Deteksi order block dari list of dict, tanpa pandas."""
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

        if c["vol"] < avg_vol * VOLUME_MULTIPLIER:
            continue

        zone_top = c["high"]
        zone_bottom = c["low"]
        after = candles[i + 1:]

        if is_bearish:
            max_high_future = max(f["high"] for f in future)
            move_pct = (max_high_future - c["close"]) / c["close"] * 100
            if move_pct >= IMPULSE_MIN_PERCENT:
                mitigated = any(a["close"] < zone_bottom for a in after)
                if not mitigated:
                    zones.append({
                        "type": "bullish", "top": zone_top, "bottom": zone_bottom,
                        "index": i, "mitigated": False,
                    })

        if is_bullish:
            min_low_future = min(f["low"] for f in future)
            move_pct = (c["close"] - min_low_future) / c["close"] * 100
            if move_pct >= IMPULSE_MIN_PERCENT:
                mitigated = any(a["close"] > zone_top for a in after)
                if not mitigated:
                    zones.append({
                        "type": "bearish", "top": zone_top, "bottom": zone_bottom,
                        "index": i, "mitigated": False,
                    })

    zones.sort(key=lambda z: z["index"], reverse=True)
    return zones[:max_zones]


def ltf_shows_reaction_raw(ltf_candles: list, zone: dict) -> bool:
    """Cek reaksi LTF dari list of dict, tanpa pandas."""
    recent = ltf_candles[-3:]
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


def resolve_trade(ltf_candles: list, signal_ts: int, zone_type: str,
                  invalidation: float, target: float) -> str:
    """Lacak hasil trade ke depan setelah signal_ts."""
    future = [c for c in ltf_candles if c["ts"] > signal_ts][:MAX_LOOKFORWARD_CANDLES]
    if not future:
        return "unresolved"

    for c in future:
        if zone_type == "bullish":
            if c["high"] >= target:
                return "win"
            if c["low"] <= invalidation:
                return "loss"
        else:
            if c["low"] <= target:
                return "win"
            if c["high"] >= invalidation:
                return "loss"

    return "unresolved"


# ── Simulasi per pair ─────────────────────────────────────────

def simulate_pair(symbol: str, htf_list: list, ltf: str, months: int) -> list:
    end_ts_ms = int(time.time() * 1000)
    start_ts_ms = int((datetime.now(timezone.utc) - timedelta(days=months * 30)).timestamp() * 1000)

    logger.info(f"[{symbol}] Mengambil histori LTF ({ltf})...")
    try:
        ltf_candles = fetch_full_history_raw(symbol, ltf, start_ts_ms, end_ts_ms)
    except Exception as e:
        logger.error(f"[{symbol}] Gagal ambil LTF: {e}")
        return []

    if len(ltf_candles) < LOOKBACK_CANDLES:
        logger.warning(f"[{symbol}] Data LTF tidak cukup ({len(ltf_candles)} candle), skip.")
        return []

    results = []

    for htf in htf_list:
        logger.info(f"[{symbol}] Mengambil histori HTF ({htf})...")
        try:
            htf_candles = fetch_full_history_raw(symbol, htf, start_ts_ms, end_ts_ms)
        except Exception as e:
            logger.error(f"[{symbol}] Gagal ambil HTF {htf}: {e}")
            continue

        if len(htf_candles) < LOOKBACK_CANDLES + 10:
            logger.warning(f"[{symbol}] Data HTF {htf} tidak cukup, skip timeframe ini.")
            continue

        seen_zones = set()

        for end_idx in range(LOOKBACK_CANDLES, len(htf_candles)):
            window = htf_candles[end_idx - LOOKBACK_CANDLES:end_idx]
            zones = detect_order_blocks_raw(window, MAX_ACTIVE_ZONES_PER_TF)
            if not zones:
                continue

            current_htf_ts = htf_candles[end_idx]["ts"]
            current_price = htf_candles[end_idx]["close"]

            for zone in zones:
                zone_key = (zone["type"], round(zone["top"], 8), round(zone["bottom"], 8))
                if zone_key in seen_zones:
                    continue

                if not (zone["bottom"] <= current_price <= zone["top"]):
                    continue

                ltf_slice = [c for c in ltf_candles if c["ts"] <= current_htf_ts][-3:]
                if len(ltf_slice) < 3:
                    continue
                if not ltf_shows_reaction_raw(ltf_slice, zone):
                    continue

                seen_zones.add(zone_key)

                risk = abs(current_price - (zone["bottom"] if zone["type"] == "bullish" else zone["top"]))
                if risk == 0:
                    continue

                if zone["type"] == "bullish":
                    target = current_price + risk * 1.5
                    invalidation = zone["bottom"]
                else:
                    target = current_price - risk * 1.5
                    invalidation = zone["top"]

                outcome = resolve_trade(ltf_candles, current_htf_ts, zone["type"], invalidation, target)
                results.append({
                    "symbol": symbol,
                    "htf": htf,
                    "zone_type": zone["type"],
                    "entry_price": current_price,
                    "outcome": outcome,
                })

    return results


# ── Rekap hasil ───────────────────────────────────────────────

def print_summary(all_results: list):
    if not all_results:
        print("\nTidak ada sinyal yang terbentuk selama periode backtest.")
        return

    total = len(all_results)
    win = sum(1 for r in all_results if r["outcome"] == "win")
    loss = sum(1 for r in all_results if r["outcome"] == "loss")
    unresolved = sum(1 for r in all_results if r["outcome"] == "unresolved")
    resolved = win + loss
    win_rate = (win / resolved * 100) if resolved > 0 else 0

    print("\n" + "=" * 50)
    print("HASIL BACKTEST")
    print("=" * 50)
    print(f"Total sinyal     : {total}")
    print(f"Win              : {win}")
    print(f"Loss             : {loss}")
    print(f"Unresolved       : {unresolved}")
    print(f"Win rate         : {win_rate:.1f}% (dari {resolved} sinyal resolved)")

    # Breakdown per timeframe
    print("\n--- Breakdown per Timeframe ---")
    by_htf = defaultdict(list)
    for r in all_results:
        by_htf[r["htf"]].append(r)
    for htf, group in sorted(by_htf.items()):
        resolved_g = [r for r in group if r["outcome"] != "unresolved"]
        wins_g = sum(1 for r in resolved_g if r["outcome"] == "win")
        wr_g = (wins_g / len(resolved_g) * 100) if resolved_g else 0
        print(f"  {htf}: {len(group)} sinyal, win rate {wr_g:.1f}% ({wins_g}/{len(resolved_g)} resolved)")

    # Breakdown per pair (top 10)
    print("\n--- Pair Paling Sering Alert (top 10) ---")
    by_symbol = defaultdict(list)
    for r in all_results:
        by_symbol[r["symbol"]].append(r)
    top_symbols = sorted(by_symbol.items(), key=lambda x: len(x[1]), reverse=True)[:10]
    for symbol, group in top_symbols:
        resolved_g = [r for r in group if r["outcome"] != "unresolved"]
        wins_g = sum(1 for r in resolved_g if r["outcome"] == "win")
        wr_g = (wins_g / len(resolved_g) * 100) if resolved_g else 0
        print(f"  {symbol}: {len(group)} sinyal, win rate {wr_g:.1f}%")

    print("\nCatatan: target pakai R:R tetap 1.5x risk (estimasi kasar).")
    print("Hasil ini bukan simulasi 1:1 bot live.")
    print("=" * 50)


# ── Main ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Backtest strategi Order Block (no pandas)")
    parser.add_argument("--months", type=int, default=3, help="Jumlah bulan historis (default: 3)")
    parser.add_argument("--pairs", type=int, default=30, help="Jumlah top pair (default: 30)")
    parser.add_argument("--symbol", type=str, default=None, help="1 pair spesifik, contoh: BTC-USDT-SWAP")
    parser.add_argument("--htf", type=str, default=",".join(HTF_LIST_DEFAULT), help="HTF dipisah koma (default: 1D,4H)")
    parser.add_argument("--ltf", type=str, default=LTF_DEFAULT, help="LTF konfirmasi (default: 1H)")
    args = parser.parse_args()

    htf_list = args.htf.split(",")

    if args.symbol:
        symbols = [args.symbol]
    else:
        symbols = get_backtest_pairs(args.pairs)

    logger.info(f"Backtest {len(symbols)} pair, {args.months} bulan, HTF={htf_list}, LTF={args.ltf}")

    all_results = []
    for i, symbol in enumerate(symbols, 1):
        logger.info(f"[{i}/{len(symbols)}] Memproses {symbol}...")
        try:
            results = simulate_pair(symbol, htf_list, args.ltf, args.months)
            all_results.extend(results)
            logger.info(f"[{symbol}] {len(results)} sinyal ditemukan.")
        except Exception as e:
            logger.error(f"[{symbol}] Gagal: {e}")
        time.sleep(0.5)  # jeda kecil antar pair agar tidak kena rate limit

    print_summary(all_results)


if __name__ == "__main__":
    main()
