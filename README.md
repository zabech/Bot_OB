# Telegram Bot - Order Block Alert Multi-Pair (Bybit Futures)

Bot Telegram yang memindai **banyak pair sekaligus** di Bybit Futures (linear USDT perpetual),
mendeteksi zona **Order Block** di beberapa timeframe, dan mengirim alert saat harga memasuki
zona dengan konfirmasi reaksi di timeframe lebih kecil.

> Sebelumnya bot ini memakai Binance API, namun diganti ke **Bybit** karena Binance API
> memblokir akses dari banyak data center cloud (termasuk sebagian server Railway).

## Cara Kerja

1. **Pemilihan pair** — bot otomatis ambil **top N pair by volume 24 jam** dari Bybit Futures
   (default top 30), refresh berkala (default tiap 6 jam)
2. **Deteksi zona (HTF)** — default `1D` dan `4H`, dicari order block:
   - Bullish OB (Demand) — candle merah terakhir sebelum lonjakan naik kuat
   - Bearish OB (Supply) — candle hijau terakhir sebelum penurunan kuat
3. **Konfirmasi (LTF)** — default `1H`, alert hanya dikirim jika candle LTF menunjukkan reaksi
   saat harga berada di dalam zona HTF
4. **Batch processing** — pair diproses per-batch (default 5 pair) dengan jeda antar batch
   agar tidak kena rate limit
5. Tiap zona hanya kirim alert sekali (ditandai "mitigated") agar tidak spam

## Environment Variables (set di Railway > Variables)

| Variable | Wajib | Keterangan |
|---|---|---|
| `BOT_TOKEN` | Ya | Token dari @BotFather |
| `CHAT_ID` | Ya | Chat ID Telegram kamu |
| `BYBIT_API_KEY` | Tidak | Tidak wajib untuk data publik (kline, ticker) |
| `BYBIT_API_SECRET` | Tidak | Sama seperti di atas |
| `TOP_N_PAIRS` | Tidak | Default `30` |
| `PAIR_QUOTE` | Tidak | Default `USDT` |
| `BATCH_SIZE` | Tidak | Default `5` |
| `BATCH_DELAY_SECONDS` | Tidak | Default `2` |
| `SYMBOL_REFRESH_HOURS` | Tidak | Default `6` |
| `HTF_LIST` | Tidak | Default `D,240` (1D, 4H) — pisahkan dengan koma, format Bybit |
| `LTF` | Tidak | Default `60` (1H) |
| `LOOKBACK_CANDLES` | Tidak | Default `50` |
| `IMPULSE_MIN_PERCENT` | Tidak | Default `1.5` |
| `MAX_ACTIVE_ZONES_PER_TF` | Tidak | Default `3` |
| `CHECK_INTERVAL_MINUTES` | Tidak | Default `15` |

**Format interval Bybit:** `1` `3` `5` `15` `30` `60` `120` `240` `360` `720` (menit), `D` (hari), `W` (minggu), `M` (bulan)

## Cara Dapat CHAT_ID

1. Chat bot kamu di Telegram, kirim pesan apa saja
2. Buka di browser: `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates`
3. Cari nilai `"chat":{"id": ...}` — itu CHAT_ID kamu

## Command Bot

- `/start` — cek status bot dan jumlah pair yang dipantau
- `/pairs` — lihat daftar pair yang sedang dipantau
- `/zones SYMBOL` — lihat zona order block untuk pair tertentu, contoh: `/zones BTCUSDT`

## Catatan

- Deteksi order block di sini adalah pendekatan umum/sederhana (rule-based), bukan standar baku tunggal.
- Memantau banyak pair sekaligus berarti makin banyak alert — sesuaikan `TOP_N_PAIRS` dan parameter
  deteksi agar tidak membanjiri chat kamu.
- Jangan jadikan satu-satunya basis keputusan trading.

## Deploy ke Railway

1. Push/upload repo ini ke GitHub
2. Railway → New Project → Deploy from GitHub repo → pilih repo ini
3. Tab **Variables** → isi `BOT_TOKEN` dan `CHAT_ID` (wajib)
4. Railway otomatis build & jalankan sesuai `Procfile`

## Jalankan Lokal (opsional, untuk testing)

```bash
pip install -r requirements.txt
export BOT_TOKEN="xxxx"
export CHAT_ID="xxxx"
python main.py
```
