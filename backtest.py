"""
Backtest V2 — menggunakan mesin deteksi yang sama dengan bot LIVE.

Fitur:
- ob_core.detect_order_blocks()
- BoS / FVG / mitigation / ATR impulse sesuai config.py
- LTF reaction advanced
- Trend filter MA
- SL ATR yang sama dengan live
- R:R dari config.py
- Tidak menggunakan data setelah waktu entry untuk menentukan sinyal
- Jika TP dan SL tersentuh pada candle yang sama:
  dianggap LOSS secara konservatif
"""

import argparse
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import ob_core
from config import *
from core_utils import (
    calculate_sl_with_atr,
    ltf_shows_reaction,
)
from market_utils import trend_allows_zone


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


HTF_LIST_DEFAULT = HTF_LIST
LTF_DEFAULT = LTF

MAX_LOOKFORWARD_CANDLES = 200

PAIR_QUOTE = PAIR_QUOTE
MIN_VOLUME_USD = MIN_VOLUME_USD


# ============================================================
# DATA FETCH
# ============================================================

def fetch_history_raw(
    symbol: str,
    interval: str,
    after_ts: str | None = None,
    limit: int = 300,
) -> list:
    """Ambil historical candles dari OKX."""

    params = {
        "instId": symbol,
        "bar": interval,
        "limit": limit,
    }

    if after_ts:
        params["after"] = after_ts

    data = ob_core.okx_get(
        "/api/v5/market/history-candles",
        params,
    )

    rows = data.get("data", [])

    # OKX mengembalikan candle terbaru lebih dahulu.
    rows = list(reversed(rows))

    result = []

    for r in rows:
        result.append(
            {
                "ts": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "vol": float(r[5]),
            }
        )

    return result


def fetch_full_history_raw(
    symbol: str,
    interval: str,
    start_ts_ms: int,
    end_ts_ms: int,
) -> list:
    """
    Ambil seluruh histori dengan paging.
    """

    all_rows = []

    cursor_after = str(end_ts_ms)

    while True:

        page = fetch_history_raw(
            symbol,
            interval,
            after_ts=cursor_after,
            limit=300,
        )

        if not page:
            break

        all_rows = page + all_rows

        oldest_ts = page[0]["ts"]

        if oldest_ts <= start_ts_ms:
            break

        cursor_after = str(oldest_ts)

        time.sleep(0.2)

    # Sort + deduplicate
    seen = set()
    result = []

    for row in sorted(all_rows, key=lambda x: x["ts"]):

        ts = row["ts"]

        if ts in seen:
            continue

        if ts < start_ts_ms:
            continue

        seen.add(ts)
        result.append(row)

    return result


# ============================================================
# PAIR
# ============================================================

def get_backtest_pairs(n: int) -> list:

    logger.info(
        f"Mengambil top {n} pair berdasarkan volume..."
    )

    return ob_core.get_top_volume_pairs(
        n,
        PAIR_QUOTE,
        MIN_VOLUME_USD,
    )


# ============================================================
# TIME ALIGNMENT
# ============================================================

def get_ltf_slice(
    ltf_candles: list,
    timestamp: int,
    count: int = 5,
) -> list:
    """
    Ambil candle LTF yang sudah close sebelum / sampai timestamp.
    """

    candles = [
        c for c in ltf_candles
        if c["ts"] <= timestamp
    ]

    return candles[-count:]


# ============================================================
# TRADE RESOLUTION
# ============================================================

