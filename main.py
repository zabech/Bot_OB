import os
import time
import logging
import asyncio
import pandas as pd
from typing import Optional
from collections import defaultdict
from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ConversationHandler, MessageHandler, filters, ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import db
import ob_core

# State untuk ConversationHandler
WAITING_SYMBOL_ZONES = 1
WAITING_SYMBOL_BACKTEST = 2
WAITING_MONTHS_BACKTEST = 3

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
IMPULSE_MIN_PERCENT = float(os.environ.get("IMPULSE_MIN_PERCENT", 3.0))   # dinaikkan dari 1.5 ke 3.0
MAX_ACTIVE_ZONES_PER_TF = int(os.environ.get("MAX_ACTIVE_ZONES_PER_TF", 3))
VOLUME_MULTIPLIER = float(os.environ.get("VOLUME_MULTIPLIER", 1.2))

# Filter kualitas tambahan
MIN_PRICE_USD = float(os.environ.get("MIN_PRICE_USD", 0.01))  # skip pair dengan harga < $0.01 (micro-price)
MA_PERIOD = int(os.environ.get("MA_PERIOD", 50))               # periode MA untuk filter trend
USE_TREND_FILTER = os.environ.get("USE_TREND_FILTER", "true").lower() == "true"

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


def get_top_volume_pairs(n: int, quote: str) -> list:
    return ob_core.get_top_volume_pairs(n, quote, MIN_VOLUME_USD)


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
    return ob_core.fetch_klines_df(symbol, interval, limit)


def detect_order_blocks(df: pd.DataFrame, max_zones: int) -> list:
    return ob_core.detect_order_blocks(df, max_zones, IMPULSE_MIN_PERCENT, VOLUME_MULTIPLIER)


def ltf_shows_reaction(ltf_df: pd.DataFrame, zone: dict) -> bool:
    return ob_core.ltf_shows_reaction(ltf_df, zone)


def merge_zone_state(old_zones: list, new_zones: list) -> list:
    return ob_core.merge_zone_state(old_zones, new_zones)


def calculate_invalidation(zone: dict) -> float:
    return ob_core.calculate_invalidation(zone)


def find_nearest_opposite_target(zone: dict, current_price: float, all_zones_for_symbol: dict) -> Optional[float]:
    return ob_core.find_nearest_opposite_target(zone, current_price, all_zones_for_symbol)


def calculate_risk_reward(zone: dict, current_price: float, target: Optional[float]) -> str:
    return ob_core.calculate_risk_reward(zone, current_price, target)


def get_current_price(symbol: str) -> Optional[float]:
    """Ambil harga terakhir pair dari endpoint ticker OKX."""
    try:
        data = ob_core.okx_get("/api/v5/market/ticker", {"instId": symbol})
        return float(data["data"][0]["last"])
    except Exception:
        return None


def is_price_above_min(current_price: float) -> bool:
    """Return True kalau harga >= MIN_PRICE_USD (filter micro-price pair)."""
    return current_price >= MIN_PRICE_USD


def calculate_ma(candles: list, period: int) -> Optional[float]:
    """Hitung Moving Average dari close price N candle terakhir."""
    if len(candles) < period:
        return None
    closes = [c["close"] if isinstance(c, dict) else float(c["close"]) for c in candles[-period:]]
    return sum(closes) / len(closes)


