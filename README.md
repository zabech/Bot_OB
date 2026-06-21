# Telegram Bot - Order Block Alert Multi-Pair (OKX Futures)

Bot Telegram yang memindai **banyak pair sekaligus** di OKX Futures (USDT-margined perpetual swap),
mendeteksi zona **Order Block** di beberapa timeframe, dan mengirim alert saat harga memasuki
zona dengan konfirmasi reaksi di timeframe lebih kecil.

> Bot ini sempat memakai Binance lalu Bybit, namun keduanya memblokir akses API dari IP Amerika
> Serikat (termasuk sebagian besar server Railway). OKX terbukti lebih permisif soal lokasi
> untuk endpoint publik (data harga/candle).

## Cara Kerja

1. **Pemilihan pair** — bot otomatis ambil **top N pair by volume 24 jam** dari OKX Swap market
   (default top 30), refresh berkala (default tiap 6 jam)
2. **Deteksi zona (HTF)** — default `1D` dan `4H`, dicari order block dengan 2 filter kualitas:
   - Bullish OB (Demand) — candle merah terakhir sebelum lonjakan naik kuat
   - Bearish OB (Supply) — candle hijau terakhir sebelum penurunan kuat
   - **Filter volume** — candle OB harus punya volume ≥ `VOLUME_MULTIPLIER` × rata-rata volume
     di window yang dianalisis, menyaring OB dari candle dengan partisipasi pasar lemah
   - **Filter unmitigated** — OB yang sudah pernah ditembus penuh oleh harga (close menembus
     sisi berlawanan zona) setelah terbentuk akan dibuang otomatis, karena sudah tidak relevan
     lagi sebagai zona supply/demand aktif
3. **Konfirmasi (LTF)** — default `1H`, alert hanya dikirim jika candle LTF menunjukkan reaksi
   saat harga berada di dalam zona HTF
4. **Batch processing** — pair diproses per-batch (default 5 pair) dengan jeda antar batch
   agar tidak kena rate limit
5. **Kontrol jumlah alert**:
   - **Filter volume minimum pair** — pair dengan volume 24h di bawah `MIN_VOLUME_USD` di-skip
     dari scan (safety net tambahan selain top-N by volume)
   - **Cooldown per pair** — setelah satu pair kirim alert, pair itu tidak akan kirim alert lagi
     selama `ALERT_COOLDOWN_MINUTES`, meskipun ada zona OB lain yang valid pada saat itu
6. **Reliability**:
   - **Retry otomatis** — request API yang gagal (timeout, gangguan jaringan, rate limit sementara)
     dicoba ulang otomatis dengan exponential backoff (`API_MAX_RETRIES` kali percobaan)
   - **Health alert** — kalau dalam satu siklus scan banyak pair gagal dicek (≥ `FAILURE_ALERT_THRESHOLD_PERCENT`),
     bot kirim 1 notifikasi peringatan ke Telegram (dengan cooldown sendiri agar tidak spam),
     sehingga kamu tahu kalau bot "diam karena bermasalah" vs "diam karena memang tidak ada sinyal"
7. **Info risk/reward di alert** — tiap alert menyertakan:
   - **Invalidasi** — harga yang membuat zona OB ini batal (bottom zona untuk bullish, top zona untuk bearish)
   - **Target terdekat** — zona OB berlawanan terdekat di sisi yang relevan (lintas semua HTF pair tersebut),
     sebagai referensi kasar, bukan rekomendasi entry/exit. Kalau tidak ada zona berlawanan yang valid,
     ditulis "tidak tersedia"
   - **Estimasi R:R** — rasio kasar jarak ke target dibanding jarak ke invalidasi
8. **Histori & tracking performa** — tiap alert dicatat ke database PostgreSQL. Tiap siklus scan,
   bot mengecek semua alert yang masih "open": apakah harga sudah mencapai target (`hit_target`)
   atau malah menembus invalidasi (`invalidated`). Gunakan `/stats` untuk lihat ringkasan win rate
   dan pair paling sering muncul alert.
9. Tiap zona hanya kirim alert sekali (ditandai "mitigated") agar tidak spam

## Environment Variables (set di Railway > Variables)

