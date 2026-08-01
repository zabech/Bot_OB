from dotenv import load_dotenv
load_dotenv()
from config import *
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    Message_handler,
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

    logger.info("Bot mulai polling...")
    app.run_polling()
        
if __name__ == "__main__":
    main()
