# Telegram Bot - Order Block Alert Multi-Pair (OKX Futures)

Bot Telegram yang memindai **banyak pair sekaligus** di OKX Futures (USDT-margined perpetual swap),
mendeteksi zona **Order Block** (Smart Money Concepts) di beberapa timeframe, dan mengirim alert
saat harga memasuki zona dengan konfirmasi reaksi di timeframe lebih kecil.

> Bot ini sempat memakai Binance lalu Bybit, namun keduanya memblokir akses API dari IP Amerika
> Serikat (termasuk sebagian besar server Railway). OKX awalnya lebih permisif, tapi akhirnya
> ikut membatasi IP Railway juga — sehingga bot ini sekarang di-deploy di VPS **ServerHandal**
> (provider Indonesia) untuk menghindari pemblokiran geografis tersebut.

## Struktur File

Bot ini modular — dipecah dari satu `main.py` monolitik menjadi beberapa file per tanggung jawab:

| File | Fungsi |
|---|---|
| `main.py` | Entry point — setup `Application`, daftarkan semua command/callback/message handler |
| `config.py` | Semua environment variable, konstanta, dan state global (`active_zones`, `active_trades`, dll) |
| `startup.py` | Inisialisasi saat bot start: setup DB, ambil daftar pair awal, jadwalkan job berkala |
| `ob_core.py` | Logika inti: fetch candle dari OKX & deteksi order block (BOS, FVG, mitigasi 50%). Dipakai bersama oleh bot live dan `backtest.py` agar logikanya identik |
| `core_utils.py` | Wrapper tipis di atas `ob_core` (mis. `detect_order_blocks`, `calculate_sl_with_atr`) dan util pair aktif |
| `market_utils.py` | Ambil harga terkini, info sesi trading (London/NY), hitung ATR & MA |
| `signal_engine.py` | Validasi zona, bangun pesan sinyal, kirim alert, simpan trade aktif & histori ke DB |
| `scanner.py` | Loop scan berkala per-batch pair, cek trade aktif yang masih terbuka |
| `trade_manager.py` | Cek TP/SL/trailing-to-breakeven untuk trade yang sedang berjalan |
| `zones.py` | Helper ambil & simpan zona per symbol/timeframe |
| `handlers.py` | Command handler: `/start`, `/pairs`, `/zones`, halaman daftar trade aktif |
| `menu_handlers.py` | Handler untuk reply keyboard & inline keyboard menu (Monitoring/Analisis/Backtest/Pengaturan) |
| `trading_handlers.py` | Placeholder pendaftaran handler khusus trading (belum ada isi) |
| `backtest_handlers.py` | Jembatan Telegram ↔ `backtest.py` untuk backtest cepat lewat menu bot |
| `backtest.py` | Script terpisah untuk simulasi historis (lihat bagian **Backtest** di bawah) |
| `stats.py` | Hitung & format ringkasan statistik alert, ringkasan harian otomatis |
| `admin.py` | Command/utility khusus admin |
| `status.py` / `health.py` | Info status bot & health check/alert kalau banyak pair gagal dicek |
| `keyboards.py` | Definisi semua `ReplyKeyboardMarkup` / `InlineKeyboardMarkup` |
| `db.py` | Koneksi PostgreSQL: simpan & query histori alert |
| `utils.py` | Util umum: cek candle sudah closed, format durasi, merge state zona |

## Cara Kerja

1. **Pemilihan pair** — bot otomatis ambil **top N pair by volume 24 jam** dari OKX Swap market
   (default top 30), refresh berkala (default tiap 6 jam)
