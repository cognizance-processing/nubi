/**
 * AdminUI — small presentational building blocks shared by the /admin pages.
 *
 * Swiss: hairline panels with a 2px ink top rule, 36px table rows with a 2px ink
 * header rule, Tags instead of pills, EmptyState + figures for empty and error
 * states. Purely presentational — no data fetching.
 */

import { Loader2, CheckCircle2, ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react'
import Button from '../../components/ui/Button.jsx'
import Tag from '../../components/ui/Tag.jsx'
import Input from '../../components/ui/Input.jsx'
import Skeleton from '../../components/ui/Skeleton.jsx'
import EmptyStateBase from '../../components/ui/EmptyState.jsx'
import { ErrorFigure, EmptyData } from '../../components/illustrations'
import { errorRequestId } from '../app/appShared.jsx'
import '../../components/app/appPatterns.css'

// ---------------------------------------------------------------------------
// Cards: a hairline panel with a 2px ink rule on top
// ---------------------------------------------------------------------------

export function AdminCard({ title = undefined, description = undefined, children, className = '' }) {
  return (
    <section className={`ap-panel overflow-hidden ${className}`}>
      {(title || description) && (
        <div className="ap-panel-head">
          {title && <h3 className="ap-panel-title">{title}</h3>}
          {description && <p className="ap-panel-sub">{description}</p>}
        </div>
      )}
      {children}
    </section>
  )
}

export function StatCard({ icon = undefined, label, value, testId = undefined }) {
  const Icon = icon
  return (
    <div data-testid={testId} className="ap-stat">
      <div className="ap-stat-label flex items-center gap-1.5">
        {Icon && <Icon size={13} aria-hidden="true" className="shrink-0" />}
        <span className="truncate">{label}</span>
      </div>
      <div className="ap-stat-value">{typeof value === 'number' ? value.toLocaleString() : (value ?? '—')}</div>
    </div>
  )
}

/** Skeleton placeholder for a StatCard, sized to match — avoids layout jump. */
export function StatCardSkeleton() {
  return (
    <div className="ap-stat" aria-hidden="true">
      <Skeleton className="h-3 w-16" />
      <Skeleton className="mt-3 h-7 w-12" />
    </div>
  )
}

// ---------------------------------------------------------------------------
// States
// ---------------------------------------------------------------------------

export function LoadingState({ label = 'Loading' }) {
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted" role="status">
      <Loader2 size={16} className="animate-spin" aria-hidden="true" />
      {label}
    </div>
  )
}

export function ErrorState({ message = 'Failed to load data.', onRetry = undefined, error = undefined }) {
  return (
    <EmptyStateBase
      kind="error"
      compact
      art={<ErrorFigure />}
      title="This didn’t load"
      description={`${message} Nothing was changed.`}
      requestId={errorRequestId(error)}
      action={onRetry ? (
        <Button variant="secondary" onClick={onRetry}><RefreshCw size={14} aria-hidden="true" /> Retry</Button>
      ) : undefined}
    />
  )
}

export function EmptyState({ message = 'Nothing here yet.', title = undefined }) {
  return (
    <EmptyStateBase compact art={<EmptyData />} title={title ?? message} description={title ? message : undefined} />
  )
}

// ---------------------------------------------------------------------------
// Table loading skeleton — used instead of the generic spinner when a page
// already knows its column count, so the loading state doesn't cause a
// layout jump once real rows arrive.
// ---------------------------------------------------------------------------

