import { Profile } from '../../types'
import { RiskBadge } from '../shared/RiskBadge'
import { WalletAddress } from '../shared/WalletAddress'
import { TradeTable } from './TradeTable'

interface Props {
  profile:  Profile | null
  onClose:  () => void
}

const FLAG_COLORS: Record<string, string> = {
  winrate:               'bg-red-900 text-red-200',
  last_minute_timing:    'bg-orange-900 text-orange-200',
  profit_factor:         'bg-yellow-900 text-yellow-200',
  market_concentration:  'bg-blue-900 text-blue-200',
  consecutive_wins:      'bg-purple-900 text-purple-200',
}

function ScoreBar({ score }: { score: number }) {
  const pct = Math.min(100, Math.max(0, score))
  const color =
    pct >= 70 ? 'bg-red-500' :
    pct >= 40 ? 'bg-yellow-500' :
                'bg-green-500'
  return (
    <div className="flex-1 bg-gray-700 rounded-full h-2 overflow-hidden">
      <div className={`h-2 rounded-full ${color}`} style={{ width: `${pct}%` }} />
    </div>
  )
}

function StatPill({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex flex-col items-center bg-gray-800 rounded px-3 py-2 min-w-[80px]">
      <span className="text-lg font-bold text-white tabular-nums">{value}</span>
      <span className="text-[10px] text-gray-400 uppercase tracking-wide mt-0.5">{label}</span>
    </div>
  )
}

export function ProfileDrawer({ profile, onClose }: Props) {
  if (!profile) return null

  const ident = profile.wallet ?? profile.ticker ?? '?'
  const winRateStr = typeof profile.win_rate === 'number'
    ? (profile.win_rate * 100).toFixed(1) + '%'
    : 'N/A'
  const profitStr = typeof profile.profit_usd === 'number'
    ? '$' + profile.profit_usd.toLocaleString(undefined, { maximumFractionDigits: 0 })
    : 'N/A'
  const volumeStr = typeof profile.total_volume_usd === 'number'
    ? '$' + profile.total_volume_usd.toLocaleString(undefined, { maximumFractionDigits: 0 })
    : 'N/A'

  const testEntries = Object.entries(profile.test_results ?? {}) as [string, {flagged: boolean; reason: string; score: number}][]

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/60 z-40"
        onClick={onClose}
      />

      {/* Drawer */}
      <aside className="fixed right-0 top-0 h-full w-full max-w-2xl bg-gray-900 z-50 flex flex-col shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700 bg-gray-900 shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <RiskBadge level={profile.risk_level} />
            <WalletAddress address={ident} />
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white text-xl ml-4 shrink-0"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-6">

          {/* Stats bar */}
          <div className="flex flex-wrap gap-2">
            <StatPill label="Trades"   value={profile.total_trades} />
            <StatPill label="Win Rate" value={winRateStr} />
            <StatPill label="Profit"   value={profitStr} />
            <StatPill label="Volume"   value={volumeStr} />
            <StatPill label="Markets"  value={profile.markets_traded_count} />
          </div>

          {/* Flags */}
          {profile.flags?.length > 0 && (
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-widest text-gray-400 mb-2">Flags</h3>
              <div className="flex flex-wrap gap-2">
                {profile.flags.map(f => (
                  <span
                    key={f}
                    className={`px-2 py-0.5 rounded text-xs font-medium ${FLAG_COLORS[f] ?? 'bg-gray-700 text-gray-200'}`}
                  >
                    {f.replace(/_/g, ' ')}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Test results */}
          {testEntries.length > 0 && (
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-widest text-gray-400 mb-3">Test Results</h3>
              <div className="space-y-3">
                {testEntries.map(([name, tr]) => (
                  <div key={name}>
                    <div className="flex items-center justify-between mb-1">
                      <span className={`text-xs font-medium ${tr.flagged ? 'text-red-300' : 'text-gray-400'}`}>
                        {tr.flagged ? '⚑ ' : ''}{name.replace(/_/g, ' ')}
                      </span>
                    </div>
                    <ScoreBar score={tr.score} />
                    <p className="text-[11px] text-gray-500 mt-1">{tr.reason}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Trades */}
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-widest text-gray-400 mb-3">
              Trades
              {profile.trades?.length
                ? <span className="ml-2 normal-case text-gray-500">(last {profile.trades.length})</span>
                : null}
            </h3>
            <TradeTable
              trades={profile.trades ?? []}
              marketResult={profile.market_result}
            />
          </div>
        </div>
      </aside>
    </>
  )
}
