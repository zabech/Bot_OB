import os
import time
import logging
import asyncio
import requests
import pandas as pd
from typing import Optional
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import db

# ── Konfigurasi ──────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

CHECK_INTERVAL_MINUTES = int(os.environ.get("CHECK_INTERVAL_MINUTES", 15))

# Timeframe: HTF (higher) menentukan zona order block utama,
# LTF (lower) dipakai untuk konfirmasi reaksi harga sebelum alert dikirim.
# Format OKX: "1m","3m","5m","15m","30m","1H","2H","4H","6H","12H","1D","1W","1M"
HTF_LIST = os.environ.get("HTF_LIST", "1D,4H").split(",")
LTF = os.environ.get("LTF", "1H")

# Parameter deteksi Order Block
LOOKBACK_CANDLES = int(os.environ.get("LOOKBACK_CANDLES", 50))
IMPULSE_MIN_PERCENT = float(os.environ.get("IMPULSE_MIN_PERCENT", 1.5))
MAX_ACTIVE_ZONES_PER_TF = int(os.environ.get("MAX_ACTIVE_ZONES_PER_TF", 3))
VOLUME_MULTIPLIER = float(os.environ.get("VOLUME_MULTIPLIER", 1.2))  # candle OB butuh volume >= 1.2x rata-rata

# Scanner multi-pair (OKX Futures - USDT-margined swap/perpetual)
TOP_N_PAIRS = int(os.environ.get("TOP_N_PAIRS", 30))
PAIR_QUOTE = os.environ.get("PAIR_QUOTE", "USDT")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", 5))
BATCH_DELAY_SECONDS = float(os.environ.get("BATCH_DELAY_SECONDS", 2))
SYMBOL_REFRESH_HOURS = int(os.environ.get("SYMBOL_REFRESH_HOURS", 6))
MIN_VOLUME_USD = float(os.environ.get("MIN_VOLUME_USD", 5_000_000))  # skip pair dengan volume 24h di bawah ini

# Kontrol jumlah alert
ALERT_COOLDOWN_MINUTES = int(os.environ.get("ALERT_COOLDOWN_MINUTES", 60))  # jeda minimum antar alert per pair

# Reliability: retry untuk request API yang gagal sementara
API_MAX_RETRIES = int(os.environ.get("API_MAX_RETRIES", 3))
API_RETRY_BACKOFF_SECONDS = float(os.environ.get("API_RETRY_BACKOFF_SECONDS", 2))  # dikali 2 tiap percobaan

# Reliability: notifikasi kalau banyak pair gagal dalam satu siklus (indikasi API/koneksi bermasalah)
FAILURE_ALERT_THRESHOLD_PERCENT = float(os.environ.get("FAILURE_ALERT_THRESHOLD_PERCENT", 50))  # % pair gagal
HEALTH_ALERT_COOLDOWN_MINUTES = int(os.environ.get("HEALTH_ALERT_COOLDOWN_MINUTES", 60))  # jeda antar health alert

# Ringkasan harian: jam dalam format UTC (jam server). Default 00:00 UTC = 08:00 WITA.
DAILY_SUMMARY_HOUR_UTC = int(os.environ.get("DAILY_SUMMARY_HOUR_UTC", 0))
DAILY_SUMMARY_MINUTE_UTC = int(os.environ.get("DAILY_SUMMARY_MINUTE_UTC", 0))

OKX_BASE_URL = "https://www.okx.com"
REQUEST_TIMEOUT = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Zona aktif per (symbol, timeframe): { "BTC-USDT-SWAP": {"1D": [...], "4H": [...]}, ... }
active_zones = {}

# Cache daftar top pair, di-refresh berkala
top_pairs_cache = {"symbols": [], "last_refresh": 0}

# Timestamp alert terakhir per pair, untuk cooldown: { "BTC-USDT-SWAP": 1719900000.0, ... }
last_alert_time = {}

# Timestamp health alert terakhir, untuk hindari spam notifikasi "bot bermasalah"
last_health_alert_time = {"ts": 0}


