import { useEffect, useRef, useState } from 'react'
import { api } from '../../api'
import { Profile, ReportListItem, ScanStatus, SurveillanceReport } from '../../types'
import { RiskBadge } from '../shared/RiskBadge'
import { PatternAlerts } from './PatternAlerts'
import { ProfileDrawer } from './ProfileDrawer'
import { ProfileTable } from './ProfileTable'

type Tab = 'flagged' | 'all' | 'patterns'

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="bg-gray-800 rounded-lg px-4 py-3 flex flex-col">
      <span className="text-2xl font-bold text-white tabular-nums">{value}</span>
      <span className="text-xs text-gray-400 mt-0.5 uppercase tracking-wide">{label}</span>
      {sub && <span className="text-[10px] text-gray-500 mt-0.5">{sub}</span>}
    </div>
  )
}

// ── Scan status banner ────────────────────────────────────────────────────────

interface ScanBannerProps {
  status:     ScanStatus | null
  onRunScan:  (days: number, source: string) => void
  scanning:   boolean
}

function ScanBanner({ status, onRunScan, scanning }: ScanBannerProps) {
  const [days, setDays]     = useState(30)
  const [source, setSource] = useState('polymarket')

  const isRunning = status?.running || scanning

  return (
    <div className={`rounded-lg border p-3 mb-5 flex flex-col sm:flex-row sm:items-center gap-3 justify-between ${
      isRunning
        ? 'bg-blue-950/50 border-blue-700'
        : status?.error
          ? 'bg-red-950/40 border-red-700'
          : 'bg-gray-800/60 border-gray-700'
    }`}>
      <div className="flex flex-col gap-0.5 min-w-0">
        {isRunning ? (
          <div className="flex items-center gap-2">
            <span className="animate-spin text-blue-400">⟳</span>
            <span className="text-sm text-blue-300 font-medium">
              Scan running — {status?.source ?? source}, {status?.days_back ?? days}d lookback
            </span>
          </div>
        ) : status?.error ? (
          <p className="text-sm text-red-300">Scan error: {status.error}</p>
        ) : status?.last_completed ? (
          <p className="text-xs text-gray-400">
            Last scan completed: <span className="text-gray-200">{status.last_completed.slice(0, 19).replace('T', ' ')} UTC</span>
            {' · '}{status.report_count} report{status.report_count !== 1 ? 's' : ''} on disk
          </p>
        ) : (
          <p className="text-xs text-gray-400">No scans run yet — run one to populate the dashboard.</p>
        )}

        {isRunning && (
          <p className="text-xs text-blue-400/70">
            This takes a few minutes. The report list will refresh automatically when done.
          </p>
        )}
      </div>

      {/* Controls */}
      <div className="flex items-center gap-2 shrink-0">
        <select
          disabled={isRunning}
          value={source}
          onChange={e => setSource(e.target.value)}
          className="bg-gray-700 disabled:opacity-50 text-gray-200 border border-gray-600 rounded px-2 py-1 text-xs"
        >
          <option value="polymarket">Polymarket</option>
          <option value="kalshi">Kalshi</option>
          <option value="both">Both</option>
        </select>

        <select
          disabled={isRunning}
          value={days}
          onChange={e => setDays(Number(e.target.value))}
          className="bg-gray-700 disabled:opacity-50 text-gray-200 border border-gray-600 rounded px-2 py-1 text-xs"
        >
          <option value={7}>7 days</option>
          <option value={14}>14 days</option>
          <option value={30}>30 days</option>
          <option value={60}>60 days</option>
        </select>

        <button
          disabled={isRunning}
          onClick={() => onRunScan(days, source)}
          className="px-3 py-1.5 rounded text-xs font-semibold transition-colors bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed text-white"
        >
          {isRunning ? 'Running…' : 'Run Scan'}
        </button>
      </div>
    </div>
  )
}

// ── Main dashboard ────────────────────────────────────────────────────────────

