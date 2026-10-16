/**
 * ToolLogoGrid - the brand sheet: every registered mark, mono and brand
 * colour, at 24 and 40px, on a hairline grid. Used to review the set in both
 * themes (and as living documentation of the registry). Not shipped on any
 * product route.
 */

import ToolLogo from './ToolLogo'
import { TOOL_LOGOS } from './registry'

export default function ToolLogoGrid() {
  return (
    <div className="bg-surface text-fg">
      <div
        className="grid border-t border-l border-border"
        style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(168px, 1fr))' }}
      >
        {TOOL_LOGOS.map((l) => (
          <figure key={l.slug} className="m-0 flex flex-col gap-3 border-r border-b border-border p-4">
            <div className="flex items-center gap-4">
              <ToolLogo slug={l.slug} mode="brand" size={40} decorative />
              <ToolLogo slug={l.slug} mode="brand" size={24} decorative />
              <ToolLogo slug={l.slug} mode="mono" size={24} decorative className="text-fg" />
              <ToolLogo slug={l.slug} mode="mono" size={16} decorative className="text-muted" />
            </div>
            <figcaption>
              <div className="text-[13px] font-medium leading-tight">{l.name}</div>
              <div className="font-mono text-[11px] uppercase tracking-[0.06em] text-muted tabular-nums">
                {l.slug} / {l.kind}
              </div>
            </figcaption>
          </figure>
        ))}
      </div>
    </div>
  )
}
