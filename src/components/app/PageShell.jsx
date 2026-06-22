/**
 * PageShell — reusable app-page layout primitives.
 *
 * Exports:
 *   PageRoot        — scrollable page container with consistent padding
 *   PageHeader      — title + subtitle + optional CTA slot
 *   Toolbar         — search + sort + view-toggle row
 *   SearchBar       — search input with icon + clear button
 *   SortMenu        — sort dropdown with keyboard nav
 *   ViewToggle      — grid / list icon toggle
 *   CardGrid        — responsive 1→2→3 column card grid
 *   ListWrap        — bordered list container
 *   ListHeader      — table-like header inside ListWrap
 *   ListRow         — single row inside ListWrap
 *   NoticeBanner    — success / error inline banner
 *   SelectionBar    — bulk-selection action bar
 *   ErrorState      — centred error with retry CTA
 *   CardSkeleton    — shimmer card placeholder
 *   ListRowSkeleton — shimmer list row placeholder
 *   DropdownMenu    — shared dropdown chrome + items
 *
 * All components use the .nubi-* CSS classes added to index.css,
 * keeping JSX free of repetitive Tailwind utility strings.
 */

import { useEffect, useRef, useState } from 'react'
import { ChevronDown, LayoutGrid, List, Search, X } from 'lucide-react'

function cx(...parts) {
  return parts.filter(Boolean).join(' ')
}

// ── Page root ────────────────────────────────────────────────────────────────

export function PageRoot({ className, children, ...rest }) {
  return (
    <div className={cx('nubi-page', className)} {...rest}>
      {children}
    </div>
  )
}

// ── Page header ──────────────────────────────────────────────────────────────

export function PageHeader({ title, subtitle, children, className }) {
  return (
    <div className={cx('nubi-page-header', className)}>
      <div>
        <h1 className="nubi-page-title">{title}</h1>
        {subtitle && <p className="nubi-page-subtitle">{subtitle}</p>}
      </div>
      {children && (
        <div className="shrink-0 self-start sm:self-auto">
          {children}
        </div>
      )}
    </div>
  )
}

// ── Toolbar ──────────────────────────────────────────────────────────────────

export function Toolbar({ className, children }) {
  return (
    <div className={cx('nubi-toolbar', className)}>
      {children}
    </div>
  )
}

// ── Search bar ───────────────────────────────────────────────────────────────

export function SearchBar({ value, onChange, placeholder = 'Search…', className }) {
  return (
    <div className={cx('nubi-search-wrap', className)}>
      <Search size={14} className="nubi-search-icon" aria-hidden="true" />
      <input
        type="search"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="nubi-search-input"
        aria-label={placeholder}
      />
      {value && (
        <button
          onClick={() => onChange('')}
          className="nubi-search-clear"
          aria-label="Clear search"
          type="button"
        >
          <X size={12} />
        </button>
      )}
    </div>
  )
}

// ── Sort menu ─────────────────────────────────────────────────────────────────

/**
 * @param {{ value: string, onChange: fn, options: Array<{value,label}>, label?: string }} props
 */
