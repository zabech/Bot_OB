"""
Script backtest sederhana untuk strategi Order Block bot ini.

Cara pakai:
    python backtest.py
    python backtest.py --months 3 --pairs 30
    python backtest.py --symbol BTC-USDT-SWAP   # backtest 1 pair saja

Logika:
    1. Ambil data historis N bulan ke belakang untuk tiap pair, di semua HTF + LTF.
    2. "Putar ulang" candle demi candle secara kronologis (rolling window, sama persis
       seperti cara bot live melihat data tiap kali scan).
    3. Tiap kali ada candle HTF yang membentuk order block valid dan harga LTF
       menunjukkan reaksi di dalamnya, itu dicatat sebagai 1 "sinyal".
    4. Sinyal dilacak ke depan: apakah harga mencapai target dulu (WIN) atau
       invalidasi dulu (LOSS), pakai logika identik dengan db.py / check_open_alerts.
    5. Hasil akhir: ringkasan win rate, breakdown per pair, dan per timeframe.

PENTING: script ini memakai fungsi deteksi yang SAMA PERSIS dengan bot live
(diimpor dari ob_core.py), supaya hasil backtest benar-benar merepresentasikan
strategi yang sedang berjalan, bukan implementasi terpisah yang bisa berbeda.

Catatan keterbatasan:
    - OKX endpoint /history-candles punya batas seberapa jauh data bisa diambil
      tergantung timeframe; untuk 1D/4H biasanya tersedia jauh ke belakang.
    - Backtest ini TIDAK memperhitungkan slippage, fee, atau eksekusi order nyata.
    - Hasil masa lalu tidak menjamin hasil masa depan.
"""
import argparse
import logging
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

import ob_core

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ── Parameter default, sama dengan default main.py ──────────────────────────
HTF_LIST_DEFAULT = ["1D", "4H"]
LTF_DEFAULT = "1H"
LOOKBACK_CANDLES = 50
IMPULSE_MIN_PERCENT = 1.5
VOLUME_MULTIPLIER = 1.2
MAX_ACTIVE_ZONES_PER_TF = 3
PAIR_QUOTE = "USDT"
MIN_VOLUME_USD = 5_000_000
MAX_LOOKFORWARD_CANDLES = 200  # batas candle LTF maksimal dilacak ke depan sebelum dianggap "tidak resolved"


def get_backtest_pairs(n: int) -> list:
    logger.info(f"Mengambil top {n} pair by volume...")
    return ob_core.get_top_volume_pairs(n, PAIR_QUOTE, MIN_VOLUME_USD)


def simulate_pair(symbol: str, htf_list: list, ltf: str, months: int) -> list:
    """Jalankan backtest untuk 1 pair, return list of trade results (dict)."""
    end_ts_ms = int(time.time() * 1000)
    start_ts_ms = int((datetime.now(timezone.utc) - timedelta(days=months * 30)).timestamp() * 1000)

    # Ambil seluruh histori LTF sekali (dipakai untuk konfirmasi & lacak hasil ke depan)
    logger.info(f"[{symbol}] Mengambil histori LTF ({ltf})...")
    ltf_df = ob_core.fetch_full_history(symbol, ltf, start_ts_ms, end_ts_ms)
    if ltf_df.empty or len(ltf_df) < LOOKBACK_CANDLES:
        logger.warning(f"[{symbol}] Data LTF tidak cukup, skip.")
        return []

    results = []

    for htf in htf_list:
        logger.info(f"[{symbol}] Mengambil histori HTF ({htf})...")
        htf_df = ob_core.fetch_full_history(symbol, htf, start_ts_ms, end_ts_ms)
        if htf_df.empty or len(htf_df) < LOOKBACK_CANDLES + 10:
            logger.warning(f"[{symbol}] Data HTF {htf} tidak cukup, skip timeframe ini.")
            continue

        seen_zones = set()  # (type, top, bottom) yang sudah pernah disinyalkan, hindari duplikat

        # Rolling window: mulai dari titik dimana ada cukup history (LOOKBACK_CANDLES)
        for end_idx in range(LOOKBACK_CANDLES, len(htf_df)):
            window = htf_df.iloc[end_idx - LOOKBACK_CANDLES:end_idx].reset_index(drop=True)
            zones = ob_core.detect_order_blocks(window, MAX_ACTIVE_ZONES_PER_TF, IMPULSE_MIN_PERCENT, VOLUME_MULTIPLIER)
            if not zones:
                continue

            current_htf_ts = int(htf_df.iloc[end_idx]["ts"])
            current_price = float(htf_df.iloc[end_idx]["close"])

            for zone in zones:
                zone_key = (zone["type"], round(zone["top"], 8), round(zone["bottom"], 8))
                if zone_key in seen_zones:
                    continue  # sudah pernah dicatat sebagai sinyal sebelumnya

                price_in_zone = zone["bottom"] <= current_price <= zone["top"]
                if not price_in_zone:
                    continue

                # Ambil potongan LTF yang sezaman dengan candle HTF ini untuk cek reaksi
                ltf_slice = ltf_df[ltf_df["ts"] <= current_htf_ts].tail(3)
                if len(ltf_slice) < 3:
                    continue
                if not ob_core.ltf_shows_reaction(ltf_slice, zone):
                    continue

                # Sinyal valid -> catat, lalu lacak hasilnya ke depan di data LTF
                seen_zones.add(zone_key)
                invalidation = ob_core.calculate_invalidation(zone)

                # Target: pakai jarak tetap 1.5x risk sebagai proxy sederhana untuk backtest
                # (di live, target pakai zona OB berlawanan; untuk backtest per-timeframe
                # terisolasi ini, dipakai pendekatan R:R tetap agar tetap bisa diukur)
                risk = abs(current_price - invalidation)
                if risk == 0:
                    continue
                if zone["type"] == "bullish":
                    target = current_price + risk * 1.5
                else:
                    target = current_price - risk * 1.5

                outcome = resolve_trade(ltf_df, current_htf_ts, zone["type"], invalidation, target)
                results.append({
                    "symbol": symbol,
                    "htf": htf,
                    "zone_type": zone["type"],
                    "entry_price": current_price,
                    "invalidation": invalidation,
                    "target": target,
                    "outcome": outcome,
                })

    return results


