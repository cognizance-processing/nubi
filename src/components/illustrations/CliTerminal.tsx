/**
 * CliTerminal — the `nubi` CLI: log in, push boards as code, set a secret.
 * Output in the mid tone, commands in ink, one accent cursor.
 */
import { FigSvg, Window, type FigProps } from './kit'

const LINES: [string, string][] = [
  ['$', 'nubi login'],
  ['', 'Logged in. Token saved.'],
  ['$', 'nubi dashboards push boards/'],
  ['', '3 boards pushed · 0 errors'],
  ['$', 'nubi secrets set PG_PASSWORD'],
  ['', 'Stored. 1 secret.'],
]

export default function CliTerminal({ className, title, compact }: FigProps) {
  return (
    <FigSvg fig="17" name="nubi CLI" className={className} title={title} compact={compact}>
      <Window x={16} y={20} w={448} h={228} bar={20} fill="fs" />
      {LINES.map(([p, t], i) => (
        <g key={i}>
          {p && <text x={34} y={62 + i * 24} className="lc t1" style={{ fontSize: 12 }}>{p}</text>}
          <text x={p ? 50 : 50} y={62 + i * 24} className={p ? 'lc t1' : 'lc'} style={{ fontSize: 12 }}>{t}</text>
        </g>
      ))}
      <text x={34} y={62 + 6 * 24} className="lc t1" style={{ fontSize: 12 }}>$</text>
      <rect x={50} y={62 + 6 * 24 - 11} width={8} height={14} className="ns fa" />
    </FigSvg>
  )
}
