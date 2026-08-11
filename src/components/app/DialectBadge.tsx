/**
 * DialectBadge — small secondary label showing a connector's SQL dialect.
 *
 * The dialect string comes from src/data/connectors.js `dialectFor()` (a
 * frontend mirror of backend/app/connectors/dialects.py CONNECTOR_DIALECT).
 * Purely presentational — renders nothing for file-only connectors
 * (sftp/ftp) that have no SQL dialect.
 */

export default function DialectBadge({ dialect, className = '' }) {
  if (!dialect) return null
  return (
    <span
      title={`SQL dialect: ${dialect}`}
      className={`
        inline-flex items-center px-1.5 py-0.5 rounded-md
        text-[9px] font-mono font-medium uppercase tracking-wide
        bg-surface-2 text-muted border border-border/60 shrink-0
        ${className}
      `}
    >
      {dialect}
    </span>
  )
}
