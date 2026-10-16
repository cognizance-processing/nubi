/**
 * OAuthConsentFlow — an AI app connects to your data through Nubi. The scopes
 * gate (the accent) is the only way through: the app gets what you consent to,
 * nothing else.
 */
import { FigSvg, Window, Cylinder, Label, Route, type FigProps } from './kit'

export default function OAuthConsentFlow({ className, title, compact }: FigProps) {
  const y = compact ? 16 : 40
  return (
    <FigSvg h={compact ? 150 : 200} fig="21" name="Consent and scopes" className={className} title={title} compact={compact}>
      <Window x={8} y={y} w={118} h={88} bar={14} />
      <text x={67} y={y + 56} textAnchor="middle" className="t1">AI app</text>
      <Route points={[[126, y + 44], [176, y + 44]]} />
      <rect x={180} y={y} width={120} height={88} rx={2} className="k1 w2 fp" />
      <text x={240} y={y + 36} textAnchor="middle" className="t1">Nubi</text>
      <polyline points={`${222},${y + 58} ${232},${y + 68} ${252},${y + 48}`} className="k1 w2" />
      <Route points={[[300, y + 44], [354, y + 44]]} weight={1} />
      <rect x={316} y={y + 8} width={8} height={72} className="ns fa" />
      <Cylinder x={358} y={y + 4} w={110} h={80} rings={2} />
      <text x={413} y={y + 54} textAnchor="middle" className="t1">Your data</text>
      {!compact && (
        <>
          <Label x={67} y={y + 108} anchor="middle">Asks</Label>
          <Label x={240} y={y + 108} anchor="middle">You consent</Label>
          <Label x={320} y={y + 108} anchor="middle" tone="accent">Scopes</Label>
          <Label x={413} y={y + 108} anchor="middle">Read only</Label>
        </>
      )}
    </FigSvg>
  )
}
