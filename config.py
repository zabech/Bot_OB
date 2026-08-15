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
ATR_PERIOD = int(os.environ.get("ATR_PERIOD", "14"))                     # periode ATR untuk hitung SL
ATR_MULTIPLIER = float(os.environ.get("ATR_MULTIPLIER", "2.0"))          # SL = invalidasi ± (ATR × multiplier)

# Konfigurasi deteksi OB tingkat lanjut
REQUIRE_BOS = os.environ.get("REQUIRE_BOS", "true").lower() == "true"
REQUIRE_FVG = os.environ.get("REQUIRE_FVG", "false").lower() == "true"
MITIGATION_50PCT = os.environ.get("MITIGATION_50PCT", "true").lower() == "true"
SWING_LOOKBACK = int(os.environ.get("SWING_LOOKBACK", "10"))

# Filter sesi trading (jam dalam UTC)
# Semua sesi tetap jalan, tapi tiap alert diberi label kualitas sesi
SESSION_LONDON_START = int(os.environ.get("SESSION_LONDON_START", "7"))
SESSION_LONDON_END = int(os.environ.get("SESSION_LONDON_END", "16"))
SESSION_NY_START = int(os.environ.get("SESSION_NY_START", "13"))
SESSION_NY_END = int(os.environ.get("SESSION_NY_END", "22"))

# Scanner multi-pair (OKX Futures - USDT-margined swap/perpetual)
TOP_N_PAIRS = int(os.environ.get("TOP_N_PAIRS", "30"))
PAIR_QUOTE = os.environ.get("PAIR_QUOTE", "USDT")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "5"))
BATCH_DELAY_SECONDS = float(os.environ.get("BATCH_DELAY_SECONDS", "2"))
SYMBOL_REFRESH_HOURS = int(os.environ.get("SYMBOL_REFRESH_HOURS", "6"))
MIN_VOLUME_USD = float(os.environ.get("MIN_VOLUME_USD", "5000000"))  # skip pair dengan volume 24h di bawah ini

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

# Zona aktif per (symbol, timeframe): { "BTC-USDT-SWAP": {"1D": [...], "4H": [...]}, ... }
active_zones = {}

# Cache daftar top pair, di-refresh berkala
top_pairs_cache = {"symbols": [], "last_refresh": 0}

# Trade aktif per pair — pair tidak boleh kirim sinyal baru sampai TP/SL tercapai
# Format: { "BTC-USDT-SWAP": {"entry": 65000, "sl": 64200, "tp": 66600, "zone_type": "bullish", "htf": "4H"} }
active_trades = {}

# Timestamp health alert terakhir, untuk hindari spam notifikasi "bot bermasalah"
last_health_alert_time = {"ts": 0}
