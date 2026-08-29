from dotenv import load_dotenv
load_dotenv()

import os
import logging

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
USE_ATR_IMPULSE = os.environ.get("USE_ATR_IMPULSE", "true").lower() == "true"
IMPULSE_ATR_MULTIPLIER = float(os.environ.get("IMPULSE_ATR_MULTIPLIER", "1.6"))
USE_MEDIAN_VOLUME = os.environ.get("USE_MEDIAN_VOLUME", "true").lower() == "true"

CHECK_INTERVAL_MINUTES = int(os.environ.get("CHECK_INTERVAL_MINUTES", "15"))

# Timeframe: HTF (higher) menentukan zona order block utama,
# LTF (lower) dipakai untuk konfirmasi reaksi harga sebelum alert dikirim.
# Format OKX: "1m","3m","5m","15m","30m","1H","2H","4H","6H","12H","1D","1W","1M"
HTF_LIST = os.environ.get("HTF_LIST", "1D,4H").split(",")
LTF = os.environ.get("LTF", "1H")

# Parameter deteksi Order Block
LOOKBACK_CANDLES = int(os.environ.get("LOOKBACK_CANDLES", "50"))
IMPULSE_MIN_PERCENT = float(os.environ.get("IMPULSE_MIN_PERCENT", "3.0"))   # dinaikkan dari 1.5 ke 3.0
MAX_ACTIVE_ZONES_PER_TF = int(os.environ.get("MAX_ACTIVE_ZONES_PER_TF", "3"))
VOLUME_MULTIPLIER = float(os.environ.get("VOLUME_MULTIPLIER", "1.2"))

# Filter kualitas tambahan
MA_PERIOD = int(os.environ.get("MA_PERIOD", "50"))               # periode MA untuk filter trend
USE_TREND_FILTER = os.environ.get("USE_TREND_FILTER", "false").lower() == "true"

# Risk management
SL_BUFFER_PERCENT = float(os.environ.get("SL_BUFFER_PERCENT", "0.5"))  # buffer SL di luar invalidasi (fallback)
RISK_REWARD_RATIO = float(os.environ.get("RISK_REWARD_RATIO", "2.0"))   # fixed R:R (default 1:2)
ATR_PERIOD = int(os.environ.get("ATR_PERIOD", "14"))                     # periode ATR untuk SL + filter impuls OB
ATR_MULTIPLIER = float(os.environ.get("ATR_MULTIPLIER", "2.0"))          # SL = invalidasi ± (ATR × multiplier)
# Batas risk (% dari harga entry). Sinyal dibatalkan jika risk di luar rentang ini.
# Mencegah SL ATR terlalu lebar di alt (risk ekstrem) atau terlalu sempit (noise).
MAX_RISK_PCT = float(os.environ.get("MAX_RISK_PCT", "10.0"))   # default max 5%
MIN_RISK_PCT = float(os.environ.get("MIN_RISK_PCT", "0.15"))  # default min 0.15%

# Konfigurasi deteksi OB tingkat lanjut
REQUIRE_BOS = os.environ.get("REQUIRE_BOS", "true").lower() == "true"
REQUIRE_FVG = os.environ.get("REQUIRE_FVG", "false").lower() == "true"
REQUIRE_LIQUIDITY_SWEEP = os.environ.get("REQUIRE_LIQUIDITY_SWEEP", "false").lower() == "true"
MITIGATION_50PCT = os.environ.get("MITIGATION_50PCT", "true").lower() == "true"
DIRECTION_FILTER = os.environ.get("DIRECTION_FILTER", "all").lower()  # "all" / "bullish" / "bearish"

