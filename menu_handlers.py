from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import ContextTypes

import logging

logger = logging.getLogger(__name__)

import db
from config import *
from keyboards import (
    monitoring_keyboard,
    analisis_keyboard,
    backtest_keyboard,
    pengaturan_keyboard,
    main_keyboard,
)

from core_utils import (
    get_active_symbols,
    detect_order_blocks,
    fetch_klines_df,
)

from market_utils import get_current_price, get_session_info
from handlers import show_trades_page
from stats import format_stats_text
from backtest_handlers import run_backtest_async
from utils import drop_unclosed_last_candle

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

async def inline_callback(update, context: ContextTypes.DEFAULT_TYPE):
    """Handle semua callback dari inline keyboard."""
    query = update.callback_query
    await query.answer()
    data = query.data

    # ── MONITORING ──
    if data == "mon_status":
        symbols = get_active_symbols()
        session_name, session_quality, session_stars = get_session_info()
        await query.edit_message_text(
            f"🤖 Status Bot\n\n"
            f"✅ Online\n"
            f"Memantau: {len(symbols)} pair\n"
            f"Zona dicari di: {', '.join(HTF_LIST)}\n"
            f"Konfirmasi: {LTF}\n"
            f"Cooldown: {ALERT_COOLDOWN_MINUTES} menit\n"
            f"Cek tiap: {CHECK_INTERVAL_MINUTES} menit\n"
            f"Filter trend MA{MA_PERIOD}: {'Aktif' if USE_TREND_FILTER else 'Nonaktif'}\n"
            f"Min harga: ${MIN_PRICE_USD}\n\n"
            f"🕐 Sesi sekarang: {session_name} {session_stars} ({session_quality})",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data="mon_status")
            ]])
        )

    elif data == "mon_pairs":
        symbols = get_active_symbols()
        text = f"📋 {len(symbols)} Pair Dipantau:\n\n" + ", ".join(symbols)
        if len(text) > 4096:
            text = text[:4090] + "..."
        await query.edit_message_text(text)

    elif data == "mon_trades":
        # Reset ke halaman pertama
        context.user_data["trade_page"] = 0
        await show_trades_page(update, context, query)

    elif data == "mon_be_trades":
        """Tampilkan daftar trade yang SL-nya sudah di breakeven."""
        # Cari trade dengan breakeven_triggered = True
        be_trades = {}
        for sym, t in active_trades.items():
            if t.get("breakeven_triggered", False):
                be_trades[sym] = t
        
        if not be_trades:
            await query.edit_message_text(
                "🔒 Breakeven Trades\n\nTidak ada trade yang SL-nya sudah digeser ke breakeven.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Refresh", callback_data="mon_be_trades")
                ]])
            )
            return
        
        total_be = len(be_trades)
        bullish_count = sum(1 for t in be_trades.values() if t["zone_type"] == "bullish")
        bearish_count = total_be - bullish_count
        
        # Hitung PnL rata-rata
        total_pnl = 0.0
        pnl_count = 0
        for sym, t in be_trades.items():
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
            except Exception as e:
                logger.debug(e)
        
        avg_pnl = total_pnl / pnl_count if pnl_count > 0 else 0
        
        # Buat daftar
        lines = [
            f"🔒 Breakeven Trades ({total_be} pair)",
            f"🟢 Bullish: {bullish_count} | 🔴 Bearish: {bearish_count}",
            f"📊 Rata-rata PnL: {avg_pnl:+.2f}%",
            "",
            "📋 Daftar trade (SL sudah di breakeven):",
        ]
        
        count = 0
        for sym, t in be_trades.items():
            count += 1
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
            except Exception as e:
                logger.debug(e)
            
            tp_str = f"{t['tp']:.4f}" if t.get("tp") else "N/A"
            entry_str = f"{t['entry']:.4f}" if t.get("entry") else "N/A"
            
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
                except Exception as e:
                    logger.debug(e)
            
            lines.append(
                f"{count}. {emoji} {sym} ({t['htf']}){entry_time_str}\n"
                f"   Entry: {entry_str} | PnL: {pnl_str}{status} | TP: {tp_str}"
            )
        
        # Batasi jika terlalu banyak
        if len(lines) > 50:
            lines = lines[:47]
            lines.append("\n... dan trade lainnya (gunakan /stats untuk detail)")
        
        text = "\n".join(lines)
        
        if len(text) > 4000:
            text = (
                f"🔒 Breakeven Trades ({total_be} pair)\n"
                f"🟢 Bullish: {bullish_count} | 🔴 Bearish: {bearish_count}\n"
                f"📊 Rata-rata PnL: {avg_pnl:+.2f}%\n\n"
                f"⚠️ Terlalu banyak data untuk ditampilkan.\n"
                f"Gunakan command /stats untuk detail lengkap."
            )
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data="mon_be_trades")
            ]])
        )

    elif data == "trade_next":
        context.user_data["trade_page"] = context.user_data.get("trade_page", 0) + 1
        await show_trades_page(update, context, query)
        
    elif data == "trade_prev":
        current_page = context.user_data.get("trade_page", 0)
        if current_page > 0:
            context.user_data["trade_page"] = current_page - 1
        await show_trades_page(update, context, query)
        
    elif data == "trade_page_info":
        current_page = context.user_data.get("trade_page", 0) + 1
        await query.answer(f"Halaman {current_page}")

    elif data == "mon_stats":
        try:
            stats = db.get_stats()
            pnl = db.get_pnl_summary()
            await query.edit_message_text(
                format_stats_text(stats, "📈 Statistik Alert (semua waktu)", pnl),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Refresh", callback_data="mon_stats")
                ]])
            )
        except Exception as e:
            await query.edit_message_text(f"Gagal ambil statistik: {e}")

    elif data == "mon_daily":
        try:
            stats = db.get_daily_stats()
            pnl = db.get_pnl_summary()
            await query.edit_message_text(
                format_stats_text(stats, "🗓️ Ringkasan 24 Jam Terakhir", pnl),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Refresh", callback_data="mon_daily")
                ]])
            )
        except Exception as e:
            await query.edit_message_text(f"Gagal ambil data harian: {e}")

    # ── ANALISIS ──
    elif data == "ana_zones":
        await query.edit_message_text(
            "🔍 Cek Zona OB\n\nKetik nama pair yang ingin dicek:\nContoh: BTC-USDT-SWAP"
        )
        context.user_data["waiting_for"] = "zones"

    elif data == "ana_price":
        await query.edit_message_text(
            "💰 Cek Harga\n\nKetik nama pair:\nContoh: BTC-USDT-SWAP"
        )
        context.user_data["waiting_for"] = "price"

    # ── BACKTEST ──
    elif data in ("bt_btc_1", "bt_eth_1", "bt_sol_1"):
        symbol_map = {
            "bt_btc_1": "BTC-USDT-SWAP",
            "bt_eth_1": "ETH-USDT-SWAP",
            "bt_sol_1": "SOL-USDT-SWAP",
        }
        symbol = symbol_map[data]
        await query.edit_message_text(f"⏳ Memulai backtest {symbol}, 1 bulan...\nMohon tunggu 1-3 menit.")
        result_text = await run_backtest_async(symbol, 1)
        await context.bot.send_message(chat_id=query.message.chat_id, text=result_text)

    elif data == "bt_custom":
        await query.edit_message_text(
            "✏️ Backtest Custom\n\nKetik nama pair:\nContoh: SOL-USDT-SWAP"
        )
        context.user_data["waiting_for"] = "backtest_symbol"

    # ── PENGATURAN ──
    elif data == "set_config":
        await query.edit_message_text(
            f"⚙️ Konfigurasi Aktif\n\n"
            f"HTF: {', '.join(HTF_LIST)}\n"
            f"LTF: {LTF}\n"
            f"Impulse min: {IMPULSE_MIN_PERCENT}%\n"
            f"Volume multiplier: {VOLUME_MULTIPLIER}x\n"
            f"MA period: {MA_PERIOD}\n"
            f"Filter trend: {'Aktif' if USE_TREND_FILTER else 'Nonaktif'}\n"
            f"Min harga pair: ${MIN_PRICE_USD}\n"
            f"SL buffer fallback: {SL_BUFFER_PERCENT}%\n"
            f"ATR period: {ATR_PERIOD} | ATR multiplier: {ATR_MULTIPLIER}x\n"
            f"Risk/Reward: 1:{RISK_REWARD_RATIO:.0f}\n"
            f"Break of Structure: {'Aktif' if REQUIRE_BOS else 'Nonaktif'}\n"
            f"Fair Value Gap: {'Aktif' if REQUIRE_FVG else 'Nonaktif'}\n"
            f"Mitigation 50%: {'Aktif' if MITIGATION_50PCT else 'Nonaktif'}\n"
            f"Swing lookback: {SWING_LOOKBACK} candle\n"
            f"Sesi London: {SESSION_LONDON_START:02d}:00-{SESSION_LONDON_END:02d}:00 UTC\n"
            f"Sesi NY    : {SESSION_NY_START:02d}:00-{SESSION_NY_END:02d}:00 UTC\n"
            f"Top N pair: {TOP_N_PAIRS}\n"
            f"Cooldown alert: {ALERT_COOLDOWN_MINUTES} menit\n"
            f"Interval scan: {CHECK_INTERVAL_MINUTES} menit"
        )

    elif data == "set_help":
        await query.edit_message_text(
            "❓ Bantuan\n\n"
            "📊 Monitoring — pantau status bot dan statistik\n"
            "📈 Analisis — cek zona OB dan harga pair tertentu\n"
            "🔬 Backtest — uji performa historis strategi OB\n"
            "⚙️ Pengaturan — lihat konfigurasi aktif\n\n"
            "Command manual:\n"
            "/start — tampilkan menu\n"
            "/zones BTC-USDT-SWAP — cek zona OB\n"
            "/backtest BTC-USDT-SWAP 3 — backtest 3 bulan\n"
            "/stats — statistik alert\n"
            "/pairs — daftar pair"
                    )