def trend_allows_zone(zone: dict, current_price: float, htf_candles) -> bool:
    """
    Filter trend: cek apakah arah zona OB searah dengan trend MA50 HTF.
    - Bullish OB valid hanya kalau harga di atas MA50 (uptrend / area demand)
    - Bearish OB valid hanya kalau harga di bawah MA50 (downtrend / area supply)
    Kalau USE_TREND_FILTER=false atau MA tidak bisa dihitung, lewatkan filter ini.
    """
    if not USE_TREND_FILTER:
        return True

    if isinstance(htf_candles, list):
        candles_list = htf_candles
    else:
        candles_list = htf_candles.to_dict("records") if hasattr(htf_candles, 'to_dict') else list(htf_candles)

    ma = calculate_ma(candles_list, MA_PERIOD)
    if ma is None:
        return True  # tidak cukup data, jangan blokir

    if zone["type"] == "bullish" and current_price > ma:
        return True   # harga di atas MA -> uptrend -> bullish OB valid
    if zone["type"] == "bearish" and current_price < ma:
        return True   # harga di bawah MA -> downtrend -> bearish OB valid
    return False


async def check_symbol(app, symbol: str) -> bool:
    """Cek satu pair di semua HTF, kirim alert kalau ada zona valid + konfirmasi LTF.
    Return True kalau berhasil dicek, False kalau gagal (untuk health tracking)."""
    global active_zones
    if symbol not in active_zones:
        active_zones[symbol] = {tf: [] for tf in HTF_LIST}

    try:
        ltf_df = fetch_klines_df(symbol, LTF, LOOKBACK_CANDLES)
        if hasattr(ltf_df, 'iloc'):
            current_price = float(ltf_df.iloc[-1]["close"])
        else:
            current_price = float(ltf_df[-1]["close"])

        # Filter 1: skip pair micro-price (harga terlalu kecil = noise tinggi)
        if not is_price_above_min(current_price):
            logger.info(f"[{symbol}] Skip — harga {current_price} < MIN_PRICE_USD {MIN_PRICE_USD}")
            return True  # bukan error, cuma di-skip

        for htf in HTF_LIST:
            htf_df = fetch_klines_df(symbol, htf, LOOKBACK_CANDLES)
            detected = detect_order_blocks(htf_df, MAX_ACTIVE_ZONES_PER_TF)
            detected = merge_zone_state(active_zones[symbol].get(htf, []), detected)
            active_zones[symbol][htf] = detected

            # Siapkan candles list untuk filter trend MA
            if hasattr(htf_df, 'to_dict'):
                htf_candles_list = htf_df.to_dict("records")
            else:
                htf_candles_list = htf_df

            for zone in detected:
                if zone["mitigated"]:
                    continue

                price_in_zone = zone["bottom"] <= current_price <= zone["top"]
                if not price_in_zone:
                    continue

                if not ltf_shows_reaction(ltf_df, zone):
                    continue

                # Filter 2: trend filter — arah zona harus searah MA50 HTF
                if not trend_allows_zone(zone, current_price, htf_candles_list):
                    logger.info(f"[{symbol}] Skip zona {zone['type']} — berlawanan dengan trend MA{MA_PERIOD}")
                    zone["mitigated"] = True
                    continue

                # Cooldown
                now = time.time()
                last_sent = last_alert_time.get(symbol, 0)
                if (now - last_sent) < ALERT_COOLDOWN_MINUTES * 60:
                    zone["mitigated"] = True
                    continue

                emoji = "🟢" if zone["type"] == "bullish" else "🔴"
                label = "BULLISH (Demand)" if zone["type"] == "bullish" else "BEARISH (Supply)"

                invalidation = calculate_invalidation(zone)
                target = find_nearest_opposite_target(zone, current_price, active_zones[symbol])
                rr = calculate_risk_reward(zone, current_price, target)
                target_text = f"{target}" if target is not None else "tidak tersedia"
                ma_val = calculate_ma(htf_candles_list, MA_PERIOD)
                trend_text = f"MA{MA_PERIOD}: {ma_val:.4g} ({'↑ Uptrend' if current_price > ma_val else '↓ Downtrend'})" if ma_val else "N/A"

                await app.bot.send_message(
                    chat_id=CHAT_ID,
                    text=(
                        f"{emoji} {symbol} memasuki Order Block {label}\n"
                        f"Timeframe zona: {htf} | Konfirmasi: {LTF}\n"
                        f"Harga sekarang: {current_price}\n"
                        f"Zona: {zone['bottom']} - {zone['top']}\n"
                        f"Invalidasi: {invalidation}\n"
                        f"Target terdekat: {target_text}\n"
                        f"Estimasi R:R: {rr}\n"
                        f"Trend ({htf}): {trend_text}"
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
        f"🤖 Bot OB aktif ✅\n\n"
        f"Memantau {len(symbols)} pair | {', '.join(HTF_LIST)} | Konfirmasi {LTF}\n"
        f"Cek tiap {CHECK_INTERVAL_MINUTES} menit\n\n"
        f"Pilih menu di bawah:",
        reply_markup=main_keyboard()
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


async def backtest_command(update, context: ContextTypes.DEFAULT_TYPE):
    """
    /backtest                    -> BTC-USDT-SWAP, 1 bulan
    /backtest ETH-USDT-SWAP      -> 1 pair custom, 1 bulan
    /backtest ETH-USDT-SWAP 3    -> 1 pair custom, 3 bulan
    """
    args = context.args
    symbol = args[0].upper() if args else "BTC-USDT-SWAP"
    try:
        months = int(args[1]) if len(args) >= 2 else 1
        months = max(1, min(months, 6))  # batasi 1-6 bulan
    except ValueError:
        await update.message.reply_text("Format: /backtest SYMBOL BULAN\nContoh: /backtest BTC-USDT-SWAP 3")
        return

    await update.message.reply_text(
        f"⏳ Memulai backtest {symbol}, {months} bulan...\n"
        f"HTF: {', '.join(HTF_LIST)} | LTF: {LTF}\n"
        f"Estimasi waktu: 1-3 menit, mohon tunggu."
    )

    try:
        from datetime import datetime, timedelta, timezone
        from collections import defaultdict

        end_ts_ms = int(time.time() * 1000)
        start_ts_ms = int((datetime.now(timezone.utc) - timedelta(days=months * 30)).timestamp() * 1000)

        # Ambil data historis LTF
        ltf_candles = ob_core.fetch_full_history(symbol, LTF, start_ts_ms, end_ts_ms)
        if hasattr(ltf_candles, 'to_dict'):
            ltf_list = ltf_candles.to_dict("records")
        else:
            ltf_list = ltf_candles

        if len(ltf_list) < LOOKBACK_CANDLES:
            await update.message.reply_text(f"Data tidak cukup untuk {symbol}. Coba pair lain.")
            return

        all_results = []
        seen_zones = set()

        for htf in HTF_LIST:
            htf_data = ob_core.fetch_full_history(symbol, htf, start_ts_ms, end_ts_ms)
            if hasattr(htf_data, 'to_dict'):
                htf_list = htf_data.to_dict("records")
            else:
                htf_list = htf_data

            if len(htf_list) < LOOKBACK_CANDLES + 10:
                continue

            for end_idx in range(LOOKBACK_CANDLES, len(htf_list)):
                window = htf_list[end_idx - LOOKBACK_CANDLES:end_idx]
                zones = ob_core.detect_order_blocks(window, MAX_ACTIVE_ZONES_PER_TF, IMPULSE_MIN_PERCENT, VOLUME_MULTIPLIER)
                if not zones:
                    continue

                current_htf_ts = htf_list[end_idx]["ts"]
                current_price = htf_list[end_idx]["close"]

                for zone in zones:
                    zone_key = (zone["type"], round(zone["top"], 8), round(zone["bottom"], 8))
                    if zone_key in seen_zones:
                        continue
                    if not (zone["bottom"] <= current_price <= zone["top"]):
                        continue

                    ltf_slice = [c for c in ltf_list if c["ts"] <= current_htf_ts][-3:]
                    if len(ltf_slice) < 3:
                        continue
                    if not ob_core.ltf_shows_reaction(ltf_slice, zone):
                        continue

                    seen_zones.add(zone_key)
                    risk = abs(current_price - (zone["bottom"] if zone["type"] == "bullish" else zone["top"]))
                    if risk == 0:
                        continue

                    target = current_price + risk * 1.5 if zone["type"] == "bullish" else current_price - risk * 1.5
                    invalidation = zone["bottom"] if zone["type"] == "bullish" else zone["top"]

                    # Resolve trade ke depan
                    future = [c for c in ltf_list if c["ts"] > current_htf_ts][:200]
                    outcome = "unresolved"
                    for c in future:
                        if zone["type"] == "bullish":
                            if c["high"] >= target:
                                outcome = "win"
                                break
                            if c["low"] <= invalidation:
                                outcome = "loss"
                                break
                        else:
                            if c["low"] <= target:
                                outcome = "win"
                                break
                            if c["high"] >= invalidation:
                                outcome = "loss"
                                break

                    all_results.append({"htf": htf, "zone_type": zone["type"], "outcome": outcome})

        # Buat ringkasan
        total = len(all_results)
        if total == 0:
            await update.message.reply_text(
                f"Backtest {symbol} ({months} bulan) selesai.\nTidak ada sinyal yang terbentuk."
            )
            return

        win = sum(1 for r in all_results if r["outcome"] == "win")
        loss = sum(1 for r in all_results if r["outcome"] == "loss")
        unresolved = sum(1 for r in all_results if r["outcome"] == "unresolved")
        resolved = win + loss
        win_rate = f"{win / resolved * 100:.1f}%" if resolved > 0 else "N/A"

        by_htf = defaultdict(lambda: {"win": 0, "loss": 0, "total": 0})
        for r in all_results:
            by_htf[r["htf"]]["total"] += 1
            if r["outcome"] == "win":
                by_htf[r["htf"]]["win"] += 1
            elif r["outcome"] == "loss":
                by_htf[r["htf"]]["loss"] += 1

        htf_lines = []
        for htf, g in sorted(by_htf.items()):
            res = g["win"] + g["loss"]
            wr = f"{g['win'] / res * 100:.1f}%" if res > 0 else "N/A"
            htf_lines.append(f"  {htf}: {g['total']} sinyal, win rate {wr}")

        await update.message.reply_text(
            f"📊 Hasil Backtest {symbol} ({months} bulan)\n"
            f"HTF: {', '.join(HTF_LIST)} | LTF: {LTF}\n\n"
            f"Total sinyal : {total}\n"
            f"Win          : {win}\n"
            f"Loss         : {loss}\n"
            f"Unresolved   : {unresolved}\n"
            f"Win rate     : {win_rate} (dari {resolved} resolved)\n\n"
            f"Per timeframe:\n" + "\n".join(htf_lines) + "\n\n"
            f"*Target pakai R:R 1.5x risk (estimasi kasar)"
        )

    except Exception as e:
        logger.error(f"Backtest command error: {e}")
        await update.message.reply_text(f"Gagal menjalankan backtest: {e}")


# ── Keyboard builders ────────────────────────────────────────

def main_keyboard() -> ReplyKeyboardMarkup:
    """Reply keyboard utama yang selalu tampil di bawah chat."""
    return ReplyKeyboardMarkup(
        [
            ["📊 Monitoring", "📈 Analisis"],
            ["🔬 Backtest",   "⚙️ Pengaturan"],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def monitoring_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Status Bot",       callback_data="mon_status")],
        [InlineKeyboardButton("📋 Daftar Pair",       callback_data="mon_pairs")],
        [InlineKeyboardButton("📈 Statistik Alert",   callback_data="mon_stats")],
        [InlineKeyboardButton("🗓️ Ringkasan Harian",  callback_data="mon_daily")],
    ])


def analisis_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Cek Zona OB",       callback_data="ana_zones")],
        [InlineKeyboardButton("💰 Harga Sekarang",    callback_data="ana_price")],
    ])