2. **Deteksi zona (HTF)** — default `1D` dan `4H`, dicari order block dengan beberapa filter kualitas:
   - Bullish OB (Demand) — candle merah terakhir sebelum lonjakan naik kuat
   - Bearish OB (Supply) — candle hijau terakhir sebelum penurunan kuat
   - **Filter impuls** — kekuatan pergerakan setelah OB diukur pakai persentase tetap atau kelipatan
     ATR (`USE_ATR_IMPULSE`), supaya threshold-nya adaptif terhadap volatilitas tiap pair
   - **Filter volume** — candle OB harus punya volume ≥ `VOLUME_MULTIPLIER` × rata-rata (atau median,
     via `USE_MEDIAN_VOLUME`) volume di window yang dianalisis
   - **Filter BOS (Break of Structure)** — opsional (`REQUIRE_BOS`), memastikan OB diikuti oleh
     penembusan struktur swing high/low sebelumnya
   - **Filter FVG (Fair Value Gap)** — opsional (`REQUIRE_FVG`), memastikan ada gap harga di dekat OB
   - **Filter unmitigated** — OB yang sudah pernah ditembus penuh (atau ≥50% via `MITIGATION_50PCT`)
     oleh harga setelah terbentuk akan dibuang otomatis karena sudah tidak relevan lagi
   - **Filter trend** — opsional (`USE_TREND_FILTER`), OB hanya dianggap valid jika searah trend MA
3. **Konfirmasi (LTF)** — default `1H`, alert hanya dikirim jika candle LTF menunjukkan reaksi
   saat harga berada di dalam zona HTF
4. **Batch processing** — pair diproses per-batch (default 5 pair) dengan jeda antar batch
   agar tidak kena rate limit
5. **Kontrol jumlah alert**:
   - **Filter volume & harga minimum pair** — pair dengan volume 24h di bawah `MIN_VOLUME_USD`
     (atau harga di bawah `MIN_PRICE_USD`, kalau diaktifkan) di-skip dari scan
   - **Cooldown per pair** — setelah satu pair kirim alert, pair itu tidak akan kirim alert lagi
     selama `ALERT_COOLDOWN_MINUTES`
   - **Satu trade aktif per pair** — pair yang sedang punya trade terbuka tidak akan kirim sinyal
     baru sampai TP/SL/invalidasi tercapai
6. **Reliability**:
   - **Retry otomatis** — request API yang gagal (timeout, gangguan jaringan, rate limit sementara)
     dicoba ulang otomatis dengan exponential backoff (`API_MAX_RETRIES` kali percobaan)
   - **Health alert** — kalau dalam satu siklus scan banyak pair gagal dicek (≥ `FAILURE_ALERT_THRESHOLD_PERCENT`),
     bot kirim 1 notifikasi peringatan ke Telegram (dengan cooldown sendiri agar tidak spam)
7. **Manajemen risiko di alert** — tiap alert menyertakan:
   - **Stop Loss** — dihitung dari invalidasi zona ± buffer ATR (`ATR_PERIOD` × `ATR_MULTIPLIER`),
     dengan fallback ke `SL_BUFFER_PERCENT` kalau ATR tidak tersedia
   - **Target** — berdasarkan `RISK_REWARD_RATIO` tetap (default 1:2) dari jarak entry ke SL
   - **Trailing to breakeven** — begitu harga bergerak searah profit, SL trade aktif otomatis
     digeser ke breakeven agar risiko terkunci (lihat `trade_manager.py`)
   - **Label kualitas sesi** — tiap alert diberi label sesi trading (London/New York/overlap/quiet)
     berdasarkan jam UTC saat sinyal muncul (`SESSION_LONDON_*`, `SESSION_NY_*`)
8. **Histori & tracking performa** — tiap alert dicatat ke database PostgreSQL. Tiap siklus scan,
   bot mengecek semua alert yang masih "open": apakah harga sudah mencapai target (`hit_target`)
   atau malah menembus invalidasi (`invalidated`). Gunakan menu **Statistik Alert** untuk lihat
   ringkasan win rate dan pair paling sering muncul alert.
9. **Ringkasan harian otomatis** — 1x sehari (default jam 00:00 UTC = 08:00 WITA), bot kirim
   ringkasan statistik 24 jam terakhir tanpa perlu diminta.
