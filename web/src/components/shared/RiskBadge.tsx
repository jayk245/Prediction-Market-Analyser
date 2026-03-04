import type { RiskLevel } from '../../types'

const STYLES: Record<string, string> = {
  CRITICAL: 'bg-red-600 text-white',
  HIGH:     'bg-orange-500 text-white',
  MEDIUM:   'bg-yellow-400 text-gray-900',
  LOW:      'bg-green-500 text-white',
}

export function RiskBadge({ level }: { level: string }) {
  const cls = STYLES[level] ?? 'bg-gray-500 text-white'
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-bold tracking-wide ${cls}`}>
      {level}
    </span>
  )
}

export function SeverityBadge({ level }: { level: string }) {
  return <RiskBadge level={level} />
}