def backtest_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("₿ Backtest BTC 1 bln",  callback_data="bt_btc_1")],
        [InlineKeyboardButton("Ξ Backtest ETH 1 bln",  callback_data="bt_eth_1")],
        [InlineKeyboardButton("⚡ Backtest SOL 1 bln",  callback_data="bt_sol_1")],
        [InlineKeyboardButton("✏️ Backtest Custom...",   callback_data="bt_custom")],
    ])


def pengaturan_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Info Konfigurasi",   callback_data="set_config")],
        [InlineKeyboardButton("❓ Bantuan",             callback_data="set_help")],
    ])


# ── Handler tombol Reply Keyboard ─────────────────────────────

async def menu_router(update, context: ContextTypes.DEFAULT_TYPE):
    """Route pesan teks dari Reply Keyboard ke sub-menu inline."""
    text = update.message.text
    if text == "📊 Monitoring":
        await update.message.reply_text("Pilih menu Monitoring:", reply_markup=monitoring_keyboard())
    elif text == "📈 Analisis":
        await update.message.reply_text("Pilih menu Analisis:", reply_markup=analisis_keyboard())
    elif text == "🔬 Backtest":
        await update.message.reply_text("Pilih menu Backtest:", reply_markup=backtest_keyboard())
    elif text == "⚙️ Pengaturan":
        await update.message.reply_text("Pilih menu Pengaturan:", reply_markup=pengaturan_keyboard())