def resolve_trade(
    ltf_candles: list,
    signal_ts: int,
    zone_type: str,
    sl: float,
    tp: float,
) -> dict:
    """
    Menentukan hasil trade setelah entry.

    Jika TP dan SL tersentuh dalam candle yang sama,
    kita anggap LOSS secara konservatif karena urutan intrabar
    tidak diketahui.
    """

    future = [
        c
        for c in ltf_candles
        if c["ts"] > signal_ts
    ]

    future = future[:MAX_LOOKFORWARD_CANDLES]

    if not future:
        return {
            "outcome": "unresolved",
            "exit_price": None,
            "exit_ts": None,
            "r_multiple": 0.0,
            "bars_held": 0,
        }

    risk = abs(
        future[0]["open"] - sl
    )

    # Risk sebenarnya seharusnya berdasarkan entry.
    # Nilai entry diberikan oleh caller melalui SL/TP relationship.
    # Caller akan menghitung R berdasarkan entry.
    for i, candle in enumerate(future, 1):

        if zone_type == "bullish":

            hit_sl = candle["low"] <= sl
            hit_tp = candle["high"] >= tp

        else:

            hit_sl = candle["high"] >= sl
            hit_tp = candle["low"] <= tp

        # ----------------------------------------------------
        # Kedua level terkena dalam candle yang sama.
        # Conservative assumption = LOSS.
        # ----------------------------------------------------

        if hit_sl and hit_tp:

            return {
                "outcome": "loss",
                "exit_price": sl,
                "exit_ts": candle["ts"],
                "r_multiple": -1.0,
                "bars_held": i,
            }

        if hit_tp:

            return {
                "outcome": "win",
                "exit_price": tp,
                "exit_ts": candle["ts"],
                "r_multiple": RISK_REWARD_RATIO,
                "bars_held": i,
            }

        if hit_sl:

            return {
                "outcome": "loss",
                "exit_price": sl,
                "exit_ts": candle["ts"],
                "r_multiple": -1.0,
                "bars_held": i,
            }

    return {
        "outcome": "unresolved",
        "exit_price": None,
        "exit_ts": None,
        "r_multiple": 0.0,
        "bars_held": len(future),
    }


# ============================================================
# SIMULATE ONE PAIR
# ============================================================

