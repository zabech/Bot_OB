from core_utils import detect_order_blocks
from utils import merge_zone_state
from config import *

def build_signal_data(
    zone,
    current_price,
    htf_candles_list,
):
    """
    Hitung seluruh data yang dibutuhkan untuk alert.
    """

    sl, sl_method = calculate_sl_with_atr(
        zone,
        current_price,
        htf_candles_list,
    )

    risk = abs(current_price - sl)

    if zone["type"] == "bullish":
        tp = current_price + risk * RISK_REWARD_RATIO
    else:
        tp = current_price - risk * RISK_REWARD_RATIO

    risk_pct = risk / current_price * 100

    atr_val = calculate_atr(
        htf_candles_list,
        ATR_PERIOD,
    )

    atr_str = f"{atr_val:.4g}" if atr_val else "N/A"

    ma_val = calculate_ma(
        htf_candles_list,
        MA_PERIOD,
    )

    trend_text = (
        f"MA{MA_PERIOD}: {ma_val:.4g} "
        f"({'↑ Uptrend' if current_price > ma_val else '↓ Downtrend'})"
        if ma_val
        else "N/A"
    )

    return {
        "sl": sl,
        "sl_method": sl_method,
        "tp": tp,
        "risk": risk,
        "risk_pct": risk_pct,
        "atr_str": atr_str,
        "trend_text": trend_text,
    }

def build_signal_message(
    symbol,
    zone,
    current_price,
    htf,
    signal,
):
    emoji = "🟢" if zone["type"] == "bullish" else "🔴"

    label = (
        "BULLISH (Demand)"
        if zone["type"] == "bullish"
        else "BEARISH (Supply)"
    )

    fvg_tag = " + FVG ⚡ " if zone.get("has_fvg") else ""

    session_name, session_quality, session_stars = get_session_info()

    return (
        f"{emoji} {symbol} memasuki Order Block {label}{fvg_tag}\n"
        f"Timeframe zona: {htf} | Konfirmasi: {LTF}\n"
        f"Harga sekarang : {current_price}\n"
        f"Zona           : {zone['bottom']} - {zone['top']}\n"
        f"🛑 Stop Loss   : {signal['sl']:.4g} "
        f"({signal['sl_method']}, ATR{ATR_PERIOD}={signal['atr_str']})\n"
        f"🎯 Take Profit : {signal['tp']:.4g} "
        f"(R:R 1:{RISK_REWARD_RATIO:.0f})\n"
        f"⚠️ Risk        : {signal['risk_pct']:.2f}%\n"
        f"📊 Trend ({htf}): {signal['trend_text']}\n"
        f"🕐 Sesi        : {session_name} "
        f"{session_stars} ({session_quality})"
    )

async def send_signal(
    app,
    symbol,
    zone,
    current_price,
    htf,
    htf_candles_list,
):
    logger.info(f"[{symbol}] LOLOS SEMUA FILTER — mengirim alert!")

    signal = build_signal_data(
        zone,
        current_price,
        htf_candles_list,
    )

    message = build_signal_message(
        symbol,
        zone,
        current_price,
        htf,
        signal,
    )

    await send_telegram_alert(
        app,
        message,
    )

    zone["mitigated"] = True
    logger.info(f"[{symbol}] Alert terkirim ke Telegram.")

    save_active_trade(
        symbol,
        zone,
        current_price,
        htf,
        signal,
    )

    save_signal_to_db(
        symbol,
        zone,
        current_price,
        htf,
        signal,
    )

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

        await send_signal(
            app,
            symbol,
            zone,
            current_price,
            htf,
            htf_candles_list,
        )

def save_active_trade(
    symbol,
    zone,
    current_price,
    htf,
    signal,
):
    active_trades[symbol] = {
        "entry": current_price,
        "sl": signal["sl"],
        "tp": signal["tp"],
        "zone_type": zone["type"],
        "htf": htf,
        "entry_time": datetime.now(timezone.utc).isoformat(),
    }

def save_signal_to_db(
    symbol,
    zone,
    current_price,
    htf,
    signal,
):
    try:
        db.record_alert(
            symbol=symbol,
            zone_type=zone["type"],
            htf=htf,
            ltf=LTF,
            entry_price=current_price,
            zone_top=zone["top"],
            zone_bottom=zone["bottom"],
            invalidation=signal["sl"],
            target=signal["tp"],
            entry_time=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as e:
        logger.error(f"Gagal simpan alert ke database: {e}")

async def send_telegram_alert(app, message):
    await app.bot.send_message(
        chat_id=CHAT_ID,
        text=message,
    )
