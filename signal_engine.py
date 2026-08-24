from datetime import datetime, timezone

import db

from core_utils import (
    detect_order_blocks,
    ltf_shows_reaction,
    calculate_sl_with_atr,
    get_current_macro_regime,
)

from market_utils import (
    trend_allows_zone,
    calculate_atr,
    calculate_ma,
    get_session_info,
)

from utils import (
    merge_zone_state,
    candle_is_closed,
    format_duration,
    drop_unclosed_last_candle,
)

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
        f"(R:R 1:{RISK_REWARD_RATIO:g})\n"
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

    # Tandai sudah alert untuk visit ini — JANGAN mark mitigated.
    # Mitigated hanya di-set kalau harga benar-benar menembus zona (50%/penuh).
    # Kalau harga keluar lalu masuk lagi nanti, alerted di-reset → boleh alert ulang.
    zone["alerted"] = True
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

    # PENTING: OKX selalu menaruh candle HTF yang sedang berjalan
    # (belum close) sebagai elemen TERAKHIR. Kalau candle itu ikut
    # dipakai untuk deteksi order block, hasilnya bisa membentuk OB
    # "hantu" yang cuma valid selama candle itu belum selesai —
    # begitu candle-nya close beneran, bentuknya sering berubah dan
    # OB itu tidak akan pernah muncul lagi di data historis/backtest.
    # Buang 1 candle terakhir supaya deteksi cuma pakai candle yang
    # sudah pasti final, sama seperti yang dipakai backtest.py.
    htf_df_closed = drop_unclosed_last_candle(htf_df)

    detected = detect_order_blocks(
        htf_df_closed,
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

def is_zone_mitigated_by_price(zone: dict, current_price: float) -> bool:
    """
    Cek apakah harga saat ini sudah menembus zona (invalidasi struktural).
    - MITIGATION_50PCT=true  → tembus midpoint zona
    - MITIGATION_50PCT=false → tembus full (bottom untuk bullish / top untuk bearish)
    """
    top = zone["top"]
    bottom = zone["bottom"]
    midpoint = (top + bottom) / 2.0

    if zone["type"] == "bullish":
        threshold = midpoint if MITIGATION_50PCT else bottom
        return current_price < threshold
    else:
        threshold = midpoint if MITIGATION_50PCT else top
        return current_price > threshold


def is_price_inside_zone(zone: dict, current_price: float) -> bool:
    return zone["bottom"] <= current_price <= zone["top"]


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

    State zona:
    - mitigated : zona invalid secara struktural (harga tembus 50%/penuh) → mati permanen
    - alerted   : sudah kirim alert saat harga di dalam zona (visit ini).
                  Di-reset kalau harga keluar zona, supaya re-entry bisa alert lagi.
    """

    # 1) Sudah mitigated struktural → skip permanen
    if zone.get("mitigated"):
        logger.info(f"[{symbol}] Zona {zone['type']} sudah mitigated, skip.")
        return False

    # 2) Harga saat ini menembus zona → mark mitigated, skip
    if is_zone_mitigated_by_price(zone, current_price):
        zone["mitigated"] = True
        zone["alerted"] = False
        logger.info(
            f"[{symbol}] Zona {zone['type']} MITIGATED oleh harga "
            f"({current_price:.4g}), skip."
        )
        return False

    # 3) Harga di luar zona (belum mitigated) → reset alerted, skip (tunggu re-entry)
    if not is_price_inside_zone(zone, current_price):
        if zone.get("alerted"):
            zone["alerted"] = False
            logger.info(
                f"[{symbol}] Harga keluar zona {zone['type']} — "
                f"reset alerted (siap alert ulang saat re-entry)."
            )
        return False

    # 4) Harga di dalam zona tapi sudah alert di visit ini → skip (hindari spam)
    if zone.get("alerted"):
        logger.info(
            f"[{symbol}] Zona {zone['type']} sudah alerted di visit ini, skip."
        )
        return False

    # 5) Candle LTF harus sudah close
    if not candle_is_closed(ltf_df, LTF):
        logger.info(f"[{symbol}] BLOCKED — candle LTF belum close.")
        return False

    # 6) Konfirmasi reaksi LTF
    if not ltf_shows_reaction(ltf_df, zone):
        logger.info(f"[{symbol}] BLOCKED — ltf_shows_reaction gagal.")
        return False

    # 7) Trend filter (JANGAN mark mitigated kalau gagal — trend bisa berubah)
    if not trend_allows_zone(zone, current_price, htf_candles_list):
        logger.info(f"[{symbol}] BLOCKED — zona berlawanan dengan trend.")
        return False

    # 8) Macro filter
    if USE_MACRO_FILTER:
        regime = get_current_macro_regime()
        if regime is not None and zone["type"] != regime:
            logger.info(
                f"[{symbol}] BLOCKED — zona {zone['type']} tidak cocok "
                f"dengan regime market makro saat ini ({regime})."
            )
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
