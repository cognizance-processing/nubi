/**
 * AdminUI — small presentational building blocks shared by the /admin pages.
 *
 * Follows the app design system (bg-surface / border-border / text-fg /
 * text-muted / primary) and the card + table patterns used by the settings
 * pages. Purely presentational — no data fetching.
 */

import { Loader2, AlertTriangle, CheckCircle2, Search, ChevronLeft, ChevronRight } from 'lucide-react'

// ---------------------------------------------------------------------------
// Cards
// ---------------------------------------------------------------------------

export function AdminCard({ title = undefined, description = undefined, children, className = '' }) {
  return (
    <section className={`rounded-2xl border border-border bg-surface overflow-hidden ${className}`}>
      {(title || description) && (
        <div className="px-5 pt-4 pb-3 border-b border-border">
          {title && <h3 className="font-display font-semibold text-sm text-fg">{title}</h3>}
          {description && <p className="text-xs text-muted mt-0.5">{description}</p>}
        </div>
      )}
      {children}
    </section>
  )
}

export function StatCard({ icon = undefined, label, value, testId = undefined }) {
  const Icon = icon
  return (
    <div
      data-testid={testId}
      className="flex flex-col gap-2.5 p-4 rounded-2xl border border-border bg-surface transition-colors hover:border-primary/30"
    >
      <div className="flex items-center gap-2 text-muted">
        {Icon && (
          <span className="flex items-center justify-center w-6 h-6 rounded-lg bg-surface-2 shrink-0">
            <Icon size={13} />
          </span>
        )}
        <span className="text-xs font-medium uppercase tracking-wider truncate">{label}</span>
      </div>
      <div className="font-display font-semibold text-2xl text-fg tabular-nums leading-none">
        {value ?? '—'}
      </div>
    </div>
  )
}

/** Skeleton placeholder for a StatCard, sized to match — avoids layout jump. */
export function StatCardSkeleton() {
  return (
    <div className="flex flex-col gap-3 p-4 rounded-2xl border border-border bg-surface animate-pulse" aria-hidden="true">
      <div className="flex items-center gap-2">
        <div className="w-6 h-6 rounded-lg bg-surface-2" />
        <div className="h-2.5 w-14 rounded-full bg-surface-2" />
      </div>
      <div className="h-6 w-12 rounded-full bg-surface-2" />
    </div>
  )
}

// ---------------------------------------------------------------------------
// States
// ---------------------------------------------------------------------------

export function LoadingState({ label = 'Loading…' }) {
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted">
      <Loader2 size={16} className="animate-spin" />
      {label}
    </div>
  )
}

export function ErrorState({ message = 'Failed to load data.', onRetry = undefined }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
      <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-danger-bg">
        <AlertTriangle size={18} className="text-danger" />
      </div>
      <p className="text-sm text-muted">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="px-3 py-1.5 rounded-lg text-sm font-medium border border-border text-fg hover:bg-surface-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring transition-colors"
        >
          Retry
        </button>
      )}
    </div>
  )
}

export function EmptyState({ message = 'Nothing here yet.' }) {
  return <p className="py-10 text-center text-sm text-muted">{message}</p>
}

// ---------------------------------------------------------------------------
// Table loading skeleton — used instead of the generic spinner when a page
// already knows its column count, so the loading state doesn't cause a
// layout jump once real rows arrive.
// ---------------------------------------------------------------------------

