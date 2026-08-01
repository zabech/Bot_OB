from dotenv import load_dotenv
load_dotenv()
from config import *
import os
import time
import logging
import asyncio
import pandas as pd
from typing import Optional
from collections import defaultdict
from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ConversationHandler, MessageHandler, filters, ContextTypes
)
from admin import register_admin_handlers
import db
import ob_core
from ob_core import calculate_atr
from datetime import datetime, timezone
from trading_handlers import register_trading_handlers
from status import register_status_handlers
from zones import zones_now
from core_utils import (
    get_top_volume_pairs,
    get_active_symbols,
    fetch_klines_df,
    detect_order_blocks,
    ltf_shows_reaction,
)
from stats import (
    format_stats_text,
    stats_now,
)
from keyboards import (
    main_keyboard,
    monitoring_keyboard,
    analisis_keyboard,
    backtest_keyboard,
    pengaturan_keyboard,
)
from backtest_handlers import (
    backtest_command,
    run_backtest_async,
)
from scanner import (
    check_active_trade,
    check_symbol,
    check_open_alerts,
)
from utils import (
    interval_to_seconds,
    candle_is_closed,
    merge_zone_state,
    format_duration,
)
from menu_handlers import (
    menu_router,
    inline_callback,
    text_input_handler,
)
from startup import on_startup

# State untuk ConversationHandler
WAITING_SYMBOL_ZONES = 1
WAITING_SYMBOL_BACKTEST = 2
WAITING_MONTHS_BACKTEST = 3

def get_session_info() -> tuple:
    """
    Tentukan sesi trading saat ini berdasarkan jam UTC.
    Return: (nama_sesi, label_kualitas, emoji_bintang)

    Sesi dan kualitasnya:
    - London-NY Overlap (13:00-16:00 UTC) → Premium ⭐⭐⭐
    - London (07:00-13:00 UTC)            → Aktif   ⭐⭐
    - New York (16:00-22:00 UTC)          → Aktif   ⭐⭐
    - Asia (22:00-07:00 UTC)              → Rendah  ⭐
    """
    from datetime import datetime, timezone
    hour_utc = datetime.now(timezone.utc).hour

    in_london = SESSION_LONDON_START <= hour_utc < SESSION_LONDON_END
    in_ny = SESSION_NY_START <= hour_utc < SESSION_NY_END
    in_overlap = in_london and in_ny

    if in_overlap:
        return ("London-NY Overlap", "Premium", "⭐⭐⭐")
    elif in_london:
        return ("London", "Aktif", "⭐⭐")
    elif in_ny:
        return ("New York", "Aktif", "⭐⭐")
    else:
        return ("Asia", "Rendah", "⭐")


def calculate_atr(candles, period: int) -> Optional[float]:
    """Hitung ATR dari list of dict candle."""
    if hasattr(candles, 'to_dict'):
        candles = candles.to_dict("records")
    if len(candles) < period + 1:
        return None
    true_ranges = []
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    if len(true_ranges) < period:
        return None
    return sum(true_ranges[-period:]) / period


def calculate_ma(candles, period: int) -> Optional[float]:
    """Hitung Moving Average dari close price N candle terakhir."""
    if hasattr(candles, 'to_dict'):
        candles = candles.to_dict("records")
    if len(candles) < period:
        return None
    closes = [c["close"] if isinstance(c, dict) else float(c["close"]) for c in candles[-period:]]
    return sum(closes) / len(closes)

def get_current_price(symbol: str) -> Optional[float]:
    """Ambil harga terakhir pair dari endpoint ticker OKX."""
    try:
        data = ob_core.okx_get("/api/v5/market/ticker", {"instId": symbol})
        return float(data["data"][0]["last"])
    except Exception:
        return None

