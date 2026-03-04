import type {
  DashboardStats, LiveFeedData, ReportListItem, SurveillanceReport, ScanStatus,
} from './types'

const BASE = '/api'

async function get<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

async function post<T>(path: string, params: Record<string, string | number> = {}): Promise<T> {
  const qs = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString()
  const r = await fetch(`${BASE}${path}${qs ? '?' + qs : ''}`, { method: 'POST' })
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw new Error(body.detail ?? `${r.status} ${r.statusText}`)
  }
  return r.json()
}

export const api = {
  stats:        ()                                              => get<DashboardStats>('/stats'),
  listReports:  ()                                             => get<ReportListItem[]>('/reports'),
  latestReport: ()                                             => get<SurveillanceReport>('/reports/latest'),
  report:       (f: string)                                    => get<SurveillanceReport>(`/reports/${f}`),
  liveAlerts:   ()                                             => get<LiveFeedData>('/live'),
  scanStatus:   ()                                             => get<ScanStatus>('/scan/status'),
  triggerScan:  (days_back = 30, source = 'polymarket')       => post<{ status: string }>('/scan', { days_back, source }),
}