export function SortMenu({ value, onChange, options = [], label }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    function handle(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handle)
    return () => document.removeEventListener('mousedown', handle)
  }, [open])

  const current = options.find(o => o.value === value)?.label ?? label ?? value

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="nubi-sort-btn"
        aria-label={`Sort by ${current}`}
        aria-expanded={open}
        aria-haspopup="listbox"
      >
        {current}
        <ChevronDown
          size={13}
          className="text-muted"
          style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 150ms' }}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div className="nubi-sort-menu" role="listbox" aria-label="Sort options">
          {options.map(opt => (
            <button
              key={opt.value}
              role="option"
              aria-selected={value === opt.value}
              type="button"
              onClick={() => { onChange(opt.value); setOpen(false) }}
              className={cx('nubi-sort-option', value === opt.value && 'active')}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── View toggle ───────────────────────────────────────────────────────────────

const VIEW_OPTS = [
  { id: 'grid', Icon: LayoutGrid, label: 'Grid view' },
  { id: 'list', Icon: List,       label: 'List view' },
]

export function ViewToggle({ value, onChange, className }) {
  return (
    <div className={cx('nubi-view-toggle', className)} role="toolbar" aria-label="View options">
      {VIEW_OPTS.map(v => (
        <button
          key={v.id}
          type="button"
          onClick={() => onChange(v.id)}
          title={v.label}
          aria-label={v.label}
          aria-pressed={value === v.id}
          data-testid={`view-toggle-${v.id}`}
          className={cx('nubi-view-btn', value === v.id && 'active')}
        >
          <v.Icon size={14} aria-hidden="true" />
        </button>
      ))}
    </div>
  )
}

// ── Card grid ─────────────────────────────────────────────────────────────────

export function CardGrid({ className, children }) {
  return (
    <div className={cx('nubi-card-grid', className)}>
      {children}
    </div>
  )
}

// ── List wrap + header + row ──────────────────────────────────────────────────

export function ListWrap({ className, children }) {
  return (
    <div className={cx('nubi-list-wrap', className)}>
      {children}
    </div>
  )
}

export function ListHeader({ className, children }) {
  return (
    <div className={cx('nubi-list-header', className)}>
      {children}
    </div>
  )
}

export function ListHeaderLabel({ children }) {
  return <span className="nubi-list-header-label flex-1">{children}</span>
}

export function ListRow({ selected, className, children, ...rest }) {
  return (
    <div
      className={cx('nubi-list-row', selected && 'selected', className)}
      {...rest}
    >
      {children}
    </div>
  )
}

// ── Notice banner ─────────────────────────────────────────────────────────────

export function NoticeBanner({ variant = 'success', icon, onDismiss, children }) {
  return (
    <div
      className={cx(
        'nubi-notice',
        variant === 'success' ? 'nubi-notice-success' : 'nubi-notice-error',
      )}
      role="status"
    >
      {icon && <span className="shrink-0">{icon}</span>}
      <span className="flex-1">{children}</span>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="shrink-0 p-0.5 rounded hover:opacity-70 transition-opacity"
        >
          <X size={13} />
        </button>
      )}
    </div>
  )
}

// ── Selection bar ─────────────────────────────────────────────────────────────

export function SelectionBar({ className, children }) {
  return (
    <div className={cx('nubi-selection-bar', className)} role="toolbar" aria-label="Bulk actions">
      {children}
    </div>
  )
}

// ── Error state ───────────────────────────────────────────────────────────────

export function ErrorState({ icon, title = 'Something went wrong', message, onRetry }) {
  return (
    <div className="nubi-error-state">
      {icon && (
        <div className="nubi-error-icon" aria-hidden="true">
          {icon}
        </div>
      )}
      <div>
        <p className="font-display font-semibold text-lg text-fg mb-1">{title}</p>
        {message && (
          <p className="text-muted text-sm max-w-xs leading-relaxed">{message}</p>
        )}
      </div>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex items-center gap-2 h-9 px-5 rounded-xl bg-primary text-primary-fg text-sm font-medium hover:opacity-90 transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring mt-1"
        >
          Try again
        </button>
      )}
    </div>
  )
}

// ── Skeleton primitives ───────────────────────────────────────────────────────

/**
 * CardSkeleton — shimmer placeholder matching the resource card layout.
 * @param {{ thumbHeight?: string }} props
 */
export function CardSkeleton({ thumbHeight = 'h-28' }) {
  return (
    <div className="flex flex-col rounded-nubi-lg border border-border bg-surface overflow-hidden" aria-hidden="true">
      <div className={cx('nubi-shimmer w-full', thumbHeight)} />
      <div className="p-4 flex flex-col gap-3">
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1 space-y-2">
            <div className="nubi-shimmer h-4 w-3/4 rounded" />
            <div className="nubi-shimmer h-3 w-1/3 rounded" />
          </div>
          <div className="nubi-shimmer h-8 w-8 rounded-lg" />
        </div>
        <div className="flex gap-2 mt-1">
          <div className="nubi-shimmer h-9 flex-1 rounded-lg" />
          <div className="nubi-shimmer h-9 flex-1 rounded-lg" />
        </div>
      </div>
    </div>
  )
}

/**
 * ListRowSkeleton — shimmer placeholder matching list rows.
 */
export function ListRowSkeleton() {
  return (
    <div className="nubi-list-row" aria-hidden="true">
      <div className="nubi-shimmer w-8 h-8 rounded-lg shrink-0" />
      <div className="flex-1 space-y-2">
        <div className="nubi-shimmer h-3.5 w-1/2 rounded" />
        <div className="nubi-shimmer h-3 w-1/4 rounded" />
      </div>
      <div className="nubi-shimmer h-3 w-20 rounded hidden md:block" />
      <div className="nubi-shimmer w-8 h-8 rounded-lg shrink-0" />
    </div>
  )
}

// ── Dropdown menu ─────────────────────────────────────────────────────────────

/**
 * DropdownMenu — renders a floating list of actions anchored to a trigger.
 *
 * Props:
 *   open       boolean
 *   onClose    fn
 *   align      'right' | 'left'  (default 'right')
 *   className
 *   children   <DropdownItem> | <DropdownDivider>
 */
export function DropdownMenu({ open, onClose, align = 'right', className, children }) {
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    function handle(e) {
      if (ref.current && !ref.current.contains(e.target)) onClose?.()
    }
    document.addEventListener('mousedown', handle)
    return () => document.removeEventListener('mousedown', handle)
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      ref={ref}
      className={cx(
        'nubi-dropdown',
        align === 'left' ? 'left-0' : 'right-0',
        'top-[calc(100%+6px)]',
        className,
      )}
      role="menu"
    >
      {children}
    </div>
  )
}

export function DropdownItem({ icon: Icon, onClick, danger, children, className, ...rest }) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className={cx('nubi-dropdown-item', danger && 'danger', className)}
      {...rest}
    >
      {Icon && <Icon size={14} className="shrink-0 text-muted" aria-hidden="true" />}
      {children}
    </button>
  )
}

export function DropdownDivider() {
  return <div className="nubi-dropdown-divider" role="separator" />
}
