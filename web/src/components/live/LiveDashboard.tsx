import { useEffect, useRef, useState } from 'react'
import { api } from '../../api'
import { LiveFeedData } from '../../types'
import { AlertCard } from './AlertCard'

const POLL_MS = 15_000
const STALE_MS = 2 * 60 * 1000   // 2 min: if last_updated is older, monitor is probably stopped

// ── helpers ───────────────────────────────────────────────────────────────────

function secondsAgo(iso: string | null): number | null {
  if (!iso) return null
  const diff = Date.now() - new Date(iso).getTime()
  return Math.floor(diff / 1000)
}

function formatAgo(secs: number): string {
  if (secs < 60)  return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  return `${Math.floor(secs / 3600)}h ago`
}

// ── sub-components ────────────────────────────────────────────────────────────

function PulseDot({ active }: { active: boolean }) {
  return (
    <span className="relative flex h-2.5 w-2.5">
      {active && (
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
      )}
      <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${active ? 'bg-green-400' : 'bg-gray-500'}`} />
    </span>
  )
}

interface MonitorBannerProps {
  lastUpdated:    string | null
  pollCount:      number
  marketsTracked: number
  source:         string | undefined
}

function MonitorBanner({ lastUpdated, pollCount, marketsTracked, source }: MonitorBannerProps) {
  const [tick, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 5000)
    return () => clearInterval(id)
  }, [])

  const secs   = secondsAgo(lastUpdated)
  const stale  = secs === null || secs > STALE_MS / 1000
  const active = !stale

  return (
    <div className={`rounded-lg border px-4 py-2.5 mb-5 flex flex-col sm:flex-row sm:items-center gap-2 justify-between text-sm ${
      stale
        ? 'bg-yellow-950/40 border-yellow-700'
        : 'bg-gray-800/60 border-gray-700'
    }`}>
      <div className="flex items-center gap-2.5">
        <PulseDot active={active} />
        {stale ? (
          <span className="text-yellow-300 font-medium">
            Monitor not running — start it with:&nbsp;
            <code className="bg-gray-800 px-1.5 py-0.5 rounded text-xs text-yellow-200">
              python3 main.py watch
            </code>
          </span>
        ) : (
          <span className="text-gray-300">
            Monitor active · polled <span className="text-white font-medium">{secs !== null ? formatAgo(secs) : '—'}</span>
          </span>
        )}
      </div>

      {!stale && (
        <div className="flex items-center gap-4 text-xs text-gray-400">
          <span><span className="text-gray-200 font-medium">{marketsTracked}</span> markets tracked</span>
          <span><span className="text-gray-200 font-medium">{pollCount}</span> polls</span>
          {source && <span className="capitalize text-gray-500">{source}</span>}
        </div>
      )}
    </div>
  )
}

// ── main component ────────────────────────────────────────────────────────────

export function LiveDashboard() {
  const [feed, setFeed]           = useState<LiveFeedData | null>(null)
  const [error, setError]         = useState<string | null>(null)
  const [lastFetch, setLastFetch] = useState<Date | null>(null)
  const [countdown, setCountdown] = useState(POLL_MS / 1000)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const countRef = useRef<ReturnType<typeof setInterval> | null>(null)

  function fetch_() {
    api.liveAlerts()
      .then(data => {
        setFeed(data)
        setLastFetch(new Date())
        setError(null)
        setCountdown(POLL_MS / 1000)
      })
      .catch(e => setError(String(e)))
  }

  useEffect(() => {
    fetch_()
    timerRef.current = setInterval(fetch_, POLL_MS)
    countRef.current = setInterval(() => setCountdown(c => Math.max(0, c - 1)), 1000)
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
      if (countRef.current) clearInterval(countRef.current)
    }
  }, [])

  const alerts        = feed?.alerts ?? []
  const criticalCount = alerts.filter(a => a.severity === 'CRITICAL').length
  const highCount     = alerts.filter(a => a.severity === 'HIGH').length

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-4 md:p-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5">
        <div>
          <h1 className="text-xl font-bold text-white">Live Alerts</h1>
          <p className="text-xs text-gray-400 mt-0.5">
            Real-time signals · dashboard refreshes every {POLL_MS / 1000}s
          </p>
        </div>

        <div className="flex items-center gap-4 text-xs text-gray-400">
          {lastFetch && (
            <span>Dashboard fetched: {lastFetch.toLocaleTimeString()}</span>
          )}
          <span>Next in <span className="font-bold text-gray-300">{countdown}s</span></span>
          <button
            onClick={() => { fetch_(); setCountdown(POLL_MS / 1000) }}
            className="px-3 py-1 bg-gray-700 hover:bg-gray-600 rounded text-gray-200 transition-colors"
          >
            Refresh now
          </button>
        </div>
      </div>

      {/* Monitor status banner — always visible */}
      <MonitorBanner
        lastUpdated={feed?.last_updated ?? null}
        pollCount={feed?.poll_count ?? 0}
        marketsTracked={feed?.markets_tracked ?? 0}
        source={feed?.source}
      />

      {error && (
        <div className="bg-red-900/40 border border-red-700 rounded p-3 text-sm text-red-300 mb-4">
          {error}
        </div>
      )}

      {/* Summary pills */}
      {alerts.length > 0 && (
        <div className="flex flex-wrap gap-3 mb-5">
          <span className="px-3 py-1 bg-gray-800 rounded text-sm text-gray-300">
            <span className="font-bold text-white">{alerts.length}</span> total alerts
          </span>
          {criticalCount > 0 && (
            <span className="px-3 py-1 bg-red-900/60 border border-red-700 rounded text-sm text-red-200">
              <span className="font-bold">{criticalCount}</span> CRITICAL
            </span>
          )}
          {highCount > 0 && (
            <span className="px-3 py-1 bg-orange-900/40 border border-orange-700 rounded text-sm text-orange-200">
              <span className="font-bold">{highCount}</span> HIGH
            </span>
          )}
        </div>
      )}

      {/* Alert list */}
      {alerts.length === 0 && !error ? (
        <div className="flex flex-col items-center justify-center py-20 text-gray-500">
          <span className="text-4xl mb-3">🔍</span>
          <p className="text-sm">
            No alerts yet. Once the monitor is running, signals appear here automatically.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {alerts
            .slice()
            .sort((a, b) => {
              const order: Record<string, number> = { CRITICAL: 0, HIGH: 1, MEDIUM: 2 }
              return (order[a.severity] ?? 9) - (order[b.severity] ?? 9)
            })
            .map((a, i) => (
              <AlertCard key={i} alert={a} />
            ))}
        </div>
      )}
    </div>
  )
}