export function AdminTableSkeleton({ columns = 5, rows = 8 }) {
  return (
    <div className="px-4 py-3" aria-hidden="true">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex h-9 items-center gap-4 border-b border-border">
          {Array.from({ length: columns }).map((__, c) => (
            <Skeleton key={c} className="h-3" style={{ width: c === 0 ? '22%' : `${Math.max(8, 16 - c * 2)}%` }} />
          ))}
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Table: 36px rows, sticky header with a 2px ink rule, hover wash. Cells keep
// their own padding (px-4 py-3); this only supplies the rules and header.
// ---------------------------------------------------------------------------

export function AdminTable({ headers, children }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr style={{ borderBottom: '2px solid var(--ink)' }}>
            {headers.map((h) => (
              <th key={h} scope="col" className="t-label whitespace-nowrap bg-surface px-4 py-2 text-left font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border [&>tr:hover]:bg-frame-hover">{children}</tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Search + pagination toolbar
// ---------------------------------------------------------------------------

export function SearchInput({ value, onChange, placeholder = 'Search' }) {
  return (
    <div className="relative max-w-sm flex-1">
      <Input
        type="search"
        size="sm"
        icon={<SearchGlyph />}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
      />
    </div>
  )
}

function SearchGlyph() {
  // lucide's Search at 14px, inlined so the Input icon slot stays a plain node
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="square" aria-hidden="true">
      <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
    </svg>
  )
}

export function Pagination({ offset, limit, total, onPage }) {
  const page = Math.floor(offset / limit) + 1
  const pages = Math.max(1, Math.ceil(total / limit))
  return (
    <div className="flex items-center gap-2">
      <span className="ap-meta">
        {total === 0 ? '0' : `${offset + 1}–${Math.min(offset + limit, total)}`} of {total.toLocaleString()}
      </span>
      <Button variant="secondary" size="icon-xs" onClick={() => onPage(Math.max(0, offset - limit))} disabled={offset === 0} aria-label="Previous page">
        <ChevronLeft size={14} aria-hidden="true" />
      </Button>
      <span className="ap-meta">{page}/{pages}</span>
      <Button variant="secondary" size="icon-xs" onClick={() => onPage(offset + limit)} disabled={offset + limit >= total} aria-label="Next page">
        <ChevronRight size={14} aria-hidden="true" />
      </Button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Misc bits
// ---------------------------------------------------------------------------

export function Avatar({ label = undefined, icon = undefined, iconSize = 13, className = '' }) {
  const Icon = icon
  // A person's initial is an avatar (round). An org/entity glyph is a hairline square.
  const round = !Icon
  return (
    <div
      className={`flex h-7 w-7 shrink-0 items-center justify-center border border-frame-line bg-surface-2 text-[11px] font-semibold text-muted ${round ? 'rounded-full' : ''} ${className}`}
      style={round ? undefined : { borderRadius: 2 }}
      aria-hidden="true"
    >
      {Icon ? <Icon size={iconSize} strokeWidth={1.75} /> : (label || '?').trim().charAt(0).toUpperCase()}
    </div>
  )
}

export function RoleChip({ children }) {
  return <Tag caps={false}>{children}</Tag>
}

export function SuperadminBadge() {
  return <Tag variant="accent">superadmin</Tag>
}

// ---------------------------------------------------------------------------
// Form bits — mirrors the settings pages' SettingsUI conventions so admin
// forms (billing overrides) read as the same system as the rest of the app.
// ---------------------------------------------------------------------------

/** Class for a bare <input>/<select>/<textarea> in an admin form. */
export const inputCls = 'nubi-input nubi-input-sm'

/**
 * FieldRow — label + description on the left, control on the right on wide
 * screens; stacks to one column on mobile. Meant for a set of siblings
 * inside an <AdminCard>.
 */
export function FieldRow({ label = undefined, htmlFor = undefined, description = undefined, hint = undefined, children }) {
  return (
    <div className="grid grid-cols-1 gap-x-6 gap-y-2 border-b border-border py-4 first:pt-0 last:border-b-0 last:pb-0 lg:grid-cols-[minmax(0,13rem)_1fr]">
      <div className="min-w-0">
        {label && (
          <label className="nubi-label" htmlFor={htmlFor}>{label}</label>
        )}
        {description && <p className="mt-1 mb-0 text-xs leading-relaxed text-muted">{description}</p>}
      </div>
      <div className="min-w-0 space-y-1.5">
        {children}
        {hint && <p className="nubi-hint m-0">{hint}</p>}
      </div>
    </div>
  )
}

/** Toggle — a switch (role=switch) with an optional label + description. */
export function Toggle({ checked, onChange, disabled = false, id = undefined, label = undefined, description = undefined }) {
  return (
    <label className="flex cursor-pointer select-none items-start gap-3" htmlFor={id}>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => !disabled && onChange?.(!checked)}
        className="ap-switch mt-0.5 disabled:opacity-50"
      >
        <span className="ap-switch-knob" />
      </button>
      {(label || description) && (
        <span>
          {label && <span className="block text-sm font-medium text-fg">{label}</span>}
          {description && <span className="mt-0.5 block text-xs leading-relaxed text-muted">{description}</span>}
        </span>
      )}
    </label>
  )
}

/** Primary save button: the one ink primary. */
export function SaveButton({ busy = false, children, className = '', ...props }) {
  return (
    <Button type="button" {...props} loading={busy} className={className}>
      {children}
    </Button>
  )
}

export function SavedBadge({ show, label = 'Saved' }) {
  if (!show) return null
  return (
    <span className="inline-flex items-center gap-1.5 text-sm" role="status" style={{ color: 'var(--success-fg)' }}>
      <CheckCircle2 size={14} aria-hidden="true" />
      {label}
    </span>
  )
}

export function ErrorText({ children }) {
  if (!children) return null
  return (
    <p className="nubi-field-error m-0 inline-flex items-center gap-1.5 text-sm" role="alert">
      {children}
    </p>
  )
}

/** A one-line "nothing here" for small panels (the full EmptyState is for whole pages). */
function InlineEmpty({ children }) {
  return <p className="m-0 flex h-24 items-center justify-center px-4 text-sm text-muted">{children}</p>
}

// ---------------------------------------------------------------------------
// Bars — compact CSS bar charts for {label, count} series (no chart deps)
// ---------------------------------------------------------------------------

/**
 * Vertical mini bar chart for a daily series [{ day, count }].
 * Pure CSS — intentionally avoids pulling echarts into the admin bundle.
 * Bars are the one accent; the last bar is ink so "today" reads first.
 */
export function SparkBars({ series = [], ariaLabel }) {
  const max = Math.max(1, ...series.map((d) => d.count))
  const totalCount = series.reduce((s, d) => s + d.count, 0)
  if (series.length === 0) return <InlineEmpty>No data yet.</InlineEmpty>
  return (
    <div aria-label={ariaLabel} role="img" className="px-4 py-4">
      <div className="flex h-24 items-end gap-[3px]">
        {series.map((d, i) => (
          <div
            key={d.day}
            title={`${d.day}: ${d.count}`}
            className="min-w-[3px] flex-1"
            style={{
              height: `${Math.max(2, Math.round((d.count / max) * 100))}%`,
              background: i === series.length - 1 ? 'var(--ink)' : 'var(--accent)',
              opacity: i === series.length - 1 ? 1 : 0.75,
            }}
          />
        ))}
      </div>
      <div className="mt-2 flex items-center justify-between ap-meta">
        <span>{series[0]?.day}</span>
        <span style={{ color: 'var(--text)' }}>{totalCount.toLocaleString()} total</span>
        <span>{series[series.length - 1]?.day}</span>
      </div>
    </div>
  )
}

/** Horizontal label + proportional bar + count list (e.g. countries). */
export function BarList({ items = [], labelKey = 'label', countKey = 'count' }) {
  const max = Math.max(1, ...items.map((it) => it[countKey]))
  if (items.length === 0) return <InlineEmpty>No data yet.</InlineEmpty>
  return (
    <ul className="m-0 list-none space-y-2.5 px-4 py-4">
      {items.map((it) => (
        <li key={it[labelKey]} className="flex items-center gap-3">
          <span className="w-28 shrink-0 truncate text-sm text-fg">{it[labelKey]}</span>
          <div className="h-2 flex-1 bg-surface-2">
            <div className="h-full" style={{ width: `${Math.max(2, Math.round((it[countKey] / max) * 100))}%`, background: 'var(--accent)' }} />
          </div>
          <span className="ap-num w-12 shrink-0 text-sm text-muted">{it[countKey].toLocaleString()}</span>
        </li>
      ))}
    </ul>
  )
}
