/**
 * EmptyState — Nubi zero-data / no-match / gated / error primitive (v2).
 *
 * One component for every "nothing here" situation. It follows the Geist
 * grammar: a title that names the situation, a description that adds NEW
 * information, one primary action (Verb + Noun), an optional second path, and,
 * for errors, a copyable request id.
 *
 * Usage:
 *   <EmptyState
 *     art={<EmptyBoards />}
 *     title="No dashboards yet"
 *     description="A dashboard is a set of charts that share filters. Start from a query."
 *     action={<Button>Create dashboard</Button>}
 *     secondaryAction={<Button variant="ghost">Read the guide</Button>}
 *   />
 *
 *   <EmptyState kind="none-match" title="No dashboards match “q3”"
 *     description="Try a shorter search, or clear the filter."
 *     action={<Button variant="secondary" onClick={clear}>Clear filter</Button>} />
 *
 *   <EmptyState kind="error" title="This list couldn't load"
 *     description="The API returned an error. Nothing was changed."
 *     requestId="req_8f31c2" action={<Button onClick={retry}>Retry</Button>} />
 *
 *   <EmptyState kind="gated" title="Flows are on the Pro plan" … />
 *
 * Props:
 *   kind             'blank' (default) | 'none-match' | 'gated' | 'error'
 *   art              ReactNode: an illustration (rendered ~160px wide, aria-hidden)
 *   icon             ReactNode: fallback when there is no art (40px hairline square)
 *   title            string
 *   description      ReactNode
 *   action           ReactNode: the one primary action
 *   secondaryAction  ReactNode: optional second path (more than two is a smell)
 *   requestId        string: shown as REQUEST ID <id> with a copy button
 *   label            string: mono eyebrow; defaults per kind ("0 RESULTS", "ERROR", …)
 *   compact          boolean: tighter padding and smaller art, for inline use
 *   bordered         boolean: hairline box with a 2px ink top rule
 *   className
 *
 * a11y: none-match is a polite live region (so filter results announce), error
 * is role="alert".
 */

import { useState, type ReactNode } from 'react'
import { Check, Copy } from 'lucide-react'

function cx(...parts: unknown[]) {
  return parts.filter(Boolean).join(' ')
}

export type EmptyKind = 'blank' | 'none-match' | 'gated' | 'error'

const DEFAULT_LABEL: Record<EmptyKind, string | undefined> = {
  blank: undefined,
  'none-match': '0 results',
  gated: 'Plan limit',
  error: 'Error',
}

function RequestId({ id }: { id: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(id)
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch {
      /* clipboard unavailable: the id is still selectable */
    }
  }
  return (
    <div className="nubi-empty-reqid">
      <span className="t-label">Request ID</span>
      <b>{id}</b>
      <button
        type="button"
        onClick={copy}
        aria-label={copied ? 'Request ID copied' : 'Copy request ID'}
        className="nubi-btn nubi-btn-ghost nubi-btn-icon-xs"
        style={{ height: 26, width: 26 }}
      >
        {copied ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
      </button>
      <span className="sr-only" role="status">{copied ? 'Copied' : ''}</span>
    </div>
  )
}

export default function EmptyState({
  kind = 'blank',
  art = undefined,
  icon = undefined,
  title,
  description = undefined,
  action = undefined,
  secondaryAction = undefined,
  requestId = undefined,
  label = undefined,
  compact = false,
  bordered = false,
  className = undefined,
}: {
  kind?: EmptyKind
  art?: ReactNode
  icon?: ReactNode
  title?: ReactNode
  description?: ReactNode
  action?: ReactNode
  secondaryAction?: ReactNode
  requestId?: string
  label?: string
  compact?: boolean
  bordered?: boolean
  className?: string
}) {
  const eyebrow = label ?? DEFAULT_LABEL[kind]
  const live =
    kind === 'none-match' ? { role: 'status', 'aria-live': 'polite' as const, 'aria-atomic': true }
    : kind === 'error' ? { role: 'alert' }
    : {}

  return (
    <div
      className={cx('nubi-empty', compact && 'nubi-empty-compact', bordered && 'nubi-empty-bordered', className)}
      data-kind={kind}
      {...live}
    >
      {art ? (
        <div className="nubi-empty-art" aria-hidden="true">{art}</div>
      ) : icon ? (
        <div className="nubi-empty-icon" aria-hidden="true">{icon}</div>
      ) : null}
      {eyebrow && <span className="nubi-empty-label t-label">{eyebrow}</span>}
      {title && <p className="nubi-empty-title">{title}</p>}
      {description && <p className="nubi-empty-body">{description}</p>}
      {(action || secondaryAction) && (
        <div className="nubi-empty-actions">
          {action}
          {secondaryAction}
        </div>
      )}
      {requestId && <RequestId id={requestId} />}
    </div>
  )
}
