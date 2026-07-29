from trade_manager import (
    check_active_trade,
    check_open_alerts,
)
from signal_engine import send_signal

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

        await send_signal(
        app=app,
        symbol=symbol,
        zone=zone,
        current_price=current_price,
        htf=htf,
        htf_candles_list=htf_candles_list,
        )

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