def simulate_pair(
    symbol: str,
    htf_list: list,
    ltf: str,
    months: int,
) -> list:

    end_ts_ms = int(
        time.time() * 1000
    )

    start_ts_ms = int(
        (
            datetime.now(timezone.utc)
            - timedelta(days=months * 30)
        ).timestamp()
        * 1000
    )

    # --------------------------------------------------------
    # LTF
    # --------------------------------------------------------

    logger.info(
        f"[{symbol}] Mengambil histori LTF {ltf}..."
    )

    try:

        ltf_candles = fetch_full_history_raw(
            symbol,
            ltf,
            start_ts_ms,
            end_ts_ms,
        )

    except Exception as e:

        logger.error(
            f"[{symbol}] Gagal ambil LTF: {e}"
        )

        return []

    if len(ltf_candles) < LOOKBACK_CANDLES:

        logger.warning(
            f"[{symbol}] Data LTF tidak cukup: "
            f"{len(ltf_candles)}"
        )

        return []

    results = []

    stats = {
        "zones_found": 0,
        "price_in_zone": 0,
        "ltf_closed": 0,
        "ltf_reaction": 0,
        "trend_allowed": 0,
        "final_signal": 0,
    }

    # ========================================================
    # HTF
    # ========================================================

    for htf in htf_list:

        logger.info(
            f"[{symbol}] Mengambil histori HTF {htf}..."
        )

        try:

            htf_candles = fetch_full_history_raw(
                symbol,
                htf,
                start_ts_ms,
                end_ts_ms,
            )

        except Exception as e:

            logger.error(
                f"[{symbol}] Gagal ambil HTF {htf}: {e}"
            )

            continue

        if len(htf_candles) < LOOKBACK_CANDLES + 20:

            logger.warning(
                f"[{symbol}] Data HTF {htf} tidak cukup."
            )

            continue

        seen_zones = set()

        # ====================================================
        # WALK FORWARD
        # ====================================================

        for end_idx in range(
            LOOKBACK_CANDLES,
            len(htf_candles),
        ):

            # ------------------------------------------------
            # Hanya gunakan candle sebelum current candle.
            # Ini mencegah look-ahead pada pembentukan zona.
            # ------------------------------------------------

            window = htf_candles[
                end_idx - LOOKBACK_CANDLES:end_idx
            ]

            if len(window) < LOOKBACK_CANDLES:
                continue

            # ------------------------------------------------
            # Mesin OB SAMA dengan LIVE
            # ------------------------------------------------

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
                impulse_atr_multiplier=IMPULSE_ATR_MULTIPLIER,
            )

            if not zones:
                continue

            stats["zones_found"] += len(zones)

            current_candle = htf_candles[end_idx]

            current_htf_ts = current_candle["ts"]

            current_price = current_candle["close"]

            # ------------------------------------------------
            # LTF confirmation
            # ------------------------------------------------

            ltf_slice = get_ltf_slice(
                ltf_candles,
                current_htf_ts,
                5,
            )

            if len(ltf_slice) < 3:
                continue

            # ------------------------------------------------
            # Check setiap zone
            # ------------------------------------------------

            for zone in zones:

                zone_key = (
                    zone["type"],
                    round(zone["top"], 8),
                    round(zone["bottom"], 8),
                )

                if zone_key in seen_zones:
                    continue

                # Harga harus berada di dalam OB
                if not (
                    zone["bottom"]
                    <= current_price
                    <= zone["top"]
                ):
                    continue

                stats["price_in_zone"] += 1

                # ------------------------------------------------
                # Candle LTF harus close
                # ------------------------------------------------

                # Dalam backtest candle terakhir pada slice
                # dianggap sudah close karena timestamp HTF
                # adalah candle HTF yang sudah selesai.
                if not candle_is_closed_backtest(
                    ltf_slice,
                    ltf,
                ):
                    continue

                stats["ltf_closed"] += 1

                # ------------------------------------------------
                # LTF reaction advanced — sama dengan LIVE
                # ------------------------------------------------

                if not ltf_shows_reaction(
                    ltf_slice,
                    zone,
                ):
                    continue

                stats["ltf_reaction"] += 1

                # ------------------------------------------------
                # Trend filter — sama dengan LIVE
                # ------------------------------------------------

                if not trend_allows_zone(
                    zone,
                    current_price,
                    window,
                ):
                    continue

                stats["trend_allowed"] += 1

                seen_zones.add(zone_key)

                # ------------------------------------------------
                # SL — sama dengan LIVE
                # ------------------------------------------------

                sl, sl_method = calculate_sl_with_atr(
                    zone,
                    current_price,
                    window,
                )

                risk = abs(
                    current_price - sl
                )

                if risk <= 0:
                    continue

                # ------------------------------------------------
                # TP — sama dengan LIVE
                # ------------------------------------------------

                if zone["type"] == "bullish":

                    tp = (
                        current_price
                        + risk * RISK_REWARD_RATIO
                    )

                else:

                    tp = (
                        current_price
                        - risk * RISK_REWARD_RATIO
                    )

                # ------------------------------------------------
                # Resolve trade
                # ------------------------------------------------

                trade = resolve_trade(
                    ltf_candles,
                    current_htf_ts,
                    zone["type"],
                    sl,
                    tp,
                )

                results.append(
                    {
                        "symbol": symbol,
                        "htf": htf,
                        "zone_type": zone["type"],
                        "entry_price": current_price,
                        "sl": sl,
                        "tp": tp,
                        "risk": risk,
                        "sl_method": sl_method,
                        "outcome": trade["outcome"],
                        "exit_price": trade["exit_price"],
                        "exit_ts": trade["exit_ts"],
                        "r_multiple": trade["r_multiple"],
                        "bars_held": trade["bars_held"],
                        stats["final_signal"] += 1,
                    }
                )

                logger.info(
                    f"[{symbol}][{htf}] DIAGNOSTIC | "
                    f"OB={stats['zones_found']} | "
                    f"PriceInZone={stats['price_in_zone']} | "
                    f"LTFClosed={stats['ltf_closed']} | "
                    f"LTFReaction={stats['ltf_reaction']} | "
                    f"TrendAllowed={stats['trend_allowed']} | "
                    f"Final={stats['final_signal']}"
                )

    return results


# ============================================================
# CANDLE CLOSED
# ============================================================

def candle_is_closed_backtest(
    candles: list,
    timeframe: str,
) -> bool:
    """
    Dalam historical backtest candle yang ada dianggap closed.

    Fungsi terpisah supaya tidak menggunakan logic live
    yang bergantung pada waktu sekarang.
    """

    return len(candles) > 0


# ============================================================
# SUMMARY
# ============================================================

