import { PatternAlerts as PA, CrossMarketEdge, PositionSpike, CoordinatedWallets } from '../../types'
import { WalletAddress } from '../shared/WalletAddress'

interface Props {
  alerts: PA
}

function Section({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  if (count === 0) return null
  return (
    <div className="mb-6">
      <h3 className="text-sm font-semibold text-yellow-400 mb-3">
        {title} <span className="text-gray-500 font-normal">({count})</span>
      </h3>
      {children}
    </div>
  )
}

const SEV_CLS: Record<string, string> = {
  HIGH:   'bg-red-900 text-red-200',
  MEDIUM: 'bg-yellow-900 text-yellow-200',
}

function SevBadge({ sev }: { sev: string }) {
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-bold ${SEV_CLS[sev] ?? 'bg-gray-700 text-gray-300'}`}>
      {sev}
    </span>
  )
}

function CrossMarketTable({ items }: { items: CrossMarketEdge[] }) {
  return (
    <table className="min-w-full text-xs text-gray-300">
      <thead className="text-gray-400 uppercase text-[10px] tracking-wide">
        <tr>
          <th className="pb-1 text-left">Wallet</th>
          <th className="pb-1 text-right">Win Rate</th>
          <th className="pb-1 text-right">Markets</th>
          <th className="pb-1 text-right">Edge Score</th>
          <th className="pb-1 text-right">Severity</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-gray-700">
        {items.slice(0, 10).map((a, i) => (
          <tr key={i} className="hover:bg-gray-800/50">
            <td className="py-1.5 pr-3"><WalletAddress address={a.wallet} /></td>
            <td className="py-1.5 text-right tabular-nums">{(a.win_rate * 100).toFixed(1)}%</td>
            <td className="py-1.5 text-right tabular-nums">{a.markets_traded}</td>
            <td className="py-1.5 text-right tabular-nums">{a.edge_score.toFixed(1)}</td>
            <td className="py-1.5 text-right"><SevBadge sev={a.severity} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function PositionSpikeTable({ items }: { items: PositionSpike[] }) {
  return (
    <table className="min-w-full text-xs text-gray-300">
      <thead className="text-gray-400 uppercase text-[10px] tracking-wide">
        <tr>
          <th className="pb-1 text-left">Market ID</th>
          <th className="pb-1 text-right">Multiplier</th>
          <th className="pb-1 text-right">Spike Vol</th>
          <th className="pb-1 text-right">Severity</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-gray-700">
        {items.slice(0, 10).map((a, i) => (
          <tr key={i} className="hover:bg-gray-800/50">
            <td className="py-1.5 pr-3 font-mono text-[10px] truncate max-w-[180px]" title={a.market_id}>
              {a.market_id.length > 20 ? a.market_id.slice(0, 20) + '…' : a.market_id}
            </td>
            <td className="py-1.5 text-right tabular-nums">{a.volume_multiplier.toFixed(1)}×</td>
            <td className="py-1.5 text-right tabular-nums">{a.spike_volume.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
            <td className="py-1.5 text-right"><SevBadge sev={a.severity} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function CoordTable({ items }: { items: CoordinatedWallets[] }) {
  return (
    <table className="min-w-full text-xs text-gray-300">
      <thead className="text-gray-400 uppercase text-[10px] tracking-wide">
        <tr>
          <th className="pb-1 text-left">Wallet 1</th>
          <th className="pb-1 text-left">Wallet 2</th>
          <th className="pb-1 text-right">Shared Bets</th>
          <th className="pb-1 text-right">Jaccard</th>
          <th className="pb-1 text-right">Severity</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-gray-700">
        {items.slice(0, 10).map((a, i) => (
          <tr key={i} className="hover:bg-gray-800/50">
            <td className="py-1.5 pr-2"><WalletAddress address={a.wallet_1} /></td>
            <td className="py-1.5 pr-2"><WalletAddress address={a.wallet_2} /></td>
            <td className="py-1.5 text-right tabular-nums">{a.shared_market_bets}</td>
            <td className="py-1.5 text-right tabular-nums">{(a.jaccard_similarity * 100).toFixed(1)}%</td>
            <td className="py-1.5 text-right"><SevBadge sev={a.severity} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function PatternAlerts({ alerts }: Props) {
  const total =
    (alerts.cross_market_edge?.length ?? 0) +
    (alerts.position_spikes?.length ?? 0) +
    (alerts.coordinated_wallets?.length ?? 0) +
    (alerts.event_timing_clusters?.length ?? 0)

  if (total === 0) {
    return <p className="text-gray-500 text-sm py-6 text-center">No pattern alerts in this report.</p>
  }

  return (
    <div className="space-y-1">
      <Section title="Cross-Market Edge" count={alerts.cross_market_edge?.length ?? 0}>
        <div className="bg-gray-800/50 rounded p-3">
          <CrossMarketTable items={alerts.cross_market_edge ?? []} />
        </div>
      </Section>

      <Section title="Coordinated Wallets" count={alerts.coordinated_wallets?.length ?? 0}>
        <div className="bg-gray-800/50 rounded p-3">
          <CoordTable items={alerts.coordinated_wallets ?? []} />
        </div>
      </Section>

      <Section title="Position Spikes" count={alerts.position_spikes?.length ?? 0}>
        <div className="bg-gray-800/50 rounded p-3">
          <PositionSpikeTable items={alerts.position_spikes ?? []} />
        </div>
      </Section>

      {(alerts.event_timing_clusters?.length ?? 0) > 0 && (
        <Section title="Event Timing Clusters" count={alerts.event_timing_clusters?.length ?? 0}>
          <p className="text-xs text-gray-400 bg-gray-800/50 rounded p-3">
            {alerts.event_timing_clusters.length} cluster(s) detected.
          </p>
        </Section>
      )}
    </div>
  )
}
