import { TradeSummary } from '../../types'
import { SideBadge } from '../shared/SideBadge'

interface Props {
  trades:        TradeSummary[]
  marketResult?: string   // e.g. "YES" or "NO" from profile
}

function wl(side: string, result?: string): 'W' | 'L' | null {
  if (!result || !side) return null
  return side.toUpperCase() === result.toUpperCase() ? 'W' : 'L'
}

function fmtTime(ts: string): { date: string; time: string } {
  if (!ts) return { date: '—', time: '' }
  const d = ts.replace('T', ' ')
  const [date, time] = d.split(' ')
  return { date: date ?? '—', time: time?.slice(0, 8) ?? '' }
}

export function TradeTable({ trades, marketResult }: Props) {
  if (!trades?.length) {
    return <p className="text-xs text-gray-500 py-3">No trade data available.</p>
  }

  return (
    <div className="overflow-x-auto rounded border border-gray-700">
      <table className="min-w-full text-xs text-left text-gray-300">
        <thead className="bg-gray-800 text-gray-400 uppercase tracking-wide">
          <tr>
            <th className="px-3 py-2 w-[130px]">Time</th>
            <th className="px-3 py-2">Market / Question</th>
            <th className="px-3 py-2 w-16 text-center">Side</th>
            <th className="px-3 py-2 w-20 text-right">Contracts</th>
            <th className="px-3 py-2 w-16 text-right">Price</th>
            <th className="px-3 py-2 w-24 text-right">Notional</th>
            <th className="px-3 py-2 w-10 text-center">W/L</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-700">
          {trades.map((t, i) => {
            const { date, time } = fmtTime(t.timestamp)
            const outcome = wl(t.side, marketResult)
            return (
              <tr key={i} className="hover:bg-gray-800/60 transition-colors">
                <td className="px-3 py-2 tabular-nums text-gray-400">
                  <span className="block">{date}</span>
                  <span className="block text-gray-500">{time}</span>
                </td>
                <td className="px-3 py-2 max-w-[260px]">
                  <span className="block truncate font-medium text-gray-100" title={t.market_name}>
                    {t.market_name || <span className="text-gray-500 italic">unknown</span>}
                  </span>
                  {t.market_id && (
                    <span className="block truncate text-gray-500 text-[10px]" title={t.market_id}>
                      {t.market_id.length > 24 ? t.market_id.slice(0, 24) + '…' : t.market_id}
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-center">
                  <SideBadge side={t.side} />
                </td>
                <td className="px-3 py-2 text-right tabular-nums">
                  {t.contracts.toLocaleString(undefined, { maximumFractionDigits: 1 })}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">
                  {t.price.toFixed(3)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">
                  ${t.notional_usd.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                </td>
                <td className="px-3 py-2 text-center font-bold">
                  {outcome === 'W' && <span className="text-green-400">W</span>}
                  {outcome === 'L' && <span className="text-red-400">L</span>}
                  {outcome === null && <span className="text-gray-600">—</span>}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
