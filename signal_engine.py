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

    signal = build_signal_data(
    zone,
    current_price,
    htf_candles_list,
    )

    sl = signal["sl"]
    sl_method = signal["sl_method"]
    tp = signal["tp"]
    risk = signal["risk"]
    risk_pct = signal["risk_pct"]
    atr_str = signal["atr_str"]
    trend_text = signal["trend_text"]

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

def prepare_zones(
    symbol: str,
    htf: str,
    htf_df,
    active_zones,
):
    """
    Deteksi dan sinkronkan order block untuk satu timeframe.
    Return list zona aktif.
    """

    detected = detect_order_blocks(
        htf_df,
        MAX_ACTIVE_ZONES_PER_TF,
    )

    detected = merge_zone_state(
        active_zones[symbol].get(htf, []),
        detected,
    )

    active_zones[symbol][htf] = detected

    return detected

def normalize_htf_candles(htf_df):
    """Ubah HTF dataframe menjadi list candle."""
    if hasattr(htf_df, "to_dict"):
        return htf_df.to_dict("records")
    return htf_df

async def validate_zone(
    symbol: str,
    zone: dict,
    current_price: float,
    ltf_df,
    htf_candles_list,
):
    """
    Validasi apakah zona layak mengirim sinyal.
    Return True jika lolos semua filter.
    """

    if zone["mitigated"]:
        logger.info(f"[{symbol}] Zona {zone['type']} sudah mitigated, skip.")
        return False

    if not (zone["bottom"] <= current_price <= zone["top"]):
        return False

    if not candle_is_closed(ltf_df, LTF):
        logger.info(f"[{symbol}] BLOCKED — candle LTF belum close.")
        return False

    if not ltf_shows_reaction(ltf_df, zone):
        logger.info(f"[{symbol}] BLOCKED — ltf_shows_reaction gagal.")
        return False

    if not trend_allows_zone(zone, current_price, htf_candles_list):
        logger.info(f"[{symbol}] BLOCKED — zona berlawanan dengan trend.")
        zone["mitigated"] = True
        return False

    return True

async def process_symbol(
    app,
    symbol,
    current_price,
    ltf_df,
    htf,
    htf_df,
    active_zones,
):
    detected = prepare_zones(
        symbol,
        htf,
        htf_df,
        active_zones,
    )
    
    htf_candles_list = normalize_htf_candles(htf_df)

    for zone in detected:
        if not await validate_zone(
            symbol,
            zone,
            current_price,
            ltf_df,
            htf_candles_list,
        ):
            continue
