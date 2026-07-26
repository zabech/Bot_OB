from telegram import Update
from telegram.ext import ContextTypes
import logging

logger = logging.getLogger(__name__)

import time
import ob_core
import db

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from config import *
from core_utils import calculate_sl_with_atr

async def run_backtest_async(symbol: str, months: int) -> str:
    """Jalankan backtest dan return teks hasil — dipakai oleh inline callback dan command."""
    try:
        from datetime import datetime, timedelta, timezone
        end_ts_ms = int(time.time() * 1000)
        start_ts_ms = int((datetime.now(timezone.utc) - timedelta(days=months * 30)).timestamp() * 1000)

        ltf_data = ob_core.fetch_full_history(symbol, LTF, start_ts_ms, end_ts_ms)
        ltf_list = ltf_data.to_dict("records") if hasattr(ltf_data, 'to_dict') else ltf_data

        if len(ltf_list) < LOOKBACK_CANDLES:
            return f"Data tidak cukup untuk {symbol}."

        all_results = []
        seen_zones = set()

        for htf in HTF_LIST:
            htf_data = ob_core.fetch_full_history(symbol, htf, start_ts_ms, end_ts_ms)
            htf_list_bt = htf_data.to_dict("records") if hasattr(htf_data, 'to_dict') else htf_data
            if len(htf_list_bt) < LOOKBACK_CANDLES + 10:
                continue

            for end_idx in range(LOOKBACK_CANDLES, len(htf_list_bt)):
                window = htf_list_bt[end_idx - LOOKBACK_CANDLES:end_idx]
                
                # ⬇️ UPDATE DI SINI ⬇️
                zones = ob_core.detect_order_blocks(
                    window, 
                    MAX_ACTIVE_ZONES_PER_TF, 
                    IMPULSE_MIN_PERCENT, 
                    VOLUME_MULTIPLIER,
                    require_bos=REQUIRE_BOS,
                    require_fvg=REQUIRE_FVG,
                    mitigation_50pct=MITIGATION_50PCT,
                    swing_lookback=SWING_LOOKBACK,
                    use_atr_impulse=USE_ATR_IMPULSE,
                    impulse_atr_multiplier=IMPULSE_ATR_MULTIPLIER
                )
                # ⬆️ UPDATE DI SINI ⬆️
                
                if not zones:
                    continue
                current_htf_ts = htf_list_bt[end_idx]["ts"]
                current_price = htf_list_bt[end_idx]["close"]

                for zone in zones:
                    zone_key = (zone["type"], round(zone["top"], 8), round(zone["bottom"], 8))
                    if zone_key in seen_zones:
                        continue
                    if not (zone["bottom"] <= current_price <= zone["top"]):
                        continue
                    ltf_slice = [c for c in ltf_list if c["ts"] <= current_htf_ts][-3:]
                    if len(ltf_slice) < 3 or not ob_core.ltf_shows_reaction(ltf_slice, zone):
                        continue
                    seen_zones.add(zone_key)

                    # SL berbasis ATR
                    sl, _ = calculate_sl_with_atr(zone, current_price, window)
                    risk = abs(current_price - sl)
                    if risk == 0:
                        continue
                    if zone["type"] == "bullish":
                        target = current_price + risk * RISK_REWARD_RATIO
                        invalidation = sl
                    else:
                        target = current_price - risk * RISK_REWARD_RATIO
                        invalidation = sl
                    future = [c for c in ltf_list if c["ts"] > current_htf_ts][:200]
                    outcome = "unresolved"
                    for c in future:
                        if zone["type"] == "bullish":
                            if c["high"] >= target:
                                outcome = "win"; break
                            if c["low"] <= invalidation:
                                outcome = "loss"; break
                        else:
                            if c["low"] <= target:
                                outcome = "win"; break
                            if c["high"] >= invalidation:
                                outcome = "loss"; break
                    all_results.append({"htf": htf, "zone_type": zone["type"], "outcome": outcome})
        
        if not all_results:
            return f"Backtest {symbol} ({months} bln): tidak ada sinyal."

        total = len(all_results)
        win = sum(1 for r in all_results if r["outcome"] == "win")
        loss = sum(1 for r in all_results if r["outcome"] == "loss")
        unresolved = total - win - loss
        resolved = win + loss
        win_rate = f"{win / resolved * 100:.1f}%" if resolved > 0 else "N/A"

        by_htf = defaultdict(lambda: {"win": 0, "loss": 0, "total": 0})
        for r in all_results:
            by_htf[r["htf"]]["total"] += 1
            if r["outcome"] == "win":
                by_htf[r["htf"]]["win"] += 1
            elif r["outcome"] == "loss":
                by_htf[r["htf"]]["loss"] += 1

        htf_lines = []
        for htf, g in sorted(by_htf.items()):
            res = g["win"] + g["loss"]
            wr = f"{g['win'] / res * 100:.1f}%" if res > 0 else "N/A"
            htf_lines.append(f"  {htf}: {g['total']} sinyal, WR {wr}")

        return (
            f"📊 Hasil Backtest {symbol} ({months} bln)\n\n"
            f"Total sinyal : {total}\n"
            f"Win          : {win}\n"
            f"Loss         : {loss}\n"
            f"Unresolved   : {unresolved}\n"
            f"Win rate     : {win_rate} ({resolved} resolved)\n\n"
            f"Per timeframe:\n" + "\n".join(htf_lines) + "\n\n"
            f"*SL berbasis ATR{ATR_PERIOD} × {ATR_MULTIPLIER} | TP R:R 1:{RISK_REWARD_RATIO:.0f}"
        )
    except Exception as e:
        return f"Gagal backtest {symbol}: {e}"
