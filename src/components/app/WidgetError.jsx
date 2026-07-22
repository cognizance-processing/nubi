/**
 * WidgetError — the failure state for a data widget.
 *
 * Replaces the widget BODY (chart / table / KPI) rather than sitting above it.
 * That distinction is the whole point: the previous behaviour drew a small amber
 * banner over a fully-rendered chart of SAMPLE_TABLE rows, so a broken query was
 * visually indistinguishable from a working one — the same board read as "fine",
 * then "broken", then "unchanged" across three separate checks. Every major BI
 * tool (Metabase, Superset, Looker) shows an error card instead; so do we.
 *
 * Props:
 *   message   string    — the real backend message ("SELECT list column count
 *                         mismatch: 7 vs 3", "No registered query found…").
 *   queryId   string?   — shown small under the message so the failing query is
 *                         identifiable straight from the board.
 *   onRetry   fn?       — when provided, renders a Retry button.
 *   compact   boolean?  — tighter padding for small widgets (KPI tiles).
 */

import { AlertTriangle } from 'lucide-react'
import EmptyState from '../ui/EmptyState.jsx'
import Button from '../ui/Button.jsx'

export default function WidgetError({ message, queryId, onRetry, compact = false }) {
  return (
    <div className="flex items-center justify-center h-full w-full px-3" data-testid="widget-error">
      <EmptyState
        compact={compact}
        icon={<AlertTriangle size={compact ? 16 : 20} className="text-danger" />}
        title="Query failed"
        description={
          <span className="block">
            <span className="block text-fg/80 break-words">
              {message || 'The query could not be run.'}
            </span>
            {queryId && (
              <span className="mt-1 block font-mono text-[10px] text-muted/70 break-all">
                {queryId}
              </span>
            )}
          </span>
        }
        action={
          onRetry ? (
            <Button size="sm" variant="secondary" onClick={onRetry}>
              Retry
            </Button>
          ) : null
        }
      />
    </div>
  )
}