async def text_input_handler(update, context: ContextTypes.DEFAULT_TYPE):
    """Handle input teks dari user setelah diminta (zones, price, backtest custom)."""
    text = update.message.text.strip().upper()
    waiting = context.user_data.get("waiting_for")

    if waiting == "zones":
        context.user_data.pop("waiting_for", None)
        await update.message.reply_text(f"🔍 Mengambil data zona OB untuk {text}...")
        try:
            ltf_df = fetch_klines_df(text, LTF, LOOKBACK_CANDLES)
            if hasattr(ltf_df, 'iloc'):
                current_price = float(ltf_df[-1]["close"] if isinstance(ltf_df, list) else ltf_df.iloc[-1]["close"])
            else:
                current_price = float(ltf_df[-1]["close"])
            lines = [f"Harga {text} sekarang: {current_price}\n"]
            for htf in HTF_LIST:
                htf_df = fetch_klines_df(text, htf, LOOKBACK_CANDLES)
                htf_df = drop_unclosed_last_candle(htf_df)
                zones = detect_order_blocks(htf_df, MAX_ACTIVE_ZONES_PER_TF)
                lines.append(f"\n📊 Timeframe {htf}:")
                if not zones:
                    lines.append("  Belum ada order block terdeteksi.")
                    continue
                for z in zones:
                    emoji = "🟢" if z["type"] == "bullish" else "🔴"
                    lines.append(f"  {emoji} {z['type'].capitalize()}: {z['bottom']} - {z['top']}")
            await update.message.reply_text("\n".join(lines), reply_markup=main_keyboard())
        except Exception as e:
            await update.message.reply_text(f"Gagal ambil data untuk {text}: {e}", reply_markup=main_keyboard())

    elif waiting == "price":
        context.user_data.pop("waiting_for", None)
        try:
            price = get_current_price(text)
            if price:
                await update.message.reply_text(f"💰 {text}\nHarga sekarang: {price}", reply_markup=main_keyboard())
            else:
                await update.message.reply_text(f"Gagal ambil harga {text}.", reply_markup=main_keyboard())
        except Exception as e:
            await update.message.reply_text(f"Error: {e}", reply_markup=main_keyboard())

    elif waiting == "backtest_symbol":
        context.user_data["backtest_symbol"] = text
        context.user_data["waiting_for"] = "backtest_months"
        await update.message.reply_text(
            f"Pair: {text}\nBerapa bulan data historis? (1-6)\nKetik angkanya:"
        )

    elif waiting == "backtest_months":
        context.user_data.pop("waiting_for", None)
        symbol = context.user_data.pop("backtest_symbol", "BTC-USDT-SWAP")
        try:
            months = max(1, min(int(text), 6))
        except ValueError:
            months = 1
        await update.message.reply_text(f"⏳ Memulai backtest {symbol}, {months} bulan...\nMohon tunggu 1-3 menit.")
        result_text = await run_backtest_async(symbol, months)
        await update.message.reply_text(result_text, reply_markup=main_keyboard())

    else:
        # Bukan input yang ditunggu, abaikan (menu router yang handle)
        pass
