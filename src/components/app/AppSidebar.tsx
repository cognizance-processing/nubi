/**
 * AppSidebar — primary navigation for the authenticated app shell.
 *
 * Information architecture (src/shell/nav.ts is the single source):
 *
 *   [logo]                                   [collapse]
 *   [workspace switcher]
 *   [Search  ⌘K]
 *   Home
 *   DATA      Connectors · Data
 *   BUILD     Queries · Dashboards · Flows
 *   OPERATE   Watches
 *   ─────────
 *   Docs · Admin (superadmin) · Settings
 *
 * Craft rules: one 32px row height; 16px lucide icons at 1.5 stroke; active = a 2px ink marker
 * plus ink text (no fills, no pills); hover = the quiet frame tint.
 *
 * Collapse: a 56px icon rail. Each icon shows a tooltip with its label and `g`-chord shortcut.
 * Keyboard: Tab reaches every item; Arrow Up/Down, Home and End move between them.
 *
 * Mobile: an off-canvas drawer (role=dialog) that slides on --dur-3 / --ease-out, closes on Esc,
 * backdrop click and navigation, and is inert while closed.
 */

import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { PanelLeftClose, PanelLeftOpen, Search, X } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext.jsx'
import { useUi } from '../../contexts/UiContext.jsx'
import WorkspaceSwitcher from './WorkspaceSwitcher.jsx'
import Logo from '../Logo.jsx'
import { openSpotlight } from './Spotlight.jsx'
import { NAV_FOOTER, NAV_GROUPS, NAV_HOME, SPOTLIGHT_KEY, type NavEntry } from '../../shell/nav.js'
import '../../shell/shell.css'

// ---------------------------------------------------------------------------
// Rail tooltip — label + g-chord, shown to the right of a collapsed icon
// ---------------------------------------------------------------------------

type Tip = { top: number; left: number } | null

function useRailTip(enabled: boolean) {
  const [tip, setTip] = useState<Tip>(null)
  const show = useCallback((el: HTMLElement) => {
    if (!enabled) return
    const r = el.getBoundingClientRect()
    setTip({ top: r.top + r.height / 2, left: r.right + 10 })
  }, [enabled])
  const hide = useCallback(() => setTip(null), [])
  useEffect(() => { if (!enabled) setTip(null) }, [enabled])
  return { tip, show, hide }
}

function RailTip({ tip, label, chord }: { tip: Tip; label: string; chord?: string }) {
  if (!tip) return null
  return createPortal(
    <div className="sh-rail-tip" role="presentation" style={{ top: tip.top, left: tip.left }}>
      {label}
      {chord && (
        <span className="inline-flex items-center gap-1" aria-hidden="true">
          <kbd className="nubi-kbd">g</kbd>
          <kbd className="nubi-kbd">{chord}</kbd>
        </span>
      )}
    </div>,
    document.body,
  )
}

// ---------------------------------------------------------------------------
// Single nav item
// ---------------------------------------------------------------------------

function SidebarNavItem({ entry, collapsed }: { entry: NavEntry; collapsed: boolean }) {
  const { to, label, Icon, chord } = entry
  const { tip, show, hide } = useRailTip(collapsed)
  return (
    <>
      <NavLink
        to={to}
        data-nav-item
        data-collapsed={collapsed ? '' : undefined}
        aria-label={collapsed ? label : undefined}
        aria-keyshortcuts={chord ? `g ${chord}` : undefined}
        onMouseEnter={e => show(e.currentTarget)}
        onMouseLeave={hide}
        onFocus={e => show(e.currentTarget)}
        onBlur={hide}
        className={({ isActive }) => ['sh-nav-item', isActive ? 'is-active' : ''].join(' ')}
      >
        <Icon size={16} strokeWidth={1.5} aria-hidden="true" className="sh-nav-icon" />
        {!collapsed && <span className="truncate">{label}</span>}
      </NavLink>
      <RailTip tip={tip} label={label} chord={chord} />
    </>
  )
}

// ---------------------------------------------------------------------------
// Search field (opens Spotlight)
// ---------------------------------------------------------------------------