| Variable | Wajib | Keterangan |
|---|---|---|
| `BOT_TOKEN` | Ya | Token dari @BotFather |
| `CHAT_ID` | Ya | Chat ID Telegram kamu |
| `DATABASE_URL` | Ya | Otomatis di-inject Railway saat addon PostgreSQL ditambahkan & ter-link |
| `TOP_N_PAIRS` | Tidak | Default `30` |
| `PAIR_QUOTE` | Tidak | Default `USDT` |
| `BATCH_SIZE` | Tidak | Default `5` |
| `BATCH_DELAY_SECONDS` | Tidak | Default `2` |
| `SYMBOL_REFRESH_HOURS` | Tidak | Default `6` |
| `MIN_VOLUME_USD` | Tidak | Default `5000000` ($5 juta) — skip pair dengan volume 24h di bawah ini |
| `ALERT_COOLDOWN_MINUTES` | Tidak | Default `60` — jeda minimum antar alert untuk pair yang sama |
| `API_MAX_RETRIES` | Tidak | Default `3` — jumlah percobaan ulang request API yang gagal |
| `API_RETRY_BACKOFF_SECONDS` | Tidak | Default `2` — jeda awal retry, dikali 2 tiap percobaan (2s, 4s, 8s...) |
| `FAILURE_ALERT_THRESHOLD_PERCENT` | Tidak | Default `50` — kirim health alert jika % pair gagal ≥ ini |
| `HEALTH_ALERT_COOLDOWN_MINUTES` | Tidak | Default `60` — jeda minimum antar health alert |
| `HTF_LIST` | Tidak | Default `1D,4H` — pisahkan dengan koma, format OKX |
| `LTF` | Tidak | Default `1H` |
| `LOOKBACK_CANDLES` | Tidak | Default `50` |
| `IMPULSE_MIN_PERCENT` | Tidak | Default `1.5` |
| `VOLUME_MULTIPLIER` | Tidak | Default `1.2` — candle OB butuh volume ≥ 1.2x rata-rata window |
| `MAX_ACTIVE_ZONES_PER_TF` | Tidak | Default `3` |
| `CHECK_INTERVAL_MINUTES` | Tidak | Default `15` |

**Format interval OKX:** `1m 3m 5m 15m 30m 1H 2H 4H 6H 12H 1D 1W 1M` (huruf besar untuk jam/hari/minggu/bulan)

**Format symbol OKX:** `BTC-USDT-SWAP`, `ETH-USDT-SWAP`, dst (bukan `BTCUSDT` seperti exchange lain)

## Cara Dapat CHAT_ID

1. Chat bot kamu di Telegram, kirim pesan apa saja
2. Buka di browser: `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates`
3. Cari nilai `"chat":{"id": ...}` — itu CHAT_ID kamu

## Command Bot

- `/start` — cek status bot dan jumlah pair yang dipantau
- `/pairs` — lihat daftar pair yang sedang dipantau
- `/zones SYMBOL` — lihat zona order block untuk pair tertentu, contoh: `/zones BTC-USDT-SWAP`
- `/stats` — lihat ringkasan performa alert: total, win rate, breakdown per pair

## Catatan

- Deteksi order block di sini adalah pendekatan umum/sederhana (rule-based), bukan standar baku tunggal.
- Target dan rasio R:R di alert adalah **estimasi kasar berbasis zona OB lain yang sudah terdeteksi**,
  bukan analisis lengkap dan bukan rekomendasi entry/exit. Selalu lakukan analisis sendiri.
- Memantau banyak pair sekaligus berarti makin banyak alert — sesuaikan `TOP_N_PAIRS` dan parameter
  deteksi agar tidak membanjiri chat kamu.
- Jangan jadikan satu-satunya basis keputusan trading.

## Setup Database (PostgreSQL di Railway)

Histori alert butuh database PostgreSQL agar datanya **tidak hilang saat bot redeploy/restart**
(Railway tidak punya persistent disk untuk file lokal).

1. Buka project Railway kamu
2. Tap **"+ New"** / **"Create"** di dalam project
3. Pilih **Database** → **Add PostgreSQL**
4. Railway otomatis provision database dan inject variable `DATABASE_URL` — pastikan ter-link
   ke service bot (`worker`) ini, biasanya otomatis, tapi cek di tab **Variables** kalau tidak muncul
5. Tabel `alerts` akan otomatis dibuat sendiri oleh bot saat pertama kali start (tidak perlu setup manual)

## Deploy ke Railway

1. Push/upload repo ini ke GitHub
2. Railway → New Project → Deploy from GitHub repo → pilih repo ini
3. Tab **Variables** → isi `BOT_TOKEN` dan `CHAT_ID` (wajib)
4. Tambahkan PostgreSQL addon (lihat **Setup Database** di atas) — wajib, bot tidak akan jalan tanpa ini
5. Railway otomatis build & jalankan sesuai `Procfile`

## Jalankan Lokal (opsional, untuk testing)

```bash
pip install -r requirements.txt
export BOT_TOKEN="xxxx"
export CHAT_ID="xxxx"
python main.py
```
