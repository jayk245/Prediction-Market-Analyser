export function SideBadge({ side }: { side: string }) {
  const s = side?.toUpperCase() ?? '?'
  const cls =
    s === 'YES' || s === 'BUY'  ? 'bg-green-700 text-green-100' :
    s === 'NO'  || s === 'SELL' ? 'bg-red-800   text-red-100'   :
                                   'bg-gray-700   text-gray-200'
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-bold ${cls}`}>
      {s}
    </span>
  )
}
