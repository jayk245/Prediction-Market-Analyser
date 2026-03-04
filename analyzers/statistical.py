"""
Statistical tests used to flag anomalous trading profiles.

Each function takes a trader/wallet profile dict and returns
a (is_flagged, reason, score) tuple where score is 0-100.
"""

import math
from scipy import stats

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DETECTION


# ── Helpers ───────────────────────────────────────────────────────────────────

def _binomial_pvalue(wins: int, trials: int, p_null: float = 0.5) -> float:
    """One-sided p-value: P(X >= wins) under Binomial(trials, p_null)."""
    if trials == 0:
        return 1.0
    return float(stats.binomtest(wins, trials, p_null, alternative="greater").pvalue)


def _normal_zscore(wins: int, trials: int, p_null: float = 0.5) -> float:
    """Normal approximation z-score for binomial proportion."""
    if trials == 0:
        return 0.0
    p_hat = wins / trials
    se = math.sqrt(p_null * (1 - p_null) / trials)
    return (p_hat - p_null) / se if se > 0 else 0.0


def _score_from_pvalue(p: float) -> float:
    """Convert a p-value to a 0-100 suspicion score (lower p → higher score)."""
    if p <= 0:
        return 100.0
    # score = 100 * (1 - p)^3  gives a steep curve near p=0
    return round(min(100.0, 100.0 * (1 - p) ** 3), 1)


# ── Test 1: Binomial win-rate test ────────────────────────────────────────────

def test_winrate(profile: dict) -> tuple[bool, str, float]:
    """
    Flag if the win rate is statistically improbable under the null H0: p=0.5.
    Uses a one-sided binomial test.
    """
    wins = profile.get("winning_trades", 0)
    losses = profile.get("losing_trades", 0)
    trials = wins + losses

    if trials < DETECTION.min_trades_for_analysis:
        return False, "insufficient_data", 0.0

    win_rate = wins / trials
    if win_rate < DETECTION.min_suspicious_winrate:
        return False, "win_rate_below_threshold", 0.0

    p_value = _binomial_pvalue(wins, trials)
    z_score = _normal_zscore(wins, trials)
    score = _score_from_pvalue(p_value)

    if p_value < DETECTION.alpha and z_score > DETECTION.winrate_zscore_threshold:
        reason = (
            f"win_rate={win_rate:.1%} over {trials} trades "
            f"(z={z_score:.2f}, p={p_value:.2e})"
        )
        return True, reason, score

    return False, f"win_rate={win_rate:.1%} not significant (p={p_value:.3f})", score


# ── Test 2: Last-minute trade timing ─────────────────────────────────────────

def test_last_minute_timing(profile: dict) -> tuple[bool, str, float]:
    """
    Flag if a disproportionate fraction of wins come from trades placed
    within `last_minute_window_seconds` of market resolution.
    """
    lm_trades = profile.get("last_minute_trades", 0)
    lm_win_rate = profile.get("last_minute_win_rate")

    if lm_trades < 3 or lm_win_rate is None:
        return False, "insufficient_last_minute_data", 0.0

    # Binomial test: is the LM win rate significantly higher than overall WR?
    overall_wr = profile.get("win_rate", 0.5)
    lm_wins = round(lm_trades * lm_win_rate)

    if lm_win_rate < DETECTION.last_minute_win_ratio_threshold:
        return False, f"last_minute_win_rate={lm_win_rate:.1%} below threshold", 0.0

    p_value = _binomial_pvalue(lm_wins, lm_trades, p_null=overall_wr)
    score = _score_from_pvalue(p_value)

    if p_value < DETECTION.alpha:
        reason = (
            f"last_minute_win_rate={lm_win_rate:.1%} over {lm_trades} "
            f"last-minute trades (p={p_value:.2e})"
        )
        return True, reason, score

    return (
        False,
        f"last_minute_win_rate={lm_win_rate:.1%} not significant (p={p_value:.3f})",
        score,
    )


# ── Test 3: Profit factor ─────────────────────────────────────────────────────

def test_profit_factor(profile: dict) -> tuple[bool, str, float]:
    """
    Flag if the trader's total profit is anomalously high relative to volume.
    Profit factor = |profit| / total_volume.
    """
    profit = profile.get("profit_usd", 0.0)
    volume = profile.get("total_volume_usd", 0.0)

    if volume < 500 or profit <= 0:
        return False, "insufficient_volume_or_no_profit", 0.0

    factor = profit / volume
    score = min(100.0, factor * 20)   # 100 at factor=5x

    if factor >= DETECTION.suspicious_profit_factor:
        reason = f"profit_factor={factor:.2f}x (profit=${profit:,.0f} on ${volume:,.0f} volume)"
        return True, reason, round(score, 1)

    return False, f"profit_factor={factor:.2f}x within normal range", round(score, 1)


