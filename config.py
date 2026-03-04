"""
Central configuration for the prediction market surveillance system.
"""

from dataclasses import dataclass, field
from typing import Optional
import os
from dotenv import load_dotenv

load_dotenv()


# ── API endpoints ────────────────────────────────────────────────────────────

KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
POLYMARKET_CLOB_URL = "https://clob.polymarket.com"
POLYMARKET_GAMMA_URL = "https://gamma-api.polymarket.com"
POLYMARKET_DATA_URL = "https://data-api.polymarket.com"


# ── Detection thresholds ─────────────────────────────────────────────────────

@dataclass
class DetectionConfig:
    # Minimum number of resolved trades required to flag a trader
    min_trades_for_analysis: int = 5

    # Win-rate z-score threshold (how many std devs above 50% is suspicious)
    # z > 2.0 → p < 0.023  (flagged as anomalous)
    winrate_zscore_threshold: float = 2.0

    # Minimum win rate to consider (avoids flagging very unlucky traders)
    min_suspicious_winrate: float = 0.60

    # Profit factor – average profit vs average loss ratio
    suspicious_profit_factor: float = 2.0

    # Kalshi: how close to resolution (seconds) counts as "last-minute"
    last_minute_window_seconds: int = 300   # 5 min

    # Fraction of last-minute winning trades that triggers a flag
    last_minute_win_ratio_threshold: float = 0.70

    # Polymarket: minimum USD notional of a trade to include in analysis
    min_trade_size_usd: float = 10.0

    # Market concentration – fraction of one market in a trader's portfolio
    market_concentration_threshold: float = 0.60

    # Consecutive wins threshold for Wald-Wolfowitz runs test flagging
    max_acceptable_consecutive_wins: int = 6

    # Significance level for binomial / chi-square tests
    alpha: float = 0.05

    # Days of history to pull on initial scan
    lookback_days: int = 30


DETECTION = DetectionConfig()


# ── Pagination / request settings ────────────────────────────────────────────

REQUEST_TIMEOUT = 30          # seconds
MAX_RETRIES = 3
PAGE_SIZE = 100               # records per API call
MAX_PAGES = 50                # safety cap so we don't loop forever


# ── Optional Kalshi API key (needed for higher rate limits) ──────────────────

KALSHI_API_KEY_ID: Optional[str] = os.getenv("KALSHI_API_KEY_ID")
KALSHI_PRIVATE_KEY_PATH: Optional[str] = os.getenv("KALSHI_PRIVATE_KEY_PATH")