export function ScanDashboard() {
  const [reports, setReports]   = useState<ReportListItem[]>([])
  const [selected, setSelected] = useState<string>('')
  const [report, setReport]     = useState<SurveillanceReport | null>(null)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState<string | null>(null)
  const [tab, setTab]           = useState<Tab>('flagged')
  const [drawer, setDrawer]     = useState<Profile | null>(null)
  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null)
  const [scanning, setScanning] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const prevRunning = useRef(false)

  // Load scan status and report list
  function refreshReports() {
    api.listReports().then(r => {
      setReports(r)
      if (r.length > 0 && !selected) setSelected(r[0].filename)
      // Auto-select newest report after a scan completes
      if (r.length > 0 && prevRunning.current) setSelected(r[0].filename)
    }).catch(() => {})
  }

  function refreshStatus() {
    api.scanStatus().then(s => {
      setScanStatus(s)
      const wasRunning = prevRunning.current
      prevRunning.current = s.running
      if (wasRunning && !s.running) {
        // Scan just finished — reload report list
        refreshReports()
      }
    }).catch(() => {})
  }

  useEffect(() => {
    refreshStatus()
    refreshReports()

    // Poll scan status every 5s
    pollRef.current = setInterval(refreshStatus, 5000)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [])

  // Load full report when selection changes
  useEffect(() => {
    if (!selected) return
    setLoading(true)
    setError(null)
    api.report(selected)
      .then(r => { setReport(r); setLoading(false) })
      .catch(e => { setError(String(e)); setLoading(false) })
  }, [selected])

  async function handleRunScan(days: number, source: string) {
    setScanning(true)
    try {
      await api.triggerScan(days, source)
      refreshStatus()
    } catch (e) {
      setError(String(e))
    } finally {
      setScanning(false)
    }
  }

  const meta = report?.metadata
  const flagged = report?.flagged_profiles ?? []
  const all = report?.all_profiles ?? []
  const patterns = report?.pattern_alerts

  const patternCount =
    (patterns?.cross_market_edge?.length ?? 0) +
    (patterns?.position_spikes?.length ?? 0) +
    (patterns?.coordinated_wallets?.length ?? 0) +
    (patterns?.event_timing_clusters?.length ?? 0)

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-4 md:p-6">
      {/* Page header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4">
        <div>
          <h1 className="text-xl font-bold text-white">Prediction Market Surveillance</h1>
          <p className="text-xs text-gray-400 mt-0.5">Scan reports · flagged wallets &amp; pattern alerts</p>
        </div>

        {/* Report picker — only shown when reports exist */}
        {reports.length > 0 && (
          <select
            className="bg-gray-800 text-gray-200 border border-gray-600 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            value={selected}
            onChange={e => setSelected(e.target.value)}
          >
            {reports.map(r => (
              <option key={r.filename} value={r.filename}>
                {r.run_time ? r.run_time.slice(0, 19).replace('T', ' ') : r.filename}
                {' — '}
                {r.flagged_count} flagged
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Scan status / run scan banner — always visible */}
      <ScanBanner status={scanStatus} onRunScan={handleRunScan} scanning={scanning} />

      {error && (
        <div className="bg-red-900/40 border border-red-700 rounded p-3 text-sm text-red-300 mb-4">
          {error}
        </div>
      )}

      {loading && (
        <div className="flex items-center justify-center py-12 text-gray-400">
          <span className="animate-spin mr-2 text-lg">⟳</span> Loading report…
        </div>
      )}

      {!loading && report && (
        <>
          {/* Stats bar */}
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3 mb-6">
            <StatCard label="Markets"    value={(meta?.total_markets ?? 0).toLocaleString()} />
            <StatCard label="Trades"     value={(meta?.total_trades ?? 0).toLocaleString()} />
            <StatCard label="Profiles"   value={(meta?.total_profiles ?? 0).toLocaleString()} />
            <StatCard label="Flagged"    value={flagged.length} />
            <StatCard
              label="Risk breakdown"
              value={`${flagged.filter(p => p.risk_level === 'CRITICAL').length}C / ${flagged.filter(p => p.risk_level === 'HIGH').length}H`}
              sub={`${flagged.filter(p => p.risk_level === 'MEDIUM').length} MEDIUM`}
            />
            <StatCard label="Pattern alerts" value={patternCount} sub={`${meta?.days_back ?? '?'}d lookback`} />
          </div>

          {/* Risk breakdown pills */}
          {flagged.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-4">
              {(['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] as const).map(lvl => {
                const n = flagged.filter(p => p.risk_level === lvl).length
                if (!n) return null
                return (
                  <span key={lvl} className="flex items-center gap-1.5 text-sm">
                    <RiskBadge level={lvl} />
                    <span className="text-gray-300">{n}</span>
                  </span>
                )
              })}
            </div>
          )}

          {/* Tabs */}
          <div className="flex gap-1 mb-4 border-b border-gray-700">
            {([
              ['flagged', `Flagged Profiles (${flagged.length})`],
              ['all',     `All Profiles (${all.length})`],
              ['patterns',`Pattern Alerts (${patternCount})`],
            ] as [Tab, string][]).map(([t, label]) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px ${
                  tab === t
                    ? 'border-blue-500 text-white'
                    : 'border-transparent text-gray-400 hover:text-gray-200'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Tab content */}
          {tab === 'flagged' && (
            <ProfileTable profiles={flagged} onSelect={setDrawer} />
          )}
          {tab === 'all' && (
            <ProfileTable profiles={all.filter(p => p.flags?.length > 0)} onSelect={setDrawer} />
          )}
          {tab === 'patterns' && patterns && (
            <PatternAlerts alerts={patterns} />
          )}
        </>
      )}

      {!loading && !report && !error && !scanStatus?.running && (
        <p className="text-gray-500 text-sm py-12 text-center">
          {reports.length === 0
            ? 'Waiting for first scan to complete…'
            : 'Select a report above to view results.'}
        </p>
      )}

      {/* Profile drawer */}
      <ProfileDrawer profile={drawer} onClose={() => setDrawer(null)} />
    </div>
  )
}
