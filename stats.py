from telegram import Update
from telegram.ext import ContextTypes
import db
import logging
from config import CHAT_ID

logger = logging.getLogger(__name__)

def format_stats_text(stats: dict, title: str, pnl: dict = None) -> str:
    """Format dict statistik jadi teks pesan Telegram."""
    win_rate_text = f"{stats['win_rate']:.1f}%" if stats["win_rate"] is not None else "belum ada data selesai"

    lines = [
        f"{title}\n",
        f"Total alert: {stats['total']}",
        f"Masih berjalan (open): {stats['open']}",
        f"Kena target: {stats['hit_target']}",
        f"Invalidasi: {stats['invalidated']}",
        f"Win rate: {win_rate_text}",
    ]

    if pnl and pnl.get("total_closed"):
        lines.append("\n💰 Ringkasan PnL (trade selesai):")
        lines.append(f"  Total PnL   : {pnl['total_pnl']:+.2f}%")
        lines.append(f"  Rata-rata   : {pnl['avg_pnl']:+.2f}% per trade")
        lines.append(f"  Trade terbaik: {pnl['best_trade']:+.2f}%")
        lines.append(f"  Trade terburuk: {pnl['worst_trade']:+.2f}%")

    if stats["top_pairs"]:
        lines.append("\n🔝 Pair paling sering alert:")
        for p in stats["top_pairs"]:
            lines.append(f"  {p['symbol']}: {p['count']}x")

    return "\n".join(lines)


async def stats_now(update, context: ContextTypes.DEFAULT_TYPE):
    try:
        stats = db.get_stats()
        pnl = db.get_pnl_summary()
    except Exception as e:
        await update.message.reply_text(f"Gagal ambil statistik dari database: {e}")
        return
    await update.message.reply_text(
        format_stats_text(stats, "📈 Statistik Alert Order Block (semua waktu)", pnl)
    )


async def send_daily_summary(app):
    """Kirim ringkasan statistik 24 jam terakhir ke Telegram, dijadwalkan 1x sehari."""
    try:
        stats = db.get_daily_stats()
        pnl = db.get_pnl_summary()
    except Exception as e:
        logger.error(f"Gagal ambil statistik harian: {e}")
        return

    text = format_stats_text(stats, "🗓️ Ringkasan Harian (24 jam terakhir)", pnl)
    try:
        await app.bot.send_message(chat_id=CHAT_ID, text=text)
        logger.info("Ringkasan harian terkirim.")
    except Exception as e:
        logger.error(f"Gagal kirim ringkasan harian: {e}")
