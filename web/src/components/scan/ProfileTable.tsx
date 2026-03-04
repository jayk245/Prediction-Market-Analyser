import { useState } from 'react'
import { Profile, RiskLevel } from '../../types'
import { RiskBadge } from '../shared/RiskBadge'
import { WalletAddress } from '../shared/WalletAddress'

type SortKey = 'composite_score' | 'win_rate' | 'profit_usd' | 'total_trades'
type SortDir = 'asc' | 'desc'

interface Props {
  profiles:   Profile[]
  onSelect:   (p: Profile) => void
}

const RISK_ORDER: Record<RiskLevel, number> = {
  CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3,
}

export function ProfileTable({ profiles, onSelect }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('composite_score')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  const sorted = [...profiles].sort((a, b) => {
    const riskDiff = RISK_ORDER[a.risk_level] - RISK_ORDER[b.risk_level]
    if (sortKey === 'composite_score') {
      const diff = (b.composite_score - a.composite_score) * (sortDir === 'desc' ? 1 : -1)
      return diff !== 0 ? diff : riskDiff
    }
    const av = (a[sortKey] as number) ?? 0
    const bv = (b[sortKey] as number) ?? 0
    return sortDir === 'desc' ? bv - av : av - bv
  })

  function SortHeader({ label, k }: { label: string; k: SortKey }) {
    const active = sortKey === k
    return (
      <th
        className={`px-3 py-2 text-right cursor-pointer select-none whitespace-nowrap hover:text-white transition-colors ${active ? 'text-white' : 'text-gray-400'}`}
        onClick={() => handleSort(k)}
      >
        {label} {active ? (sortDir === 'desc' ? '↓' : '↑') : ''}
      </th>
    )
  }

  if (!profiles.length) {
    return <p className="text-gray-500 text-sm py-6 text-center">No flagged profiles in this report.</p>
  }

  return (
    <div className="overflow-x-auto rounded border border-gray-700">
      <table className="min-w-full text-sm text-left text-gray-300">
        <thead className="bg-gray-800 text-gray-400 uppercase text-xs tracking-wide">
          <tr>
            <th className="px-3 py-2">#</th>
            <th className="px-3 py-2">Risk</th>
            <th className="px-3 py-2">Wallet / Ticker</th>
            <th className="px-3 py-2">Source</th>
            <SortHeader label="Trades"    k="total_trades" />
            <SortHeader label="Win Rate"  k="win_rate" />
            <SortHeader label="Profit $"  k="profit_usd" />
            <th className="px-3 py-2">Flags</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-700">
          {sorted.map((p, i) => {
            const ident = p.wallet ?? p.ticker ?? '?'
            const winRate = typeof p.win_rate === 'number'
              ? (p.win_rate * 100).toFixed(1) + '%'
              : '—'
            const profit = typeof p.profit_usd === 'number'
              ? '$' + p.profit_usd.toLocaleString(undefined, { maximumFractionDigits: 0 })
              : '—'
            return (
              <tr
                key={i}
                className="hover:bg-gray-800 cursor-pointer transition-colors"
                onClick={() => onSelect(p)}
              >
                <td className="px-3 py-2 text-gray-500">{i + 1}</td>
                <td className="px-3 py-2">
                  <RiskBadge level={p.risk_level} />
                </td>
                <td className="px-3 py-2 font-mono">
                  <WalletAddress address={ident} />
                </td>
                <td className="px-3 py-2 text-gray-400 capitalize">{p.source}</td>
                <td className="px-3 py-2 text-right tabular-nums">{p.total_trades}</td>
                <td className="px-3 py-2 text-right tabular-nums">{winRate}</td>
                <td className="px-3 py-2 text-right tabular-nums">{profit}</td>
                <td className="px-3 py-2">
                  <div className="flex flex-wrap gap-1">
                    {(p.flags ?? []).slice(0, 3).map(f => (
                      <span key={f} className="px-1.5 py-0.5 bg-gray-700 text-gray-300 rounded text-[10px]">
                        {f.replace(/_/g, ' ')}
                      </span>
                    ))}
                    {(p.flags ?? []).length > 3 && (
                      <span className="text-[10px] text-gray-500">+{p.flags.length - 3}</span>
                    )}
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
