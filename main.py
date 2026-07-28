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

def calculate_invalidation(zone: dict) -> float:
    return ob_core.calculate_invalidation(zone)


def find_nearest_opposite_target(zone: dict, current_price: float, all_zones_for_symbol: dict) -> Optional[float]:
    return ob_core.find_nearest_opposite_target(zone, current_price, all_zones_for_symbol)


def calculate_risk_reward(zone: dict, current_price: float, target: Optional[float]) -> str:
    return ob_core.calculate_risk_reward(zone, current_price, target)


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

async def check_active_trade(app, symbol: str, current_price: float) -> bool:
    """
    Cek apakah trade aktif untuk pair ini sudah resolved (TP atau SL tercapai).
    Juga menangani trailing stop: kalau harga mencapai +1R, SL digeser ke breakeven (entry).
    Return True kalau pair masih punya trade aktif yang belum selesai (tidak boleh sinyal baru).
    Return False kalau tidak ada trade aktif (boleh sinyal baru).
    """
    trade = active_trades.get(symbol)
    if not trade:
        return False

    sl = trade["sl"]
    tp = trade["tp"]
    zone_type = trade["zone_type"]
    entry = trade["entry"]
    htf = trade["htf"]
    breakeven_triggered = trade.get("breakeven_triggered", False)

    # Guard: kalau tp None, skip cek TP
    if tp is None:
        hit_tp = False
    else:
        hit_tp = (zone_type == "bullish" and current_price >= tp) or \
                 (zone_type == "bearish" and current_price <= tp)

    hit_sl = (zone_type == "bullish" and current_price <= sl) or \
             (zone_type == "bearish" and current_price >= sl)

    # ── Trailing Stop: geser SL ke breakeven setelah +1R ──────────────
    if not breakeven_triggered and tp is not None:
        risk = abs(entry - sl)
        one_r_target = entry + risk if zone_type == "bullish" else entry - risk

        reached_1r = (zone_type == "bullish" and current_price >= one_r_target) or \
                     (zone_type == "bearish" and current_price <= one_r_target)

        if reached_1r and abs(sl - entry) > entry * 0.0001:  # SL belum di breakeven
            # Geser SL ke entry (breakeven)
            active_trades[symbol]["sl"] = entry
            active_trades[symbol]["breakeven_triggered"] = True
            sl = entry  # update lokal juga

            pnl_pct = abs(current_price - entry) / entry * 100
            await app.bot.send_message(
                chat_id=CHAT_ID,
                text=(
                    f"🔒 {symbol} SL DIGESER KE BREAKEVEN\n"
                    f"Timeframe: {htf} | {zone_type.capitalize()}\n"
                    f"Entry: {entry:.4g}\n"
                    f"SL baru: {entry:.4g} (breakeven)\n"
                    f"TP: {tp:.4g}\n"
                    f"PnL saat ini: +{pnl_pct:.2f}% (+1R tercapai)"
                )
            )
            try:
                db.update_alert_sl(symbol, entry)
            except Exception:
                pass

    if hit_tp:
        risk = abs(entry - sl)
        profit_pct = abs(current_price - entry) / entry * 100
        
        entry_time = trade.get("entry_time")
        duration_str = format_duration(entry_time) if entry_time else "N/A"
        
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"✅ {symbol} TP TERCAPAI!\n"
                f"Timeframe: {htf} | {zone_type.capitalize()}\n"
                f"Entry: {entry:.4g} → TP: {tp:.4g}\n"
                f"Profit: +{profit_pct:.2f}%\n"
                f"⏱️ Durasi: {duration_str}\n\n"
                f"Pair kini terbuka untuk sinyal berikutnya."
            )
        )
        del active_trades[symbol]
        try:
            profit_pct_final = abs(current_price - entry) / entry * 100
            db.resolve_alert_by_symbol(symbol, "hit_target", pnl_pct=profit_pct_final)
        except Exception:
            pass
        return False

    if hit_sl:
        if zone_type == "bullish":
            pnl_pct = (sl - entry) / entry * 100
        else:
            pnl_pct = (entry - sl) / entry * 100
        
        pnl_str = f"+{pnl_pct:.2f}% (breakeven)" if breakeven_triggered else f"-{abs(pnl_pct):.2f}%"
        emoji = "⚖️" if breakeven_triggered else "❌"
        label = "BREAKEVEN" if breakeven_triggered else "SL TERKENA"
        status = "hit_target" if breakeven_triggered else "invalidated"
        
        entry_time = trade.get("entry_time")
        duration_str = format_duration(entry_time) if entry_time else "N/A"
        
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"{emoji} {symbol} {label}!\n"
                f"Timeframe: {htf} | {zone_type.capitalize()}\n"
                f"Entry: {entry:.4g} → SL: {sl:.4g}\n"
                f"PnL: {pnl_str}\n"
                f"⏱️ Durasi: {duration_str}\n\n"
                f"Pair kini terbuka untuk sinyal berikutnya."
            )
        )
        del active_trades[symbol]
        try:
            db.resolve_alert_by_symbol(symbol, status, pnl_pct=pnl_pct)
        except Exception:
            pass
        return False
        
    return True