function SearchField({ collapsed, showKbd = true }: { collapsed: boolean; showKbd?: boolean }) {
  const { tip, show, hide } = useRailTip(collapsed)
  return (
    <>
      <button
        type="button"
        data-nav-item
        onClick={() => openSpotlight()}
        aria-label={`Search (${SPOTLIGHT_KEY})`}
        aria-keyshortcuts="Control+K Meta+K"
        onMouseEnter={e => show(e.currentTarget)}
        onMouseLeave={hide}
        onFocus={e => show(e.currentTarget)}
        onBlur={hide}
        className={[
          'flex items-center gap-2 border border-[var(--frame-line)] bg-surface text-[13px] text-subtle rounded-sm',
          'hover:text-fg hover:border-[var(--ink)] transition-colors',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-bg',
          collapsed ? 'w-10 h-8 justify-center mx-auto' : 'w-full h-8 px-2.5',
        ].join(' ')}
      >
        <Search size={16} strokeWidth={1.5} aria-hidden="true" className="shrink-0" />
        {!collapsed && (
          <>
            <span className="flex-1 text-left">Search</span>
            {showKbd && <kbd className="nubi-kbd" aria-hidden="true">{SPOTLIGHT_KEY}</kbd>}
          </>
        )}
      </button>
      <RailTip tip={tip} label="Search" />
    </>
  )
}

// ---------------------------------------------------------------------------
// Sidebar inner content — shared by desktop + mobile
// ---------------------------------------------------------------------------

