export type RiskLevel = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'
export type Source    = 'polymarket' | 'kalshi'
export type SignalType =
  | 'volume_spike' | 'order_flow_skew' | 'price_drift'
  | 'coordinated_entry' | 'known_bad_actor' | 'time_to_close_rush'

// ── Scan report ───────────────────────────────────────────────────────────────

export interface ReportMeta {
  run_time: string
  run_duration_seconds: number
  days_back: number
  source: string
  total_markets: number
  total_trades: number
  total_profiles: number
}

export interface ReportListItem {
  filename: string
  run_time: string
  days_back: number
  source: string
  total_markets: number
  total_trades: number
  total_profiles: number
  flagged_count: number
  critical: number
  high: number
  medium: number
}

export interface TestResult {
  flagged: boolean
  reason: string
  score: number
}

export interface TradeSummary {
  timestamp:    string   // "2026-03-01T14:22:05"
  market_name:  string   // "Minnesota Wild"
  market_id:    string   // conditionId hex or Kalshi ticker
  side:         string   // "YES" | "NO" | "BUY" | "SELL"
  contracts:    number
  price:        number
  notional_usd: number
  wallet:       string
}

export interface Profile {
  source:               Source
  wallet?:              string   // polymarket
  ticker?:              string   // kalshi
  market_name?:         string   // kalshi single-market profile
  total_trades:         number
  winning_trades:       number
  losing_trades:        number
  win_rate:             number
  total_volume_usd?:    number
  total_volume?:        number
  profit_usd?:          number
  markets_traded_count: number
  last_minute_trades?:  number
  last_minute_win_rate?: number | null
  market_result?:       string
  flags:                string[]
  composite_score:      number
  risk_level:           RiskLevel
  test_results: {
    winrate:             TestResult
    last_minute_timing:  TestResult
    profit_factor:       TestResult
    market_concentration: TestResult
    consecutive_wins:    TestResult
  }
  trades?: TradeSummary[]
}

export interface CrossMarketEdge {
  type:           'cross_market_edge'
  wallet:         string
  win_rate:       number
  markets_traded: number
  total_trades:   number
  edge_score:     number
  severity:       'HIGH' | 'MEDIUM'
}

export interface PositionSpike {
  type:                      'position_spike'
  market_id:                 string
  spike_volume:              number
  baseline_volume_per_window: number
  volume_multiplier:         number
  spike_trade_count:         number
  severity:                  'HIGH' | 'MEDIUM'
}

export interface CoordinatedWallets {
  type:                'coordinated_wallets'
  wallet_1:            string
  wallet_2:            string
  shared_market_bets:  number
  jaccard_similarity:  number
  severity:            'HIGH' | 'MEDIUM'
}

export interface PatternAlerts {
  position_spikes:        PositionSpike[]
  coordinated_wallets:    CoordinatedWallets[]
  cross_market_edge:      CrossMarketEdge[]
  event_timing_clusters:  unknown[]
}

export interface SurveillanceReport {
  metadata:        ReportMeta
  flagged_profiles: Profile[]
  all_profiles:    Profile[]
  pattern_alerts:  PatternAlerts
}

// ── Live alerts ───────────────────────────────────────────────────────────────

export interface NormalisedTrade {
  time:         string
  timestamp:    string
  market_id:    string
  market_name:  string
  wallet:       string
  side:         string
  contracts:    number
  price:        number
  notional_usd: number
}

export interface LiveAlert {
  signal:             SignalType
  market_id:          string
  severity:           'CRITICAL' | 'HIGH' | 'MEDIUM'
  description:        string
  triggering_trades:  NormalisedTrade[]
  _source:            Source
  _fired_at:          string
  // signal-specific
  dominant_side?:     string
  side_fraction?:     number
  total_volume?:      number
  window_minutes?:    number
  spike_volume?:      number
  multiplier?:        number
  price_start?:       number
  price_end?:         number
  drift?:             number
  direction?:         string
  wallet?:            string
  unique_wallets?:    number
  minutes_to_close?:  number
}

export interface LiveFeedData {
  alerts:          LiveAlert[]
  last_updated:    string | null   // ISO timestamp when watch command last wrote the file
  poll_count:      number
  markets_tracked: number
  source?:         string
}

// ── Scan state ────────────────────────────────────────────────────────────────

export interface ScanStatus {
  running:        boolean
  started_at:     string | null
  last_completed: string | null
  error:          string | null
  source:         string | null
  days_back:      number | null
  progress:       string | null
  report_count:   number
}

// ── Stats ─────────────────────────────────────────────────────────────────────

export interface DashboardStats {
  report_count:    number
  latest_run_time: string
  days_back:       number
  total_profiles:  number
  total_flagged:   number
  by_risk_level:   Record<RiskLevel, number>
  live_alert_count: number
  total_trades:    number
  total_markets:   number
}