async def check_symbol(app, symbol: str) -> bool:
    """Cek satu pair di semua HTF, kirim alert kalau ada zona valid + konfirmasi LTF.
    Return True kalau berhasil dicek, False kalau gagal (untuk health tracking)."""
    global active_zones
    if symbol not in active_zones:
        active_zones[symbol] = {tf: [] for tf in HTF_LIST}

    try:
        ltf_df = fetch_klines_df(symbol, LTF, LOOKBACK_CANDLES)
        if hasattr(ltf_df, 'iloc'):
            current_price = float(ltf_df[-1]["close"] if isinstance(ltf_df, list) else ltf_df.iloc[-1]["close"])
        else:
            current_price = float(ltf_df[-1]["close"])

        # Cek trade aktif dulu
        still_active = await check_active_trade(app, symbol, current_price)
        if still_active:
            logger.info(f"[{symbol}] Trade aktif belum selesai, skip sinyal baru.")
            return True

        for htf in HTF_LIST:
            htf_df = fetch_klines_df(symbol, htf, LOOKBACK_CANDLES)
            detected = detect_order_blocks(htf_df, MAX_ACTIVE_ZONES_PER_TF)
            detected = merge_zone_state(active_zones[symbol].get(htf, []), detected)
            active_zones[symbol][htf] = detected

            if hasattr(htf_df, 'to_dict'):
                htf_candles_list = htf_df.to_dict("records")
            else:
                htf_candles_list = htf_df

            for zone in detected:
                if zone["mitigated"]:
                    logger.info(f"[{symbol}] Zona {zone['type']} sudah mitigated, skip.")
                    continue

                price_in_zone = zone["bottom"] <= current_price <= zone["top"]
                if not price_in_zone:
                    continue

                logger.info(f"[{symbol}] Harga {current_price} MASUK zona {zone['type']} di {htf}.")

                if not candle_is_closed(ltf_df, LTF):
                    logger.info(f"[{symbol}] BLOCKED — candle LTF belum close.")
                    continue

                if not ltf_shows_reaction(ltf_df, zone):
                    logger.info(f"[{symbol}] BLOCKED — ltf_shows_reaction gagal.")
                    continue

                if not trend_allows_zone(zone, current_price, htf_candles_list):
                    logger.info(f"[{symbol}] BLOCKED — zona berlawanan dengan trend.")
                    zone["mitigated"] = True
                    continue

                logger.info(f"[{symbol}] LOLOS SEMUA FILTER — mengirim alert!")

                emoji = "🟢" if zone["type"] == "bullish" else "🔴"
                label = "BULLISH (Demand)" if zone["type"] == "bullish" else "BEARISH (Supply)"
                fvg_tag = " + FVG ⚡" if zone.get("has_fvg") else ""

                session_name, session_quality, session_stars = get_session_info()

                sl, sl_method = calculate_sl_with_atr(zone, current_price, htf_candles_list)
                risk = abs(current_price - sl)

                if zone["type"] == "bullish":
                    tp = current_price + risk * RISK_REWARD_RATIO
                else:
                    tp = current_price - risk * RISK_REWARD_RATIO

                risk_pct = (risk / current_price * 100)
                atr_val = calculate_atr(htf_candles_list, ATR_PERIOD)
                atr_str = f"{atr_val:.4g}" if atr_val else "N/A"

                ma_val = calculate_ma(htf_candles_list, MA_PERIOD)
                trend_text = f"MA{MA_PERIOD}: {ma_val:.4g} ({'↑ Uptrend' if current_price > ma_val else '↓ Downtrend'})" if ma_val else "N/A"

                await app.bot.send_message(
                    chat_id=CHAT_ID,
                    text=(
                        f"{emoji} {symbol} memasuki Order Block {label}{fvg_tag}\n"
                        f"Timeframe zona: {htf} | Konfirmasi: {LTF}\n"
                        f"Harga sekarang : {current_price}\n"
                        f"Zona           : {zone['bottom']} - {zone['top']}\n"
                        f"🛑 Stop Loss   : {sl:.4g} ({sl_method}, ATR{ATR_PERIOD}={atr_str})\n"
                        f"🎯 Take Profit : {tp:.4g} (R:R 1:{RISK_REWARD_RATIO:.0f})\n"
                        f"⚠️ Risk        : {risk_pct:.2f}%\n"
                        f"📊 Trend ({htf}): {trend_text}\n"
                        f"🕐 Sesi        : {session_name} {session_stars} ({session_quality})"
                    ),
                )
                zone["mitigated"] = True
                logger.info(f"[{symbol}] Alert terkirim ke Telegram.")

                # Simpan trade aktif
                active_trades[symbol] = {
                    "entry": current_price,
                    "sl": sl,
                    "tp": tp,
                    "zone_type": zone["type"],
                    "htf": htf,
                    "entry_time": datetime.now(timezone.utc).isoformat(),
                }

                # Simpan ke database
                try:
                    db.record_alert(
                        symbol=symbol, zone_type=zone["type"], htf=htf, ltf=LTF,
                        entry_price=current_price, zone_top=zone["top"], zone_bottom=zone["bottom"],
                        invalidation=sl,
                        target=tp,
                        entry_time=datetime.now(timezone.utc).isoformat(),
                    )
                except Exception as e:
                    logger.error(f"Gagal simpan alert ke database: {e}")

        return True

    except Exception as e:
        logger.error(f"Gagal cek {symbol}: {e}")
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
