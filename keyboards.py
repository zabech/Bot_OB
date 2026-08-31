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

_MONTH_ID = {
    1: "Januari", 2: "Februari", 3: "Maret", 4: "April",
    5: "Mei", 6: "Juni", 7: "Juli", 8: "Agustus",
    9: "September", 10: "Oktober", 11: "November", 12: "Desember",
}


def stats_month_keyboard(months: list) -> InlineKeyboardMarkup:
    """
    Keyboard pilihan bulan untuk statistik alert.
    months: list dari db.get_available_months() → year, month, label, count
    """
    rows = []
    row = []
    for m in months:
        label = f"{_MONTH_ID.get(m['month'], m['month'])} {m['year']} ({m['count']})"
        cb = f"stats_month_{m['year']}_{m['month']:02d}"
        row.append(InlineKeyboardButton(label, callback_data=cb))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([
        InlineKeyboardButton("📊 Semua waktu", callback_data="stats_all"),
        InlineKeyboardButton("« Monitoring", callback_data="mon_back"),
    ])
    return InlineKeyboardMarkup(rows)


def stats_detail_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data=f"stats_month_{year}_{month:02d}")],
        [InlineKeyboardButton("« Pilih bulan", callback_data="mon_stats")],
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
