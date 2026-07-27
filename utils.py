import time
import math
from datetime import datetime, timezone

import ob_core
from config import *

def interval_to_seconds(interval: str) -> int:
    """Konversi string interval OKX ke detik."""
    mapping = {
        "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
        "1H": 3600, "2H": 7200, "4H": 14400, "6H": 21600, "12H": 43200,
        "1D": 86400, "1W": 604800,
    }
    return mapping.get(interval, 3600)
