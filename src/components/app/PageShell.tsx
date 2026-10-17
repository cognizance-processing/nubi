/**
 * PageShell — the app page template + reusable page primitives.
 *
 * New (Swiss shell):
 *   PageShell       — eyebrow, h1, description, actions, tabs, toolbar, width. THE page template.
 *   PageSection     — 2px ink rule + mono index + title + actions, for long pages / settings groups
 *   TopbarPortal    — render a page toolbar inside the topbar's page-actions slot
 *   usePageCrumb    — label the last breadcrumb segment on a dynamic route
 *
 * Existing (unchanged signatures; PageRoot/PageHeader now paint the Swiss header):
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
 * Full API + migration notes: uiteam/reports/shell-pageshell.md
 *
 * Content sits directly on --surface (the sheet). Page roots must not paint
 * bg-bg / bg-surface-2 — that grey wash made the sheet read as two surfaces.
 */

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Link, useLocation } from 'react-router-dom'
import { ChevronDown, LayoutGrid, List, Search, X } from 'lucide-react'
import { useUi } from '../../contexts/UiContext.jsx'
import Button from '../ui/Button.jsx'
import '../../shell/shell.css'

function cx(...parts) {
  return parts.filter(Boolean).join(' ')
}

// ── PageShell ────────────────────────────────────────────────────────────────

export interface PageTab {
  id: string
  label: string
  count?: number | string
  /** Router link target. Takes precedence over onSelect. */
  href?: string
  onSelect?: () => void
  disabled?: boolean
}

export interface PageShellProps {
  /** Mono label above the title, e.g. "11 DASHBOARDS". */
  eyebrow?: string
  /** The single visible h1. */
  title: string
  description?: ReactNode
  /** Right-aligned header actions. At most one ink primary. */
  actions?: ReactNode
  tabs?: PageTab[]
  activeTab?: string
  /** Search / filter / sort row under the header. */
  toolbar?: ReactNode
  /** default = 1200px max, narrow = 720px (settings / forms), full = no max. */
  width?: 'full' | 'default' | 'narrow'
  /** Label for the last breadcrumb segment on dynamic routes. */
  crumb?: string
  /** Body fills the viewport height (split panes, tables with their own scroll). */
  fill?: boolean
  className?: string
  children?: ReactNode
}

/** Label the last breadcrumb segment (dynamic routes: a board or query name). Cleared on unmount. */
export function usePageCrumb(label: string | null | undefined) {
  const { setCrumbLabel } = useUi()
  useEffect(() => {
    setCrumbLabel(label || null)
    return () => setCrumbLabel(null)
  }, [label, setCrumbLabel])
}

function PageTabs({ tabs, activeTab }: { tabs: PageTab[]; activeTab?: string }) {
  const { pathname } = useLocation()
  const listRef = useRef<HTMLDivElement>(null)
  const active = activeTab ?? tabs.find(t => t.href && (t.href === pathname))?.id

  // Arrow keys move focus between tabs (roving focus; Tab still reaches every tab).
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(e.key)) return
    const els = Array.from(listRef.current?.querySelectorAll<HTMLElement>('[role="tab"]:not([aria-disabled="true"])') ?? [])
    if (!els.length) return
    const i = els.indexOf(document.activeElement as HTMLElement)
    let next = i
    if (e.key === 'ArrowRight') next = (i + 1) % els.length
    else if (e.key === 'ArrowLeft') next = (i - 1 + els.length) % els.length
    else if (e.key === 'Home') next = 0
    else next = els.length - 1
    e.preventDefault()
    els[next].focus()
  }

  // Tabs that change the route are navigation, not an ARIA tab widget: expose them as
  // links in a <nav> with aria-current, so assistive tech announces where they go.
  if (tabs.every(t => t.href)) {
    return (
      <nav aria-label="Sections" className="sh-tabs">
        {tabs.map(t => {
          const cls = cx('sh-tab', active === t.id && 'is-active')
          const body = (
            <>
              {t.label}
              {t.count != null && <span className="sh-tab-count">{t.count}</span>}
            </>
          )
          return t.disabled ? (
            <span key={t.id} className={cls} aria-disabled="true">{body}</span>
          ) : (
            <Link key={t.id} to={t.href!} className={cls} aria-current={active === t.id ? 'page' : undefined}>{body}</Link>
          )
        })}
      </nav>
    )
  }

  return (
    <div ref={listRef} role="tablist" className="sh-tabs" onKeyDown={onKeyDown}>
      {tabs.map(t => {
        const common = {
          role: 'tab' as const,
          'aria-selected': active === t.id,
          'aria-disabled': t.disabled || undefined,
          className: cx('sh-tab', active === t.id && 'is-active'),
        }
        const body = (
          <>
            {t.label}
            {t.count != null && <span className="sh-tab-count">{t.count}</span>}
          </>
        )
        return t.href && !t.disabled ? (
          <Link key={t.id} to={t.href} {...common}>{body}</Link>
        ) : (
          <button key={t.id} type="button" disabled={t.disabled} onClick={t.onSelect} {...common}>{body}</button>
        )
      })}
    </div>
  )
}