10. Tiap zona hanya kirim alert sekali (ditandai "mitigated") agar tidak spam

## Menu Bot (Reply Keyboard)

Selain command, bot punya menu tombol persisten di bawah chat:

- **📊 Monitoring** → Status Bot, Daftar Pair, Trade Aktif, Breakeven Trades, Statistik Alert, Ringkasan Harian
- **📈 Analisis** → Cek Zona OB per pair, Harga Sekarang
- **🔬 Backtest** → Backtest cepat BTC/ETH/SOL 1 bulan, atau custom pair/durasi
- **⚙️ Pengaturan** → Info Konfigurasi aktif, Bantuan

## Environment Variables

| Variable | Wajib | Keterangan |
|---|---|---|
| `BOT_TOKEN` | Ya | Token dari @BotFather |
| `CHAT_ID` | Ya | Chat ID Telegram kamu |
| `DATABASE_URL` | Ya | Connection string PostgreSQL |
| `TOP_N_PAIRS` | Tidak | Default `30` |
| `PAIR_QUOTE` | Tidak | Default `USDT` |
| `BATCH_SIZE` | Tidak | Default `5` |
| `BATCH_DELAY_SECONDS` | Tidak | Default `2` |
| `SYMBOL_REFRESH_HOURS` | Tidak | Default `6` |
| `MIN_VOLUME_USD` | Tidak | Default `5000000` ($5 juta) — skip pair dengan volume 24h di bawah ini |
| `MIN_PRICE_USD` | Tidak | Default `0` (nonaktif) — skip pair dengan harga di bawah ini |
| `ALERT_COOLDOWN_MINUTES` | Tidak | Default `60` — jeda minimum antar alert untuk pair yang sama |
| `API_MAX_RETRIES` | Tidak | Default `3` — jumlah percobaan ulang request API yang gagal |
| `API_RETRY_BACKOFF_SECONDS` | Tidak | Default `2` — jeda awal retry, dikali 2 tiap percobaan (2s, 4s, 8s...) |
| `FAILURE_ALERT_THRESHOLD_PERCENT` | Tidak | Default `50` — kirim health alert jika % pair gagal ≥ ini |
| `HEALTH_ALERT_COOLDOWN_MINUTES` | Tidak | Default `60` — jeda minimum antar health alert |
| `DAILY_SUMMARY_HOUR_UTC` | Tidak | Default `0` — jam (UTC, 0-23) ringkasan harian dikirim |
| `DAILY_SUMMARY_MINUTE_UTC` | Tidak | Default `0` — menit (UTC, 0-59) ringkasan harian dikirim |
| `HTF_LIST` | Tidak | Default `1D,4H` — pisahkan dengan koma, format OKX |
| `LTF` | Tidak | Default `1H` |
| `CHECK_INTERVAL_MINUTES` | Tidak | Default `15` |
| `LOOKBACK_CANDLES` | Tidak | Default `50` |
| `IMPULSE_MIN_PERCENT` | Tidak | Default `3.0` — dipakai kalau `USE_ATR_IMPULSE=false` |
| `USE_ATR_IMPULSE` | Tidak | Default `true` — pakai kelipatan ATR untuk filter kekuatan impuls, bukan persentase tetap |
| `IMPULSE_ATR_MULTIPLIER` | Tidak | Default `1.6` — dipakai kalau `USE_ATR_IMPULSE=true` |
| `VOLUME_MULTIPLIER` | Tidak | Default `1.2` — candle OB butuh volume ≥ 1.2x rata-rata/median window |
| `USE_MEDIAN_VOLUME` | Tidak | Default `true` — pakai median (bukan rata-rata) sebagai baseline volume |
| `MAX_ACTIVE_ZONES_PER_TF` | Tidak | Default `3` |
| `MA_PERIOD` | Tidak | Default `50` — periode MA untuk filter trend |
| `USE_TREND_FILTER` | Tidak | Default `false` — hanya ambil OB yang searah trend MA |
| `SL_BUFFER_PERCENT` | Tidak | Default `0.5` — buffer SL fallback kalau ATR tidak tersedia |
| `RISK_REWARD_RATIO` | Tidak | Default `2.0` — rasio target tetap (1:2) |
| `ATR_PERIOD` | Tidak | Default `14` — periode ATR untuk hitung SL |
| `ATR_MULTIPLIER` | Tidak | Default `2.0` — SL = invalidasi ± (ATR × multiplier) |
| `REQUIRE_BOS` | Tidak | Default `true` — wajibkan Break of Structure sebelum OB dianggap valid |
| `REQUIRE_FVG` | Tidak | Default `false` — wajibkan Fair Value Gap di dekat OB |
| `MITIGATION_50PCT` | Tidak | Default `true` — anggap zona termitigasi kalau tertembus ≥50%, bukan hanya 100% |
| `SWING_LOOKBACK` | Tidak | Default `10` — jumlah candle untuk cari swing high/low (BOS) |
| `SESSION_LONDON_START` / `SESSION_LONDON_END` | Tidak | Default `7` / `16` (UTC) |
| `SESSION_NY_START` / `SESSION_NY_END` | Tidak | Default `13` / `22` (UTC) |