# ── Macro Market Filter ──
# Gerbang tambahan di atas DIRECTION_FILTER: sinyal cuma lolos kalau
# arahnya cocok dengan regime market makro (BTC vs MA jangka panjang).
USE_MACRO_FILTER = os.environ.get("USE_MACRO_FILTER", "false").lower() == "true"
MACRO_SYMBOL = os.environ.get("MACRO_SYMBOL", "BTC-USDT-SWAP")
MACRO_TIMEFRAME = os.environ.get("MACRO_TIMEFRAME", "1D")
MACRO_MA_PERIOD = int(os.environ.get("MACRO_MA_PERIOD", "200"))
MACRO_REFRESH_MINUTES = int(os.environ.get("MACRO_REFRESH_MINUTES", "60"))

# ── Biaya Trading (dipakai backtest.py untuk hitung R net) ──
# OKX taker fee futures default \~0.05% per sisi (entry & exit masing-masing).
# Slippage adalah estimasi kasar selisih harga fill vs harga sinyal.
TAKER_FEE_PERCENT = float(os.environ.get("TAKER_FEE_PERCENT", "0.05"))
SLIPPAGE_PERCENT = float(os.environ.get("SLIPPAGE_PERCENT", "0.02"))

# ── Web Dashboard (webapp.py) ──
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "5000"))
DASHBOARD_USERNAME = os.environ.get("DASHBOARD_USERNAME", "")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
DASHBOARD_REFRESH_SECONDS = int(os.environ.get("DASHBOARD_REFRESH_SECONDS", "30"))
SWING_LOOKBACK = int(os.environ.get("SWING_LOOKBACK", "10"))

# Filter sesi trading (jam dalam UTC)
# Semua sesi tetap jalan, tapi tiap alert diberi label kualitas sesi
SESSION_LONDON_START = int(os.environ.get("SESSION_LONDON_START", "7"))
SESSION_LONDON_END = int(os.environ.get("SESSION_LONDON_END", "16"))
SESSION_NY_START = int(os.environ.get("SESSION_NY_START", "13"))
SESSION_NY_END = int(os.environ.get("SESSION_NY_END", "22"))

# Scanner multi-pair (OKX Futures - USDT-margined swap/perpetual)
TOP_N_PAIRS = int(os.environ.get("TOP_N_PAIRS", "40"))
PAIR_QUOTE = os.environ.get("PAIR_QUOTE", "USDT")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "5"))
BATCH_DELAY_SECONDS = float(os.environ.get("BATCH_DELAY_SECONDS", "2"))
SYMBOL_REFRESH_HOURS = int(os.environ.get("SYMBOL_REFRESH_HOURS", "6"))
MIN_VOLUME_USD = float(os.environ.get("MIN_VOLUME_USD", "5000000"))  # skip pair dengan volume 24h di bawah ini
MIN_PRICE_USD = float(os.environ.get("MIN_PRICE_USD", "0"))  # skip pair dengan harga di bawah ini (0 = nonaktif)

# Kontrol jumlah alert
ALERT_COOLDOWN_MINUTES = int(os.environ.get("ALERT_COOLDOWN_MINUTES", "60"))  # jeda minimum antar alert per pair

# Reliability: retry untuk request API yang gagal sementara
API_MAX_RETRIES = int(os.environ.get("API_MAX_RETRIES", "3"))
API_RETRY_BACKOFF_SECONDS = float(os.environ.get("API_RETRY_BACKOFF_SECONDS", "2"))  # dikali 2 tiap percobaan

# Reliability: notifikasi kalau banyak pair gagal dalam satu siklus (indikasi API/koneksi bermasalah)
FAILURE_ALERT_THRESHOLD_PERCENT = float(os.environ.get("FAILURE_ALERT_THRESHOLD_PERCENT", "50"))  # % pair gagal
HEALTH_ALERT_COOLDOWN_MINUTES = int(os.environ.get("HEALTH_ALERT_COOLDOWN_MINUTES", "60"))  # jeda antar health alert