/**
 * The page template. See uiteam/reports/shell-pageshell.md.
 *
 * Header anatomy: eyebrow → title + actions → description → rule (2px ink, or a hairline
 * when tabs carry the 2px active underline) → toolbar → body.
 */
export default function PageShell({
  eyebrow, title, description, actions, tabs, activeTab, toolbar,
  width = 'default', crumb, fill = false, className, children,
}: PageShellProps) {
  usePageCrumb(crumb)
  const hasTabs = Boolean(tabs?.length)
  return (
    <div className={cx('sh-page', fill && 'sh-page-fill', className)} data-width={width}>
      <div className="sh-page-inner">
        <header className={cx('sh-head', hasTabs && 'has-tabs')}>
          {eyebrow && <p className="sh-eyebrow">{eyebrow}</p>}
          <div className="sh-head-row">
            <div className="sh-head-text">
              <h1 className="sh-title">{title}</h1>
              {description && <p className="sh-desc">{description}</p>}
            </div>
            {actions && <div className="sh-head-actions">{actions}</div>}
          </div>
          {hasTabs && <PageTabs tabs={tabs!} activeTab={activeTab} />}
        </header>
        {toolbar && <div className="sh-toolbar-row">{toolbar}</div>}
        <div className={cx('sh-body', fill && 'sh-body-fill')}>{children}</div>
      </div>
    </div>
  )
}

/** A titled section: 2px ink rule, optional mono index, title, right-aligned actions. */
export function PageSection({
  index, title, description, actions, id, className, children,
}: {
  index?: string
  title: string
  description?: ReactNode
  actions?: ReactNode
  id?: string
  className?: string
  children?: ReactNode
}) {
  const headingId = id ? `${id}-heading` : undefined
  return (
    <section id={id} aria-labelledby={headingId} className={cx('sh-section', className)}>
      <div className="sh-section-head">
        {index && <span className="sh-section-index" aria-hidden="true">{index}</span>}
        <div className="sh-section-text">
          <h2 id={headingId} className="sh-section-title">{title}</h2>
          {description && <p className="sh-section-desc">{description}</p>}
        </div>
        {actions && <div className="sh-section-actions">{actions}</div>}
      </div>
      {children}
    </section>
  )
}

/** Render children inside the topbar's page-actions slot (nothing until the slot has mounted). */
export function TopbarPortal({ children }: { children?: ReactNode }) {
  const { topbarSlot } = useUi()
  return topbarSlot ? createPortal(children, topbarSlot) : null
}

// ── Page root (legacy API, Swiss paint) ──────────────────────────────────────

export function PageRoot({ className = undefined, children, ...rest }) {
  return (
    <div className={cx('nubi-page sh-surface', className)} {...rest}>
      {children}
    </div>
  )
}

// ── Page header (legacy API, Swiss paint) ────────────────────────────────────

export function PageHeader({ title, subtitle = undefined, children, className = undefined }) {
  return (
    <header className={cx('sh-head', className)}>
      <div className="sh-head-row">
        <div className="sh-head-text">
          <h1 className="sh-title">{title}</h1>
          {subtitle && <p className="sh-desc">{subtitle}</p>}
        </div>
        {children && <div className="sh-head-actions">{children}</div>}
      </div>
    </header>
  )
}

// ── Toolbar ──────────────────────────────────────────────────────────────────

export function Toolbar({ className = undefined, children }) {
  return (
    <div className={cx('nubi-toolbar', className)}>
      {children}
    </div>
  )
}

// ── Search bar ───────────────────────────────────────────────────────────────

export function SearchBar({ value, onChange, placeholder = 'Search…', className = undefined }) {
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
export function SortMenu({ value, onChange, options = [], label = undefined }) {
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

export function ViewToggle({ value, onChange, className = undefined }) {
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

export function CardGrid({ className = undefined, children }) {
  return (
    <div className={cx('nubi-card-grid', className)}>
      {children}
    </div>
  )
}

// ── List wrap + header + row ──────────────────────────────────────────────────

export function ListWrap({ className = undefined, children }) {
  return (
    <div className={cx('nubi-list-wrap', className)}>
      {children}
    </div>
  )
}

export function ListHeader({ className = undefined, children }) {
  return (
    <div className={cx('nubi-list-header', className)}>
      {children}
    </div>
  )
}

export function ListHeaderLabel({ children }) {
  return <span className="nubi-list-header-label flex-1">{children}</span>
}

export function ListRow({ selected = undefined, className = undefined, children, ...rest }) {
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

export function NoticeBanner({ variant = 'success', icon = undefined, onDismiss = undefined, children }) {
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

export function SelectionBar({ className = undefined, children }) {
  return (
    <div className={cx('nubi-selection-bar', className)} role="toolbar" aria-label="Bulk actions">
      {children}
    </div>
  )
}

// ── Error state ───────────────────────────────────────────────────────────────

export function ErrorState({ icon = undefined, title = 'Something went wrong', message = undefined, onRetry = undefined }) {
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
        <Button type="button" onClick={onRetry} className="mt-1">
          Try again
        </Button>
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
export function DropdownMenu({ open, onClose, align = 'right', className = undefined, children }) {
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

export function DropdownItem({ icon: Icon = undefined, onClick, danger = false, children, className = undefined, ...rest }) {
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