# ── Test 4: Market concentration ─────────────────────────────────────────────

def test_market_concentration(profile: dict) -> tuple[bool, str, float]:
    """
    Flag if a trader places nearly all bets on a single market —
    unusual for a typical diversified bettor.
    """
    markets_traded = profile.get("markets_traded_count", 0)
    total_trades = profile.get("total_trades", 0)

    if markets_traded == 0 or total_trades < DETECTION.min_trades_for_analysis:
        return False, "insufficient_data", 0.0

    # Herfindahl-like: if markets_traded==1, concentration=1.0
    concentration = 1.0 / markets_traded if markets_traded > 0 else 0.0
    score = min(100.0, concentration * 100)

    if concentration >= DETECTION.market_concentration_threshold:
        reason = (
            f"trades concentrated in {markets_traded} market(s) "
            f"(concentration={concentration:.0%})"
        )
        return True, reason, round(score, 1)

    return False, f"trades spread across {markets_traded} markets", round(score, 1)


# ── Test 5: Consecutive wins (runs test) ─────────────────────────────────────

def test_consecutive_wins(profile: dict) -> tuple[bool, str, float]:
    """
    Compute the longest run of consecutive winning trades.
    Uses the Wald-Wolfowitz runs test to assess randomness.
    """
    trades = profile.get("raw_trades", [])
    if len(trades) < DETECTION.min_trades_for_analysis:
        return False, "insufficient_data", 0.0

    # Build win/loss sequence sorted by time if available
    results = []
    for t in trades:
        outcome = t.get("_market_result") or t.get("outcome") or ""
        side = (t.get("taker_side") or t.get("side") or "").upper()
        # Determine if this was a winning trade
        if side in ("BUY", "YES") and outcome.lower() == "yes":
            results.append(1)
        elif side in ("BUY", "NO") and outcome.lower() == "no":
            results.append(1)
        elif outcome:
            results.append(0)

    if len(results) < DETECTION.min_trades_for_analysis:
        return False, "insufficient_outcome_data", 0.0

    # Find max consecutive wins
    max_streak = current_streak = 0
    for r in results:
        if r == 1:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    # Expected max streak under fair coin: log2(n) ≈ E[max run]
    n = len(results)
    expected_max_streak = math.log2(n) if n > 1 else 1.0
    score = min(100.0, (max_streak / expected_max_streak) * 20)

    if max_streak > DETECTION.max_acceptable_consecutive_wins:
        reason = (
            f"longest winning streak={max_streak} trades "
            f"(expected ≈{expected_max_streak:.1f} for n={n})"
        )
        return True, reason, round(score, 1)

    return False, f"max_streak={max_streak} within normal range", round(score, 1)


# ── Composite scorer ──────────────────────────────────────────────────────────

TESTS = [
    ("winrate",              test_winrate),
    ("last_minute_timing",   test_last_minute_timing),
    ("profit_factor",        test_profit_factor),
    ("market_concentration", test_market_concentration),
    ("consecutive_wins",     test_consecutive_wins),
]

# Weights for composite score (must sum to 1.0)
WEIGHTS = {
    "winrate":              0.35,
    "last_minute_timing":   0.30,
    "profit_factor":        0.15,
    "market_concentration": 0.10,
    "consecutive_wins":     0.10,
}


def score_profile(profile: dict) -> dict:
    """
    Run all statistical tests on a trader profile.
    Returns an enriched dict with:
      - test_results: {test_name: {flagged, reason, score}}
      - flags: list of test names that triggered
      - composite_score: 0-100
      - risk_level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
    """
    test_results = {}
    flags = []
    weighted_score = 0.0

    for name, fn in TESTS:
        flagged, reason, score = fn(profile)
        test_results[name] = {"flagged": flagged, "reason": reason, "score": score}
        if flagged:
            flags.append(name)
        weighted_score += WEIGHTS[name] * score

    composite = round(weighted_score, 1)

    if composite >= 75:
        risk_level = "CRITICAL"
    elif composite >= 50:
        risk_level = "HIGH"
    elif composite >= 25:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    return {
        **profile,
        "test_results": test_results,
        "flags": flags,
        "composite_score": composite,
        "risk_level": risk_level,
    }


def score_profiles(profiles: list[dict]) -> list[dict]:
    """Score a list of profiles and return sorted by composite_score descending."""
    scored = [score_profile(p) for p in profiles]
    return sorted(scored, key=lambda x: x["composite_score"], reverse=True)