**Format interval OKX:** `1m 3m 5m 15m 30m 1H 2H 4H 6H 12H 1D 1W 1M` (huruf besar untuk jam/hari/minggu/bulan)

**Format symbol OKX:** `BTC-USDT-SWAP`, `ETH-USDT-SWAP`, dst (bukan `BTCUSDT` seperti exchange lain)

**Catatan timezone ringkasan harian:** server berjalan di UTC, bukan waktu lokal kamu.
Default `DAILY_SUMMARY_HOUR_UTC=0` berarti jam 00:00 UTC, yang setara dengan **08:00 WITA** (UTC+8).
Kalau mau jam lain, hitung dulu selisihnya — misal ingin 07:00 WITA, set `DAILY_SUMMARY_HOUR_UTC=23` (hari sebelumnya dalam UTC).

## Cara Dapat CHAT_ID

1. Chat bot kamu di Telegram, kirim pesan apa saja
2. Buka di browser: `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates`
3. Cari nilai `"chat":{"id": ...}` — itu CHAT_ID kamu

## Command Bot

- `/start` — cek status bot dan jumlah pair yang dipantau, tampilkan menu utama
- `/pairs` — lihat daftar pair yang sedang dipantau
- `/zones SYMBOL` — lihat zona order block untuk pair tertentu, contoh: `/zones BTC-USDT-SWAP`
- `/stats` — lihat ringkasan performa alert: total, win rate, breakdown per pair
- `/backtest` — jalankan backtest cepat lewat chat (alternatif dari menu **🔬 Backtest**)

Selain command di atas, sebagian besar fitur (Trade Aktif, Breakeven Trades, Ringkasan Harian,
Info Konfigurasi, dll) diakses lewat menu tombol — lihat bagian **Menu Bot** di atas.

## Catatan

- Deteksi order block di sini adalah pendekatan Smart Money Concepts (rule-based: OB + BOS + FVG
  + mitigasi), bukan standar baku tunggal.
- Stop Loss & target di alert dihitung otomatis (ATR-based SL, R:R tetap) — tetap **estimasi**,
  bukan analisis lengkap dan bukan rekomendasi entry/exit. Selalu lakukan analisis sendiri.
- Memantau banyak pair sekaligus berarti makin banyak alert — sesuaikan `TOP_N_PAIRS` dan parameter
  deteksi agar tidak membanjiri chat kamu.
- Jangan jadikan satu-satunya basis keputusan trading.

## Backtest

