"""
Polymarket data collector.

Fetches:
  - Active and recently closed markets (Gamma API)
  - Public trade history (Data API)
  - Per-wallet position and activity data (Data API)
  - Top holders per market (Data API)
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from collections import defaultdict

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    POLYMARKET_GAMMA_URL,
    POLYMARKET_DATA_URL,
    POLYMARKET_CLOB_URL,
    REQUEST_TIMEOUT,
    MAX_RETRIES,
    PAGE_SIZE,
    MAX_PAGES,
    DETECTION,
)


# ── HTTP client ───────────────────────────────────────────────────────────────

class PolymarketClient:
    def __init__(self):
        self._clients: dict[str, httpx.AsyncClient] = {
            "gamma": httpx.AsyncClient(
                base_url=POLYMARKET_GAMMA_URL,
                timeout=REQUEST_TIMEOUT,
                headers={"Accept": "application/json"},
            ),
            "data": httpx.AsyncClient(
                base_url=POLYMARKET_DATA_URL,
                timeout=REQUEST_TIMEOUT,
                headers={"Accept": "application/json"},
            ),
            "clob": httpx.AsyncClient(
                base_url=POLYMARKET_CLOB_URL,
                timeout=REQUEST_TIMEOUT,
                headers={"Accept": "application/json"},
            ),
        }

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        for c in self._clients.values():
            await c.aclose()

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def get(self, service: str, path: str, params: Optional[dict] = None) -> Any:
        resp = await self._clients[service].get(path, params=params)
        resp.raise_for_status()
        return resp.json()


# ── Market fetchers ───────────────────────────────────────────────────────────

async def fetch_closed_markets(
    client: PolymarketClient,
    days_back: int = DETECTION.lookback_days,
    limit: int = PAGE_SIZE,
) -> list[dict]:
    """Return recently closed markets from Gamma API.

    Uses end_date_min to efficiently retrieve only markets whose scheduled
    end date falls within the lookback window — avoids scanning tens of
    thousands of older markets that have no data in the current CLOB API.
    """
    markets: list[dict] = []
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days_back)
    # Gamma API accepts end_date_min in ISO format
    end_date_min = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    offset = 0

    for _ in range(MAX_PAGES):
        params = {
            "limit": limit,
            "offset": offset,
            "closed": "true",
            "end_date_min": end_date_min,
        }
        data = await client.get("gamma", "/markets", params=params)
        if isinstance(data, dict):
            batch = data.get("data", data.get("markets", []))
        else:
            batch = data

        if not batch:
            break

        for m in batch:
            # Skip non-binary markets (price-range, multi-outcome, etc.)
            if not _is_binary_yes_no(m):
                continue
            markets.append(m)

        if len(batch) < limit:
            break
        offset += limit
        await asyncio.sleep(0.1)

    # Sort most-recently-closed first
    def _end_ts(m: dict) -> int:
        try:
            return int(datetime.fromisoformat(
                (m.get("endDateIso") or m.get("end_date_iso") or "").replace("Z", "+00:00")
            ).timestamp())
        except (ValueError, TypeError):
            return 0

    markets.sort(key=_end_ts, reverse=True)
    return markets


async def fetch_active_markets(
    client: PolymarketClient,
    limit: int = PAGE_SIZE,
    max_pages: int = 20,
) -> list[dict]:
    """Return currently active markets."""
    markets: list[dict] = []
    offset = 0

    for _ in range(max_pages):
        params = {"limit": limit, "offset": offset, "active": "true"}
        data = await client.get("gamma", "/markets", params=params)
        if isinstance(data, dict):
            batch = data.get("data", data.get("markets", []))
        else:
            batch = data

        if not batch:
            break
        markets.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
        await asyncio.sleep(0.1)

    return markets


# ── Trade / activity fetchers ─────────────────────────────────────────────────

async def fetch_trades_for_market(
    client: PolymarketClient,
    condition_id: str,
    limit: int = PAGE_SIZE,
    max_pages: int = MAX_PAGES,
) -> list[dict]:
    """Return all trades for a market identified by its condition_id."""
    trades: list[dict] = []
    offset = 0

    for _ in range(max_pages):
        params = {
            "market": condition_id,
            "limit": limit,
            "offset": offset,
        }
        try:
            data = await client.get("data", "/trades", params=params)
        except httpx.HTTPStatusError:
            break

        if isinstance(data, list):
            batch = data
        else:
            batch = data.get("data", [])

        if not batch:
            break
        trades.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
        await asyncio.sleep(0.05)

    return trades


async def fetch_top_holders(
    client: PolymarketClient,
    condition_id: str,
    limit: int = 50,
) -> list[dict]:
    """Return top position holders for a market."""
    try:
        params = {"market": condition_id, "limit": limit}
        data = await client.get("data", "/holders", params=params)
        if isinstance(data, list):
            return data
        return data.get("data", [])
    except httpx.HTTPStatusError:
        return []


async def fetch_wallet_activity(
    client: PolymarketClient,
    wallet_address: str,
    limit: int = PAGE_SIZE,
    max_pages: int = 10,
) -> list[dict]:
    """Return all activity for a given wallet address."""
    activities: list[dict] = []
    offset = 0

    for _ in range(max_pages):
        params = {
            "user": wallet_address,
            "limit": limit,
            "offset": offset,
            "type": "TRADE",
        }
        try:
            data = await client.get("data", "/activity", params=params)
        except httpx.HTTPStatusError:
            break

        if isinstance(data, list):
            batch = data
        else:
            batch = data.get("data", [])

        if not batch:
            break
        activities.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
        await asyncio.sleep(0.05)

    return activities


# ── Per-wallet aggregation ────────────────────────────────────────────────────

def aggregate_wallet_stats(
    market_trades: dict[str, list[dict]],
    market_outcomes: dict[str, str],       # condition_id → "yes" | "no"
    market_close_times: dict[str, datetime],
    market_names: Optional[dict[str, str]] = None,
) -> list[dict]:
    """
    Build per-wallet trading profiles from raw market trades.

    Polymarket trades contain proxyWallet (anonymised) and side ("BUY"/"SELL"),
    outcome ("Yes"/"No"), price (0-1), and size (USDC).
    """
    wallet_data: dict[str, dict] = defaultdict(lambda: {
        "trades": [],
        "markets_traded": set(),
        "winning_trades": 0,
        "losing_trades": 0,
        "total_volume_usd": 0.0,
        "last_minute_wins": 0,
        "last_minute_trades": 0,
        "profit_usd": 0.0,
    })

    _names = market_names or {}

    for condition_id, trades in market_trades.items():
        outcome = market_outcomes.get(condition_id, "").lower()
        close_time = market_close_times.get(condition_id)
        mname = _names.get(condition_id, condition_id[:20])

        for trade in trades:
            trade["_market_name"] = mname
            wallet = trade.get("proxyWallet") or trade.get("maker") or ""
            if not wallet:
                continue

            # Filter tiny trades
            size = float(trade.get("size", 0) or 0)
            price = float(trade.get("price", 0) or 0)
            notional = size * price
            if notional < DETECTION.min_trade_size_usd:
                continue

            side = (trade.get("side") or "").upper()           # BUY / SELL
            trade_outcome = (trade.get("outcome") or "").lower()  # yes / no

            # A BUY of the correct outcome = winning trade
            # A BUY of the wrong outcome = losing trade
            # We ignore SELLs (could be taking profit or cutting losses)
            if side != "BUY" or not outcome:
                continue

            is_win = (trade_outcome == outcome)
            pnl = size * (1.0 - price) if is_win else -size * price

            w = wallet_data[wallet]
            w["trades"].append(trade)
            w["markets_traded"].add(condition_id)
            w["total_volume_usd"] += notional
            if is_win:
                w["winning_trades"] += 1
                w["profit_usd"] += pnl
            else:
                w["losing_trades"] += 1
                w["profit_usd"] += pnl

            # Last-minute detection
            if close_time:
                created_raw = trade.get("timestamp") or trade.get("createdAt") or ""
                try:
                    if isinstance(created_raw, (int, float)):
                        created = datetime.fromtimestamp(
                            float(created_raw), tz=timezone.utc
                        )
                    else:
                        created = datetime.fromisoformat(
                            str(created_raw).replace("Z", "+00:00")
                        )
                        if created.tzinfo is None:
                            created = created.replace(tzinfo=timezone.utc)
                    secs_before = (close_time - created).total_seconds()
                    if 0 <= secs_before <= DETECTION.last_minute_window_seconds:
                        w["last_minute_trades"] += 1
                        if is_win:
                            w["last_minute_wins"] += 1
                except (ValueError, OSError):
                    pass

    # Convert to list of profile dicts
    profiles = []
    for wallet, stats in wallet_data.items():
        total = stats["winning_trades"] + stats["losing_trades"]
        if total < DETECTION.min_trades_for_analysis:
            continue

        win_rate = stats["winning_trades"] / total if total else 0.0
        lm_trades = stats["last_minute_trades"]
        lm_win_rate = (
            stats["last_minute_wins"] / lm_trades if lm_trades > 0 else None
        )

        profiles.append({
            "source": "polymarket",
            "wallet": wallet,
            "total_trades": total,
            "winning_trades": stats["winning_trades"],
            "losing_trades": stats["losing_trades"],
            "win_rate": win_rate,
            "total_volume_usd": round(stats["total_volume_usd"], 2),
            "profit_usd": round(stats["profit_usd"], 2),
            "markets_traded_count": len(stats["markets_traded"]),
            "last_minute_trades": lm_trades,
            "last_minute_win_rate": lm_win_rate,
            "raw_trades": stats["trades"],
        })

    return profiles


# ── Winner resolution helpers ─────────────────────────────────────────────────

import json as _json


def _parse_outcomes(m: dict) -> list:
    """
    Return the outcomes list, handling the Gamma API returning it as a
    JSON-encoded string (e.g. '["Yes","No"]') rather than a parsed list.
    """
    raw = m.get("outcomes") or []
    if isinstance(raw, str):
        try:
            raw = _json.loads(raw)
        except (ValueError, TypeError):
            return []
    return [str(o) for o in raw] if isinstance(raw, list) else []


def _parse_outcome_prices(m: dict) -> list:
    """Same treatment for outcomePrices."""
    raw = m.get("outcomePrices") or []
    if isinstance(raw, str):
        try:
            raw = _json.loads(raw)
        except (ValueError, TypeError):
            return []
    try:
        return [float(p) for p in raw] if isinstance(raw, list) else []
    except (ValueError, TypeError):
        return []


def _is_binary_yes_no(m: dict) -> bool:
    """Return True only for binary YES/NO markets (excludes price-range markets)."""
    labels = {o.lower() for o in _parse_outcomes(m)}
    return labels == {"yes", "no"}


def _resolve_winner(m: dict) -> str:
    """
    Extract the winning outcome ('yes' or 'no') from a closed Polymarket market.

    Priority order:
      1. winnerOutcome field (not always populated)
      2. tokens list where tok['winner'] == True (dict format)
      3. outcomePrices — resolved outcome has price 1.0, loser 0.0
      4. lastTradePrice — YES shares settle at ~1.0 (YES won) or ~0.0 (NO won)

    Returns '' if winner cannot be determined.
    """
    winner = (m.get("winnerOutcome") or "").strip()
    if winner:
        return winner.lower()

    tokens = m.get("tokens") or []
    if tokens and isinstance(tokens[0], dict):
        for tok in tokens:
            if tok.get("winner"):
                return (tok.get("outcome") or "").lower()

    outcome_labels = _parse_outcomes(m)
    outcome_prices = _parse_outcome_prices(m)

    if outcome_labels and outcome_prices and len(outcome_labels) == len(outcome_prices):
        max_price = max(outcome_prices)
        if max_price >= 0.99:
            idx = outcome_prices.index(max_price)
            return outcome_labels[idx].lower()

    # lastTradePrice: use explicit None check so lastTradePrice=0 is handled correctly
    # (0 is falsy in Python, so `0 or -1` wrongly gives -1)
    if m.get("closed") or m.get("archived"):
        ltp_raw = m.get("lastTradePrice")
        if ltp_raw is not None:
            try:
                ltp = float(ltp_raw)
                if ltp >= 0.95 and outcome_labels:
                    return outcome_labels[0].lower()
                if 0.0 <= ltp <= 0.05 and len(outcome_labels) >= 2:
                    return outcome_labels[1].lower()
            except (ValueError, TypeError):
                pass

    return ""


# ── Public entry point ────────────────────────────────────────────────────────

async def collect_polymarket_data(days_back: int = DETECTION.lookback_days) -> dict:
    """
    Main entry: collect all data needed for Polymarket anomaly analysis.
    Returns:
      {
        "markets": [...],
        "trades": {condition_id: [...]},
        "wallet_profiles": [...],
      }
    """
    async with PolymarketClient() as client:
        print("[Polymarket] Fetching recently closed markets...")
        closed = await fetch_closed_markets(client, days_back=days_back)
        print(f"[Polymarket] Found {len(closed)} closed markets in last {days_back} days")

        print("[Polymarket] Fetching active markets...")
        active = await fetch_active_markets(client)
        print(f"[Polymarket] Found {len(active)} active markets")

        all_markets = closed + active

        # Extract condition IDs, outcome data, and human-readable names
        market_outcomes: dict[str, str] = {}
        market_close_times: dict[str, datetime] = {}
        market_names: dict[str, str] = {}

        for m in closed:
            cid = m.get("conditionId") or m.get("condition_id") or ""
            if not cid:
                continue
            # Prefer the full question text so the dashboard shows what the bet
            # is actually about ("Will the New York Rangers win the 2026 NHL
            # Stanley Cup?" rather than just "New York Rangers").
            # Polymarket categorical outcomes use "yes X" / "no X" questions —
            # strip that prefix so we get the clean subject when question is used.
            q = (m.get("question") or "").strip()
            if q.lower().startswith(("yes ", "no ")):
                q = q.split(" ", 1)[1].strip()
            market_names[cid] = q or (m.get("groupItemTitle") or "").strip() or cid[:24]
            winner = _resolve_winner(m)
            if winner:
                market_outcomes[cid] = winner

            end_date = m.get("endDateIso") or m.get("end_date_iso") or ""
            try:
                ct = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                if ct.tzinfo is None:
                    ct = ct.replace(tzinfo=timezone.utc)
                market_close_times[cid] = ct
            except (ValueError, TypeError):
                pass

        # Fetch trades for top markets (cap to avoid rate limits)
        cids = [
            m.get("conditionId") or m.get("condition_id")
            for m in closed
            if (m.get("conditionId") or m.get("condition_id"))
        ][:80]

        print(f"[Polymarket] Fetching trades for {len(cids)} markets...")
        semaphore = asyncio.Semaphore(5)
        market_trades: dict[str, list[dict]] = {}

        async def _fetch_trades(cid: str):
            async with semaphore:
                trades = await fetch_trades_for_market(client, cid)
                market_trades[cid] = trades

        await asyncio.gather(*[_fetch_trades(c) for c in cids])
        total_trades = sum(len(v) for v in market_trades.values())
        print(f"[Polymarket] Collected {total_trades} total trades")

        wallet_profiles = aggregate_wallet_stats(
            market_trades, market_outcomes, market_close_times, market_names
        )
        print(f"[Polymarket] Built {len(wallet_profiles)} wallet profiles")

    return {
        "markets": all_markets,
        "closed_markets": closed,
        "trades": market_trades,
        "wallet_profiles": wallet_profiles,
    }


if __name__ == "__main__":
    result = asyncio.run(collect_polymarket_data(days_back=7))
    print(f"\nSummary: {len(result['markets'])} markets, "
          f"{sum(len(v) for v in result['trades'].values())} trades, "
          f"{len(result['wallet_profiles'])} wallet profiles")
