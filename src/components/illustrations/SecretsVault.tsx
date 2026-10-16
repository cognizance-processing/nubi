/**
 * SecretsVault — connector config stays readable, credentials are split off,
 * sealed and referenced by name; they never reach the database or the API.
 */
import { FigSvg, Label, Link, type FigProps } from './kit'

const CFG = [['host', 'db.acme.internal'], ['port', '5432'], ['database', 'sales'], ['user', 'readonly']]

export default function SecretsVault({ className, title, compact }: FigProps) {
  return (
    <FigSvg fig="14" name="Secrets vault" className={className} title={title} compact={compact}>
      <rect x={16} y={36} width={178} height={198} rx={2} className="k1 fp" />
      <line x1={16} y1={62} x2={194} y2={62} className="k1 w2" />
      <Label x={28} y={53} tone="ink">Config · plain</Label>
      {CFG.map(([k, v], i) => (
        <g key={k}>
          <line x1={16} y1={96 + i * 34} x2={194} y2={96 + i * 34} className="k3 w1" />
          <Label x={28} y={77 + i * 34}>{k}</Label>
          <text x={28} y={91 + i * 34} className="lc t1">{v}</text>
        </g>
      ))}
      <text x={28} y={218} className="lc t1">password = secret://pg</text>
      <Link x1={196} y1={214} x2={288} y2={150} tone="k2" dash head />
      <rect x={290} y={36} width={174} height={198} rx={2} className="k1 w2 fs" />
      <rect x={302} y={48} width={150} height={174} rx={2} className="k1 fp" />
      <Label x={377} y={72} anchor="middle" tone="ink">Sealed</Label>
      {/* lock */}
      <path d="M363,128 v-14 a14,14 0 0 1 28,0 v14" className="k1 w2" />
      <rect x={353} y={128} width={48} height={34} rx={2} className="ns fa" />
      <rect x={374} y={139} width={6} height={12} className="ns fp" />
      {[0, 1, 2, 3, 4, 5, 6, 7].map(i => <rect key={i} x={324 + i * 14} y={182} width={7} height={7} className="ns fm" />)}
      <Label x={377} y={212} anchor="middle">Encrypted at rest</Label>
    </FigSvg>
  )
}
