/**
 * ConnectorLogo - a connector's official mark in a square hairline tile.
 *
 * Shared between the connector-type picker (ConnectorsPage), the connector
 * form header, and any connector-instance picker (QueryWorkspace /
 * BlendBuilder's "Connector" combobox) so every place that shows a connector
 * renders the same logo.
 *
 * The mark is drawn by ToolLogo from the registry (src/components/brand), which
 * resolves `info.logo` (a /logos/... URL from src/data/connectors.ts) or, for
 * entries without a file, `info.id`. Default: a 24px mark in a 40px tile with a
 * 1px hairline and a 2px radius, no tint. If a connector has no registered
 * mark we fall back to a generic database glyph so the UI never shows a broken
 * image.
 */

import { Database } from 'lucide-react'
import ToolLogo from '../brand/ToolLogo'
import { getToolLogo, getToolLogoByFile } from '../brand/registry'

export default function ConnectorLogo({ info, size = 24, className = '', mode = 'brand' as const }) {
  const logo = getToolLogoByFile(info?.logo) ?? getToolLogo(info?.id)
  const box = size >= 20 ? size + 16 : size + 8

  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center border border-border bg-surface text-fg ${className}`}
      style={{ width: box, height: box, borderRadius: 2 }}
    >
      {logo ? (
        <ToolLogo
          slug={logo.slug}
          mode={mode}
          size={size}
          title={`${info?.label ?? logo.name} logo`}
        />
      ) : (
        <Database size={size * 0.72} strokeWidth={1.75} className="text-muted" role="img" aria-label="Connector" />
      )}
    </span>
  )
}
