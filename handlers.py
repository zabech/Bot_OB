from telegram import Update
from telegram.ext import ContextTypes
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

from market_utils import get_current_price

from config import active_trades
from trade_manager import get_current_price
from keyboards import *
from stats import *
from core_utils import get_active_symbols

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
