import os
import time
import platform
import psutil

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

START_TIME = time.time()


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = int(time.time() - START_TIME)

    days = uptime // 86400
    hours = (uptime % 86400) // 3600
    minutes = (uptime % 3600) // 60
    seconds = uptime % 60

    memory = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=1)
    disk = psutil.disk_usage("/")

    process = psutil.Process(os.getpid())
    ram_mb = process.memory_info().rss / 1024 / 1024

    text = (
        "🤖 Bot Order Block\n\n"
        "🟢 Status : Running\n"
        f"⏱ Uptime : {days}h {hours}j {minutes}m {seconds}d\n"
        f"💻 CPU : {cpu:.1f}%\n"
        f"🧠 RAM Bot : {ram_mb:.1f} MB\n"
        f"💾 Disk : {disk.percent}%\n"
        f"🖥 Sistem : {platform.system()} {platform.release()}\n"
        f"🐍 Python : {platform.python_version()}"
    )

    await update.message.reply_text(text)


def register_status_handlers(app):
    app.add_handler(CommandHandler("status", status_command))