def resolve_trade(ltf_df: pd.DataFrame, signal_ts: int, zone_type: str, invalidation: float, target: float) -> str:
    """Lacak ke depan di data LTF setelah signal_ts: apakah harga hit target dulu
    atau invalidasi dulu. Return 'win', 'loss', atau 'unresolved'."""
    future = ltf_df[ltf_df["ts"] > signal_ts].head(MAX_LOOKFORWARD_CANDLES)
    if future.empty:
        return "unresolved"

    for _, c in future.iterrows():
        price_high = float(c["high"])
        price_low = float(c["low"])

        if zone_type == "bullish":
            if price_high >= target:
                return "win"
            if price_low <= invalidation:
                return "loss"
        else:
            if price_low <= target:
                return "win"
            if price_high >= invalidation:
                return "loss"

    return "unresolved"


def print_summary(all_results: list):
    if not all_results:
        print("\nTidak ada sinyal yang terbentuk selama periode backtest.")
        return

    df = pd.DataFrame(all_results)
    total = len(df)
    win = (df["outcome"] == "win").sum()
    loss = (df["outcome"] == "loss").sum()
    unresolved = (df["outcome"] == "unresolved").sum()
    resolved = win + loss
    win_rate = (win / resolved * 100) if resolved > 0 else 0

    print("\n" + "=" * 50)
    print("HASIL BACKTEST")
    print("=" * 50)
    print(f"Total sinyal     : {total}")
    print(f"Win              : {win}")
    print(f"Loss             : {loss}")
    print(f"Unresolved       : {unresolved} (belum hit target/invalidasi sampai akhir data)")
    print(f"Win rate         : {win_rate:.1f}% (dari {resolved} sinyal yang resolved)")

    print("\n--- Breakdown per Timeframe ---")
    for htf, group in df.groupby("htf"):
        g_resolved = group[group["outcome"] != "unresolved"]
        g_win = (g_resolved["outcome"] == "win").sum()
        g_total_resolved = len(g_resolved)
        g_wr = (g_win / g_total_resolved * 100) if g_total_resolved > 0 else 0
        print(f"  {htf}: {len(group)} sinyal, win rate {g_wr:.1f}% ({g_win}/{g_total_resolved} resolved)")

    print("\n--- Breakdown per Pair (top 10 by jumlah sinyal) ---")
    pair_counts = df.groupby("symbol").size().sort_values(ascending=False).head(10)
    for symbol, count in pair_counts.items():
        sub = df[df["symbol"] == symbol]
        sub_resolved = sub[sub["outcome"] != "unresolved"]
        sub_win = (sub_resolved["outcome"] == "win").sum()
        sub_total_resolved = len(sub_resolved)
        sub_wr = (sub_win / sub_total_resolved * 100) if sub_total_resolved > 0 else 0
        print(f"  {symbol}: {count} sinyal, win rate {sub_wr:.1f}%")

    print("\nCatatan: target pada backtest memakai R:R tetap 1.5x risk (bukan zona OB")
    print("berlawanan seperti versi live), karena perhitungan lintas-zona butuh konteks")
    print("seluruh pasangan timeframe yang sulit direplikasi persis secara historis.")
    print("Hasil ini adalah estimasi kasar performa pola deteksi, bukan simulasi 1:1 bot live.")
    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="Backtest strategi Order Block")
    parser.add_argument("--months", type=int, default=3, help="Jumlah bulan data historis (default: 3)")
    parser.add_argument("--pairs", type=int, default=30, help="Jumlah pair top-volume untuk dites (default: 30)")
    parser.add_argument("--symbol", type=str, default=None, help="Backtest 1 pair spesifik saja, contoh: BTC-USDT-SWAP")
    parser.add_argument("--htf", type=str, default=",".join(HTF_LIST_DEFAULT), help="Daftar HTF dipisah koma (default: 1D,4H)")
    parser.add_argument("--ltf", type=str, default=LTF_DEFAULT, help="LTF untuk konfirmasi (default: 1H)")
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
            logger.error(f"[{symbol}] Gagal backtest: {e}")

    print_summary(all_results)


if __name__ == "__main__":
    main()
