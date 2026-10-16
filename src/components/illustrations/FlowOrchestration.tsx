/**
 * FlowOrchestration — a directed graph of tasks: one source fans out to two
 * tasks that merge into a load step. One node is running (the accent).
 */
import { FigSvg, Label, Route, Dot, type FigProps } from './kit'

type N = { id: string; x: number; y: number; t: string; s: 'done' | 'run' | 'wait' }
const NODES: N[] = [
  { id: 'a', x: 20, y: 114, t: 'Extract', s: 'done' },
  { id: 'b', x: 188, y: 46, t: 'Clean', s: 'run' },
  { id: 'c', x: 188, y: 182, t: 'Enrich', s: 'done' },
  { id: 'd', x: 356, y: 114, t: 'Load', s: 'wait' },
]
const W = 104, H = 44

export default function FlowOrchestration({ className, title, compact }: FigProps) {
  return (
    <FigSvg fig="10" name="Flow graph" className={className} title={title} compact={compact}>
      <Route points={[[124, 136], [156, 136], [156, 68], [188, 68]]} />
      <Route points={[[124, 136], [156, 136], [156, 204], [188, 204]]} />
      <Route points={[[292, 68], [324, 68], [324, 136], [356, 136]]} tone="k2" dash />
      <Route points={[[292, 204], [324, 204], [324, 136], [356, 136]]} />
      {NODES.map(n => (
        <g key={n.id}>
          <rect x={n.x} y={n.y} width={W} height={H} rx={2}
            className={n.s === 'run' ? 'ka w2 fp' : n.s === 'wait' ? 'k2 dd fp' : 'k1 fp'} />
          <text x={n.x + 12} y={n.y + 19} className={n.s === 'wait' ? '' : 't1'}>{n.t}</text>
          <Dot x={n.x + 15} y={n.y + 32} tone={n.s === 'run' ? 'accent' : n.s === 'wait' ? 'mid' : 'ink'} s={6} />
          <text x={n.x + 24} y={n.y + 35} className={n.s === 'run' ? 'ta' : ''} style={{ fontSize: 9 }}>
            {n.s === 'run' ? 'Running' : n.s === 'wait' ? 'Queued' : 'Done'}
          </text>
        </g>
      ))}
      {!compact && <Label x={20} y={24}>Schedule · hourly</Label>}
    </FigSvg>
  )
}
