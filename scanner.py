from trade_manager import (
    check_active_trade,
    check_open_alerts,
)

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

async def check_and_alert(app):
    symbols = get_active_symbols()
    if not symbols:
        logger.warning("Belum ada daftar pair untuk dipantau.")
        return

    logger.info(f"Mulai scan {len(symbols)} pair (batch size {BATCH_SIZE})...")

    failed_count = 0
    for i in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[i:i + BATCH_SIZE]
        results = await asyncio.gather(*(check_symbol(app, s) for s in batch))
        failed_count += results.count(False)
        if i + BATCH_SIZE < len(symbols):
            await asyncio.sleep(BATCH_DELAY_SECONDS)

    total = len(symbols)
    failure_pct = (failed_count / total * 100) if total else 0
    logger.info(f"Scan selesai: {total - failed_count}/{total} pair berhasil ({failure_pct:.0f}% gagal).")

    if failure_pct >= FAILURE_ALERT_THRESHOLD_PERCENT:
        await send_health_alert(app, failed_count, total)

    await check_open_alerts()
