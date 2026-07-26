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
