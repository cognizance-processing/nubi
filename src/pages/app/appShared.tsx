/**
 * appShared — small presentational helpers shared by the working surfaces
 * (Connectors, Queries, Dashboards, Flows, Watches, Data, Admin).
 *
 *   errorRequestId(err)   a request id carried on an API error payload, if any
 *   LoadError             inline error state: figure, message, copyable request id, Retry
 *   ListSkeleton          layout-matching list rows (the Skeleton primitive delays itself 300ms)
 *   FilterBar             "N results" + removable filter chips + clear
 *   StatusDot             6px square dot + label (no pills)
 *
 * Presentation only. No data or API behaviour lives here.
 */

import type { ReactNode } from 'react'
import { RefreshCw } from 'lucide-react'
import Button from '../../components/ui/Button.jsx'
import Tag from '../../components/ui/Tag.jsx'
import EmptyState from '../../components/ui/EmptyState.jsx'
import Skeleton from '../../components/ui/Skeleton.jsx'
import { ErrorFigure } from '../../components/illustrations'
import '../../components/app/appPatterns.css'

/** A request id the backend attached to an error, if it did. Never invented. */
export function errorRequestId(err: any): string | undefined {
  const p = err?.payload
  const id = p?.request_id ?? p?.error?.request_id ?? p?.requestId ?? err?.requestId
  return typeof id === 'string' && id ? id : undefined
}

/** Plain-language message for an unknown thrown value. */
export function errorMessage(err: any, fallback = 'The server did not answer.'): string {
  if (!err) return fallback
  if (typeof err === 'string') return err
  return err.message || fallback
}

/**
 * Inline error state for a failed list / page load. Says what failed and what
 * did not change, carries the request id when the API gave one, and always
 * offers Retry. `secondary` is an optional second path (e.g. Open in editor).
 */
export function LoadError({
  title,
  error,
  onRetry,
  secondary,
  className,
}: {
  title: string
  error?: any
  onRetry?: () => void
  secondary?: ReactNode
  className?: string
}) {
  return (
    <EmptyState
      kind="error"
      art={<ErrorFigure />}
      title={title}
      description={`${errorMessage(error)} Nothing was changed.`}
      requestId={errorRequestId(error)}
      action={onRetry ? (
        <Button variant="secondary" onClick={onRetry}>
          <RefreshCw size={14} aria-hidden="true" /> Retry
        </Button>
      ) : undefined}
      secondaryAction={secondary}
      className={className}
    />
  )
}

/** Rows that mirror `.ap-row`: 40px tile, two text lines, a meta cell. */
export function ListSkeleton({ rows = 5, tile = true }: { rows?: number; tile?: boolean }) {
  return (
    <div className="ap-list" aria-hidden="true" data-testid="list-skeleton">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="ap-skel-row">
          {tile && <Skeleton className="h-10 w-10 shrink-0" />}
          <div className="flex-1 min-w-0 flex flex-col gap-2">
            <Skeleton className="h-3.5" style={{ width: `${48 + ((i * 13) % 30)}%` }} />
            <Skeleton className="h-3" style={{ width: `${24 + ((i * 7) % 20)}%` }} />
          </div>
          <Skeleton className="h-5 w-16 hidden sm:block" />
          <Skeleton className="h-8 w-20 hidden md:block" />
        </div>
      ))}
    </div>
  )
}

export interface FilterChip {
  key: string
  label: string
  onRemove: () => void
}

/** "12 connectors" count line + removable chips + a clear-all link. */
export function FilterBar({
  count,
  noun,
  chips = [],
  onClear,
  trailing,
}: {
  count: number
  noun: string
  chips?: FilterChip[]
  onClear?: () => void
  trailing?: ReactNode
}) {
  return (
    <div className="ap-filterbar">
      <span className="ap-count" aria-live="polite">
        {count} {count === 1 ? noun : `${noun}s`}
      </span>
      {chips.map(c => (
        <Tag key={c.key} size="md" caps={false} onRemove={c.onRemove}>{c.label}</Tag>
      ))}
      {chips.length > 1 && onClear && (
        <button type="button" className="ap-clear" onClick={onClear}>Clear all</button>
      )}
      {trailing && <span className="ml-auto flex items-center gap-2">{trailing}</span>}
    </div>
  )
}

/** 6px square status dot + text. Colour is the dot; the text stays legible ink. */
export function StatusDot({
  tone = 'muted',
  children,
  className = '',
}: {
  tone?: 'live' | 'success' | 'warning' | 'danger' | 'muted'
  children: ReactNode
  className?: string
}) {
  return (
    <span className={`inline-flex items-center gap-1.5 text-[13px] text-fg ${className}`}>
      <span className={`nubi-dot nubi-dot-${tone}`} aria-hidden="true" />
      {children}
    </span>
  )
}
