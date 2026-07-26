from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton

def main_keyboard() -> ReplyKeyboardMarkup:
    """Reply keyboard utama yang selalu tampil di bawah chat."""
    return ReplyKeyboardMarkup(
        [
            ["📊 Monitoring", "📈 Analisis"],
            ["🔬 Backtest",   "⚙️ Pengaturan"],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def monitoring_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Status Bot",       callback_data="mon_status")],
        [InlineKeyboardButton("📋 Daftar Pair",       callback_data="mon_pairs")],
        [InlineKeyboardButton("💼 Trade Aktif",       callback_data="mon_trades")],
        [InlineKeyboardButton("🔒 Breakeven Trades",  callback_data="mon_be_trades")],
        [InlineKeyboardButton("📈 Statistik Alert",   callback_data="mon_stats")],
        [InlineKeyboardButton("🗓️ Ringkasan Harian",  callback_data="mon_daily")],
    ])


def analisis_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Cek Zona OB",       callback_data="ana_zones")],
        [InlineKeyboardButton("💰 Harga Sekarang",    callback_data="ana_price")],
    ])


def backtest_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("₿ Backtest BTC 1 bln",  callback_data="bt_btc_1")],
        [InlineKeyboardButton("Ξ Backtest ETH 1 bln",  callback_data="bt_eth_1")],
        [InlineKeyboardButton("⚡ Backtest SOL 1 bln",  callback_data="bt_sol_1")],
        [InlineKeyboardButton("✏️ Backtest Custom...",   callback_data="bt_custom")],
    ])


def pengaturan_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Info Konfigurasi",   callback_data="set_config")],
        [InlineKeyboardButton("❓ Bantuan",             callback_data="set_help")],
    ])