def trend_allows_zone(zone: dict, current_price: float, htf_candles) -> bool:
    """
    Filter trend: cek apakah arah zona OB searah dengan trend MA50 HTF.
    - Bullish OB valid hanya kalau harga di atas MA50 (uptrend / area demand)
    - Bearish OB valid hanya kalau harga di bawah MA50 (downtrend / area supply)
    Kalau USE_TREND_FILTER=false atau MA tidak bisa dihitung, lewatkan filter ini.
    """
    if not USE_TREND_FILTER:
        return True

    if isinstance(htf_candles, list):
        candles_list = htf_candles
    else:
        candles_list = htf_candles.to_dict("records") if hasattr(htf_candles, 'to_dict') else list(htf_candles)

    ma = calculate_ma(candles_list, MA_PERIOD)
    if ma is None:
        return True  # tidak cukup data, jangan blokir

    if zone["type"] == "bullish" and current_price > ma:
        return True   # harga di atas MA -> uptrend -> bullish OB valid
    if zone["type"] == "bearish" and current_price < ma:
        return True   # harga di bawah MA -> downtrend -> bearish OB valid
    return False
    
async def send_health_alert(app, failed: int, total: int):
    """Kirim notifikasi ke Telegram kalau terlalu banyak pair gagal dicek dalam satu siklus,
    dengan cooldown agar tidak spam notifikasi yang sama berulang-ulang."""
    now = time.time()
    if (now - last_health_alert_time["ts"]) < HEALTH_ALERT_COOLDOWN_MINUTES * 60:
        return  # masih dalam cooldown, skip

    try:
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"⚠️ Peringatan: {failed}/{total} pair gagal dicek di siklus terakhir.\n"
                f"Kemungkinan ada gangguan koneksi atau API OKX sedang bermasalah.\n"
                f"Bot tetap berjalan dan akan terus mencoba di siklus berikutnya."
            ),
        )
        last_health_alert_time["ts"] = now
    except Exception as e:
        logger.error(f"Gagal kirim health alert: {e}")

# ── Command handlers ─────────────────────────────────────────
async def start(update, context: ContextTypes.DEFAULT_TYPE):
    # Reset page state
    context.user_data["trade_page"] = 0
    
    symbols = get_active_symbols()
    await update.message.reply_text(
        f"🤖 Bot OB aktif ✅\n\n"
        f"Memantau {len(symbols)} pair | {', '.join(HTF_LIST)} | Konfirmasi {LTF}\n"
        f"Cek tiap {CHECK_INTERVAL_MINUTES} menit\n\n"
        f"Pilih menu di bawah:",
        reply_markup=main_keyboard()
    )

async def pairs_now(update, context: ContextTypes.DEFAULT_TYPE):
    symbols = get_active_symbols()
    if not symbols:
        await update.message.reply_text("Daftar pair belum tersedia, coba lagi sebentar.")
        return
    await update.message.reply_text(
        f"Memantau {len(symbols)} pair:\n" + ", ".join(symbols)
    )