def print_summary(
    all_results: list,
):

    if not all_results:

        print(
            "\nTidak ada sinyal yang terbentuk "
            "selama periode backtest."
        )

        return

    total = len(all_results)

    wins = [
        r for r in all_results
        if r["outcome"] == "win"
    ]

    losses = [
        r for r in all_results
        if r["outcome"] == "loss"
    ]

    unresolved = [
        r for r in all_results
        if r["outcome"] == "unresolved"
    ]

    resolved_count = len(wins) + len(losses)

    win_rate = (
        len(wins)
        / resolved_count
        * 100
        if resolved_count
        else 0
    )

    total_r = sum(
        r["r_multiple"]
        for r in all_results
    )

    gross_profit = sum(
        max(r["r_multiple"], 0)
        for r in all_results
    )

    gross_loss = abs(
        sum(
            min(r["r_multiple"], 0)
            for r in all_results
        )
    )

    profit_factor = (
        gross_profit / gross_loss
        if gross_loss
        else float("inf")
    )

    # --------------------------------------------------------
    # Equity curve + max drawdown
    # --------------------------------------------------------

    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0

    for r in all_results:

        equity += r["r_multiple"]

        peak = max(
            peak,
            equity,
        )

        drawdown = peak - equity

        max_drawdown = max(
            max_drawdown,
            drawdown,
        )

    # ========================================================
    # OUTPUT
    # ========================================================

    print("\n" + "=" * 65)

    print(
        "BACKTEST V2 — LIVE ENGINE"
    )

    print("=" * 65)

    print(
        f"Total sinyal       : {total}"
    )

    print(
        f"Win                : {len(wins)}"
    )

    print(
        f"Loss               : {len(losses)}"
    )

    print(
        f"Unresolved         : {len(unresolved)}"
    )

    print(
        f"Win rate           : {win_rate:.2f}%"
        f" ({len(wins)}/{resolved_count})"
    )

    print(
        f"Total R            : {total_r:.2f}R"
    )

    print(
        f"Profit Factor      : "
        f"{profit_factor:.2f}"
        if profit_factor != float("inf")
        else "Profit Factor      : INF"
    )

    print(
        f"Max Drawdown       : "
        f"{max_drawdown:.2f}R"
    )

    print(
        f"Avg R / signal     : "
        f"{total_r / total:.3f}R"
    )

    print("\n--- CONFIG ---")

    print(
        f"HTF                : {','.join(HTF_LIST_DEFAULT)}"
    )

    print(
        f"LTF                : {LTF_DEFAULT}"
    )

    print(
        f"Lookback           : {LOOKBACK_CANDLES}"
    )

    print(
        f"ATR impulse        : {USE_ATR_IMPULSE}"
    )

    print(
        f"ATR multiplier     : "
        f"{IMPULSE_ATR_MULTIPLIER}"
    )

    print(
        f"Median volume      : {USE_MEDIAN_VOLUME}"
    )

    print(
        f"Volume multiplier  : "
        f"{VOLUME_MULTIPLIER}"
    )

    print(
        f"BoS required       : {REQUIRE_BOS}"
    )

    print(
        f"FVG required       : {REQUIRE_FVG}"
    )

    print(
        f"Mitigation 50%     : {MITIGATION_50PCT}"
    )

    print(
        f"MA period          : {MA_PERIOD}"
    )

    print(
        f"Trend filter       : {USE_TREND_FILTER}"
    )

    print(
        f"ATR SL period      : {ATR_PERIOD}"
    )

    print(
        f"ATR SL multiplier  : {ATR_MULTIPLIER}"
    )

    print(
        f"Risk / Reward      : "
        f"1:{RISK_REWARD_RATIO:.2f}"
    )

    # ========================================================
    # HTF
    # ========================================================

    print("\n--- BREAKDOWN HTF ---")

    by_htf = defaultdict(list)

    for r in all_results:
        by_htf[r["htf"]].append(r)

    for htf, group in sorted(
        by_htf.items()
    ):

        resolved = [
            r for r in group
            if r["outcome"] != "unresolved"
        ]

        w = sum(
            1 for r in resolved
            if r["outcome"] == "win"
        )

        wr = (
            w / len(resolved) * 100
            if resolved
            else 0
        )

        r_total = sum(
            r["r_multiple"]
            for r in group
        )

        print(
            f"{htf:5} | "
            f"{len(group):4} signal | "
            f"WR {wr:6.2f}% | "
            f"{r_total:+7.2f}R"
        )

    # ========================================================
    # BULLISH / BEARISH
    # ========================================================

    print("\n--- BREAKDOWN ARAH ---")

    by_type = defaultdict(list)

    for r in all_results:
        by_type[r["zone_type"]].append(r)

    for zone_type, group in sorted(
        by_type.items()
    ):

        resolved = [
            r for r in group
            if r["outcome"] != "unresolved"
        ]

        w = sum(
            1 for r in resolved
            if r["outcome"] == "win"
        )

        wr = (
            w / len(resolved) * 100
            if resolved
            else 0
        )

        r_total = sum(
            r["r_multiple"]
            for r in group
        )

        print(
            f"{zone_type:8} | "
            f"{len(group):4} signal | "
            f"WR {wr:6.2f}% | "
            f"{r_total:+7.2f}R"
        )

    # ========================================================
    # SL METHOD
    # ========================================================

    print("\n--- BREAKDOWN SL ---")

    by_sl = defaultdict(list)

    for r in all_results:
        by_sl[r["sl_method"]].append(r)

    for method, group in sorted(
        by_sl.items()
    ):

        resolved = [
            r for r in group
            if r["outcome"] != "unresolved"
        ]

        w = sum(
            1 for r in resolved
            if r["outcome"] == "win"
        )

        wr = (
            w / len(resolved) * 100
            if resolved
            else 0
        )

        r_total = sum(
            r["r_multiple"]
            for r in group
        )

        print(
            f"{method:8} | "
            f"{len(group):4} signal | "
            f"WR {wr:6.2f}% | "
            f"{r_total:+7.2f}R"
        )

    # ========================================================
    # TOP PAIRS
    # ========================================================

    print(
        "\n--- PAIR DENGAN SIGNAL TERBANYAK ---"
    )

    by_symbol = defaultdict(list)

    for r in all_results:
        by_symbol[r["symbol"]].append(r)

    top_symbols = sorted(
        by_symbol.items(),
        key=lambda x: len(x[1]),
        reverse=True,
    )[:10]

    for symbol, group in top_symbols:

        resolved = [
            r for r in group
            if r["outcome"] != "unresolved"
        ]

        w = sum(
            1 for r in resolved
            if r["outcome"] == "win"
        )

        wr = (
            w / len(resolved) * 100
            if resolved
            else 0
        )

        r_total = sum(
            r["r_multiple"]
            for r in group
        )

        print(
            f"{symbol:24} | "
            f"{len(group):3} signal | "
            f"WR {wr:6.2f}% | "
            f"{r_total:+7.2f}R"
        )

    print("=" * 65)


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description="Backtest V2 menggunakan live engine"
    )

    parser.add_argument(
        "--months",
        type=int,
        default=3,
        help="Jumlah bulan historis",
    )

    parser.add_argument(
        "--pairs",
        type=int,
        default=30,
        help="Jumlah top pair",
    )

    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Pair spesifik",
    )

    parser.add_argument(
        "--htf",
        type=str,
        default=",".join(HTF_LIST_DEFAULT),
        help="HTF dipisah koma",
    )

    parser.add_argument(
        "--ltf",
        type=str,
        default=LTF_DEFAULT,
        help="LTF konfirmasi",
    )

    args = parser.parse_args()

    htf_list = [
        x.strip()
        for x in args.htf.split(",")
        if x.strip()
    ]

    if args.symbol:

        symbols = [
            args.symbol
        ]

    else:

        symbols = get_backtest_pairs(
            args.pairs
        )

    logger.info(
        f"Backtest V2: "
        f"{len(symbols)} pair, "
        f"{args.months} bulan, "
        f"HTF={htf_list}, "
        f"LTF={args.ltf}"
    )

    all_results = []

    for i, symbol in enumerate(
        symbols,
        1,
    ):

        logger.info(
            f"[{i}/{len(symbols)}] "
            f"Memproses {symbol}..."
        )

        try:

            results = simulate_pair(
                symbol,
                htf_list,
                args.ltf,
                args.months,
            )

            all_results.extend(
                results
            )

            logger.info(
                f"[{symbol}] "
                f"{len(results)} signal."
            )

        except Exception as e:

            logger.error(
                f"[{symbol}] Gagal: {e}"
            )

        time.sleep(0.5)

    print_summary(
        all_results
    )


if __name__ == "__main__":
    main()
