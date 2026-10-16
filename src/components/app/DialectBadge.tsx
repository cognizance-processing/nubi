/**
 * DialectBadge — small secondary label showing a connector's SQL dialect.
 *
 * The dialect string comes from src/data/connectors.js `dialectFor()` (a
 * frontend mirror of backend/app/connectors/dialects.py CONNECTOR_DIALECT).
 * Purely presentational — renders nothing for file-only connectors
 * (sftp/ftp) that have no SQL dialect. Drawn with the shared Tag: square,
 * hairline, mono, no fill.
 */

import Tag from '../ui/Tag.jsx'

export default function DialectBadge({ dialect, className = '' }) {
  if (!dialect) return null
  return (
    <Tag title={`SQL dialect: ${dialect}`} className={`shrink-0 ${className}`}>
      {dialect}
    </Tag>
  )
}
