/**
 * EmbedAuth — auth as code. A signed token (header, claims, signature) passes
 * the policy gate and a scoped dashboard mounts in the host page.
 */
import { FigSvg, Window, Label, Link, type FigProps } from './kit'

export default function EmbedAuth({ className, title, compact }: FigProps) {
  return (
    <FigSvg fig="07" name="Embed with a signed token" className={className} title={title} compact={compact}>
      {/* token */}
      {['header', 'claims', 'signature'].map((t, i) => (
        <g key={t}>
          <rect x={16} y={72 + i * 44} width={118} height={38} rx={2} className={i === 1 ? 'k1 w2 fp' : 'k1 fp'} />
          <Label x={26} y={87 + i * 44} tone={i === 1 ? 'ink' : 'mid'}>{t}</Label>
        </g>
      ))}
      <text x={26} y={102} className="lc t1" style={{ fontSize: 9.5 }}>{'{ alg: ES256 }'}</text>
      <text x={26} y={146} className="lc t1" style={{ fontSize: 9.5 }}>{'{ region: EMEA }'}</text>
      <Label x={16} y={56} tone="ink">Signed JWT</Label>
      <Link x1={134} y1={138} x2={190} y2={138} head />
      {/* policy gate */}
      <rect x={196} y={92} width={8} height={92} className="ns fi" />
      <rect x={236} y={92} width={8} height={92} className="ns fi" />
      <polyline points="210,138 217,146 232,126" className="ka w3" />
      <Label x={220} y={206} anchor="middle" tone="ink">Policy</Label>
      <Link x1={248} y1={138} x2={290} y2={138} head />
      {/* host page + embedded board */}
      <Window x={296} y={46} w={168} h={170} />
      <Label x={304} y={72}>Your app</Label>
      <rect x={306} y={84} width={148} height={120} rx={2} className="k2 dd" />
      <polyline points="316,184 338,172 358,176 382,156 404,162 440,138" className="k1 w2" />
      {[0, 1, 2, 3].map(i => <rect key={i} x={318 + i * 28} y={98 + (i % 2) * 6} width={20} height={16 - (i % 2) * 6} className="k1 fl" />)}
      <text x={380} y={236} textAnchor="middle" className="lc">{'<nubi-dashboard>'}</text>
    </FigSvg>
  )
}
