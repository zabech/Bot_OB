# Telegram Bot - Order Block Alert Multi-Pair (Binance Futures)

Bot Telegram yang memindai **banyak pair sekaligus** di Binance Futures (USDT-M), mendeteksi zona
**Order Block** di beberapa timeframe, dan mengirim alert saat harga memasuki zona dengan konfirmasi
reaksi di timeframe lebih kecil.

## Cara Kerja

1. **Pemilihan pair** — bot otomatis ambil **top N pair by volume 24 jam** dari Binance Futures (default top 30),
   dan refresh daftar ini secara berkala (default tiap 6 jam) agar selalu mengikuti pair paling aktif.
2. **Deteksi zona (HTF)** — default `1D` dan `4H`, dicari order block:
   - Bullish OB (Demand) — candle merah terakhir sebelum lonjakan naik kuat
   - Bearish OB (Supply) — candle hijau terakhir sebelum penurunan kuat
3. **Konfirmasi (LTF)** — default `1H`, alert hanya dikirim jika candle LTF menunjukkan reaksi
   (mulai berbalik arah) saat harga berada di dalam zona HTF
4. **Batch processing** — pair diproses per-batch (default 5 pair sekaligus) dengan jeda antar batch
   agar tidak kena rate limit Binance API
5. Tiap zona hanya kirim alert sekali (ditandai "mitigated") agar tidak spam

## Environment Variables (set di Railway > Variables)

| Variable | Wajib | Keterangan |
|---|---|---|
| `BOT_TOKEN` | Ya | Token dari @BotFather |
| `CHAT_ID` | Ya | Chat ID Telegram kamu |
| `BINANCE_API_KEY` | Tidak | Tidak wajib untuk data publik |
| `BINANCE_API_SECRET` | Tidak | Sama seperti di atas |
| `TOP_N_PAIRS` | Tidak | Default `30` — jumlah pair top-volume yang dipantau |
| `PAIR_QUOTE` | Tidak | Default `USDT` — hanya pair dengan quote currency ini |
| `BATCH_SIZE` | Tidak | Default `5` — jumlah pair diproses bersamaan per batch |
| `BATCH_DELAY_SECONDS` | Tidak | Default `2` — jeda antar batch (detik) |
| `SYMBOL_REFRESH_HOURS` | Tidak | Default `6` — seberapa sering daftar top pair di-refresh |
| `HTF_LIST` | Tidak | Default `1d,4h` — timeframe tempat zona OB dicari |
| `LTF` | Tidak | Default `1h` — timeframe konfirmasi reaksi harga |
| `LOOKBACK_CANDLES` | Tidak | Default `50` |
| `IMPULSE_MIN_PERCENT` | Tidak | Default `1.5` |
| `MAX_ACTIVE_ZONES_PER_TF` | Tidak | Default `3` |
| `CHECK_INTERVAL_MINUTES` | Tidak | Default `15` — seberapa sering scan semua pair |

## Estimasi Beban API

Tiap siklus scan = `jumlah pair × (jumlah HTF + 1 LTF)` request ke Binance.
Contoh default: 30 pair × 3 timeframe = 90 request per siklus, dibagi batch 5 → 18 batch dengan jeda 2 detik
(~36 detik total, jauh di bawah limit Binance Futures yang cukup longgar untuk endpoint publik).

Kalau ingin pantau lebih banyak pair atau timeframe lebih sering, naikkan `BATCH_DELAY_SECONDS` atau
turunkan `BATCH_SIZE` supaya lebih aman dari rate limit.

## Cara Dapat CHAT_ID

1. Chat bot kamu di Telegram, kirim pesan apa saja
2. Buka di browser: `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates`
3. Cari nilai `"chat":{"id": ...}` — itu CHAT_ID kamu

## Command Bot

- `/start` — cek status bot dan jumlah pair yang dipantau
- `/pairs` — lihat daftar pair yang sedang dipantau
- `/zones SYMBOL` — lihat zona order block untuk pair tertentu, contoh: `/zones BTCUSDT`

## Catatan

Deteksi order block di sini adalah pendekatan umum/sederhana (rule-based), bukan standar baku tunggal.
Memantau banyak pair sekaligus juga berarti makin banyak alert — sesuaikan `TOP_N_PAIRS` dan parameter
deteksi agar tidak membanjiri chat kamu. Jangan jadikan satu-satunya basis keputusan trading.

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
