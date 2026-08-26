"""
Handler backtest untuk Telegram.

Memakai mesin yang SAMA dengan CLI backtest.py / bot live:
  - ob_core.detect_order_blocks (BoS / FVG / sweep / mitigation / ATR impulse)
  - ltf_shows_reaction_advanced (via core_utils)
  - trend_allows_zone
  - macro filter (jika USE_MACRO_FILTER)
  - calculate_sl_with_atr
  - resolve_trade (TP/SL, R-multiple)

Jangan duplikasi logika deteksi di sini — selalu lewat backtest.simulate_pair().
"""

import asyncio
import logging
from collections import defaultdict

from telegram.ext import ContextTypes

from config import *
from backtest import simulate_pair, BACKTEST_DIRECTION

logger = logging.getLogger(__name__)

# Batas bulan di Telegram (blocking + rate limit OKX). CLI tidak dibatasi ini.
_TELEGRAM_MAX_MONTHS = 6


def _format_results(symbol: str, months: int, results: list) -> str:
    """Format hasil simulate_pair menjadi teks Telegram."""
    if not results:
        return (
            f"Backtest {symbol} ({months} bln): tidak ada sinyal.\n"
            f"Mesin: sama dengan live (simulate_pair)\n"
            f"HTF: {', '.join(HTF_LIST)} | LTF: {LTF}\n"
            f"Direction: {BACKTEST_DIRECTION} | "
            f"FVG={REQUIRE_FVG} Trend={USE_TREND_FILTER} Macro={USE_MACRO_FILTER}"
        )

    total = len(results)
    win = sum(1 for r in results if r.get("outcome") == "win")
    loss = sum(1 for r in results if r.get("outcome") == "loss")
    unresolved = sum(1 for r in results if r.get("outcome") == "unresolved")
    resolved = win + loss
    win_rate = f"{win / resolved * 100:.1f}%" if resolved > 0 else "N/A"

    r_values = [
        float(r["r_multiple"])
        for r in results
        if r.get("outcome") in ("win", "loss") and r.get("r_multiple") is not None
    ]
    avg_r = sum(r_values) / len(r_values) if r_values else None
    total_r = sum(r_values) if r_values else None

    by_htf = defaultdict(lambda: {"win": 0, "loss": 0, "total": 0, "r": []})
    by_type = defaultdict(lambda: {"win": 0, "loss": 0, "total": 0})

    for r in results:
        htf = r.get("htf", "?")
        by_htf[htf]["total"] += 1
        if r.get("outcome") == "win":
            by_htf[htf]["win"] += 1
        elif r.get("outcome") == "loss":
            by_htf[htf]["loss"] += 1
        if r.get("r_multiple") is not None and r.get("outcome") in ("win", "loss"):
            by_htf[htf]["r"].append(float(r["r_multiple"]))

        zt = r.get("zone_type", "?")
        by_type[zt]["total"] += 1
        if r.get("outcome") == "win":
            by_type[zt]["win"] += 1
        elif r.get("outcome") == "loss":
            by_type[zt]["loss"] += 1

    htf_lines = []
    for htf, g in sorted(by_htf.items()):
        res = g["win"] + g["loss"]
        wr = f"{g['win'] / res * 100:.1f}%" if res > 0 else "N/A"
        avg = f", avg R {sum(g['r']) / len(g['r']):+.2f}" if g["r"] else ""
        htf_lines.append(f"  {htf}: {g['total']} sinyal, WR {wr}{avg}")

    type_lines = []
    for zt, g in sorted(by_type.items()):
        res = g["win"] + g["loss"]
        wr = f"{g['win'] / res * 100:.1f}%" if res > 0 else "N/A"
        type_lines.append(f"  {zt}: {g['total']} sinyal, WR {wr}")

    avg_r_str = f"{avg_r:+.2f}R" if avg_r is not None else "N/A"
    total_r_str = f"{total_r:+.2f}R" if total_r is not None else "N/A"

    return (
        f"📊 Hasil Backtest {symbol} ({months} bln)\n"
        f"Mesin: sama dengan live (simulate_pair)\n"
        f"HTF: {', '.join(HTF_LIST)} | LTF: {LTF}\n"
        f"Direction: {BACKTEST_DIRECTION}\n\n"
        f"Total sinyal : {total}\n"
        f"Win          : {win}\n"
        f"Loss         : {loss}\n"
        f"Unresolved   : {unresolved}\n"
        f"Win rate     : {win_rate} ({resolved} resolved)\n"
        f"Avg R (net)  : {avg_r_str}\n"
        f"Total R      : {total_r_str}\n\n"
        f"Per timeframe:\n"
        + ("\n".join(htf_lines) if htf_lines else "  (kosong)")
        + "\n\nPer arah:\n"
        + ("\n".join(type_lines) if type_lines else "  (kosong)")
        + f"\n\n*Filter: BOS={REQUIRE_BOS} FVG={REQUIRE_FVG} "
        f"Sweep={REQUIRE_LIQUIDITY_SWEEP} Trend={USE_TREND_FILTER} "
        f"Macro={USE_MACRO_FILTER}\n"
        f"*SL ATR{ATR_PERIOD}×{ATR_MULTIPLIER} | TP R:R 1:{RISK_REWARD_RATIO:g}"
    )


async def run_backtest_async(symbol: str, months: int) -> str:
    """
    Jalankan backtest dengan mesin live (simulate_pair) di thread pool
    agar tidak memblokir event loop Telegram.
    """
    months = max(1, min(int(months), _TELEGRAM_MAX_MONTHS))
    symbol = symbol.strip().upper()

    try:
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None,
            lambda: simulate_pair(symbol, list(HTF_LIST), LTF, months),
        )
        return _format_results(symbol, months, results or [])
    except Exception as e:
        logger.exception(f"Backtest gagal untuk {symbol}")
        return f"Gagal backtest {symbol}: {e}"


async def backtest_command(update, context: ContextTypes.DEFAULT_TYPE):
    """
    /backtest                    -> BTC-USDT-SWAP, 1 bulan
    /backtest ETH-USDT-SWAP      -> 1 pair custom, 1 bulan
    /backtest ETH-USDT-SWAP 3    -> 1 pair custom, 3 bulan (max 6 di Telegram)
    """
    args = context.args or []
    symbol = args[0].upper() if args else "BTC-USDT-SWAP"
    try:
        months = int(args[1]) if len(args) >= 2 else 1
        months = max(1, min(months, _TELEGRAM_MAX_MONTHS))
    except ValueError:
        await update.message.reply_text(
            "Format: /backtest SYMBOL BULAN\n"
            "Contoh: /backtest BTC-USDT-SWAP 3\n"
            f"(Maks {_TELEGRAM_MAX_MONTHS} bulan via Telegram; pakai CLI untuk lebih panjang)"
        )
        return

    await update.message.reply_text(
        f"⏳ Memulai backtest {symbol}, {months} bulan...\n"
        f"Mesin: sama dengan live (simulate_pair)\n"
        f"HTF: {', '.join(HTF_LIST)} | LTF: {LTF}\n"
        f"Estimasi: 1–5 menit (tergantung data OKX), mohon tunggu."
    )

    try:
        result_text = await run_backtest_async(symbol, months)
        if len(result_text) > 4000:
            result_text = result_text[:4000] + "\n…(terpotong)"
        await update.message.reply_text(result_text)
    except Exception as e:
        logger.error(f"Backtest command error: {e}")
        await update.message.reply_text(f"Gagal menjalankan backtest: {e}")
