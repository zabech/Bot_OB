from dotenv import load_dotenv
load_dotenv()
from config import *
import logging
import traceback
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from admin import register_admin_handlers
import db
import ob_core
from trading_handlers import register_trading_handlers
from status import register_status_handlers
from zones import zones_now

from stats import stats_now

from backtest_handlers import backtest_command
    
from menu_handlers import (
    menu_router,
    inline_callback,
    text_input_handler,
)

from startup import on_startup

from handlers import (
    start,
    pairs_now,
    show_trades_page,
)

# State untuk ConversationHandler
WAITING_SYMBOL_ZONES = 1
WAITING_SYMBOL_BACKTEST = 2
WAITING_MONTHS_BACKTEST = 3

async def global_error_handler(update, context):
    """
    Tangkap SEMUA exception yang tidak tertangani di handler manapun.

    Tanpa ini, kalau ada bug di suatu handler (mis. show_trades_page),
    Telegram-nya cuma "diam" — user tidak lihat error apapun, cuma
    server yang log-nya kelihatan (kalau sempat dicek manual). Ini
    kirim notifikasi singkat ke user + log lengkap ke server supaya
    gampang di-debug.
    """
    error = context.error

    # "Message is not modified" — muncul kalau user tekan Refresh
    # padahal isi pesan belum berubah sama sekali. Bukan bug beneran,
    # aman diabaikan (jangan kirim notif error ke user untuk ini).
    if "Message is not modified" in str(error):
        logger.debug(f"Ignored harmless error: {error}")
        return

    logger.error(
        f"Unhandled exception: {error}\n"
        f"{''.join(traceback.format_exception(type(error), error, error.__traceback__))}"
    )

    try:
        if update and hasattr(update, "effective_chat") and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    "⚠️ Terjadi error saat memproses permintaan ini.\n"
                    "Coba lagi, atau cek log bot kalau berulang."
                ),
            )
    except Exception as notify_err:
        logger.error(f"Gagal kirim notifikasi error ke user: {notify_err}")

    
def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("BOT_TOKEN dan CHAT_ID wajib di-set di environment variables")

    # Pakai konfigurasi retry dari env var untuk semua request ob_core
    ob_core.DEFAULT_MAX_RETRIES = API_MAX_RETRIES
    ob_core.DEFAULT_BACKOFF_SECONDS = API_RETRY_BACKOFF_SECONDS

    try:
        db.init_db()
        db.migrate_db()
    except Exception as e:
        raise RuntimeError(
            f"Gagal inisialisasi database: {e}\n"
            f"Pastikan PostgreSQL addon sudah ditambahkan dan ter-link ke service ini di Railway."
        )

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(on_startup).build()
    
    register_admin_handlers(app)
    register_trading_handlers(app)
    register_status_handlers(app)

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

    # Global error handler — WAJIB ada, tanpa ini exception di handler
    # manapun bikin bot "diam" tanpa feedback apapun ke user.
    app.add_error_handler(global_error_handler)

    logger.info("Bot mulai polling...")
    app.run_polling()
        
if __name__ == "__main__":
    main()
