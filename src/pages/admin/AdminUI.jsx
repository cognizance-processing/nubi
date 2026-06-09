/**
 * AdminUI — small presentational building blocks shared by the /admin pages.
 *
 * Follows the app design system (bg-surface / border-border / text-fg /
 * text-muted / primary) and the card + table patterns used by the settings
 * pages. Purely presentational — no data fetching.
 */

import { Loader2, AlertTriangle, Search, ChevronLeft, ChevronRight } from 'lucide-react'

// ---------------------------------------------------------------------------
// Cards
// ---------------------------------------------------------------------------

export function AdminCard({ title, description, children, className = '' }) {
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

export function StatCard({ icon, label, value, testId }) {
  const Icon = icon
  return (
    <div
      data-testid={testId}
      className="flex flex-col gap-2.5 p-4 rounded-2xl border border-border bg-surface"
    >
      <div className="flex items-center gap-2 text-muted">
        {Icon && <Icon size={14} />}
        <span className="text-xs font-medium uppercase tracking-wider">{label}</span>
      </div>
      <div className="font-display font-semibold text-2xl text-fg tabular-nums leading-none">
        {value ?? '—'}
      </div>
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

export function ErrorState({ message = 'Failed to load data.', onRetry }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
      <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-red-500/10">
        <AlertTriangle size={18} className="text-red-500" />
      </div>
      <p className="text-sm text-muted">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="px-3 py-1.5 rounded-lg text-sm font-medium border border-border text-fg hover:bg-surface-2 transition-colors"
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
          hover:bg-surface-2 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
      >
        <ChevronLeft size={14} />
      </button>
      <span className="tabular-nums">{page}/{pages}</span>
      <button
        onClick={() => onPage(offset + limit)}
        disabled={offset + limit >= total}
        aria-label="Next page"
        className="flex items-center justify-center w-7 h-7 rounded-lg border border-border text-fg
          hover:bg-surface-2 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
      >
        <ChevronRight size={14} />
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Misc bits
// ---------------------------------------------------------------------------

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
