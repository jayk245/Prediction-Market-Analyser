"""
Pre-resolution anomaly detectors.

These fire on OPEN markets where the outcome is not yet known.
Instead of win/loss, we look for:

  1. Volume spike        – sudden trade volume surge vs. recent baseline
  2. Order flow skew     – one side (YES or NO) being bought overwhelmingly
  3. Price drift         – price moving fast without visible public catalyst
  4. Coordinated entry   – multiple distinct wallets entering the same side rapidly
  5. Known bad actor     – a historically flagged wallet is now active
  6. Time-to-close rush  – unusual activity as resolution approaches

Every fired signal now includes a `triggering_trades` list with the exact
individual trades that caused it to fire, in a normalised format.
"""

from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Optional

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Trade normalisation ───────────────────────────────────────────────────────

def _parse_ts(trade: dict) -> datetime:
    raw = trade.get("created_time") or trade.get("timestamp") or trade.get("createdAt") or ""
    try:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, OSError):
        return datetime.now(timezone.utc)


def normalise_trade(raw: dict, market_id: str = "", market_name: str = "") -> dict:
    """
    Convert a raw Kalshi or Polymarket trade dict into a uniform format
    with all fields needed for display.
    """
    ts         = _parse_ts(raw)
    side_raw   = (raw.get("taker_side") or raw.get("side") or "").upper()
    outcome    = (raw.get("outcome") or "").lower()
    side       = outcome.upper() if (side_raw == "BUY" and outcome) else side_raw or "?"
    contracts  = float(raw.get("count") or raw.get("size") or 0)
    price      = float(raw.get("yes_price") or raw.get("price") or 0)
    notional   = round(contracts * price, 2)
    wallet     = raw.get("proxyWallet") or raw.get("maker") or raw.get("taker") or ""
    resolved_name = (
        market_name
        or raw.get("_market_name", "")
        or market_id
        or raw.get("market", "")
    )

    return {
        "time":         ts.strftime("%H:%M:%S"),
        "timestamp":    ts.isoformat(),
        "market_id":    market_id or raw.get("market", ""),
        "market_name":  resolved_name,
        "wallet":       wallet,
        "side":         side,
        "contracts":    contracts,
        "price":        round(price, 4),
        "notional_usd": notional,
    }


# ── Per-market state ──────────────────────────────────────────────────────────