def okx_get(path: str, params: dict) -> dict:
    """Request ke OKX API dengan retry otomatis (exponential backoff) untuk
    menangani gangguan jaringan/rate-limit sementara."""
    last_error = None
    for attempt in range(API_MAX_RETRIES):
        try:
            resp = requests.get(f"{OKX_BASE_URL}{path}", params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0":
                raise RuntimeError(f"OKX API error: {data.get('msg')}")
            return data
        except Exception as e:
            last_error = e
            if attempt < API_MAX_RETRIES - 1:
                wait = API_RETRY_BACKOFF_SECONDS * (2 ** attempt)
                logger.warning(f"Request gagal ({e}), retry dalam {wait:.0f}s [percobaan {attempt + 1}/{API_MAX_RETRIES}]")
                time.sleep(wait)
    raise last_error


def get_top_volume_pairs(n: int, quote: str) -> list:
    """Ambil n pair USDT-margined perpetual swap dengan volume 24h tertinggi,
    dan skip pair dengan volume di bawah MIN_VOLUME_USD (safety net agar tidak
    memproses pair dengan likuiditas terlalu kecil)."""
    data = okx_get("/api/v5/market/tickers", {"instType": "SWAP"})
    tickers = data.get("data", [])
    filtered = [
        t for t in tickers
        if t["instId"].endswith(f"-{quote}-SWAP") and float(t.get("volCcy24h", 0)) >= MIN_VOLUME_USD
    ]
    filtered.sort(key=lambda t: float(t.get("volCcy24h", 0)), reverse=True)
    return [t["instId"] for t in filtered[:n]]


def get_active_symbols() -> list:
    """Refresh daftar top pair tiap SYMBOL_REFRESH_HOURS, selain itu pakai cache."""
    now = time.time()
    if not top_pairs_cache["symbols"] or (now - top_pairs_cache["last_refresh"]) > SYMBOL_REFRESH_HOURS * 3600:
        try:
            symbols = get_top_volume_pairs(TOP_N_PAIRS, PAIR_QUOTE)
            top_pairs_cache["symbols"] = symbols
            top_pairs_cache["last_refresh"] = now
            logger.info(f"Daftar top {len(symbols)} pair di-refresh: {symbols[:5]}...")
        except Exception as e:
            logger.error(f"Gagal refresh daftar pair: {e}")
    return top_pairs_cache["symbols"]


def fetch_klines_df(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    data = okx_get("/api/v5/market/candles", {"instId": symbol, "bar": interval, "limit": limit})
    rows = data.get("data", [])
    # OKX mengembalikan data terbaru lebih dulu -> balik urutannya jadi kronologis
    rows = list(reversed(rows))
    df = pd.DataFrame(rows, columns=[
        "ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"
    ])
    for col in ["open", "high", "low", "close", "vol"]:
        df[col] = df[col].astype(float)
    return df


def detect_order_blocks(df: pd.DataFrame, max_zones: int) -> list:
    """
    Deteksi order block dengan 2 filter kualitas tambahan:
    1. Filter volume: candle OB harus punya volume >= VOLUME_MULTIPLIER x rata-rata
       volume di sekitarnya (VOLUME_LOOKBACK candle sebelum-sesudah), menyaring OB
       yang terbentuk dari candle dengan partisipasi pasar kecil/lemah.
    2. Filter unmitigated: OB yang sudah pernah ditembus penuh oleh harga setelah
       terbentuk (candle close menembus sisi berlawanan zona) dibuang karena sudah
       tidak relevan lagi sebagai zona supply/demand aktif.
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

        # Filter volume: bandingkan volume candle OB terhadap rata-rata seluruh window
        if candle["vol"] < avg_volume * VOLUME_MULTIPLIER:
            continue

        zone_top = float(candle["high"])
        zone_bottom = float(candle["low"])

        # Semua candle setelah candle OB ini (untuk cek apakah pernah ditembus)
        after_candle = df.iloc[i + 1:]

        if is_bearish_candle:
            move_pct = (future["high"].max() - candle["close"]) / candle["close"] * 100
            if move_pct >= IMPULSE_MIN_PERCENT:
                # Filter unmitigated: buang jika close pernah turun di bawah zona (ditembus penuh)
                already_mitigated = (after_candle["close"] < zone_bottom).any()
                if not already_mitigated:
                    zones.append({
                        "type": "bullish", "top": zone_top, "bottom": zone_bottom,
                        "index": i, "mitigated": False,
                    })

        if is_bullish_candle:
            move_pct = (candle["close"] - future["low"].min()) / candle["close"] * 100
            if move_pct >= IMPULSE_MIN_PERCENT:
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
    """Harga invalidasi: untuk bullish OB, harga break di bawah bottom zona.
    Untuk bearish OB, harga break di atas top zona."""
    if zone["type"] == "bullish":
        return zone["bottom"]
    return zone["top"]


def find_nearest_opposite_target(zone: dict, current_price: float, all_zones_for_symbol: dict) -> Optional[float]:
    """Cari zona OB berlawanan terdekat (lintas semua HTF pair ini) sebagai target profit kasar.
    Bullish OB -> cari bearish OB terdekat DI ATAS harga saat ini.
    Bearish OB -> cari bullish OB terdekat DI BAWAH harga saat ini.
    Return None kalau tidak ada kandidat yang valid."""
    opposite_type = "bearish" if zone["type"] == "bullish" else "bullish"
    candidates = []

    for tf_zones in all_zones_for_symbol.values():
        for z in tf_zones:
            if z["type"] != opposite_type:
                continue
            if zone["type"] == "bullish" and z["bottom"] > current_price:
                candidates.append(z["bottom"])  # sisi terdekat zona supply dari bawah
            elif zone["type"] == "bearish" and z["top"] < current_price:
                candidates.append(z["top"])  # sisi terdekat zona demand dari atas

    if not candidates:
        return None
    if zone["type"] == "bullish":
        return min(candidates)  # target terdekat di atas
    return max(candidates)  # target terdekat di bawah


def calculate_risk_reward(zone: dict, current_price: float, target: Optional[float]) -> str:
    """Hitung rasio risk:reward kasar berdasarkan jarak ke invalidasi vs jarak ke target."""
    invalidation = calculate_invalidation(zone)
    risk = abs(current_price - invalidation)
    if risk == 0:
        return "N/A"
    if target is None:
        return "N/A (target tidak tersedia)"
    reward = abs(target - current_price)
    ratio = reward / risk
    return f"1:{ratio:.1f}"


async def check_symbol(app, symbol: str) -> bool:
    """Cek satu pair di semua HTF, kirim alert kalau ada zona valid + konfirmasi LTF.
    Return True kalau berhasil dicek, False kalau gagal (untuk health tracking)."""
    global active_zones
    if symbol not in active_zones:
        active_zones[symbol] = {tf: [] for tf in HTF_LIST}

    try:
        ltf_df = fetch_klines_df(symbol, LTF, LOOKBACK_CANDLES)
        current_price = float(ltf_df.iloc[-1]["close"])

        for htf in HTF_LIST:
            htf_df = fetch_klines_df(symbol, htf, LOOKBACK_CANDLES)
            detected = detect_order_blocks(htf_df, MAX_ACTIVE_ZONES_PER_TF)
            detected = merge_zone_state(active_zones[symbol].get(htf, []), detected)
            active_zones[symbol][htf] = detected

            for zone in detected:
                if zone["mitigated"]:
                    continue

                price_in_zone = zone["bottom"] <= current_price <= zone["top"]
                if not price_in_zone:
                    continue

                if not ltf_shows_reaction(ltf_df, zone):
                    continue

                # Cooldown: skip alert kalau pair ini baru saja kirim alert (apapun jenisnya)
                now = time.time()
                last_sent = last_alert_time.get(symbol, 0)
                if (now - last_sent) < ALERT_COOLDOWN_MINUTES * 60:
                    zone["mitigated"] = True  # tetap tandai biar tidak dicek ulang terus, tapi tidak kirim alert
                    continue

                emoji = "🟢" if zone["type"] == "bullish" else "🔴"
                label = "BULLISH (Demand)" if zone["type"] == "bullish" else "BEARISH (Supply)"

                invalidation = calculate_invalidation(zone)
                target = find_nearest_opposite_target(zone, current_price, active_zones[symbol])
                rr = calculate_risk_reward(zone, current_price, target)
                target_text = f"{target}" if target is not None else "tidak tersedia"

                await app.bot.send_message(
                    chat_id=CHAT_ID,
                    text=(
                        f"{emoji} {symbol} memasuki Order Block {label}\n"
                        f"Timeframe zona: {htf} | Konfirmasi: {LTF}\n"
                        f"Harga sekarang: {current_price}\n"
                        f"Zona: {zone['bottom']} - {zone['top']}\n"
                        f"Invalidasi: {invalidation}\n"
                        f"Target terdekat: {target_text}\n"
                        f"Estimasi R:R: {rr}"
                    ),
                )
                zone["mitigated"] = True
                last_alert_time[symbol] = now

                try:
                    db.record_alert(
                        symbol=symbol, zone_type=zone["type"], htf=htf, ltf=LTF,
                        entry_price=current_price, zone_top=zone["top"], zone_bottom=zone["bottom"],
                        invalidation=invalidation, target=target,
                    )
                except Exception as e:
                    logger.error(f"Gagal simpan alert ke database: {e}")

        return True

    except Exception as e:
        logger.error(f"Gagal cek {symbol}: {e}")
        return False


async def check_open_alerts():
    """Cek semua alert berstatus 'open' di database: apakah harga sekarang sudah
    mencapai target (hit_target) atau malah menembus invalidasi (invalidated).
    Dipanggil tiap siklus scan agar histori tetap terupdate."""
    try:
        open_alerts = db.get_open_alerts()
    except Exception as e:
        logger.error(f"Gagal ambil open alerts dari database: {e}")
        return

    if not open_alerts:
        return

    # Group by symbol biar tidak fetch harga berkali-kali untuk symbol yang sama
    symbols_needed = {a["symbol"] for a in open_alerts}
    current_prices = {}
    for symbol in symbols_needed:
        try:
            df = fetch_klines_df(symbol, LTF, 2)
            current_prices[symbol] = float(df.iloc[-1]["close"])
        except Exception as e:
            logger.warning(f"Gagal ambil harga terkini {symbol} untuk cek open alert: {e}")

    for alert in open_alerts:
        price = current_prices.get(alert["symbol"])
        if price is None:
            continue

        if alert["zone_type"] == "bullish":
            if alert["target"] is not None and price >= alert["target"]:
                db.resolve_alert(alert["id"], "hit_target")
            elif price <= alert["invalidation"]:
                db.resolve_alert(alert["id"], "invalidated")
        else:  # bearish
            if alert["target"] is not None and price <= alert["target"]:
                db.resolve_alert(alert["id"], "hit_target")
            elif price >= alert["invalidation"]:
                db.resolve_alert(alert["id"], "invalidated")


async def send_health_alert(app, failed: int, total: int):
    """Kirim notifikasi ke Telegram kalau terlalu banyak pair gagal dicek dalam satu siklus,
    dengan cooldown agar tidak spam notifikasi yang sama berulang-ulang."""
    now = time.time()
    if (now - last_health_alert_time["ts"]) < HEALTH_ALERT_COOLDOWN_MINUTES * 60:
        return  # masih dalam cooldown, skip

    try:
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"⚠️ Peringatan: {failed}/{total} pair gagal dicek di siklus terakhir.\n"
                f"Kemungkinan ada gangguan koneksi atau API OKX sedang bermasalah.\n"
                f"Bot tetap berjalan dan akan terus mencoba di siklus berikutnya."
            ),
        )
        last_health_alert_time["ts"] = now
    except Exception as e:
        logger.error(f"Gagal kirim health alert: {e}")


async def check_and_alert(app):
    symbols = get_active_symbols()
    if not symbols:
        logger.warning("Belum ada daftar pair untuk dipantau.")
        return

    logger.info(f"Mulai scan {len(symbols)} pair (batch size {BATCH_SIZE})...")

    failed_count = 0
    for i in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[i:i + BATCH_SIZE]
        results = await asyncio.gather(*(check_symbol(app, s) for s in batch))
        failed_count += results.count(False)
        if i + BATCH_SIZE < len(symbols):
            await asyncio.sleep(BATCH_DELAY_SECONDS)

    total = len(symbols)
    failure_pct = (failed_count / total * 100) if total else 0
    logger.info(f"Scan selesai: {total - failed_count}/{total} pair berhasil ({failure_pct:.0f}% gagal).")

    if failure_pct >= FAILURE_ALERT_THRESHOLD_PERCENT:
        await send_health_alert(app, failed_count, total)

    await check_open_alerts()


# ── Command handlers ─────────────────────────────────────────
async def start(update, context: ContextTypes.DEFAULT_TYPE):
    symbols = get_active_symbols()
    await update.message.reply_text(
        f"Bot alert Order Block (multi-pair, OKX Futures) aktif ✅\n"
        f"Memantau top {len(symbols)} pair by volume ({PAIR_QUOTE}, min ${MIN_VOLUME_USD:,.0f})\n"
        f"Zona dicari di: {', '.join(HTF_LIST)}\n"
        f"Konfirmasi di: {LTF}\n"
        f"Cooldown alert: {ALERT_COOLDOWN_MINUTES} menit per pair\n"
        f"Cek tiap {CHECK_INTERVAL_MINUTES} menit.\n"
        f"Ringkasan harian: jam {DAILY_SUMMARY_HOUR_UTC:02d}:{DAILY_SUMMARY_MINUTE_UTC:02d} UTC.\n\n"
        f"Gunakan /pairs untuk lihat daftar pair yang dipantau.\n"
        f"Gunakan /zones SYMBOL untuk lihat zona OB pair tertentu (misal /zones BTC-USDT-SWAP).\n"
        f"Gunakan /stats untuk lihat ringkasan performa alert."
    )


async def pairs_now(update, context: ContextTypes.DEFAULT_TYPE):
    symbols = get_active_symbols()
    if not symbols:
        await update.message.reply_text("Daftar pair belum tersedia, coba lagi sebentar.")
        return
    await update.message.reply_text(
        f"Memantau {len(symbols)} pair:\n" + ", ".join(symbols)
    )


async def zones_now(update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Gunakan format: /zones SYMBOL\nContoh: /zones BTC-USDT-SWAP")
        return

    symbol = args[0].upper()
    try:
        ltf_df = fetch_klines_df(symbol, LTF, LOOKBACK_CANDLES)
        current_price = float(ltf_df.iloc[-1]["close"])

        lines = [f"Harga {symbol} sekarang: {current_price}\n"]
        for htf in HTF_LIST:
            htf_df = fetch_klines_df(symbol, htf, LOOKBACK_CANDLES)
            zones = detect_order_blocks(htf_df, MAX_ACTIVE_ZONES_PER_TF)

            lines.append(f"\n📊 Timeframe {htf}:")
            if not zones:
                lines.append("  Belum ada order block terdeteksi.")
                continue
            for z in zones:
                emoji = "🟢" if z["type"] == "bullish" else "🔴"
                lines.append(f"  {emoji} {z['type'].capitalize()}: {z['bottom']} - {z['top']}")

        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Gagal ambil data untuk {symbol}: {e}")


def format_stats_text(stats: dict, title: str) -> str:
    """Format dict statistik jadi teks pesan Telegram, dipakai untuk /stats dan ringkasan harian."""
    win_rate_text = f"{stats['win_rate']:.1f}%" if stats["win_rate"] is not None else "belum ada data selesai"

    lines = [
        f"{title}\n",
        f"Total alert: {stats['total']}",
        f"Masih berjalan (open): {stats['open']}",
        f"Kena target: {stats['hit_target']}",
        f"Invalidasi: {stats['invalidated']}",
        f"Win rate: {win_rate_text}",
    ]

    if stats["top_pairs"]:
        lines.append("\n🔝 Pair paling sering alert:")
        for p in stats["top_pairs"]:
            lines.append(f"  {p['symbol']}: {p['count']}x")

    return "\n".join(lines)


async def stats_now(update, context: ContextTypes.DEFAULT_TYPE):
    try:
        stats = db.get_stats()
    except Exception as e:
        await update.message.reply_text(f"Gagal ambil statistik dari database: {e}")
        return

    await update.message.reply_text(format_stats_text(stats, "📈 Statistik Alert Order Block (semua waktu)"))


async def send_daily_summary(app):
    """Kirim ringkasan statistik 24 jam terakhir ke Telegram, dijadwalkan 1x sehari."""
    try:
        stats = db.get_daily_stats()
    except Exception as e:
        logger.error(f"Gagal ambil statistik harian: {e}")
        return

    text = format_stats_text(stats, "🗓️ Ringkasan Harian (24 jam terakhir)")
    try:
        await app.bot.send_message(chat_id=CHAT_ID, text=text)
        logger.info("Ringkasan harian terkirim.")
    except Exception as e:
        logger.error(f"Gagal kirim ringkasan harian: {e}")


async def on_startup(app):
    """Dipanggil setelah event loop bot aktif — aman untuk start scheduler di sini."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_and_alert, "interval", minutes=CHECK_INTERVAL_MINUTES, args=[app])
    scheduler.add_job(
        send_daily_summary,
        CronTrigger(hour=DAILY_SUMMARY_HOUR_UTC, minute=DAILY_SUMMARY_MINUTE_UTC),
        args=[app],
    )
    scheduler.start()
    logger.info(f"Scheduler dimulai, cek tiap {CHECK_INTERVAL_MINUTES} menit.")
    logger.info(f"Ringkasan harian dijadwalkan jam {DAILY_SUMMARY_HOUR_UTC:02d}:{DAILY_SUMMARY_MINUTE_UTC:02d} UTC.")


def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("BOT_TOKEN dan CHAT_ID wajib di-set di environment variables")

    try:
        db.init_db()
    except Exception as e:
        raise RuntimeError(
            f"Gagal inisialisasi database: {e}\n"
            f"Pastikan PostgreSQL addon sudah ditambahkan dan ter-link ke service ini di Railway."
        )

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(on_startup).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pairs", pairs_now))
    app.add_handler(CommandHandler("zones", zones_now))
    app.add_handler(CommandHandler("stats", stats_now))

    logger.info("Bot mulai polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
