from telegram import Update
from telegram.ext import ContextTypes

from config import (
    HTF_LIST,
    LTF,
    LOOKBACK_CANDLES,
    MAX_ACTIVE_ZONES_PER_TF,
)

from core_utils import (
    fetch_klines_df,
    detect_order_blocks,
)
from utils import drop_unclosed_last_candle

async def zones_now(update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Gunakan format: /zones SYMBOL\nContoh: /zones BTC-USDT-SWAP")
        return

    symbol = args[0].upper()
    try:
        ltf_df = fetch_klines_df(symbol, LTF, LOOKBACK_CANDLES)
        current_price = float(ltf_df[-1]["close"] if isinstance(ltf_df, list) else ltf_df.iloc[-1]["close"])

        lines = [f"Harga {symbol} sekarang: {current_price}\n"]
        for htf in HTF_LIST:
            htf_df = fetch_klines_df(symbol, htf, LOOKBACK_CANDLES)
            htf_df = drop_unclosed_last_candle(htf_df)
            zones = detect_order_blocks(htf_df, MAX_ACTIVE_ZONES_PER_TF)

            lines.append(f"\n📊 Timeframe {htf}:")
            if not zones:
                lines.append("  Belum ada order block terdeteksi.")
                continue
            for z in zones:
                emoji = "🟢" if z["type"] == "bullish" else "🔴"
                lines.append(f"  {emoji} {z['type'].capitalize()}: {z['bottom']} - {z['top']}")

        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Gagal ambil data untuk {symbol}: {e}")