# ── Handler tombol Inline Keyboard (callback) ─────────────────

async def inline_callback(update, context: ContextTypes.DEFAULT_TYPE):
    """Handle semua callback dari inline keyboard."""
    query = update.callback_query
    await query.answer()
    data = query.data

    # ── MONITORING ──
    if data == "mon_status":
        symbols = get_active_symbols()
        await query.edit_message_text(
            f"🤖 Status Bot\n\n"
            f"✅ Online\n"
            f"Memantau: {len(symbols)} pair\n"
            f"Zona dicari di: {', '.join(HTF_LIST)}\n"
            f"Konfirmasi: {LTF}\n"
            f"Cooldown: {ALERT_COOLDOWN_MINUTES} menit\n"
            f"Cek tiap: {CHECK_INTERVAL_MINUTES} menit\n"
            f"Filter trend MA{MA_PERIOD}: {'Aktif' if USE_TREND_FILTER else 'Nonaktif'}\n"
            f"Min harga: ${MIN_PRICE_USD}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data="mon_status")
            ]])
        )

    elif data == "mon_pairs":
        symbols = get_active_symbols()
        text = f"📋 {len(symbols)} Pair Dipantau:\n\n" + ", ".join(symbols)
        if len(text) > 4096:
            text = text[:4090] + "..."
        await query.edit_message_text(text)

    elif data == "mon_stats":
        try:
            stats = db.get_stats()
            await query.edit_message_text(
                format_stats_text(stats, "📈 Statistik Alert (semua waktu)"),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Refresh", callback_data="mon_stats")
                ]])
            )
        except Exception as e:
            await query.edit_message_text(f"Gagal ambil statistik: {e}")

    elif data == "mon_daily":
        try:
            stats = db.get_daily_stats()
            await query.edit_message_text(
                format_stats_text(stats, "🗓️ Ringkasan 24 Jam Terakhir"),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Refresh", callback_data="mon_daily")
                ]])
            )
        except Exception as e:
            await query.edit_message_text(f"Gagal ambil data harian: {e}")

    # ── ANALISIS ──
    elif data == "ana_zones":
        await query.edit_message_text(
            "🔍 Cek Zona OB\n\nKetik nama pair yang ingin dicek:\nContoh: BTC-USDT-SWAP"
        )
        context.user_data["waiting_for"] = "zones"

    elif data == "ana_price":
        await query.edit_message_text(
            "💰 Cek Harga\n\nKetik nama pair:\nContoh: BTC-USDT-SWAP"
        )
        context.user_data["waiting_for"] = "price"

    # ── BACKTEST ──
    elif data in ("bt_btc_1", "bt_eth_1", "bt_sol_1"):
        symbol_map = {
            "bt_btc_1": "BTC-USDT-SWAP",
            "bt_eth_1": "ETH-USDT-SWAP",
            "bt_sol_1": "SOL-USDT-SWAP",
        }
        symbol = symbol_map[data]
        await query.edit_message_text(f"⏳ Memulai backtest {symbol}, 1 bulan...\nMohon tunggu 1-3 menit.")
        result_text = await run_backtest_async(symbol, 1)
        await context.bot.send_message(chat_id=query.message.chat_id, text=result_text)

    elif data == "bt_custom":
        await query.edit_message_text(
            "✏️ Backtest Custom\n\nKetik nama pair:\nContoh: SOL-USDT-SWAP"
        )
        context.user_data["waiting_for"] = "backtest_symbol"

    # ── PENGATURAN ──
    elif data == "set_config":
        await query.edit_message_text(
            f"⚙️ Konfigurasi Aktif\n\n"
            f"HTF: {', '.join(HTF_LIST)}\n"
            f"LTF: {LTF}\n"
            f"Impulse min: {IMPULSE_MIN_PERCENT}%\n"
            f"Volume multiplier: {VOLUME_MULTIPLIER}x\n"
            f"MA period: {MA_PERIOD}\n"
            f"Filter trend: {'Aktif' if USE_TREND_FILTER else 'Nonaktif'}\n"
            f"Min harga pair: ${MIN_PRICE_USD}\n"
            f"Top N pair: {TOP_N_PAIRS}\n"
            f"Cooldown alert: {ALERT_COOLDOWN_MINUTES} menit\n"
            f"Interval scan: {CHECK_INTERVAL_MINUTES} menit"
        )

    elif data == "set_help":
        await query.edit_message_text(
            "❓ Bantuan\n\n"
            "📊 Monitoring — pantau status bot dan statistik\n"
            "📈 Analisis — cek zona OB dan harga pair tertentu\n"
            "🔬 Backtest — uji performa historis strategi OB\n"
            "⚙️ Pengaturan — lihat konfigurasi aktif\n\n"
            "Command manual:\n"
            "/start — tampilkan menu\n"
            "/zones BTC-USDT-SWAP — cek zona OB\n"
            "/backtest BTC-USDT-SWAP 3 — backtest 3 bulan\n"
            "/stats — statistik alert\n"
            "/pairs — daftar pair"
        )