function SidebarContent({
  collapsed,
  mode,
  onClose,
}: {
  collapsed: boolean
  mode: 'desktop' | 'mobile'
  onClose?: () => void
}) {
  const { toggleSidebar } = useUi()
  const { user } = useAuth()
  const isSuperadmin = Boolean(user?.is_superadmin)
  const rootRef = useRef<HTMLDivElement>(null)

  // Arrow-key navigation across every sidebar stop (Tab still visits them all in DOM order).
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return
    const target = e.target as HTMLElement
    if (!target.closest('[data-nav-item]')) return
    const items = Array.from(rootRef.current?.querySelectorAll<HTMLElement>('[data-nav-item]') ?? [])
    const i = items.indexOf(target.closest('[data-nav-item]') as HTMLElement)
    if (i < 0) return
    let next = i
    if (e.key === 'ArrowDown') next = Math.min(items.length - 1, i + 1)
    else if (e.key === 'ArrowUp') next = Math.max(0, i - 1)
    else if (e.key === 'Home') next = 0
    else next = items.length - 1
    e.preventDefault()
    items[next]?.focus()
  }

  const toggleLabel = collapsed ? 'Expand sidebar' : 'Collapse sidebar'

  return (
    <div ref={rootRef} onKeyDown={onKeyDown} className="flex flex-col h-full">
      {/* Logo + collapse toggle */}
      <div className={['flex items-center shrink-0 px-3 pt-3 pb-2 gap-1', collapsed ? 'flex-col' : 'justify-between'].join(' ')}>
        <Link
          to="/"
          aria-label="Nubi, back to the landing page"
          className="inline-flex items-center rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
        >
          <Logo size={24} showName={!collapsed} />
        </Link>

        {mode === 'desktop' ? (
          <button
            type="button"
            onClick={toggleSidebar}
            aria-label={toggleLabel}
            aria-keyshortcuts="["
            title={`${toggleLabel}  [`}
            className="sh-icon-btn"
            style={{ width: 28, height: 28 }}
          >
            {collapsed
              ? <PanelLeftOpen size={16} strokeWidth={1.5} aria-hidden="true" />
              : <PanelLeftClose size={16} strokeWidth={1.5} aria-hidden="true" />}
          </button>
        ) : (
          <button type="button" onClick={onClose} aria-label="Close navigation" className="sh-icon-btn" style={{ width: 28, height: 28 }}>
            <X size={16} strokeWidth={1.5} aria-hidden="true" />
          </button>
        )}
      </div>

      {/* Workspace switcher */}
      <div className={collapsed ? 'px-1 mb-2' : 'px-2 mb-2'}>
        <WorkspaceSwitcher collapsed={collapsed} />
      </div>

      {/* Spotlight */}
      <div className={collapsed ? 'px-2 mb-2' : 'px-2 mb-2'}>
        <SearchField collapsed={collapsed} showKbd={mode === 'desktop'} />
      </div>

      {/* Primary nav — scrolls internally on short screens */}
      <nav
        aria-label="Primary"
        className={['nubi-sidebar-scroll flex flex-col gap-0.5 flex-1 min-h-0 overflow-y-auto pb-1', collapsed ? 'px-2' : 'px-2'].join(' ')}
      >
        <SidebarNavItem entry={NAV_HOME} collapsed={collapsed} />

        {NAV_GROUPS.map(group => (
          <div key={group.id} role="group" aria-labelledby={collapsed ? undefined : `nav-g-${group.id}`} aria-label={collapsed ? group.label : undefined} className="flex flex-col gap-0.5">
            {collapsed ? (
              <div className="sh-nav-rule" aria-hidden="true" />
            ) : (
              <div id={`nav-g-${group.id}`} className="sh-nav-group t-label select-none">{group.label}</div>
            )}
            {group.items.map(item => (
              <SidebarNavItem key={item.to} entry={item} collapsed={collapsed} />
            ))}
          </div>
        ))}
      </nav>

      {/* Footer nav — pinned */}
      <nav aria-label="Workspace" className="flex flex-col gap-0.5 px-2 pt-2 pb-1 border-t border-[var(--frame-line)]">
        {NAV_FOOTER.filter(e => !e.adminOnly || isSuperadmin).map(item => (
          <SidebarNavItem key={item.to} entry={item} collapsed={collapsed} />
        ))}
      </nav>

      {/* Version */}
      {!collapsed && (
        <div className="px-4 pb-3 pt-1 flex items-center gap-2 font-mono text-[11px] text-subtle select-none tabular-nums">
          <span className="nubi-dot nubi-dot-success" aria-hidden="true" />
          nubi v{import.meta.env.APP_VERSION}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Desktop sidebar
// ---------------------------------------------------------------------------

export function AppSidebarDesktop() {
  const { sidebarCollapsed } = useUi()
  return (
    <aside
      className={[
        'sh-sidebar hidden md:flex flex-col shrink-0 bg-bg overflow-hidden',
        sidebarCollapsed ? 'w-[56px]' : 'w-[216px]',
      ].join(' ')}
      aria-label="Main sidebar"
    >
      <SidebarContent collapsed={sidebarCollapsed} mode="desktop" />
    </aside>
  )
}

// ---------------------------------------------------------------------------
// Mobile drawer
// ---------------------------------------------------------------------------

export function AppSidebarMobile({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { pathname } = useLocation()
  const panelRef = useRef<HTMLElement>(null)
  const firstPath = useRef(pathname)

  // Close after navigating.
  useEffect(() => {
    if (pathname !== firstPath.current) { firstPath.current = pathname; if (open) onClose() }
  }, [pathname, open, onClose])

  // Esc closes; focus moves into the drawer on open.
  useEffect(() => {
    if (!open) return
    const onKey = (e: globalThis.KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    const t = window.setTimeout(() => panelRef.current?.querySelector<HTMLElement>('a[href], button')?.focus(), 30)
    return () => { window.removeEventListener('keydown', onKey); window.clearTimeout(t) }
  }, [open, onClose])

  return (
    <>
      {/* Backdrop */}
      <div
        className={[
          'sh-backdrop md:hidden fixed inset-0 z-40',
          open ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none',
        ].join(' ')}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer panel */}
      <aside
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Navigation"
        inert={!open ? true : undefined}
        className={[
          'sh-drawer md:hidden fixed inset-y-0 left-0 z-50 w-[272px] max-w-[85vw]',
          'bg-bg border-r border-[var(--frame-line)]',
          open ? 'translate-x-0' : '-translate-x-full',
        ].join(' ')}
      >
        <SidebarContent collapsed={false} mode="mobile" onClose={onClose} />
      </aside>
    </>
  )
}

export default AppSidebarDesktop
