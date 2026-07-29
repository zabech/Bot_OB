async def send_signal(
    app,
    symbol,
    zone,
    current_price,
    htf,
    htf_candles_list,
):

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

