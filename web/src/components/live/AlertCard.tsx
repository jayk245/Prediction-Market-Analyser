import { useState } from 'react'
import { LiveAlert } from '../../types'
import { SideBadge } from '../shared/SideBadge'

const SIGNAL_LABELS: Record<string, string> = {
  volume_spike:       'Volume Spike',
  order_flow_skew:    'Order Flow Skew',
  price_drift:        'Price Drift',
  coordinated_entry:  'Coordinated Entry',
  known_bad_actor:    'Known Bad Actor',
  time_to_close_rush: 'Time-to-Close Rush',
}

const SEV_CLS: Record<string, string> = {
  CRITICAL: 'bg-red-900/60 border-red-700',
  HIGH:     'bg-orange-900/40 border-orange-700',
  MEDIUM:   'bg-yellow-900/30 border-yellow-700',
}

const SEV_BADGE: Record<string, string> = {
  CRITICAL: 'bg-red-600 text-white',
  HIGH:     'bg-orange-500 text-white',
  MEDIUM:   'bg-yellow-500 text-black',
}

function fmtFired(ts: string) {
  if (!ts) return ''
  return ts.slice(0, 19).replace('T', ' ')
}

interface Props {
  alert: LiveAlert
}

export function AlertCard({ alert: a }: Props) {
  const [open, setOpen] = useState(false)

  const marketName =
    a.triggering_trades?.[0]?.market_name ||
    a.market_id?.slice(0, 32) ||
    '?'

  const cardCls = SEV_CLS[a.severity] ?? 'bg-gray-800/60 border-gray-600'

  return (
    <div className={`rounded-lg border p-4 ${cardCls}`}>
      {/* Top row */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`px-2 py-0.5 rounded text-xs font-bold ${SEV_BADGE[a.severity] ?? 'bg-gray-600 text-white'}`}>
              {a.severity}
            </span>
            <span className="text-sm font-semibold text-gray-100">
              {SIGNAL_LABELS[a.signal] ?? a.signal}
            </span>
            <span className="text-xs text-gray-400 uppercase">{a._source}</span>
          </div>

          <p className="text-sm text-white font-medium truncate" title={marketName}>
            {marketName}
          </p>
          <p className="text-xs text-gray-400">{a.description}</p>
        </div>

        <div className="flex flex-col items-end gap-1 shrink-0 text-right">
          <span className="text-[10px] text-gray-500">{fmtFired(a._fired_at)}</span>
          {a.triggering_trades?.length > 0 && (
            <button
              onClick={() => setOpen(o => !o)}
              className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
            >
              {open ? 'Hide trades ▲' : `${a.triggering_trades.length} trades ▼`}
            </button>
          )}
        </div>
      </div>

      {/* Signal-specific details */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-xs text-gray-400">
        {a.dominant_side   && <span>Side: <span className="text-gray-200">{a.dominant_side}</span></span>}
        {a.side_fraction   && <span>Fraction: <span className="text-gray-200">{(a.side_fraction * 100).toFixed(0)}%</span></span>}
        {a.total_volume    && <span>Volume: <span className="text-gray-200">{a.total_volume.toLocaleString()}</span></span>}
        {a.window_minutes  && <span>Window: <span className="text-gray-200">{a.window_minutes}m</span></span>}
        {a.spike_volume    && <span>Spike: <span className="text-gray-200">{a.spike_volume.toLocaleString()}</span></span>}
        {a.multiplier      && <span>Multiplier: <span className="text-gray-200">{a.multiplier.toFixed(1)}×</span></span>}
        {a.price_start     && a.price_end && (
          <span>Price: <span className="text-gray-200">{a.price_start.toFixed(3)} → {a.price_end.toFixed(3)}</span></span>
        )}
        {a.drift           && <span>Drift: <span className="text-gray-200">{(a.drift * 100).toFixed(1)}%</span></span>}
        {a.direction       && <span>Direction: <span className="text-gray-200">{a.direction}</span></span>}
        {a.unique_wallets  && <span>Wallets: <span className="text-gray-200">{a.unique_wallets}</span></span>}
        {a.minutes_to_close !== undefined && (
          <span>Closes in: <span className="text-gray-200">{a.minutes_to_close.toFixed(0)}m</span></span>
        )}
      </div>

      {/* Expanded trade list */}
      {open && a.triggering_trades?.length > 0 && (
        <div className="mt-3 overflow-x-auto rounded border border-gray-700">
          <table className="min-w-full text-xs text-gray-300">
            <thead className="bg-gray-800 text-gray-400 uppercase text-[10px] tracking-wide">
              <tr>
                <th className="px-2 py-1.5">Time</th>
                <th className="px-2 py-1.5">Market / Question</th>
                <th className="px-2 py-1.5 text-center">Side</th>
                <th className="px-2 py-1.5 text-right">Contracts</th>
                <th className="px-2 py-1.5 text-right">Price</th>
                <th className="px-2 py-1.5 text-right">Notional</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-700">
              {a.triggering_trades.map((t, i) => (
                <tr key={i} className="hover:bg-gray-800/60">
                  <td className="px-2 py-1 tabular-nums text-gray-400 whitespace-nowrap">{(t.time || t.timestamp || '').slice(0, 8)}</td>
                  <td className="px-2 py-1 max-w-[280px]">
                    <span className="block truncate font-medium text-gray-100" title={t.market_name}>
                      {t.market_name || t.market_id?.slice(0, 32) || '?'}
                    </span>
                  </td>
                  <td className="px-2 py-1 text-center"><SideBadge side={t.side} /></td>
                  <td className="px-2 py-1 text-right tabular-nums">
                    {t.contracts?.toLocaleString(undefined, { maximumFractionDigits: 1 })}
                  </td>
                  <td className="px-2 py-1 text-right tabular-nums">
                    {t.price?.toFixed(3)}
                  </td>
                  <td className="px-2 py-1 text-right tabular-nums">
                    ${t.notional_usd?.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
