import asyncio
from trade_manager import (
    check_active_trade,
    check_open_alerts,
)
from signal_engine import process_symbol
from config import *
from core_utils import (
    get_active_symbols,
    fetch_klines_df,
)

from health import send_health_alert

async def check_symbol(app, symbol: str) -> bool:
    """Cek satu pair di semua HTF, kirim alert kalau ada zona valid + konfirmasi LTF.
    Return True kalau berhasil dicek, False kalau gagal (untuk health tracking)."""
    # Mutasi dict di state.py — tidak perlu keyword global
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
            htf_df = fetch_klines_df(
                symbol,
                htf,
                LOOKBACK_CANDLES
            )

            await process_symbol(
                app=app,
                symbol=symbol,
                current_price=current_price,
                ltf_df=ltf_df,
                htf=htf,
                htf_df=htf_df,
                active_zones=active_zones,
            )
               
        return True

    except Exception as e:
        logger.error(f"Gagal cek {symbol}: {e}")
        return False

async def check_and_alert(app):
    symbols = get_active_symbols()

    # PENTING: pair yang masih punya trade aktif HARUS tetap dicek,
    # meski sudah keluar dari daftar top volume. Tanpa ini, trade bisa
    # "mengambang" berbulan-bulan (TP/SL tidak pernah dievaluasi).
    trade_only = [s for s in active_trades.keys() if s not in symbols]
    if trade_only:
        logger.info(
            f"{len(trade_only)} pair hanya ada di active_trades "
            f"(sudah di luar top volume) — tetap dipantau: {trade_only[:5]}"
            f"{'...' if len(trade_only) > 5 else ''}"
        )

    # Gabungkan: top volume dulu, lalu pair trade-only (tanpa duplikat)
    all_symbols = list(symbols) + trade_only

    if not all_symbols:
        logger.warning("Belum ada daftar pair untuk dipantau.")
        return

    logger.info(
        f"Mulai scan {len(all_symbols)} pair "
        f"({len(symbols)} top volume + {len(trade_only)} trade aktif) "
        f"(batch size {BATCH_SIZE})..."
    )

    failed_count = 0
    for i in range(0, len(all_symbols), BATCH_SIZE):
        batch = all_symbols[i:i + BATCH_SIZE]
        results = await asyncio.gather(*(check_symbol(app, s) for s in batch))
        failed_count += results.count(False)
        if i + BATCH_SIZE < len(all_symbols):
            await asyncio.sleep(BATCH_DELAY_SECONDS)

    total = len(all_symbols)
    failure_pct = (failed_count / total * 100) if total else 0
    logger.info(f"Scan selesai: {total - failed_count}/{total} pair berhasil ({failure_pct:.0f}% gagal).")

    if failure_pct >= FAILURE_ALERT_THRESHOLD_PERCENT:
        await send_health_alert(app, failed_count, total)

    await check_open_alerts()
