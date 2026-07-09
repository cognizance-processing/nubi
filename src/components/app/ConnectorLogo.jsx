/**
 * ConnectorLogo — a connector's brand logo on a faint brand-tinted tile.
 *
 * Shared between the connector-type picker (ConnectorsPage), the connector
 * form header, and any connector-instance picker (e.g. QueryWorkspace /
 * BlendBuilder's "Connector" combobox) so every place that shows a connector
 * renders the same logo chip.
 *
 * Gracefully falls back to a generic database icon if the logo file 404s
 * (e.g. a catalog entry references a logo that hasn't been added to
 * public/logos/connectors/ yet) so the UI never shows a broken-image glyph.
 */

import { useState } from 'react'
import { Database } from 'lucide-react'

export default function ConnectorLogo({ info, size = 24, className = '' }) {
  const [broken, setBroken] = useState(false)
  const box = size + 18
  const showFallback = broken || !info?.logo

  return (
    <span
      className={`inline-flex items-center justify-center rounded-xl border shrink-0 ${className}`}
      style={{
        width: box,
        height: box,
        background: `${info?.color ?? '#6b7280'}14`,
        borderColor: `${info?.color ?? '#6b7280'}33`,
      }}
    >
      {showFallback ? (
        <Database size={size * 0.72} style={{ color: info?.color ?? '#6b7280' }} strokeWidth={2} />
      ) : (
        <img
          src={info.logo}
          alt={`${info.label ?? 'Connector'} logo`}
          className="object-contain"
          style={{ width: size, height: size }}
          loading="lazy"
          onError={() => setBroken(true)}
        />
      )}
    </span>
  )
}
