/**
 * MeteringWallet — prepaid, metered on what the server does. Server seconds,
 * storage and egress draw the wallet down; viewers cost nothing (the accent).
 * Illustrative numbers.
 */
import { FigSvg, Label, type FigProps } from './kit'

const ROWS: [string, string, string][] = [
  ['Server seconds', '412 s', '$0.41'],
  ['Storage written', '3.2 GB', '$0.07'],
  ['Egress', '0 B', '$0.00'],
]

export default function MeteringWallet({ className, title, compact }: FigProps) {
  return (
    <FigSvg fig="18" name="Usage wallet" className={className} title={title} compact={compact}>
      <Label x={16} y={30}>Wallet</Label>
      <text x={16} y={58} className="t1" style={{ fontSize: 26, letterSpacing: '-.02em' }}>$49.52</text>
      <Label x={464} y={30} anchor="end">of $50.00</Label>
      {Array.from({ length: 40 }).map((_, i) => (
        <rect key={i} x={16 + i * 11.2} y={72} width={8} height={22} className={i < 39 ? 'ns fi' : 'ka w1'} />
      ))}
      <line x1={16} y1={124} x2={464} y2={124} className="k1 w2" />
      {ROWS.map(([a, b, c], i) => (
        <g key={a}>
          <line x1={16} y1={124 + (i + 1) * 36} x2={464} y2={124 + (i + 1) * 36} className="k3 w1" />
          <text x={16} y={146 + i * 36} className="t1">{a}</text>
          <text x={300} y={146 + i * 36} textAnchor="end">{b}</text>
          <text x={464} y={146 + i * 36} textAnchor="end" className="t1">{c}</text>
        </g>
      ))}
      <rect x={16} y={232} width={4} height={26} className="ns fa" />
      <text x={30} y={250} className="ta">Viewers</text>
      <text x={300} y={250} textAnchor="end" className="ta">1,800</text>
      <text x={464} y={250} textAnchor="end" className="ta">$0.00</text>
    </FigSvg>
  )
}
