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
