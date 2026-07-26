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
