from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime

import db
import ob_core

from config import (
    active_trades,
    CHECK_INTERVAL_MINUTES,
    TOP_N_PAIRS,
    HTF_LIST,
    LTF,
    DAILY_SUMMARY_HOUR_UTC,
    DAILY_SUMMARY_MINUTE_UTC,
    CHAT_ID,
    RISK_REWARD_RATIO,
    REQUIRE_BOS,
    IMPULSE_MIN_PERCENT,
)
from scanner import check_and_alert
from stats import send_daily_summary

import logging

logger = logging.getLogger(__name__)

async def on_startup(app):
    """Dipanggil setelah event loop bot aktif — aman untuk start scheduler di sini."""
    logger.info("=" * 50)
    logger.info("BOT STARTUP — memulai inisialisasi scheduler...")
    logger.info(f"CHECK_INTERVAL_MINUTES = {CHECK_INTERVAL_MINUTES}")
    logger.info(f"TOP_N_PAIRS = {TOP_N_PAIRS}")
    logger.info(f"HTF_LIST = {HTF_LIST}")
    logger.info(f"LTF = {LTF}")
    logger.info("=" * 50)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_and_alert, "interval", minutes=CHECK_INTERVAL_MINUTES, args=[app])
    scheduler.add_job(
        send_daily_summary,
        CronTrigger(hour=DAILY_SUMMARY_HOUR_UTC, minute=DAILY_SUMMARY_MINUTE_UTC),
        args=[app],
    )
    scheduler.start()
    logger.info(f"Scheduler AKTIF — cek tiap {CHECK_INTERVAL_MINUTES} menit.")

    # Load active_trades dari database supaya tidak hilang setelah restart
    try:
        open_alerts = db.get_open_alerts()
        for alert in open_alerts:
            symbol = alert["symbol"]
            if symbol not in active_trades:
                entry = float(alert["entry_price"])
                sl = float(alert["invalidation"])
                zone_type = alert["zone_type"]
                entry_time = alert.get("entry_time")
                # Kolom DB bertipe TIMESTAMP → psycopg2 mengembalikan objek
                # datetime, BUKAN string. Sementara trade yang terbentuk
                # langsung di sesi live (signal_engine.py) menyimpan
                # entry_time sebagai string (.isoformat()). Normalisasi di
                # sini supaya tipe-nya SELALU string, konsisten di semua
                # tempat — kalau tidak, sorted()/datetime.fromisoformat()
                # di handlers.py & menu_handlers.py bisa error karena
                # membandingkan/parsing dua tipe berbeda.
                if isinstance(entry_time, datetime):
                    entry_time = entry_time.isoformat()

                breakeven_triggered = False

                # Kalau TP tidak tersimpan di DB, hitung ulang dari R:R
                if alert["target"]:
                    tp = float(alert["target"])
                else:
                    risk = abs(entry - sl)
                    if zone_type == "bullish":
                        tp = entry + risk * RISK_REWARD_RATIO
                    else:
                        tp = entry - risk * RISK_REWARD_RATIO
                    logger.info(f"[{symbol}] TP dihitung ulang: {tp:.4g} (entry={entry}, sl={sl}, R:R 1:{RISK_REWARD_RATIO:.0f})")

                active_trades[symbol] = {
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "zone_type": zone_type,
                    "htf": alert["htf"],
                    "entry_time": entry_time,
                    "breakeven_triggered": breakeven_triggered,
                }

                # Update DB kalau TP sebelumnya NULL
                if not alert["target"]:
                    try:
                        db.update_alert_target(alert["id"], tp)
                    except Exception:
                        pass
        logger.info(f"Loaded {len(active_trades)} trade aktif dari database.")
    except Exception as e:
        logger.warning(f"Gagal load active_trades dari DB: {e}")

    # Kirim notifikasi startup ke Telegram sebagai konfirmasi versi kode
    try:
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"🚀 Bot OB dimulai (versi terbaru)\n"
                f"TOP_N_PAIRS={TOP_N_PAIRS} | HTF={HTF_LIST} | LTF={LTF}\n"
                f"REQUIRE_BOS={REQUIRE_BOS} | IMPULSE={IMPULSE_MIN_PERCENT}%\n"
                f"Scan pertama dimulai..."
            )
        )
    except Exception as e:
        logger.error(f"Gagal kirim notif startup: {e}")

    # Jalankan scan pertama LANGSUNG saat startup
    logger.info("Menjalankan scan pertama saat startup...")
    try:
        await check_and_alert(app)
        logger.info("Scan pertama selesai.")
    except Exception as e:
        logger.error(f"Scan pertama gagal: {e}")