export function AdminTableSkeleton({ columns = 5, rows = 8 }) {
  return (
    <div className="animate-pulse px-4 py-3 space-y-3" aria-hidden="true">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex items-center gap-4">
          {Array.from({ length: columns }).map((__, c) => (
            <div
              key={c}
              className="h-3 rounded-full bg-surface-2"
              style={{ width: c === 0 ? '22%' : `${Math.max(8, 16 - c * 2)}%` }}
            />
          ))}
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Table
// ---------------------------------------------------------------------------

export function AdminTable({ headers, children }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border">
            {headers.map((h) => (
              <th
                key={h}
                className="px-4 py-2.5 text-left text-xs font-medium text-muted uppercase tracking-wider whitespace-nowrap"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">{children}</tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Search + pagination toolbar
// ---------------------------------------------------------------------------

export function SearchInput({ value, onChange, placeholder = 'Search…' }) {
  return (
    <div className="relative flex-1 max-w-sm">
      <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className="w-full pl-8 pr-3 py-2 rounded-xl bg-bg border border-border text-sm text-fg
          placeholder:text-muted focus:outline-none focus:border-primary"
      />
    </div>
  )
}

export function Pagination({ offset, limit, total, onPage }) {
  const page = Math.floor(offset / limit) + 1
  const pages = Math.max(1, Math.ceil(total / limit))
  return (
    <div className="flex items-center gap-2 text-xs text-muted">
      <span className="tabular-nums">
        {total === 0 ? '0' : `${offset + 1}–${Math.min(offset + limit, total)}`} of {total}
      </span>
      <button
        onClick={() => onPage(Math.max(0, offset - limit))}
        disabled={offset === 0}
        aria-label="Previous page"
        className="flex items-center justify-center w-7 h-7 rounded-lg border border-border text-fg
          hover:bg-surface-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
      >
        <ChevronLeft size={14} />
      </button>
      <span className="tabular-nums">{page}/{pages}</span>
      <button
        onClick={() => onPage(offset + limit)}
        disabled={offset + limit >= total}
        aria-label="Next page"
        className="flex items-center justify-center w-7 h-7 rounded-lg border border-border text-fg
          hover:bg-surface-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
      >
        <ChevronRight size={14} />
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Misc bits
// ---------------------------------------------------------------------------

export function Avatar({ label = undefined, icon = undefined, iconSize = 13, className = '' }) {
  const Icon = icon
  return (
    <div
      className={`flex items-center justify-center w-7 h-7 rounded-full bg-surface-2 border border-border text-[11px] font-semibold text-muted shrink-0 ${className}`}
      aria-hidden="true"
    >
      {Icon ? <Icon size={iconSize} /> : (label || '?').trim().charAt(0).toUpperCase()}
    </div>
  )
}

export function RoleChip({ children }) {
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium bg-surface-2 text-muted">
      {children}
    </span>
  )
}

export function SuperadminBadge() {
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium bg-primary/10 text-primary">
      superadmin
    </span>
  )
}

// ---------------------------------------------------------------------------
// Form bits — mirrors the settings pages' SettingsUI.jsx conventions
// (Field/FieldRow/Toggle/PrimaryButton/SavedBadge/ErrorText) so admin forms
// (billing overrides) read as the same system as the rest of the app.
// ---------------------------------------------------------------------------

export const inputCls =
  'rounded-lg bg-bg border border-border text-sm text-fg px-2.5 py-1.5 ' +
  'focus:outline-none focus:ring-2 focus:ring-ring/50 focus:border-primary placeholder:text-muted transition-[border-color,box-shadow] duration-100'

/**
 * FieldRow — label + description on the left, control on the right on wide
 * screens; stacks to one column on mobile. Meant for a set of siblings
 * inside an <AdminCard>.
 */
export function FieldRow({ label = undefined, htmlFor = undefined, description = undefined, hint = undefined, children }) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,13rem)_1fr] gap-x-6 gap-y-2 py-4 first:pt-0 last:pb-0">
      <div className="min-w-0">
        {label && (
          <label className="block text-sm font-medium text-fg" htmlFor={htmlFor}>
            {label}
          </label>
        )}
        {description && <p className="text-xs text-muted mt-1 leading-relaxed">{description}</p>}
      </div>
      <div className="min-w-0 space-y-1.5">
        {children}
        {hint && <p className="text-xs text-muted leading-relaxed">{hint}</p>}
      </div>
    </div>
  )
}

/** Toggle — native checkbox styled as an accessible switch (.nubi-switch). */
export function Toggle({ checked, onChange, disabled = false, id = undefined, label = undefined, description = undefined }) {
  return (
    <label className="flex items-start gap-3 cursor-pointer select-none" htmlFor={id}>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => !disabled && onChange?.(!checked)}
        className="nubi-switch mt-0.5 shrink-0"
      >
        <span className="nubi-switch-thumb" />
      </button>
      {(label || description) && (
        <span>
          {label && <span className="block text-sm font-medium text-fg">{label}</span>}
          {description && <span className="block text-xs text-muted mt-0.5 leading-relaxed">{description}</span>}
        </span>
      )}
    </label>
  )
}

/** Primary save button — brand-gradient, matches PrimaryButton in SettingsUI.jsx. */
export function SaveButton({ busy = false, children, className = '', ...props }) {
  return (
    <button
      type="button"
      {...props}
      className={[
        'inline-flex items-center justify-center gap-2 px-3.5 py-2 rounded-lg text-sm font-medium text-white',
        'transition-opacity duration-150 disabled:opacity-50 disabled:cursor-not-allowed',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
        className,
      ].join(' ')}
      style={{ background: 'linear-gradient(135deg, #2456a6, #17b3a3)' }}
    >
      {busy && <Loader2 size={14} className="animate-spin" />}
      {children}
    </button>
  )
}

export function SavedBadge({ show, label = 'Saved' }) {
  if (!show) return null
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-success">
      <CheckCircle2 size={14} aria-hidden="true" />
      {label}
    </span>
  )
}