async def show_trades_page(update, context, query):
    """Fungsi helper untuk menampilkan halaman trade tertentu."""
    if not active_trades:
        await query.edit_message_text(
            "💼 Trade Aktif\n\nTidak ada trade aktif saat ini.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data="mon_trades")
            ]])
        )
        return
    
    total_trades = len(active_trades)
    bullish_count = sum(1 for t in active_trades.values() if t["zone_type"] == "bullish")
    bearish_count = total_trades - bullish_count
    
    # Hitung total PnL
    total_pnl = 0.0
    pnl_count = 0
    for sym, t in active_trades.items():
        try:
            price = get_current_price(sym)
            if price and t.get("entry"):
                entry = t["entry"]
                if t["zone_type"] == "bullish":
                    pnl = (price - entry) / entry * 100
                else:
                    pnl = (entry - price) / entry * 100
                total_pnl += pnl
                pnl_count += 1
        except Exception:
            pass
    
    avg_pnl = total_pnl / pnl_count if pnl_count > 0 else 0
    
    # ── Buat daftar semua trade dengan SORTING ──
    all_trades = []
    
    # Sortir berdasarkan entry_time (terbaru di atas)
    # Jika tidak ada entry_time, sortir berdasarkan symbol
    sorted_trades = sorted(
        active_trades.items(),
        key=lambda x: x[1].get("entry_time", x[0]),
        reverse=True
    )
    
    for idx, (sym, t) in enumerate(sorted_trades, 1):
        emoji = "🟢" if t["zone_type"] == "bullish" else "🔴"
        
        pnl_str = "N/A"
        status = ""
        try:
            price = get_current_price(sym)
            if price and t.get("entry"):
                entry = t["entry"]
                if t["zone_type"] == "bullish":
                    pnl = (price - entry) / entry * 100
                else:
                    pnl = (entry - price) / entry * 100
                pnl_emoji = "📈" if pnl >= 0 else "📉"
                pnl_str = f"{pnl_emoji} {pnl:+.1f}%"
                
                if t.get("tp") and t.get("sl") and abs(t["sl"] - entry) > 0:
                    risk = abs(entry - t["sl"])
                    progress = abs(price - entry) / risk
                    status = f" ({progress:.1f}R)"
        except Exception:
            pass
        
        tp_str = f"{t['tp']:.4f}" if t.get("tp") else "N/A"
        sl_str = f"{t['sl']:.4f}" if t.get("sl") else "N/A"
        
        # Entry time
        entry_time_str = ""
        if t.get("entry_time"):
            try:
                from datetime import datetime, timezone
                entry_time = datetime.fromisoformat(t["entry_time"])
                if entry_time.tzinfo is None:
                    entry_time = entry_time.replace(tzinfo=timezone.utc)
                duration = datetime.now(timezone.utc) - entry_time
                hours = int(duration.total_seconds() / 3600)
                if hours > 24:
                    days = hours // 24
                    hours = hours % 24
                    entry_time_str = f" ({days}d {hours}h)"
                else:
                    entry_time_str = f" ({hours}h)"
            except Exception:
                pass
        
        all_trades.append({
            "num": idx,  # <-- NOMOR URUT BERDASARKAN SORTING
            "emoji": emoji,
            "symbol": sym,
            "htf": t["htf"],
            "pnl": pnl_str,
            "status": status,
            "sl": sl_str,
            "tp": tp_str,
            "entry_time": entry_time_str,
            "breakeven_triggered": t.get("breakeven_triggered", False),
        })
    
    # ── Pagination ──
    items_per_page = 10
    total_pages = (len(all_trades) + items_per_page - 1) // items_per_page
    current_page = context.user_data.get("trade_page", 0)
    
    if current_page >= total_pages:
        current_page = total_pages - 1
    if current_page < 0:
        current_page = 0
    
    start_idx = current_page * items_per_page
    end_idx = min(start_idx + items_per_page, len(all_trades))
    page_trades = all_trades[start_idx:end_idx]
    
    # ── Buat pesan ──
    lines = [
        f"💼 Trade Aktif ({total_trades} pair) - Halaman {current_page + 1}/{total_pages}",
        f"🟢 Bullish: {bullish_count} | 🔴 Bearish: {bearish_count}",
        f"📊 Rata-rata PnL: {avg_pnl:+.2f}%",
        "",
        "📋 Daftar trade:",
    ]
    
    for t in page_trades:
        be_indicator = " 🔒" if t.get("breakeven_triggered", False) else ""
        lines.append(
            f"{t['num']}. {t['emoji']} {t['symbol']} ({t['htf']}){t['entry_time']}{be_indicator} | "
            f"PnL: {t['pnl']}{t['status']} | "
            f"SL: {t['sl']} | TP: {t['tp']}"
        )
    
    if total_trades > items_per_page:
        lines.append(f"\nHalaman {current_page + 1} dari {total_pages} (total {total_trades} trade)")
    
    text = "\n".join(lines)
    
    # ── Buat tombol navigasi ──
    nav_buttons = []
    
    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data="trade_prev"))
    
    nav_buttons.append(InlineKeyboardButton(f"{current_page + 1}/{total_pages}", callback_data="trade_page_info"))
    
    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data="trade_next"))
    
    nav_buttons.append(InlineKeyboardButton("🔄 Refresh", callback_data="mon_trades"))
    
    # Buat keyboard
    if len(nav_buttons) > 3:
        keyboard = [nav_buttons[:2], nav_buttons[2:4]]
    else:
        keyboard = [nav_buttons]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

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