`backtest.py` adalah script terpisah (dijalankan manual dari terminal, atau lewat menu **🔬 Backtest**
di bot untuk versi cepat) untuk menguji performa historis strategi order block sebelum dipercaya
secara live. Script ini memakai fungsi deteksi yang **sama persis** dengan bot live (sama-sama
import dari `ob_core.py`), jadi hasilnya merepresentasikan strategi yang benar-benar berjalan.

### Cara pakai (manual, terminal)

```bash
pip install -r requirements.txt

# Backtest top 30 pair, 3 bulan terakhir (default)
python backtest.py

# Custom: 1 bulan, top 10 pair
python backtest.py --months 1 --pairs 10

# Backtest 1 pair spesifik saja
python backtest.py --symbol BTC-USDT-SWAP

# Custom timeframe
python backtest.py --htf 1D,4H --ltf 1H
```

### Cara kerja

1. Ambil data historis N bulan ke belakang (lewat endpoint `/history-candles` OKX, dengan paging otomatis)
2. "Putar ulang" candle demi candle secara kronologis — rolling window persis seperti bot live
3. Tiap kali order block valid + ada konfirmasi reaksi LTF, dicatat sebagai 1 sinyal
4. Sinyal dilacak ke depan: hit target dulu (win) atau invalidasi dulu (loss)
5. Hasil akhir: ringkasan win rate, breakdown per timeframe dan per pair

### Keterbatasan penting

- **Target di backtest beda dari live**: live pakai zona OB berlawanan atau R:R tetap sebagai target,
  sedangkan backtest pakai proxy R:R tetap (karena perhitungan lintas-zona historis sulit
  direplikasi persis). Hasilnya jadi estimasi kasar, bukan simulasi 1:1.
- Tidak memperhitungkan slippage, fee, atau eksekusi order nyata
- Hasil masa lalu tidak menjamin hasil masa depan — gunakan sebagai salah satu input,
  bukan satu-satunya dasar keputusan
- Endpoint historis OKX punya batas seberapa jauh data tersedia tergantung timeframe

## Setup Database (PostgreSQL)

Histori alert butuh database PostgreSQL agar datanya **tidak hilang saat bot restart**.

1. Siapkan instance PostgreSQL (self-hosted di VPS yang sama, atau managed provider mana pun)
2. Set `DATABASE_URL` di environment sesuai format `postgresql://user:password@host:port/dbname`
3. Tabel `alerts` akan otomatis dibuat sendiri oleh bot saat pertama kali start (tidak perlu setup manual)

## Deploy ke VPS (ServerHandal atau VPS lain)

1. Clone repo ini ke VPS:
   ```bash
   git clone https://github.com/zabech/Bot_OB.git
   cd Bot_OB
   ```
2. Install dependency:
   ```bash
   pip install -r requirements.txt
   ```
3. Siapkan file `.env` (atau export langsung) berisi minimal `BOT_TOKEN`, `CHAT_ID`, `DATABASE_URL`
4. Jalankan bot, idealnya di dalam process manager supaya auto-restart kalau crash, misalnya `screen`/`tmux`
   untuk testing cepat, atau `systemd`/`pm2`/`supervisor` untuk produksi:
   ```bash
   python main.py
   ```
5. Cek log untuk pastikan bot berhasil connect ke Telegram dan OKX tanpa error koneksi/geo-block

> **Catatan migrasi:** bot ini sebelumnya di-deploy di Railway, tapi dipindah ke VPS ServerHandal
> karena OKX (dan sebelumnya Binance, Bybit) memblokir akses API dari IP milik Railway di Amerika
> Serikat. VPS dengan IP Indonesia terbukti tidak kena blokir ini.

## Jalankan Lokal (opsional, untuk testing)

```bash
pip install -r requirements.txt
export BOT_TOKEN="xxxx"
export CHAT_ID="xxxx"
export DATABASE_URL="postgresql://user:password@localhost:5432/botob"
python main.py
```