async def text_input_handler(update, context: ContextTypes.DEFAULT_TYPE):
    """Handle input teks dari user setelah diminta (zones, price, backtest custom)."""
    text = update.message.text.strip().upper()
    waiting = context.user_data.get("waiting_for")

    if waiting == "zones":
        context.user_data.pop("waiting_for", None)
        await update.message.reply_text(f"🔍 Mengambil data zona OB untuk {text}...")
        try:
            ltf_df = fetch_klines_df(text, LTF, LOOKBACK_CANDLES)
            if hasattr(ltf_df, 'iloc'):
                current_price = float(ltf_df.iloc[-1]["close"])
            else:
                current_price = float(ltf_df[-1]["close"])
            lines = [f"Harga {text} sekarang: {current_price}\n"]
            for htf in HTF_LIST:
                htf_df = fetch_klines_df(text, htf, LOOKBACK_CANDLES)
                zones = detect_order_blocks(htf_df, MAX_ACTIVE_ZONES_PER_TF)
                lines.append(f"\n📊 Timeframe {htf}:")
                if not zones:
                    lines.append("  Belum ada order block terdeteksi.")
                    continue
                for z in zones:
                    emoji = "🟢" if z["type"] == "bullish" else "🔴"
                    lines.append(f"  {emoji} {z['type'].capitalize()}: {z['bottom']} - {z['top']}")
            await update.message.reply_text("\n".join(lines), reply_markup=main_keyboard())
        except Exception as e:
            await update.message.reply_text(f"Gagal ambil data untuk {text}: {e}", reply_markup=main_keyboard())

    elif waiting == "price":
        context.user_data.pop("waiting_for", None)
        try:
            price = get_current_price(text)
            if price:
                await update.message.reply_text(f"💰 {text}\nHarga sekarang: {price}", reply_markup=main_keyboard())
            else:
                await update.message.reply_text(f"Gagal ambil harga {text}.", reply_markup=main_keyboard())
        except Exception as e:
            await update.message.reply_text(f"Error: {e}", reply_markup=main_keyboard())

    elif waiting == "backtest_symbol":
        context.user_data["backtest_symbol"] = text
        context.user_data["waiting_for"] = "backtest_months"
        await update.message.reply_text(
            f"Pair: {text}\nBerapa bulan data historis? (1-6)\nKetik angkanya:"
        )

    elif waiting == "backtest_months":
        context.user_data.pop("waiting_for", None)
        symbol = context.user_data.pop("backtest_symbol", "BTC-USDT-SWAP")
        try:
            months = max(1, min(int(text), 6))
        except ValueError:
            months = 1
        await update.message.reply_text(f"⏳ Memulai backtest {symbol}, {months} bulan...\nMohon tunggu 1-3 menit.")
        result_text = await run_backtest_async(symbol, months)
        await update.message.reply_text(result_text, reply_markup=main_keyboard())

    else:
        # Bukan input yang ditunggu, abaikan (menu router yang handle)
        pass


