import os
import platform
import time
import shutil
import subprocess
import psutil

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

ADMIN_ID = int(os.getenv("CHAT_ID"))


def is_admin(update: Update):
    return update.effective_user.id == ADMIN_ID


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(update):
        return

    keyboard = [
        [
            InlineKeyboardButton("📊 Status", callback_data="status"),
            InlineKeyboardButton("💾 Backup", callback_data="backup"),
        ],
        [
            InlineKeyboardButton("🔄 Restart", callback_data="restart"),
            InlineKeyboardButton("📄 Logs", callback_data="logs"),
        ],
    ]

    await update.message.reply_text(
        "🛠 Admin Panel",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "status":
        cpu = psutil.cpu_percent(interval=1)
        ram = psutil.virtual_memory()
        disk = shutil.disk_usage("/")

        text = (
            "🟢 <b>BOT OB STATUS</b>\n\n"
            f"🐧 OS : {platform.system()} {platform.release()}\n"
            f"⚡ CPU : {cpu}%\n"
            f"🧠 RAM : {ram.percent}%\n"
            f"💽 Disk : {disk.used // (1024**3)} GB / {disk.total // (1024**3)} GB\n"
        )

        await query.edit_message_text(
            text,
            parse_mode="HTML"
        )

def register_admin_handlers(app):
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CallbackQueryHandler(button_callback, pattern="^(status|backup|restart|logs)$"))
