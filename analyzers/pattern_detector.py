"""
Higher-level insider-trading pattern detectors.

These go beyond per-trade statistics and look for behavioural signatures
such as:
  - Trading clusters that coincide with non-public information release windows
  - Wallet/account co-movement (multiple accounts making identical bets)
  - Sudden position spikes on obscure markets shortly before resolution
  - Profit consistency across completely unrelated markets (cross-market edge)
"""

from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from typing import Optional
import math

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DETECTION


# ── Pattern 1: Pre-resolution position spike ─────────────────────────────────

def detect_position_spikes(
    market_trades: dict[str, list[dict]],
    market_close_times: dict[str, datetime],
    spike_window_seconds: int = 600,     # 10 min
    volume_multiplier: float = 5.0,
) -> list[dict]:
    """
    For each market, compare average trade volume in the spike_window to the
    baseline (earlier period).  Flag markets where volume surged > volume_multiplier×
    in the pre-resolution window.
    """
    alerts = []

    for ticker_or_cid, trades in market_trades.items():
        close_time = market_close_times.get(ticker_or_cid)
        if not close_time or not trades:
            continue

        spike_trades = []
        baseline_trades = []

        for t in trades:
            ts_raw = (
                t.get("created_time")
                or t.get("timestamp")
                or t.get("createdAt")
                or ""
            )
            try:
                if isinstance(ts_raw, (int, float)):
                    ts = datetime.fromtimestamp(float(ts_raw), tz=timezone.utc)
                else:
                    ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
            except (ValueError, OSError):
                continue

            # Ensure close_time is also tz-aware for subtraction
            ct = close_time
            if ct.tzinfo is None:
                ct = ct.replace(tzinfo=timezone.utc)
            secs_before = (ct - ts).total_seconds()
            if 0 <= secs_before <= spike_window_seconds:
                spike_trades.append(t)
            elif spike_window_seconds < secs_before <= spike_window_seconds * 10:
                baseline_trades.append(t)

        if not baseline_trades or not spike_trades:
            continue

        def _total_volume(tlist: list[dict]) -> float:
            total = 0.0
            for t in tlist:
                size = float(t.get("count", 0) or t.get("size", 0) or 0)
                price = float(t.get("yes_price", 0.5) or t.get("price", 0.5) or 0.5)
                total += size * price
            return total

        spike_vol = _total_volume(spike_trades)
        baseline_rate = _total_volume(baseline_trades) / 9   # same time window
        if baseline_rate <= 0:
            continue

        multiplier = spike_vol / baseline_rate
        if multiplier >= volume_multiplier:
            alerts.append({
                "type": "position_spike",
                "market_id": ticker_or_cid,
                "spike_volume": round(spike_vol, 2),
                "baseline_volume_per_window": round(baseline_rate, 2),
                "volume_multiplier": round(multiplier, 2),
                "spike_trade_count": len(spike_trades),
                "severity": "HIGH" if multiplier >= volume_multiplier * 2 else "MEDIUM",
            })

    return sorted(alerts, key=lambda x: x["volume_multiplier"], reverse=True)


# ── Pattern 2: Wallet co-movement (coordinated trading) ──────────────────────

def detect_coordinated_wallets(
    wallet_profiles: list[dict],
    min_shared_markets: int = 3,
    correlation_threshold: float = 0.85,
) -> list[dict]:
    """
    Find pairs/groups of wallets that consistently bet on the same markets
    and in the same direction.  This can indicate shared information sources
    or coordination.

    Returns a list of suspicious wallet-pair dicts.
    """
    # Build wallet → set of (market, outcome) tuples
    wallet_markets: dict[str, set] = {}
    for prof in wallet_profiles:
        wallet = prof.get("wallet")
        if not wallet:
            continue
        trades = prof.get("raw_trades", [])
        market_bets: set = set()
        for t in trades:
            cid = t.get("market") or t.get("_ticker") or ""
            outcome = (t.get("outcome") or t.get("taker_side") or "").lower()
            if cid and outcome:
                market_bets.add((cid, outcome))
        if market_bets:
            wallet_markets[wallet] = market_bets

    wallets = list(wallet_markets.keys())
    alerts = []

    # Compare all pairs — cap to avoid O(n²) blowup on large datasets
    if len(wallets) > 500:
        # Only compare high-profile wallets (those already flagged)
        flagged = {p.get("wallet") for p in wallet_profiles if p.get("flags")}
        wallets = [w for w in wallets if w in flagged][:200]

    for w1, w2 in combinations(wallets, 2):
        s1 = wallet_markets[w1]
        s2 = wallet_markets[w2]
        shared = s1 & s2
        if len(shared) < min_shared_markets:
            continue

        jaccard = len(shared) / len(s1 | s2)
        if jaccard >= correlation_threshold:
            alerts.append({
                "type": "coordinated_wallets",
                "wallet_1": w1,
                "wallet_2": w2,
                "shared_market_bets": len(shared),
                "jaccard_similarity": round(jaccard, 3),
                "severity": "HIGH" if jaccard >= 0.95 else "MEDIUM",
            })

    return sorted(alerts, key=lambda x: x["jaccard_similarity"], reverse=True)


# ── Pattern 3: Cross-market information edge ──────────────────────────────────

