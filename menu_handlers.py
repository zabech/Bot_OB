from telegram import Update
from telegram.ext import ContextTypes

import db
from config import *
from keyboards import (
    monitoring_keyboard,
    analisis_keyboard,
    backtest_keyboard,
    pengaturan_keyboard,
    main_keyboard,
)

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
