from telegram.ext import Application
from main import (
    pairs_now,
    zones_now,
    stats_now,
    backtest_command,
)

def register_trading_handlers(app):
    app.add_handler(CommandHandler("pairs", pairs_now))
    app.add_handler(CommandHandler("zones", zones_now))
    app.add_handler(CommandHandler("stats", stats_now))
    app.add_handler(CommandHandler("backtest", backtest_command))
