import asyncio
import logging

from trade_manager import (
    check_active_trade,
    check_open_alerts,
)
from signal_engine import process_symbol
from config import *
from core_utils import (
    get_scan_symbols,
    fetch_klines_df,
)

from health import send_health_alert

logger = logging.getLogger(__name__)


async def _fetch_price(symbol: str) -> float:
    ltf_df = fetch_klines_df(symbol, LTF, LOOKBACK_CANDLES)
    if hasattr(ltf_df, "iloc"):
        return float(
            ltf_df[-1]["close"] if isinstance(ltf_df, list) else ltf_df.iloc[-1]["close"]
        )
    return float(ltf_df[-1]["close"])


async def monitor_active_trade_only(app, symbol: str) -> bool:
    """
    Hanya cek TP/SL / breakeven untuk trade aktif.
    Tidak menjalankan deteksi OB / sinyal baru.
    """
    try:
        current_price = await _fetch_price(symbol)
        await check_active_trade(app, symbol, current_price)
        return True
    except Exception as e:
        logger.error(f"Gagal monitor trade aktif {symbol}: {e}")
        return False


async def check_symbol(app, symbol: str) -> bool:
    """
    Scan sinyal baru untuk satu pair (semua HTF).
    Dipanggil hanya untuk pair yang TIDAK ada di active_trades.
    """
    if symbol not in active_zones:
        active_zones[symbol] = {tf: [] for tf in HTF_LIST}

    # Safety: kalau ternyata masih ada trade aktif, jangan scan sinyal
    if symbol in active_trades:
        logger.info(f"[{symbol}] Masih trade aktif — dialihkan ke monitor saja.")
        return await monitor_active_trade_only(app, symbol)

    try:
        ltf_df = fetch_klines_df(symbol, LTF, LOOKBACK_CANDLES)
        if hasattr(ltf_df, "iloc"):
            current_price = float(
                ltf_df[-1]["close"] if isinstance(ltf_df, list) else ltf_df.iloc[-1]["close"]
            )
        else:
            current_price = float(ltf_df[-1]["close"])

        for htf in HTF_LIST:
            htf_df = fetch_klines_df(
                symbol,
                htf,
                LOOKBACK_CANDLES,
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
    """
    Satu siklus scan:

    1) Monitor SEMUA trade aktif (TP/SL) — termasuk yang sudah keluar top volume.
    2) Scan sinyal baru pada TOP_N_PAIRS pair yang BEBAS (bukan trade aktif).
       Contoh: 23 trade aktif + TOP_N=30 → tetap scan 30 pair lain untuk sinyal baru.
    """
    # ── 1. Monitor trade aktif ─────────────────────────────────
    active_list = list(active_trades.keys())
    mon_failed = 0
    if active_list:
        logger.info(
            f"Monitor {len(active_list)} trade aktif (TP/SL saja)..."
        )
        for i in range(0, len(active_list), BATCH_SIZE):
            batch = active_list[i:i + BATCH_SIZE]
            results = await asyncio.gather(
                *(monitor_active_trade_only(app, s) for s in batch)
            )
            mon_failed += results.count(False)
            if i + BATCH_SIZE < len(active_list):
                await asyncio.sleep(BATCH_DELAY_SECONDS)

    # ── 2. Scan sinyal baru (kuota penuh, exclude trade aktif) ─
    scan_symbols = get_scan_symbols(TOP_N_PAIRS)
    if not scan_symbols and not active_list:
        logger.warning("Belum ada daftar pair untuk dipantau.")
        return

    logger.info(
        f"Scan sinyal baru: {len(scan_symbols)} pair bebas "
        f"(kuota {TOP_N_PAIRS}, trade aktif di-skip: {len(active_trades)}) "
        f"(batch size {BATCH_SIZE})..."
    )

    scan_failed = 0
    for i in range(0, len(scan_symbols), BATCH_SIZE):
        batch = scan_symbols[i:i + BATCH_SIZE]
        results = await asyncio.gather(*(check_symbol(app, s) for s in batch))
        scan_failed += results.count(False)
        if i + BATCH_SIZE < len(scan_symbols):
            await asyncio.sleep(BATCH_DELAY_SECONDS)

    total = len(scan_symbols) + len(active_list)
    failed_count = scan_failed + mon_failed
    failure_pct = (failed_count / total * 100) if total else 0
    logger.info(
        f"Siklus selesai: scan {len(scan_symbols) - scan_failed}/{len(scan_symbols)} | "
        f"monitor {len(active_list) - mon_failed}/{len(active_list)} | "
        f"gagal {failure_pct:.0f}%."
    )

    # Health alert hanya berdasarkan kegagalan scan sinyal (bukan monitor)
    if scan_symbols:
        scan_fail_pct = scan_failed / len(scan_symbols) * 100
        if scan_fail_pct >= FAILURE_ALERT_THRESHOLD_PERCENT:
            await send_health_alert(app, scan_failed, len(scan_symbols))

    await check_open_alerts()
