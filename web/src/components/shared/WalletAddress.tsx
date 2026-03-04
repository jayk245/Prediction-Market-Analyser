import { useState } from 'react'

export function WalletAddress({ address }: { address: string }) {
  const [copied, setCopied] = useState(false)

  if (!address) return <span className="text-gray-500 italic">—</span>

  const display = address.length > 12
    ? `${address.slice(0, 6)}…${address.slice(-4)}`
    : address

  const copy = () => {
    navigator.clipboard.writeText(address).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <button
      onClick={copy}
      title={address}
      className="font-mono text-sm text-blue-300 hover:text-blue-100 transition-colors"
    >
      {display}
      {copied && <span className="ml-1 text-green-400 text-xs">✓</span>}
    </button>
  )
}