export function ErrorText({ children }) {
  if (!children) return null
  return (
    <p className="inline-flex items-center gap-1.5 text-sm text-danger" role="alert">
      <AlertTriangle size={14} aria-hidden="true" className="shrink-0" />
      {children}
    </p>
  )
}

// ---------------------------------------------------------------------------
// BarList — compact CSS bar chart for {label, count} series (no chart deps)
// ---------------------------------------------------------------------------

/**
 * Vertical mini bar chart for a daily series [{ day, count }].
 * Pure CSS — intentionally avoids pulling echarts into the admin bundle.
 */
export function SparkBars({ series = [], ariaLabel }) {
  const max = Math.max(1, ...series.map((d) => d.count))
  const totalCount = series.reduce((s, d) => s + d.count, 0)
  if (series.length === 0) return <EmptyState message="No data yet." />
  return (
    <div aria-label={ariaLabel} role="img" className="px-5 py-4">
      <div className="flex items-end gap-[3px] h-24">
        {series.map((d) => (
          <div
            key={d.day}
            title={`${d.day}: ${d.count}`}
            className="flex-1 min-w-[3px] rounded-t-sm bg-primary/70 hover:bg-primary transition-colors"
            style={{ height: `${Math.max(2, Math.round((d.count / max) * 100))}%` }}
          />
        ))}
      </div>
      <div className="flex items-center justify-between mt-2 text-[11px] text-muted tabular-nums">
        <span>{series[0]?.day}</span>
        <span className="font-medium text-fg">{totalCount} total</span>
        <span>{series[series.length - 1]?.day}</span>
      </div>
    </div>
  )
}

/** Horizontal label + proportional bar + count list (e.g. countries). */
export function BarList({ items = [], labelKey = 'label', countKey = 'count' }) {
  const max = Math.max(1, ...items.map((it) => it[countKey]))
  if (items.length === 0) return <EmptyState message="No data yet." />
  return (
    <ul className="px-5 py-4 space-y-2.5">
      {items.map((it) => (
        <li key={it[labelKey]} className="flex items-center gap-3">
          <span className="w-28 shrink-0 truncate text-sm text-fg">{it[labelKey]}</span>
          <div className="flex-1 h-2 rounded-full bg-surface-2 overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-brand-blue to-brand-teal"
              style={{ width: `${Math.max(2, Math.round((it[countKey] / max) * 100))}%` }}
            />
          </div>
          <span className="w-10 shrink-0 text-right text-sm text-muted tabular-nums">{it[countKey]}</span>
        </li>
      ))}
    </ul>
  )
}
