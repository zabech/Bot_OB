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
import bisect
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
from market_utils import trend_allows_zone, get_macro_regime


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


HTF_LIST_DEFAULT = HTF_LIST
LTF_DEFAULT = LTF

MAX_LOOKFORWARD_CANDLES = 200

# BACKTEST_DIRECTION di-set dari CLI --direction (lihat main()), default
# ambil dari config.py DIRECTION_FILTER (default "all"). Sebelumnya
# hardcoded ke "bearish" — artinya SEMUA backtest sebelum fix ini cuma
# menguji sinyal bearish, padahal bot live deteksi bullish+bearish.
BACKTEST_DIRECTION = DIRECTION_FILTER

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
    Ambil seluruh histori dengan paging mundur via parameter `after`.

    CATATAN PENTING:
    - OKX history-candles sering TIDAK punya data sepanjang yang diminta
      (batas internal exchange + umur listing pair).
    - Kalau data terlama yang tersedia > start_ts_ms, hasil 24 bulan
      dan 36 bulan bisa IDENTIK — itu batas data, bukan bug months.
    """

    all_rows = []

    cursor_after = str(end_ts_ms)
    # Safety: cegah loop tak terbatas kalau API terus mengembalikan
    # halaman yang sama / tidak mundur.
    max_pages = 500
    pages = 0
    prev_oldest = None

    while pages < max_pages:

        page = fetch_history_raw(
            symbol,
            interval,
            after_ts=cursor_after,
            limit=300,
        )

        if not page:
            break

        all_rows = page + all_rows
        pages += 1

        oldest_ts = page[0]["ts"]

        if oldest_ts <= start_ts_ms:
            break

        # API tidak maju ke masa lalu → berhenti
        if prev_oldest is not None and oldest_ts >= prev_oldest:
            logger.warning(
                f"[{symbol}] {interval}: paging berhenti lebih awal "
                f"(oldest tidak mundur, ts={oldest_ts}). "
                f"Kemungkinan batas histori OKX."
            )
            break

        prev_oldest = oldest_ts
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

    if result:
        first = datetime.fromtimestamp(result[0]["ts"] / 1000, tz=timezone.utc)
        last = datetime.fromtimestamp(result[-1]["ts"] / 1000, tz=timezone.utc)
        span_days = (result[-1]["ts"] - result[0]["ts"]) / 1000 / 86400
        requested_days = (end_ts_ms - start_ts_ms) / 1000 / 86400
        logger.info(
            f"[{symbol}] {interval}: {len(result)} candle | "
            f"{first.date()} → {last.date()} "
            f"(\~{span_days:.0f} hari tersedia, diminta \~{requested_days:.0f} hari)"
        )
        if span_days < requested_days * 0.9:
            logger.warning(
                f"[{symbol}] {interval}: data LEBIH PENDEK dari yang diminta "
                f"({span_days:.0f} vs {requested_days:.0f} hari). "
                f"Penyebab umum: batas histori OKX atau pair baru listing. "
                f"--months lebih besar tidak akan menambah data."
            )

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
    entry_price: float,
    risk: float,
) -> dict:
    """
    Menentukan hasil trade setelah entry.

    Jika TP dan SL tersentuh dalam candle yang sama,
    kita anggap LOSS secara konservatif karena urutan intrabar
    tidak diketahui.

    r_multiple_gross = hasil murni sebelum fee/slippage (yang selama ini
    dipakai). r_multiple = hasil NET setelah fee (entry+exit, taker) dan
    slippage — ini yang lebih realistis dipakai untuk Total R/PF laporan.
    """

    future = [
        c
        for c in ltf_candles
        if c["ts"] > signal_ts
    ]

    future = future[:MAX_LOOKFORWARD_CANDLES]

    def _apply_costs(gross_r: float, exit_price: float) -> float:
        """Kurangi gross_r dengan biaya fee (2x, entry+exit) dan slippage (2x)."""
        if risk <= 0:
            return gross_r
        fee_cost = (entry_price + exit_price) * (TAKER_FEE_PERCENT / 100)
        slippage_cost = (entry_price + exit_price) * (SLIPPAGE_PERCENT / 100)
        cost_in_r = (fee_cost + slippage_cost) / risk
        return gross_r - cost_in_r

    if not future:
        return {
            "outcome": "unresolved",
            "exit_price": None,
            "exit_ts": None,
            "r_multiple": 0.0,
            "r_multiple_gross": 0.0,
            "bars_held": 0,
        }

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
                "r_multiple": _apply_costs(-1.0, sl),
                "r_multiple_gross": -1.0,
                "bars_held": i,
            }

        if hit_tp:

            return {
                "outcome": "win",
                "exit_price": tp,
                "exit_ts": candle["ts"],
                "r_multiple": _apply_costs(RISK_REWARD_RATIO, tp),
                "r_multiple_gross": RISK_REWARD_RATIO,
                "bars_held": i,
            }

        if hit_sl:

            return {
                "outcome": "loss",
                "exit_price": sl,
                "exit_ts": candle["ts"],
                "r_multiple": _apply_costs(-1.0, sl),
                "r_multiple_gross": -1.0,
                "bars_held": i,
            }

    return {
        "outcome": "unresolved",
        "exit_price": None,
        "exit_ts": None,
        "r_multiple": 0.0,
        "r_multiple_gross": 0.0,
        "bars_held": len(future),
    }


# ============================================================
# SIMULATE ONE PAIR
# ============================================================

def build_macro_regime_lookup(months: int) -> tuple:
    """
    Bangun lookup regime market makro (BTC vs MA jangka panjang) sepanjang
    periode backtest, TANPA lookahead — tiap titik cuma pakai candle
    SAMPAI titik itu untuk hitung MA, persis seperti live.

    Return: (list_ts, list_regime) — dua list sejajar terurut menaik
    berdasarkan ts, siap dipakai bisect di regime_at(). Kosong kalau
    USE_MACRO_FILTER mati atau fetch gagal.
    """
    if not USE_MACRO_FILTER:
        return [], []

    end_ts_ms = int(time.time() * 1000)
    # Buffer ekstra di depan supaya MA_PERIOD candle pertama backtest
    # tetap punya cukup histori untuk dihitung (bukan cuma "None" terus).
    buffer_days = MACRO_MA_PERIOD * 2
    start_ts_ms = int(
        (
            datetime.now(timezone.utc)
            - timedelta(days=months * 30 + buffer_days)
        ).timestamp() * 1000
    )

    try:
        macro_candles = fetch_full_history_raw(
            MACRO_SYMBOL, MACRO_TIMEFRAME, start_ts_ms, end_ts_ms
        )
    except Exception as e:
        logger.error(f"[MACRO] Gagal ambil histori {MACRO_SYMBOL}: {e}")
        return [], []

    if len(macro_candles) < MACRO_MA_PERIOD:
        logger.warning(
            f"[MACRO] Data {MACRO_SYMBOL} {MACRO_TIMEFRAME} tidak cukup "
            f"untuk MA{MACRO_MA_PERIOD} ({len(macro_candles)} candle)."
        )
        return [], []

    lookup_ts = []
    lookup_regime = []
    for i in range(MACRO_MA_PERIOD, len(macro_candles) + 1):
        # Slice [:i] → cuma candle sampai titik ini, tidak ada lookahead
        regime = get_macro_regime(macro_candles[:i], MACRO_MA_PERIOD)
        if regime is not None:
            lookup_ts.append(macro_candles[i - 1]["ts"])
            lookup_regime.append(regime)

    logger.info(
        f"[MACRO] Lookup regime dibangun: {len(lookup_ts)} titik "
        f"({MACRO_SYMBOL} {MACRO_TIMEFRAME} MA{MACRO_MA_PERIOD})"
    )
    return lookup_ts, lookup_regime


def regime_at(lookup_ts: list, lookup_regime: list, ts) -> str | None:
    """
    Cari regime yang berlaku pada waktu `ts`, pakai titik lookup terakhir
    yang timestamp-nya <= ts (candle daily terakhir yang SUDAH close
    sebelum/sama dengan waktu sinyal — tidak ada lookahead).
    lookup_ts harus sudah terurut menaik.
    """
    if not lookup_ts:
        return None
    idx = bisect.bisect_right(lookup_ts, int(ts)) - 1
    if idx < 0:
        return None
    return lookup_regime[idx]


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

    # --------------------------------------------------------
    # Macro regime lookup (dibangun sekali per pair, dipakai di
    # semua HTF — regime-nya sama karena sumbernya BTC, bukan
    # bergantung pair yang sedang disimulasikan)
    # --------------------------------------------------------
    macro_lookup_ts, macro_lookup_regime = build_macro_regime_lookup(months)

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
                require_liquidity_sweep=REQUIRE_LIQUIDITY_SWEEP,
                mitigation_50pct=MITIGATION_50PCT,
                swing_lookback=SWING_LOOKBACK,
                use_atr_impulse=USE_ATR_IMPULSE,
                impulse_atr_multiplier=IMPULSE_ATR_MULTIPLIER,
            )

            # DIAGNOSTIC RAW OB — SEBELUM FILTER DIRECTION
            bullish_raw = sum(
                1 for z in zones
                if z.get("type") == "bullish"
            )

            bearish_raw = sum(
                1 for z in zones
                if z.get("type") == "bearish"
            )

            logger.info(
                f"[{symbol}][{htf}] RAW OB | "
                f"bullish={bullish_raw} | "
                f"bearish={bearish_raw}"
            )
          
            if BACKTEST_DIRECTION != "all":
                zones = [
                    z for z in zones
                    if z["type"] == BACKTEST_DIRECTION
                ]

            if not zones:
                continue

            stats["zones_found"] += len(zones)

            current_candle = htf_candles[end_idx]

            current_htf_ts = current_candle["ts"]

            if USE_MACRO_FILTER and macro_lookup_ts:
                regime = regime_at(macro_lookup_ts, macro_lookup_regime, current_htf_ts)
                if regime is not None:
                    zones = [z for z in zones if z["type"] == regime]

            if not zones:
                continue

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
                    current_price,
                    risk,
                )

                stats["final_signal"] += 1

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
                        "r_multiple_gross": trade["r_multiple_gross"],
                        "bars_held": trade["bars_held"],
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
    htf_list_used: list | None = None,
    ltf_used: str | None = None,
    direction_used: str | None = None,
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

    total_r_gross = sum(
        r.get("r_multiple_gross", r["r_multiple"])
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

    # PF gross (tanpa fee/slippage) untuk perbandingan
    gp_gross = sum(
        max(r.get("r_multiple_gross", r["r_multiple"]), 0)
        for r in all_results
    )
    gl_gross = abs(
        sum(
            min(r.get("r_multiple_gross", r["r_multiple"]), 0)
            for r in all_results
        )
    )
    profit_factor_gross = (
        gp_gross / gl_gross
        if gl_gross
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
        f"   (gross sebelum fee: {total_r_gross:.2f}R)"
    )

    print(
        f"Profit Factor      : "
        f"{profit_factor:.2f}"
        if profit_factor != float("inf")
        else "Profit Factor      : INF"
    )

    print(
        f"  (gross sebelum fee: "
        f"{profit_factor_gross:.2f}"
        f")"
        if profit_factor_gross != float("inf")
        else "  (gross sebelum fee: INF)"
    )

    print(
        f"Biaya per trade    : fee {TAKER_FEE_PERCENT}% + "
        f"slippage {SLIPPAGE_PERCENT}% (per sisi, entry & exit)"
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
        f"HTF                : "
        f"{','.join(htf_list_used or HTF_LIST_DEFAULT)}"
    )

    print(
        f"LTF                : {ltf_used or LTF_DEFAULT}"
    )

    print(
        f"Direction          : {direction_used or DIRECTION_FILTER}"
    )

    macro_status = (
        f"ON ({MACRO_SYMBOL} {MACRO_TIMEFRAME} MA{MACRO_MA_PERIOD})"
        if USE_MACRO_FILTER else "OFF"
    )
    print(
        f"Macro filter       : {macro_status}"
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
        f"Liquidity sweep    : {REQUIRE_LIQUIDITY_SWEEP}"
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
        "--symbols",
        type=str,
        default=None,
        help="Daftar pair dipisah koma",
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

    parser.add_argument(
        "--direction",
        type=str,
        default=DIRECTION_FILTER,
        choices=["all", "bullish", "bearish"],
        help="Filter arah sinyal: all/bullish/bearish (default dari config.py DIRECTION_FILTER)",
    )

    parser.add_argument(
        "--export-csv",
        type=str,
        default=None,
        help="Path CSV untuk export detail tiap sinyal (untuk analisis lanjutan, mis. bootstrap_test.py)",
    )

    args = parser.parse_args()

    global BACKTEST_DIRECTION
    BACKTEST_DIRECTION = args.direction

    htf_list = [
        x.strip()
        for x in args.htf.split(",")
        if x.strip()
    ]

    if args.symbol:
        symbols = [
            args.symbol.strip()
        ]

    elif args.symbols:
        symbols = [
            s.strip()
            for s in args.symbols.split(",")
            if s.strip()
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
        all_results,
        htf_list_used=htf_list,
        ltf_used=args.ltf,
        direction_used=args.direction,
    )

    if args.export_csv:
        import csv

        fieldnames = [
            "symbol", "htf", "zone_type", "entry_price", "sl", "tp",
            "risk", "sl_method", "outcome", "exit_price", "exit_ts",
            "r_multiple", "r_multiple_gross", "bars_held",
        ]

        with open(args.export_csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for r in all_results:
                writer.writerow(r)

        print(f"\nDetail sinyal diexport ke: {args.export_csv}")


if __name__ == "__main__":
    main()
