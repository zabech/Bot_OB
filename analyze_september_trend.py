"""
analyze_september_trend.py — Analisa retrospektif sinyal September 2026:
untuk tiap sinyal (bullish/bearish), hitung apakah entry-nya SEARAH atau
KEBALIKAN trend MA50 HTF pada saat sinyal itu dibuat (walau trend filter
belum aktif waktu itu), plus persentase bullish vs bearish.

Pakai fetch_full_history_raw (dari backtest.py) supaya betul-betul
mengambil candle HISTORIS pada titik waktu sinyal dibuat — bukan candle
"sekarang" seperti fetch_klines_df biasa.

Cara pakai (di VPS):
    cd /root/Bot_OB
    source venv/bin/activate
    python3 analyze_september_trend.py
"""

import time

# PENTING: config harus di-import PERTAMA — config.py yang memanggil
# load_dotenv() untuk memuat .env. db.py membaca DATABASE_URL langsung
# saat di-import, jadi kalau db diimpor duluan, .env belum termuat.
from config import MA_PERIOD
import db
from market_utils import calculate_ma
from backtest import fetch_full_history_raw

TF_MS = {
    "15m": 15 * 60 * 1000,
    "1H": 60 * 60 * 1000,
    "4H": 4 * 60 * 60 * 1000,
    "1D": 24 * 60 * 60 * 1000,
}


def get_september_signals():
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol, zone_type, htf, entry_price, created_at
                FROM alerts
                WHERE EXTRACT(YEAR FROM created_at) = 2026
                  AND EXTRACT(MONTH FROM created_at) = 9
                  AND status != 'open'
                ORDER BY created_at;
            """)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def main():
    signals = get_september_signals()
    print(f"Total sinyal September (selesai, bukan open): {len(signals)}\n")

    bullish = sum(1 for s in signals if s["zone_type"] == "bullish")
    bearish = len(signals) - bullish
    total = len(signals) or 1
    print(f"Bullish OB : {bullish} ({bullish/total*100:.1f}%)")
    print(f"Bearish OB : {bearish} ({bearish/total*100:.1f}%)\n")

    aligned = 0
    counter = 0
    no_data = 0

    print(f"{'Symbol':<18} {'Arah':<8} {'HTF':<4} {'Entry':<14} {'MA50':<14} {'Hasil'}")
    print("-" * 75)

    for s in signals:
        htf = s["htf"]
        bar_ms = TF_MS.get(htf, TF_MS["4H"])
        entry_ts_ms = int(s["created_at"].timestamp() * 1000)

        # Ambil cukup candle ke belakang untuk MA_PERIOD + buffer
        start_ts = entry_ts_ms - bar_ms * (MA_PERIOD + 20)
        end_ts = entry_ts_ms

        try:
            candles = fetch_full_history_raw(s["symbol"], htf, start_ts, end_ts)
        except Exception as e:
            print(f"{s['symbol']:<18} GAGAL fetch: {e}")
            no_data += 1
            continue

        # Hanya candle yang sudah close SEBELUM/PAS entry (no lookahead)
        candles = [c for c in candles if c["ts"] <= entry_ts_ms]

        ma = calculate_ma(candles, MA_PERIOD)
        if ma is None:
            print(f"{s['symbol']:<18} data MA kurang ({len(candles)}/{MA_PERIOD} candle)")
            no_data += 1
            continue

        entry_price = float(s["entry_price"])
        zone_type = s["zone_type"]

        if zone_type == "bullish":
            is_aligned = entry_price > ma
        else:
            is_aligned = entry_price < ma

        if is_aligned:
            aligned += 1
            hasil = "SEARAH trend"
        else:
            counter += 1
            hasil = "KEBALIKAN trend"

        print(f"{s['symbol']:<18} {zone_type:<8} {htf:<4} {entry_price:<14.8g} {ma:<14.8g} {hasil}")

        time.sleep(0.15)  # hindari rate limit OKX

    classified = aligned + counter
    print("\n" + "=" * 75)
    print("RINGKASAN TREND ALIGNMENT")
    print("=" * 75)
    if classified:
        print(f"Searah trend MA50    : {aligned} ({aligned/classified*100:.1f}%)")
        print(f"Kebalikan trend MA50 : {counter} ({counter/classified*100:.1f}%)")
    if no_data:
        print(f"Tidak bisa dihitung (data kurang/gagal fetch): {no_data}")


if __name__ == "__main__":
    main()
