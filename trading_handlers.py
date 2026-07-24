from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
import db

from main import (
    fetch_klines_df,
    detect_order_blocks,
    HTF_LIST,
    LTF,
    LOOKBACK_CANDLES,
    MAX_ACTIVE_ZONES_PER_TF,
)

def register_trading_handlers(app):
    app.add_handler(CommandHandler("pairs", pairs_now))
    
async def pairs_now(update, context: ContextTypes.DEFAULT_TYPE):
    symbols = get_active_symbols()
    if not symbols:
        await update.message.reply_text("Daftar pair belum tersedia, coba lagi sebentar.")
        return
    await update.message.reply_text(
        f"Memantau {len(symbols)} pair:\n" + ", ".join(symbols)
    )
