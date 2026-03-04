"""
Kalshi data collector.

Fetches:
  - Active and recently resolved markets
  - Trade history per market (public endpoint, no auth required)
  - Aggregated per-trader stats derived from public trade feed
"""

import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from collections import defaultdict

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    KALSHI_BASE_URL,
    REQUEST_TIMEOUT,
    MAX_RETRIES,
    PAGE_SIZE,
    MAX_PAGES,
    DETECTION,
)


# ── Low-level HTTP client ─────────────────────────────────────────────────────

class KalshiClient:
    def __init__(self):
        self._client = httpx.AsyncClient(
            base_url=KALSHI_BASE_URL,
            timeout=REQUEST_TIMEOUT,
            headers={"Accept": "application/json"},
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self._client.aclose()

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def get(self, path: str, params: Optional[dict] = None) -> dict:
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()


# ── Market fetcher ────────────────────────────────────────────────────────────

async def fetch_markets(
    client: KalshiClient,
    status: str = "closed",          # "open" | "closed" | "settled"
    limit: int = PAGE_SIZE,
    max_pages: int = MAX_PAGES,
) -> list[dict]:
    """Return a flat list of market dicts."""
    markets: list[dict] = []
    cursor: Optional[str] = None

    for _ in range(max_pages):
        params: dict[str, Any] = {"limit": limit, "status": status}
        if cursor:
            params["cursor"] = cursor

        data = await client.get("/markets", params=params)
        batch = data.get("markets", [])
        markets.extend(batch)

        cursor = data.get("cursor")
        if not cursor or len(batch) < limit:
            break
        await asyncio.sleep(0.1)          # gentle rate limiting

    return markets


async def fetch_open_markets(client: KalshiClient) -> list[dict]:
    return await fetch_markets(client, status="open", max_pages=20)


async def fetch_recent_settled_markets(
    client: KalshiClient,
    days_back: int = DETECTION.lookback_days,
) -> list[dict]:
    """Return markets settled within the last `days_back` days."""
    all_settled = await fetch_markets(client, status="settled", max_pages=MAX_PAGES)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    recent = []
    for m in all_settled:
        settled_at_str = m.get("close_time") or m.get("expiration_time")
        if not settled_at_str:
            continue
        try:
            settled_at = datetime.fromisoformat(
                settled_at_str.replace("Z", "+00:00")
            )
            if settled_at >= cutoff:
                recent.append(m)
        except ValueError:
            continue
    return recent


# ── Trade fetcher ─────────────────────────────────────────────────────────────

async def fetch_trades_for_market(
    client: KalshiClient,
    ticker: str,
    max_pages: int = MAX_PAGES,
) -> list[dict]:
    """Return all public trades for a single market ticker."""
    trades: list[dict] = []
    cursor: Optional[str] = None

    for _ in range(max_pages):
        params: dict[str, Any] = {"ticker": ticker, "limit": PAGE_SIZE}
        if cursor:
            params["cursor"] = cursor

        data = await client.get("/markets/trades", params=params)
        batch = data.get("trades", [])
        trades.extend(batch)

        cursor = data.get("cursor")
        if not cursor or len(batch) < PAGE_SIZE:
            break
        await asyncio.sleep(0.05)

    return trades


async def fetch_trades_for_markets(
    client: KalshiClient,
    tickers: list[str],
    concurrency: int = 5,
) -> dict[str, list[dict]]:
    """Fetch trades for multiple tickers, respecting concurrency."""
    semaphore = asyncio.Semaphore(concurrency)
    results: dict[str, list[dict]] = {}

    async def _fetch(ticker: str):
        async with semaphore:
            trades = await fetch_trades_for_market(client, ticker)
            results[ticker] = trades

    await asyncio.gather(*[_fetch(t) for t in tickers])
    return results


# ── Trader aggregation ────────────────────────────────────────────────────────

def aggregate_trader_stats(
    all_trades: dict[str, list[dict]],
    settled_markets: list[dict],
) -> list[dict]:
    """
    Given trades keyed by ticker and a list of settled markets (with results),
    build per-trader stats.

    Kalshi public trades include: taker_side, count (contracts), yes_price,
    created_time.  They do NOT include a member_id in the public feed.

    We do what Kalshi's own surveillance does: look at statistical patterns
    within each market (e.g., which side was consistently taken ahead of
    resolution) rather than per-user (since user IDs aren't in public trades).

    Returns a list of "trader profile" dicts with aggregated stats.
    """
    # Build a ticker → result / name mapping
    market_results: dict[str, str] = {}
    market_close_times: dict[str, datetime] = {}
    market_names: dict[str, str] = {}
    for m in settled_markets:
        ticker = m.get("ticker", "")
        result = m.get("result", "")           # "yes" or "no"
        market_results[ticker] = result.lower() if result else ""
        market_names[ticker] = (
            m.get("title") or m.get("subtitle") or ticker
        )
        close_str = m.get("close_time") or m.get("expiration_time", "")
        try:
            market_close_times[ticker] = datetime.fromisoformat(
                close_str.replace("Z", "+00:00")
            )
        except ValueError:
            pass

    # Group trades by (taker_side, market) and compute win/loss
    # Since we lack user IDs we model "consistent directional traders":
    # a synthetic "trader profile" per (market, side) that took large positions.
    profiles: list[dict] = []

    for ticker, trades in all_trades.items():
        result = market_results.get(ticker)
        close_time = market_close_times.get(ticker)
        if not result or not close_time:
            continue

        market_name = market_names.get(ticker, ticker)
        for trade in trades:
            trade["_ticker"] = ticker
            trade["_market_name"] = market_name
            trade["_market_result"] = result
            trade["_close_time"] = close_time

        # Bucket trades into 5-min windows before resolution
        last_min_trades = []
        normal_trades = []
        for t in trades:
            created_raw = t.get("created_time", "")
            try:
                created = datetime.fromisoformat(
                    created_raw.replace("Z", "+00:00")
                )
                seconds_before_close = (close_time - created).total_seconds()
                if 0 <= seconds_before_close <= DETECTION.last_minute_window_seconds:
                    last_min_trades.append(t)
                else:
                    normal_trades.append(t)
            except ValueError:
                normal_trades.append(t)

        # For each trade check if the taker side matched the winning result
        def is_winning_trade(trade: dict) -> bool:
            side = (trade.get("taker_side") or "").lower()
            return side == market_results.get(trade["_ticker"], "")

        if last_min_trades:
            wins = sum(1 for t in last_min_trades if is_winning_trade(t))
            losses = len(last_min_trades) - wins
            profiles.append({
                "source": "kalshi",
                "ticker": ticker,
                "market_name": market_names.get(ticker, ticker),
                "profile_type": "last_minute_trades",
                "total_trades": len(last_min_trades),
                "winning_trades": wins,
                "losing_trades": losses,
                "win_rate": wins / len(last_min_trades) if last_min_trades else 0.0,
                "markets_traded_count": 1,
                "total_volume": sum(t.get("count", 0) for t in last_min_trades),
                "market_result": result,
                "raw_trades": last_min_trades,
            })

    return profiles


# ── Public entry point ────────────────────────────────────────────────────────

async def collect_kalshi_data(days_back: int = DETECTION.lookback_days) -> dict:
    """
    Main entry: collect all data needed for anomaly analysis.
    Returns:
      {
        "markets": [...],
        "trades": {ticker: [...]},
        "trader_profiles": [...],
      }
    """
    async with KalshiClient() as client:
        print("[Kalshi] Fetching recently settled markets...")
        settled = await fetch_recent_settled_markets(client, days_back=days_back)
        print(f"[Kalshi] Found {len(settled)} settled markets in last {days_back} days")

        print("[Kalshi] Fetching open markets...")
        open_markets = await fetch_open_markets(client)
        print(f"[Kalshi] Found {len(open_markets)} open markets")

        all_markets = settled + open_markets
        tickers = [m["ticker"] for m in settled if m.get("ticker")][:100]   # cap for now

        print(f"[Kalshi] Fetching trades for {len(tickers)} settled markets...")
        trades = await fetch_trades_for_markets(client, tickers)
        total_trades = sum(len(v) for v in trades.values())
        print(f"[Kalshi] Collected {total_trades} total trades")

        profiles = aggregate_trader_stats(trades, settled)
        print(f"[Kalshi] Built {len(profiles)} trader profiles")

    return {
        "markets": all_markets,
        "settled_markets": settled,
        "trades": trades,
        "trader_profiles": profiles,
    }


if __name__ == "__main__":
    result = asyncio.run(collect_kalshi_data(days_back=7))
    print(f"\nSummary: {len(result['markets'])} markets, "
          f"{sum(len(v) for v in result['trades'].values())} trades, "
          f"{len(result['trader_profiles'])} profiles")
