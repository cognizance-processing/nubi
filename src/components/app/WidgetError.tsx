/**
 * WidgetError — the failure state for a data widget.
 *
 * Replaces the widget BODY (chart / table / KPI) rather than sitting above it.
 * That distinction is the whole point: a broken query must never read as a
 * working one, so the tile says plainly that it failed instead of drawing
 * substitute rows. Every major BI tool (Metabase, Superset, Looker) shows an
 * error card; so do we.
 *
 * What it says depends on who is looking:
 *
 *   Everyone   "This chart couldn't load." plus one sentence on the cause
 *              ("The data source couldn't be reached.") and a Retry button.
 *              No host names, ports, SQL or driver text: viewers are the free
 *              audience and none of that is theirs to see.
 *   Editors    the same, plus a "Details" disclosure holding the engine's own
 *              message, the query id and (when the message carries one) the
 *              request id, each copyable, so a failure can be reported or
 *              debugged straight from the board.
 *
 * Presentation is a hairline tile with a 6px square danger dot and a mono
 * label. No tinted wash: the tile frame is already the container, and colour is
 * reserved for the dot. When three or more tiles share a cause the board shows
 * one grouped banner (see dashboards/widgets/boardHealth.tsx) and the tiles stay
 * short and quiet.
 *
 * Props:
 *   message   string    the real backend message. Editors-only (Details).
 *   queryId   string?   the failing query's id. Editors-only (Details).
 *   onRetry   fn?       renders a Retry button.
 *   compact   boolean?  tighter layout for short tiles (KPI).
 *   kind      string?   widget type, picks the noun ("chart", "table", "number").
 */

import { useState } from 'react'
import { Check, ChevronRight, Copy, RotateCw } from 'lucide-react'
import Button from '../ui/Button.jsx'
import Modal from '../ui/Modal.jsx'
import {
  classifyFailure, extractRequestId, viewerMessage,
} from '../../dashboards/widgets/failure.js'
import {
  useCanSeeDetails, useIsGrouped, useReportFailure,
} from '../../dashboards/widgets/boardHealth.jsx'

function CopyValue({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1400)
    } catch {
      // Clipboard can be blocked (insecure origin, iframe). The value is still
      // selectable text next to the button, so there is nothing to recover.
    }
  }
  return (
    <div className="flex items-start gap-2 min-w-0">
      <dt className="t-label shrink-0 w-[4.5rem] pt-px">{label}</dt>
      <dd className="m-0 min-w-0 flex-1 font-mono text-[12px] leading-snug text-fg break-all tabular-nums select-text">
        {value}
      </dd>
      <button
        type="button"
        onClick={copy}
        aria-label={copied ? `${label} copied` : `Copy ${label}`}
        className="shrink-0 inline-flex h-5 w-5 items-center justify-center rounded-sm text-muted hover:text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {copied ? <Check size={12} strokeWidth={1.5} /> : <Copy size={12} strokeWidth={1.5} />}
      </button>
    </div>
  )
}

export default function WidgetError({
  message, queryId = undefined, onRetry = undefined, compact = false, kind = 'chart',
}: {
  message?: string
  queryId?: string
  onRetry?: () => void
  compact?: boolean
  kind?: string
}) {
  const failure = classifyFailure(message)
  const requestId = extractRequestId(message)
  const canSeeDetails = useCanSeeDetails()
  const [open, setOpen] = useState(false)

  useReportFailure(
    { key: failure.key, cause: failure.cause, label: failure.label, message: message ?? '' },
    onRetry,
  )
  const grouped = useIsGrouped(failure.key)

  const hasDetails = canSeeDetails && Boolean(message || queryId)

  return (
    <div
      className={[
        'flex h-full w-full min-h-0 flex-col justify-center overflow-hidden text-left',
        compact ? 'gap-1.5 px-4 py-2.5' : 'gap-2 px-5 py-4',
      ].join(' ')}
      role="group"
      aria-label={viewerMessage(kind)}
      data-testid="widget-error"
    >
      <div className="flex items-center gap-2">
        <span className="nubi-dot nubi-dot-danger" aria-hidden="true" />
        <span className="t-label nbc-error-label">Error</span>
      </div>

      <p className={['font-semibold text-fg leading-snug', compact ? 'text-[13px]' : 'text-sm'].join(' ')}>
        {viewerMessage(kind)}
      </p>

      {!compact && (
        <p className="text-[13px] leading-snug text-muted max-w-[44ch]">
          {grouped ? 'The cause is the same across this board. See the notice above.' : failure.label}
        </p>
      )}

      {(onRetry || hasDetails) && (
        <div className="flex flex-wrap items-center gap-1.5 -ml-1.5">
          {onRetry && (
            <Button size="xs" variant="ghost" onClick={onRetry}>
              <RotateCw size={12} strokeWidth={1.5} aria-hidden="true" />
              Retry
            </Button>
          )}
          {hasDetails && (
            <Button
              size="xs"
              variant="ghost"
              onClick={() => setOpen(true)}
              aria-haspopup="dialog"
            >
              <ChevronRight size={12} strokeWidth={1.5} aria-hidden="true" />
              Details
            </Button>
          )}
        </div>
      )}

      {hasDetails && (
        // The engine's message can be long and the tile can be short, so the
        // details open in a dialog rather than growing the tile.
        <Modal
          open={open}
          onClose={() => setOpen(false)}
          size="lg"
          title={viewerMessage(kind)}
          description="What the data source reported. Only editors can see this."
        >
          <Modal.Body>
            <dl className="m-0 space-y-3">
              {message && <CopyValue label="Engine" value={message} />}
              {queryId && <CopyValue label="Query" value={queryId} />}
              {requestId && <CopyValue label="Request" value={requestId} />}
            </dl>
          </Modal.Body>
          <Modal.Footer>
            {onRetry && (
              <Button size="sm" variant="secondary" onClick={() => { setOpen(false); onRetry() }}>
                <RotateCw size={14} strokeWidth={1.5} aria-hidden="true" />
                Retry
              </Button>
            )}
            <Button size="sm" onClick={() => setOpen(false)}>Close</Button>
          </Modal.Footer>
        </Modal>
      )}
    </div>
  )
}