async def run_backtest_async(symbol: str, months: int) -> str:
    """Jalankan backtest dan return teks hasil — dipakai oleh inline callback dan command."""
    try:
        from datetime import datetime, timedelta, timezone
        end_ts_ms = int(time.time() * 1000)
        start_ts_ms = int((datetime.now(timezone.utc) - timedelta(days=months * 30)).timestamp() * 1000)

        ltf_data = ob_core.fetch_full_history(symbol, LTF, start_ts_ms, end_ts_ms)
        ltf_list = ltf_data.to_dict("records") if hasattr(ltf_data, 'to_dict') else ltf_data

        if len(ltf_list) < LOOKBACK_CANDLES:
            return f"Data tidak cukup untuk {symbol}."

        all_results = []
        seen_zones = set()

        for htf in HTF_LIST:
            htf_data = ob_core.fetch_full_history(symbol, htf, start_ts_ms, end_ts_ms)
            htf_list_bt = htf_data.to_dict("records") if hasattr(htf_data, 'to_dict') else htf_data
            if len(htf_list_bt) < LOOKBACK_CANDLES + 10:
                continue

            for end_idx in range(LOOKBACK_CANDLES, len(htf_list_bt)):
                window = htf_list_bt[end_idx - LOOKBACK_CANDLES:end_idx]
                zones = ob_core.detect_order_blocks(window, MAX_ACTIVE_ZONES_PER_TF, IMPULSE_MIN_PERCENT, VOLUME_MULTIPLIER)
                if not zones:
                    continue
                current_htf_ts = htf_list_bt[end_idx]["ts"]
                current_price = htf_list_bt[end_idx]["close"]

                for zone in zones:
                    zone_key = (zone["type"], round(zone["top"], 8), round(zone["bottom"], 8))
                    if zone_key in seen_zones:
                        continue
                    if not (zone["bottom"] <= current_price <= zone["top"]):
                        continue
                    ltf_slice = [c for c in ltf_list if c["ts"] <= current_htf_ts][-3:]
                    if len(ltf_slice) < 3 or not ob_core.ltf_shows_reaction(ltf_slice, zone):
                        continue
                    seen_zones.add(zone_key)
                    risk = abs(current_price - (zone["bottom"] if zone["type"] == "bullish" else zone["top"]))
                    if risk == 0:
                        continue
                    target = current_price + risk * 1.5 if zone["type"] == "bullish" else current_price - risk * 1.5
                    invalidation = zone["bottom"] if zone["type"] == "bullish" else zone["top"]
                    future = [c for c in ltf_list if c["ts"] > current_htf_ts][:200]
                    outcome = "unresolved"
                    for c in future:
                        if zone["type"] == "bullish":
                            if c["high"] >= target:
                                outcome = "win"; break
                            if c["low"] <= invalidation:
                                outcome = "loss"; break
                        else:
                            if c["low"] <= target:
                                outcome = "win"; break
                            if c["high"] >= invalidation:
                                outcome = "loss"; break
                    all_results.append({"htf": htf, "zone_type": zone["type"], "outcome": outcome})

        if not all_results:
            return f"Backtest {symbol} ({months} bln): tidak ada sinyal."

        total = len(all_results)
        win = sum(1 for r in all_results if r["outcome"] == "win")
        loss = sum(1 for r in all_results if r["outcome"] == "loss")
        unresolved = total - win - loss
        resolved = win + loss
        win_rate = f"{win / resolved * 100:.1f}%" if resolved > 0 else "N/A"

        by_htf = defaultdict(lambda: {"win": 0, "loss": 0, "total": 0})
        for r in all_results:
            by_htf[r["htf"]]["total"] += 1
            if r["outcome"] == "win":
                by_htf[r["htf"]]["win"] += 1
            elif r["outcome"] == "loss":
                by_htf[r["htf"]]["loss"] += 1

        htf_lines = []
        for htf, g in sorted(by_htf.items()):
            res = g["win"] + g["loss"]
            wr = f"{g['win'] / res * 100:.1f}%" if res > 0 else "N/A"
            htf_lines.append(f"  {htf}: {g['total']} sinyal, WR {wr}")

        return (
            f"📊 Hasil Backtest {symbol} ({months} bln)\n\n"
            f"Total sinyal : {total}\n"
            f"Win          : {win}\n"
            f"Loss         : {loss}\n"
            f"Unresolved   : {unresolved}\n"
            f"Win rate     : {win_rate} ({resolved} resolved)\n\n"
            f"Per timeframe:\n" + "\n".join(htf_lines) + "\n\n"
            f"*Target R:R 1.5x risk (estimasi kasar)"
        )
    except Exception as e:
        return f"Gagal backtest {symbol}: {e}"


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

    # Pakai konfigurasi retry dari env var untuk semua request ob_core
    ob_core.DEFAULT_MAX_RETRIES = API_MAX_RETRIES
    ob_core.DEFAULT_BACKOFF_SECONDS = API_RETRY_BACKOFF_SECONDS

    try:
        db.init_db()
    except Exception as e:
        raise RuntimeError(
            f"Gagal inisialisasi database: {e}\n"
            f"Pastikan PostgreSQL addon sudah ditambahkan dan ter-link ke service ini di Railway."
        )

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(on_startup).build()

    # Command handlers (tetap tersedia untuk power user)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pairs", pairs_now))
    app.add_handler(CommandHandler("zones", zones_now))
    app.add_handler(CommandHandler("stats", stats_now))
    app.add_handler(CommandHandler("backtest", backtest_command))

    # Inline keyboard callback
    app.add_handler(CallbackQueryHandler(inline_callback))

    # Reply keyboard router + text input handler
    # Urutan penting: menu_router duluan untuk tombol menu, text_input_handler untuk input custom
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex("^(📊 Monitoring|📈 Analisis|🔬 Backtest|⚙️ Pengaturan)$"),
        menu_router
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input_handler))

    logger.info("Bot mulai polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