# Ringkasan harian: jam dalam format UTC (jam server). Default 00:00 UTC = 08:00 WITA.
DAILY_SUMMARY_HOUR_UTC = int(os.environ.get("DAILY_SUMMARY_HOUR_UTC", "0"))
DAILY_SUMMARY_MINUTE_UTC = int(os.environ.get("DAILY_SUMMARY_MINUTE_UTC", "0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ── Runtime state (re-export dari state.py) ──────────────────────
# State yang berubah saat bot jalan TIDAK disimpan di config lagi.
# Diimpor ulang di sini supaya `from config import active_trades` /
# `from config import *` tetap kompatibel dengan kode lama.
from state import (  # noqa: E402
    active_zones,
    active_trades,
    top_pairs_cache,
    last_alert_times,
    last_health_alert_time,
    macro_regime_cache,
)

# Normalisasi HTF_LIST (buang spasi kosong)
HTF_LIST = [tf.strip() for tf in HTF_LIST if tf.strip()]

# Timeframe OKX yang dikenali (untuk validasi)
_VALID_OKX_BARS = {
    "1m", "3m", "5m", "15m", "30m",
    "1H", "2H", "4H", "6H", "12H",
    "1D", "1W", "1M",
}


def validate_config() -> None:
    """
    Validasi environment / konstanta saat startup.
    Raise RuntimeError dengan daftar masalah jika ada yang tidak valid.
    Dipanggil dari main() sebelum bot mulai polling.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # ── Wajib ada ──
    if not BOT_TOKEN:
        errors.append("BOT_TOKEN wajib di-set")
    if not CHAT_ID:
        errors.append("CHAT_ID wajib di-set")
    if not os.environ.get("DATABASE_URL"):
        errors.append("DATABASE_URL wajib di-set (koneksi PostgreSQL)")

    # ── Timeframe ──
    if not HTF_LIST:
        errors.append("HTF_LIST kosong — set minimal satu timeframe (contoh: 1D,4H)")
    for tf in HTF_LIST:
        if tf not in _VALID_OKX_BARS:
            errors.append(f"HTF_LIST berisi timeframe tidak dikenal: '{tf}'")
    if not LTF:
        errors.append("LTF kosong")
    elif LTF not in _VALID_OKX_BARS:
        errors.append(f"LTF tidak dikenal: '{LTF}'")

    # ── Angka harus positif / masuk akal ──
    if CHECK_INTERVAL_MINUTES < 1:
        errors.append(f"CHECK_INTERVAL_MINUTES harus ≥ 1 (sekarang {CHECK_INTERVAL_MINUTES})")
    if LOOKBACK_CANDLES < 20:
        errors.append(f"LOOKBACK_CANDLES terlalu kecil ({LOOKBACK_CANDLES}), minimal 20")
    if MAX_ACTIVE_ZONES_PER_TF < 1:
        errors.append(f"MAX_ACTIVE_ZONES_PER_TF harus ≥ 1 (sekarang {MAX_ACTIVE_ZONES_PER_TF})")
    if RISK_REWARD_RATIO <= 0:
        errors.append(f"RISK_REWARD_RATIO harus > 0 (sekarang {RISK_REWARD_RATIO})")
    if ATR_PERIOD < 1:
        errors.append(f"ATR_PERIOD harus ≥ 1 (sekarang {ATR_PERIOD})")
    if ATR_MULTIPLIER <= 0:
        errors.append(f"ATR_MULTIPLIER harus > 0 (sekarang {ATR_MULTIPLIER})")
    if VOLUME_MULTIPLIER <= 0:
        errors.append(f"VOLUME_MULTIPLIER harus > 0 (sekarang {VOLUME_MULTIPLIER})")
    if IMPULSE_ATR_MULTIPLIER <= 0:
        errors.append(f"IMPULSE_ATR_MULTIPLIER harus > 0 (sekarang {IMPULSE_ATR_MULTIPLIER})")
    if IMPULSE_MIN_PERCENT < 0:
        errors.append(f"IMPULSE_MIN_PERCENT tidak boleh negatif (sekarang {IMPULSE_MIN_PERCENT})")
    if TOP_N_PAIRS < 1:
        errors.append(f"TOP_N_PAIRS harus ≥ 1 (sekarang {TOP_N_PAIRS})")
    if BATCH_SIZE < 1:
        errors.append(f"BATCH_SIZE harus ≥ 1 (sekarang {BATCH_SIZE})")
    if BATCH_DELAY_SECONDS < 0:
        errors.append(f"BATCH_DELAY_SECONDS tidak boleh negatif (sekarang {BATCH_DELAY_SECONDS})")
    if ALERT_COOLDOWN_MINUTES < 0:
        errors.append(f"ALERT_COOLDOWN_MINUTES tidak boleh negatif (sekarang {ALERT_COOLDOWN_MINUTES})")
    if API_MAX_RETRIES < 1:
        errors.append(f"API_MAX_RETRIES harus ≥ 1 (sekarang {API_MAX_RETRIES})")
    if SL_BUFFER_PERCENT < 0:
        errors.append(f"SL_BUFFER_PERCENT tidak boleh negatif (sekarang {SL_BUFFER_PERCENT})")
    if MAX_RISK_PCT <= 0:
        errors.append(f"MAX_RISK_PCT harus > 0 (sekarang {MAX_RISK_PCT})")
    if MIN_RISK_PCT < 0:
        errors.append(f"MIN_RISK_PCT tidak boleh negatif (sekarang {MIN_RISK_PCT})")
    if MIN_RISK_PCT >= MAX_RISK_PCT:
        errors.append(
            f"MIN_RISK_PCT ({MIN_RISK_PCT}) harus < MAX_RISK_PCT ({MAX_RISK_PCT})"
        )

    # ── Enum / pilihan terbatas ──
    if DIRECTION_FILTER not in ("all", "bullish", "bearish"):
        errors.append(
            f"DIRECTION_FILTER harus 'all', 'bullish', atau 'bearish' (sekarang '{DIRECTION_FILTER}')"
        )

    # ── Jam sesi & ringkasan harian (0–23 / 0–59) ──
    for name, val in (
        ("SESSION_LONDON_START", SESSION_LONDON_START),
        ("SESSION_LONDON_END", SESSION_LONDON_END),
        ("SESSION_NY_START", SESSION_NY_START),
        ("SESSION_NY_END", SESSION_NY_END),
        ("DAILY_SUMMARY_HOUR_UTC", DAILY_SUMMARY_HOUR_UTC),
    ):
        if not (0 <= val <= 23):
            errors.append(f"{name} harus antara 0–23 (sekarang {val})")
    if not (0 <= DAILY_SUMMARY_MINUTE_UTC <= 59):
        errors.append(
            f"DAILY_SUMMARY_MINUTE_UTC harus antara 0–59 (sekarang {DAILY_SUMMARY_MINUTE_UTC})"
        )

    # ── Peringatan (tidak menghentikan bot) ──
    if RISK_REWARD_RATIO < 1.0:
        warnings.append(
            f"RISK_REWARD_RATIO={RISK_REWARD_RATIO} < 1 — reward lebih kecil dari risk"
        )
    if CHECK_INTERVAL_MINUTES < 5:
        warnings.append(
            f"CHECK_INTERVAL_MINUTES={CHECK_INTERVAL_MINUTES} sangat pendek — risiko rate limit OKX"
        )
    if TOP_N_PAIRS > 50:
        warnings.append(
            f"TOP_N_PAIRS={TOP_N_PAIRS} besar — scan bisa lambat / kena rate limit"
        )
    if BATCH_SIZE > 10:
        warnings.append(
            f"BATCH_SIZE={BATCH_SIZE} besar — pertimbangkan turunkan agar lebih aman ke API"
        )

    for w in warnings:
        logger.warning(f"[CONFIG] {w}")

    if errors:
        msg = "Konfigurasi tidak valid:\n  - " + "\n  - ".join(errors)
        raise RuntimeError(msg)

    logger.info(
        "[CONFIG] Validasi OK — "
        f"HTF={HTF_LIST} LTF={LTF} interval={CHECK_INTERVAL_MINUTES}m "
        f"pairs={TOP_N_PAIRS} RR=1:{RISK_REWARD_RATIO:g}"
    )
