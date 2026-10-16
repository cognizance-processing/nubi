/**
 * OnboardingSteps — connect, query, board. Three ruled frames; `step` puts the
 * accent on the current one. `compact` drops the labels for the Home checklist.
 */
import { FigSvg, Cylinder, Label, Link, type FigProps } from './kit'

const FRAMES = [
  { x: 8, n: '01', t: 'Connect' },
  { x: 172, n: '02', t: 'Query' },
  { x: 336, n: '03', t: 'Board' },
]

export default function OnboardingSteps({ className, title, compact, step = 1 }: FigProps & { step?: 1 | 2 | 3 | 0 }) {
  return (
    <FigSvg h={compact ? 150 : 200} fig="20" name="Connect · query · board" className={className} title={title} compact={compact}>
      {FRAMES.map((f, i) => {
        const on = step === i + 1
        const y = compact ? 16 : 36
        return (
          <g key={f.n}>
            {!compact && (
              <>
                <Label x={f.x} y={20} tone={on ? 'accent' : 'ink'}>{f.n}</Label>
                <Label x={f.x + 24} y={20} tone={on ? 'accent' : 'mid'}>{f.t}</Label>
              </>
            )}
            <rect x={f.x} y={y} width={136} height={112} rx={2} className={on ? 'ka w2 fp' : 'k1 fp'} />
            {i === 0 && (
              <g>
                <Cylinder x={f.x + 76} y={y + 30} w={40} h={52} tone="k1" rings={1} />
                <rect x={f.x + 18} y={y + 46} width={26} height={20} rx={2} className="k1 fl" />
                <line x1={f.x + 44} y1={y + 56} x2={f.x + 76} y2={y + 56} className="k1 w2" />
                <line x1={f.x + 12} y1={y + 52} x2={f.x + 18} y2={y + 52} className="k1" />
                <line x1={f.x + 12} y1={y + 60} x2={f.x + 18} y2={y + 60} className="k1" />
              </g>
            )}
            {i === 1 && (
              <g>
                {[0, 1, 2, 3].map(r => <line key={r} x1={f.x + 14} y1={y + 24 + r * 16} x2={f.x + [90, 70, 104, 54][r]} y2={y + 24 + r * 16} className={r === 0 ? 'k1 w2' : 'k2'} />)}
                <rect x={f.x + 14} y={y + 84} width={7} height={13} className="ns fi" />
              </g>
            )}
            {i === 2 && (
              <g>
                {[0, 1, 2, 3, 4].map(b => <rect key={b} x={f.x + 16 + b * 22} y={y + 92 - [26, 44, 34, 60, 76][b]} width={14} height={[26, 44, 34, 60, 76][b]} className={b === 4 ? 'k1 fa' : 'k1 fl'} />)}
                <line x1={f.x + 12} y1={y + 92} x2={f.x + 124} y2={y + 92} className="k1" />
              </g>
            )}
            {i < 2 && <Link x1={f.x + 138} y1={y + 56} x2={f.x + 162} y2={y + 56} head tone={step === i + 1 ? 'ka' : 'k1'} />}
          </g>
        )
      })}
    </FigSvg>
  )
}
