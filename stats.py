from telegram import Update
from telegram.ext import ContextTypes
import db
import logging
from config import CHAT_ID
from keyboards import stats_month_keyboard, _MONTH_ID

logger = logging.getLogger(__name__)

def format_stats_text(stats: dict, title: str, pnl: dict = None) -> str:
    """Format dict statistik jadi teks pesan Telegram."""
    win_rate_text = (
        f"{stats['win_rate']:.1f}%"
        if stats.get("win_rate") is not None
        else "belum ada data selesai"
    )

    lines = [
        f"{title}\n",
        f"Total alert: {stats['total']}",
        f"Masih berjalan (open): {stats['open']}",
        f"✅ Kena TP (hit_target): {stats['hit_target']}",
        f"❌ Kena SL (invalidated): {stats['invalidated']}",
        f"Win rate (TP vs SL): {win_rate_text}",
    ]

    resolved = (stats.get("hit_target") or 0) + (stats.get("invalidated") or 0)
    if resolved:
        lines.append(f"Trade selesai: {resolved}")

    if pnl and pnl.get("total_closed"):
        total_tp = float(pnl.get("total_tp_pnl") or 0)
        total_sl = float(pnl.get("total_sl_pnl") or 0)
        hasil = total_tp + total_sl
        lines.append(f"\nTotal TP: +{total_tp:.1f}%")
        lines.append(f"Total SL: {total_sl:.1f}%")
        lines.append(f"Hasil: {hasil:+.1f}%")

    if stats.get("top_pairs"):
        lines.append("\n🔝 Pair paling sering alert:")
        for p in stats["top_pairs"]:
            lines.append(f"  {p['symbol']}: {p['count']}x")

    return "\n".join(lines)


def format_month_title(year: int, month: int) -> str:
    name = _MONTH_ID.get(month, str(month))
    return f"📈 Statistik Alert — {name} {year}"


async def stats_now(update, context: ContextTypes.DEFAULT_TYPE):
    """
    /stats → tampilkan pilihan bulan (bukan langsung semua waktu).
    """
    try:
        months = db.get_available_months(limit=12)
    except Exception as e:
        await update.message.reply_text(f"Gagal ambil daftar bulan: {e}")
        return

    if not months:
        await update.message.reply_text(
            "Belum ada data alert di database.\n"
            "Statistik bulanan akan muncul setelah ada alert."
        )
        return

    await update.message.reply_text(
        "📈 Statistik Alert\n\nPilih bulan:",
        reply_markup=stats_month_keyboard(months),
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