class MarketState:
    """
    Tracks rolling trade history for one market.

    trade_window entries: (timestamp, side, volume, price, wallet, raw_trade_dict)
    """

    def __init__(self, market_id: str, close_time: Optional[datetime] = None, market_name: str = ""):
        self.market_id   = market_id
        self.market_name = market_name or market_id
        self.close_time  = close_time
        self.seen_trade_ids: set   = set()
        # 6-tuple: (ts, side, vol, price, wallet, raw_dict)
        self.trade_window: deque   = deque()
        self.price_history: deque  = deque()   # (ts, price)
        self.total_volume: float   = 0.0
        self.last_poll_time: Optional[datetime] = None

    def ingest_trades(self, trades: list[dict]) -> list[dict]:
        """Add new trades, deduplicate, return only truly new ones."""
        new_trades = []
        for t in trades:
            tid = t.get("id") or t.get("trade_id") or t.get("transactionHash") or str(t)
            if tid in self.seen_trade_ids:
                continue
            self.seen_trade_ids.add(tid)
            new_trades.append(t)

            ts      = _parse_ts(t)
            side    = (t.get("taker_side") or t.get("side") or "").upper()
            outcome = (t.get("outcome") or "").lower()
            if side == "BUY":
                side = outcome.upper() if outcome else "YES"
            vol    = float(t.get("count") or t.get("size") or 0)
            price  = float(t.get("yes_price") or t.get("price") or 0.5)
            wallet = t.get("proxyWallet") or t.get("maker") or ""

            self.trade_window.append((ts, side, vol, price, wallet, t))
            self.price_history.append((ts, price))
            self.total_volume += vol

        # Keep last 2 hours
        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
        while self.trade_window and self.trade_window[0][0] < cutoff:
            self.trade_window.popleft()
        while self.price_history and self.price_history[0][0] < cutoff:
            self.price_history.popleft()

        self.last_poll_time = datetime.now(timezone.utc)
        return new_trades

    def trades_in_window(self, minutes: float) -> list[tuple]:
        """Return trade_window entries from the last `minutes` minutes."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        return [e for e in self.trade_window if e[0] >= cutoff]


# ── Signal 1: Volume spike ────────────────────────────────────────────────────

def detect_volume_spike(
    state: MarketState,
    spike_window_minutes: int = 5,
    baseline_window_minutes: int = 60,
    min_multiplier: float = 4.0,
) -> Optional[dict]:
    now             = datetime.now(timezone.utc)
    spike_cutoff    = now - timedelta(minutes=spike_window_minutes)
    baseline_cutoff = now - timedelta(minutes=baseline_window_minutes)

    spike_entries    = [e for e in state.trade_window if e[0] >= spike_cutoff]
    baseline_entries = [e for e in state.trade_window if baseline_cutoff <= e[0] < spike_cutoff]

    spike_vol    = sum(e[2] for e in spike_entries)
    baseline_vol = sum(e[2] for e in baseline_entries)

    baseline_rate = baseline_vol / max(1, baseline_window_minutes - spike_window_minutes)
    spike_rate    = spike_vol / max(1, spike_window_minutes)

    if baseline_rate <= 0 or spike_rate < 10:
        return None

    multiplier = spike_rate / baseline_rate
    if multiplier < min_multiplier:
        return None

    return {
        "signal":             "volume_spike",
        "market_id":          state.market_id,
        "spike_volume":       round(spike_vol, 2),
        "baseline_rate_per_min": round(baseline_rate, 2),
        "multiplier":         round(multiplier, 2),
        "severity":           "CRITICAL" if multiplier >= min_multiplier * 2 else "HIGH",
        "description":        (
            f"Volume surged {multiplier:.1f}× vs baseline "
            f"({spike_vol:.0f} contracts in {spike_window_minutes}min)"
        ),
        "triggering_trades":  [
            normalise_trade(e[5], state.market_id, state.market_name) for e in spike_entries
        ],
    }


# ── Signal 2: Order flow skew ─────────────────────────────────────────────────

def detect_order_flow_skew(
    state: MarketState,
    window_minutes: int = 10,
    skew_threshold: float = 0.85,
    min_trades: int = 5,
) -> Optional[dict]:
    recent = state.trades_in_window(window_minutes)
    if len(recent) < min_trades:
        return None

    yes_entries = [e for e in recent if e[1] in ("YES", "BUY")]
    no_entries  = [e for e in recent if e[1] == "NO"]
    yes_vol     = sum(e[2] for e in yes_entries)
    no_vol      = sum(e[2] for e in no_entries)
    total       = yes_vol + no_vol
    if total <= 0:
        return None

    yes_frac  = yes_vol / total
    dominant  = (
        "YES" if yes_frac >= skew_threshold
        else "NO" if (1 - yes_frac) >= skew_threshold
        else None
    )
    if not dominant:
        return None

    frac            = yes_frac if dominant == "YES" else (1 - yes_frac)
    dominant_entries = yes_entries if dominant == "YES" else no_entries

    return {
        "signal":            "order_flow_skew",
        "market_id":         state.market_id,
        "dominant_side":     dominant,
        "side_fraction":     round(frac, 3),
        "total_volume":      round(total, 2),
        "window_minutes":    window_minutes,
        "severity":          "HIGH" if frac >= 0.92 else "MEDIUM",
        "description":       (
            f"{frac:.0%} of last-{window_minutes}min volume is buying {dominant} "
            f"({total:.0f} total contracts)"
        ),
        "triggering_trades": [
            normalise_trade(e[5], state.market_id, state.market_name) for e in dominant_entries
        ],
    }


# ── Signal 3: Price drift ─────────────────────────────────────────────────────

def detect_price_drift(
    state: MarketState,
    window_minutes: int = 15,
    min_drift: float = 0.12,
    min_data_points: int = 3,
) -> Optional[dict]:
    cutoff        = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    recent_prices = [(ts, p) for ts, p in state.price_history if ts >= cutoff]

    if len(recent_prices) < min_data_points:
        return None

    prices    = [p for _, p in recent_prices]
    drift     = max(prices) - min(prices)
    start, end = prices[0], prices[-1]
    direction = "UP" if end > start else "DOWN"

    if drift < min_drift:
        return None

    # Trades that occurred during the drift window
    drift_entries = state.trades_in_window(window_minutes)

    return {
        "signal":            "price_drift",
        "market_id":         state.market_id,
        "price_start":       round(start, 4),
        "price_end":         round(end, 4),
        "drift":             round(drift, 4),
        "direction":         direction,
        "window_minutes":    window_minutes,
        "severity":          "HIGH" if drift >= min_drift * 2 else "MEDIUM",
        "description":       (
            f"YES price drifted {direction} by {drift:.0%} "
            f"({start:.3f} → {end:.3f}) in {window_minutes}min"
        ),
        "triggering_trades": [
            normalise_trade(e[5], state.market_id, state.market_name) for e in drift_entries
        ],
    }


# ── Signal 4: Coordinated entry ───────────────────────────────────────────────

def detect_coordinated_entry(
    state: MarketState,
    window_minutes: int = 3,
    min_wallets: int = 3,
    same_side_threshold: float = 0.90,
) -> Optional[dict]:
    recent  = state.trades_in_window(window_minutes)
    wallets = {e[4] for e in recent if e[4]}

    if len(recent) < min_wallets or len(wallets) < min_wallets:
        return None

    yes_entries = [e for e in recent if e[1] in ("YES", "BUY") and e[4]]
    no_entries  = [e for e in recent if e[1] == "NO" and e[4]]
    total       = len(yes_entries) + len(no_entries)
    if total == 0:
        return None

    yes_frac = len(yes_entries) / total
    dominant = (
        "YES" if yes_frac >= same_side_threshold
        else "NO" if (1 - yes_frac) >= same_side_threshold
        else None
    )
    if not dominant or len(wallets) < min_wallets:
        return None

    dominant_entries = yes_entries if dominant == "YES" else no_entries
    dominant_wallets = sorted({e[4] for e in dominant_entries})

    return {
        "signal":            "coordinated_entry",
        "market_id":         state.market_id,
        "unique_wallets":    len(dominant_wallets),
        "dominant_side":     dominant,
        "window_minutes":    window_minutes,
        "wallet_list":       dominant_wallets,
        "severity":          "CRITICAL" if len(dominant_wallets) >= min_wallets * 2 else "HIGH",
        "description":       (
            f"{len(dominant_wallets)} distinct wallets all buying {dominant} "
            f"within {window_minutes}min"
        ),
        "triggering_trades": [
            normalise_trade(e[5], state.market_id, state.market_name) for e in dominant_entries
        ],
    }


# ── Signal 5: Known bad actor ─────────────────────────────────────────────────

def detect_known_bad_actor(
    state: MarketState,
    flagged_wallets: set[str],
    window_minutes: int = 60,
) -> list[dict]:
    if not flagged_wallets:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    alerts: list[dict] = []
    seen:   set[str]   = set()

    for ts, side, vol, price, wallet, _ in state.trade_window:
        if ts < cutoff or wallet not in flagged_wallets or wallet in seen:
            continue
        seen.add(wallet)

        # Collect ALL trades by this wallet in the window
        wallet_entries = [
            e for e in state.trade_window
            if e[0] >= cutoff and e[4] == wallet
        ]

        alerts.append({
            "signal":            "known_bad_actor",
            "market_id":         state.market_id,
            "wallet":            wallet,
            "side":              side,
            "volume":            round(vol, 2),
            "price":             round(price, 4),
            "trade_time":        ts.isoformat(),
            "severity":          "CRITICAL",
            "description":       (
                f"Previously flagged wallet {wallet[:10]}… "
                f"is buying {side} at {price:.3f} ({vol:.0f} contracts)"
            ),
            "triggering_trades": [
                normalise_trade(e[5], state.market_id, state.market_name) for e in wallet_entries
            ],
        })

    return alerts


# ── Signal 6: Time-to-close rush ──────────────────────────────────────────────

def detect_time_to_close_rush(
    state: MarketState,
    rush_window_minutes: int = 10,
    min_trades: int = 5,
    min_volume: float = 100.0,
) -> Optional[dict]:
    if not state.close_time:
        return None

    now              = datetime.now(timezone.utc)
    minutes_to_close = (state.close_time - now).total_seconds() / 60

    if not (0 < minutes_to_close <= rush_window_minutes):
        return None

    rush_entries = state.trades_in_window(rush_window_minutes)
    if len(rush_entries) < min_trades:
        return None

    rush_vol = sum(e[2] for e in rush_entries)
    if rush_vol < min_volume:
        return None

    return {
        "signal":            "time_to_close_rush",
        "market_id":         state.market_id,
        "minutes_to_close":  round(minutes_to_close, 1),
        "rush_trade_count":  len(rush_entries),
        "rush_volume":       round(rush_vol, 2),
        "severity":          "CRITICAL" if minutes_to_close <= 5 else "HIGH",
        "description":       (
            f"{len(rush_entries)} trades ({rush_vol:.0f} contracts) "
            f"with only {minutes_to_close:.1f}min until resolution"
        ),
        "triggering_trades": [
            normalise_trade(e[5], state.market_id, state.market_name) for e in rush_entries
        ],
    }


# ── Composite: run all signals on one market ──────────────────────────────────

def analyze_market(
    state: MarketState,
    flagged_wallets: Optional[set[str]] = None,
) -> list[dict]:
    signals = []

    s = detect_volume_spike(state)
    if s: signals.append(s)

    s = detect_order_flow_skew(state)
    if s: signals.append(s)

    s = detect_price_drift(state)
    if s: signals.append(s)

    s = detect_coordinated_entry(state)
    if s: signals.append(s)

    if flagged_wallets:
        signals.extend(detect_known_bad_actor(state, flagged_wallets))

    s = detect_time_to_close_rush(state)
    if s: signals.append(s)

    return signals