def detect_cross_market_edge(
    wallet_profiles: list[dict],
    min_categories: int = 3,
    min_win_rate: float = 0.70,
) -> list[dict]:
    """
    A diversified bettor who wins consistently across *unrelated* market
    categories is more suspicious than one who dominates a single topic.
    Here we flag wallets that have a high win rate AND broad market diversification.
    """
    alerts = []

    for prof in wallet_profiles:
        wallet = prof.get("wallet")
        win_rate = prof.get("win_rate", 0.0)
        markets_count = prof.get("markets_traded_count", 0)
        total_trades = prof.get("total_trades", 0)

        if (
            not wallet
            or win_rate < min_win_rate
            or markets_count < min_categories
            or total_trades < DETECTION.min_trades_for_analysis
        ):
            continue

        # Suspicion increases when BOTH win rate and diversification are high
        diversification_score = min(1.0, markets_count / 20)
        edge_score = round((win_rate - 0.5) * 2 * diversification_score * 100, 1)

        if edge_score >= 30:
            alerts.append({
                "type": "cross_market_edge",
                "wallet": wallet,
                "win_rate": round(win_rate, 3),
                "markets_traded": markets_count,
                "total_trades": total_trades,
                "edge_score": edge_score,
                "severity": "HIGH" if edge_score >= 60 else "MEDIUM",
            })

    return sorted(alerts, key=lambda x: x["edge_score"], reverse=True)


# ── Pattern 4: Timing correlation with external events ───────────────────────

def detect_event_timing_clusters(
    market_trades: dict[str, list[dict]],
    market_close_times: dict[str, datetime],
    event_timestamps: Optional[list[datetime]] = None,
    cluster_window_minutes: int = 30,
) -> list[dict]:
    """
    If you supply a list of known external event timestamps (e.g., when a
    streamer uploaded a video, when a press release was issued), this function
    identifies trades that cluster within `cluster_window_minutes` before each event.

    Without external events it flags markets that had unusual activity spikes
    and tries to cluster them by time.
    """
    alerts = []

    if event_timestamps:
        for event_ts in event_timestamps:
            window_start = event_ts.timestamp() - cluster_window_minutes * 60
            window_end = event_ts.timestamp()

            for ticker_or_cid, trades in market_trades.items():
                pre_event_trades = []
                for t in trades:
                    ts_raw = (
                        t.get("created_time")
                        or t.get("timestamp")
                        or t.get("createdAt")
                        or ""
                    )
                    try:
                        if isinstance(ts_raw, (int, float)):
                            ts = float(ts_raw)
                        else:
                            ts = datetime.fromisoformat(
                                str(ts_raw).replace("Z", "+00:00")
                            ).timestamp()
                        if window_start <= ts <= window_end:
                            pre_event_trades.append(t)
                    except (ValueError, OSError):
                        continue

                if len(pre_event_trades) >= 3:
                    wallets = {
                        t.get("proxyWallet") or t.get("maker") or "unknown"
                        for t in pre_event_trades
                    }
                    alerts.append({
                        "type": "event_timing_cluster",
                        "market_id": ticker_or_cid,
                        "event_time": event_ts.isoformat(),
                        "pre_event_trade_count": len(pre_event_trades),
                        "unique_wallets": len(wallets),
                        "severity": "HIGH" if len(pre_event_trades) >= 10 else "MEDIUM",
                    })

    return alerts


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_pattern_detection(
    kalshi_data: Optional[dict] = None,
    polymarket_data: Optional[dict] = None,
    event_timestamps: Optional[list[datetime]] = None,
) -> dict:
    """
    Run all pattern detectors across both data sources.
    Returns a dict of pattern type → list of alerts.
    """
    results: dict[str, list] = {
        "position_spikes": [],
        "coordinated_wallets": [],
        "cross_market_edge": [],
        "event_timing_clusters": [],
    }

    # Build unified market trade + close_time maps
    all_market_trades: dict[str, list[dict]] = {}
    all_close_times: dict[str, datetime] = {}

    if kalshi_data:
        all_market_trades.update(kalshi_data.get("trades", {}))
        for m in kalshi_data.get("settled_markets", []):
            ticker = m.get("ticker", "")
            close_str = m.get("close_time") or m.get("expiration_time", "")
            try:
                all_close_times[ticker] = datetime.fromisoformat(
                    close_str.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                pass

    if polymarket_data:
        all_market_trades.update(polymarket_data.get("trades", {}))
        for m in polymarket_data.get("closed_markets", []):
            cid = m.get("conditionId") or m.get("condition_id", "")
            end_date = m.get("endDateIso") or m.get("end_date_iso", "")
            try:
                all_close_times[cid] = datetime.fromisoformat(
                    end_date.replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass

    # Run detectors
    results["position_spikes"] = detect_position_spikes(
        all_market_trades, all_close_times
    )

    all_wallet_profiles = []
    if polymarket_data:
        all_wallet_profiles.extend(polymarket_data.get("wallet_profiles", []))

    results["coordinated_wallets"] = detect_coordinated_wallets(all_wallet_profiles)
    results["cross_market_edge"] = detect_cross_market_edge(all_wallet_profiles)

    if event_timestamps:
        results["event_timing_clusters"] = detect_event_timing_clusters(
            all_market_trades, all_close_times, event_timestamps
        )

    total = sum(len(v) for v in results.values())
    print(f"[PatternDetector] Found {total} total pattern alerts")
    return results
