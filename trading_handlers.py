from telegram.ext import CommandHandler

def register_trading_handlers(app):
    app.add_handler(CommandHandler("pairs", pairs_now))
