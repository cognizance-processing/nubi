/**
 * AppTopbar — the bar across the top of the white sheet in the authenticated AppShell.
 *
 * Left:   [mobile menu] [mobile logo] [breadcrumb — only at depth >= 2]
 * Center: the page-actions slot (pages portal their toolbar here via <TopbarPortal>)
 * Right:  [search (mobile)] [panels: notifications · git · chat] [account menu]
 *
 * Rules:
 *   - Top-level pages never repeat their title here (the page header owns it); the breadcrumb
 *     appears only on nested routes, from the label map in shell/shellLogic.ts.
 *   - Panel toggles are icon buttons (16px, 1.5 stroke). The notification count is a square ink
 *     badge capped at "9+" — never a red pill.
 *   - Below 768px the git/chat toggles collapse into a "⋯" menu so the bar never truncates the
 *     breadcrumb or the page's own actions. Notifications stays visible (it carries the count).
 *
 * Contracts:
 *   - UiContext: setTopbarSlot (the page-actions portal target), crumbLabel
 *   - AuthContext: user, logout. ThemeContext: theme, toggleTheme
 */

import { Fragment } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { ChevronRight, Keyboard, LogOut, Menu, MoreHorizontal, Search, Settings, Sun, Moon } from 'lucide-react'
import { useUi } from '../../contexts/UiContext.jsx'
import { useAuth } from '../../contexts/AuthContext.jsx'
import { useTheme } from '../../contexts/ThemeContext.jsx'
import { visibleRailItems, railItemAriaLabel, formatBadgeCount, crumbTrail } from '../../shell/shellLogic.js'
import { useMenu } from '../../shell/useMenu.js'
import { openSpotlight } from './Spotlight.jsx'
import { openShortcuts } from '../../shell/ShortcutSheet.jsx'
import Logo from '../Logo.jsx'
import '../../shell/shell.css'

type RailItem = {
  id: string
  Icon: any
  label: string
  active?: boolean
  onToggle: () => void
  badge?: number
  hidden?: boolean
}

// ---------------------------------------------------------------------------
// Panel switcher (Notifications / Git / Chat)
// ---------------------------------------------------------------------------

function PanelButton({ item, className = '' }: { item: RailItem; className?: string }) {
  const { id, Icon, label, active, onToggle, badge } = item
  const count = formatBadgeCount(badge, 9)
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={railItemAriaLabel({ active, label, badge })}
      aria-pressed={active}
      title={label}
      data-testid={`rail-toggle-${id}`}
      className={['sh-icon-btn', className].join(' ')}
    >
      <Icon size={16} strokeWidth={1.5} aria-hidden="true" />
      {count && <span className="sh-count" aria-hidden="true">{count}</span>}
    </button>
  )
}

function TopbarActions({ items }: { items: RailItem[] }) {
  const visible = visibleRailItems(items || [])
  const overflow = new Set(['git', 'chat']) // collapse into "⋯" on small screens
  const { open: menuOpen, setOpen: setMenuOpen, close: closeMenu, rootRef, triggerRef, menuRef, onMenuKeyDown } = useMenu()
  if (visible.length === 0) return null

  const inMenu = visible.filter(i => overflow.has(i.id))
  const menuActive = inMenu.some(i => i.active)

  return (
    <div className="flex items-center gap-1" role="toolbar" aria-label="Panels" data-testid="app-right-rail">
      {visible.map(item => (
        <PanelButton key={item.id} item={item} className={overflow.has(item.id) ? 'hidden md:inline-flex' : ''} />
      ))}

      {/* Mobile overflow */}
      {inMenu.length > 0 && (
        <div ref={rootRef} className="relative md:hidden">
          <button
            ref={triggerRef}
            type="button"
            onClick={() => setMenuOpen(v => !v)}
            aria-label="More actions"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            className="sh-icon-btn"
            data-active={menuActive || undefined}
            style={menuActive && !menuOpen ? { color: 'var(--text)', borderColor: 'var(--ink)' } : undefined}
          >
            <MoreHorizontal size={16} strokeWidth={1.5} aria-hidden="true" />
          </button>
          {menuOpen && (
            <div
              ref={menuRef}
              role="menu"
              aria-label="More actions"
              onKeyDown={onMenuKeyDown}
              className="sh-pop sh-pop-in absolute right-0 top-full mt-2 z-40 w-52 p-1 origin-top-right"
            >
              {inMenu.map(({ id, Icon, label, active, onToggle }) => (
                <button
                  key={id}
                  type="button"
                  role="menuitem"
                  className="sh-menu-item"
                  onClick={() => { onToggle(); closeMenu(false) }}
                >
                  <Icon size={16} strokeWidth={1.5} aria-hidden="true" className="text-subtle" />
                  <span className="flex-1">{label}</span>
                  {active && <span className="nubi-dot nubi-dot-live" role="img" aria-label="Open" />}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Breadcrumb — only on nested routes ("Settings / AI providers")
// ---------------------------------------------------------------------------

function Crumb() {
  const { pathname } = useLocation()
  const { crumbLabel } = useUi()
  const trail = crumbTrail(pathname, crumbLabel)
  if (trail.length === 0) return null
  return (
    <nav aria-label="Breadcrumb" className="flex items-center min-w-0 text-[13px]">
      <ol className="flex items-center gap-1.5 min-w-0 m-0 p-0 list-none">
        {trail.map((c, i) => {
          const last = i === trail.length - 1
          return (
            <Fragment key={c.to}>
              {i > 0 && (
                <li aria-hidden="true" className={['text-subtle', last ? '' : 'hidden sm:block'].join(' ')}>
                  <ChevronRight size={12} strokeWidth={1.5} />
                </li>
              )}
              <li className={['min-w-0', last ? '' : 'hidden sm:block'].join(' ')}>
                {last ? (
                  <span aria-current="page" className="block font-semibold text-fg truncate">{c.label}</span>
                ) : (
                  <Link to={c.to} className="text-muted hover:text-fg rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                    {c.label}
                  </Link>
                )}
              </li>
            </Fragment>
          )
        })}
      </ol>
    </nav>
  )
}

// ---------------------------------------------------------------------------
// Account menu
// ---------------------------------------------------------------------------

function UserMenu() {
  const { user, logout } = useAuth()
  const { theme, toggleTheme } = useTheme()
  const navigate = useNavigate()
  const { open: menuOpen, setOpen: setMenuOpen, close: closeMenu, rootRef, triggerRef, menuRef, onMenuKeyDown } = useMenu()

  if (!user) return null

  const initials = (user.name || user.email || '?')
    .split(' ')
    .map((s: string) => s[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()

  async function handleLogout() {
    closeMenu(false)
    await logout()
    navigate('/login')
  }

  return (
    <div className="relative" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setMenuOpen(v => !v)}
        aria-label="Account menu"
        aria-expanded={menuOpen}
        aria-haspopup="menu"
        className="flex items-center justify-center overflow-hidden w-8 h-8 rounded-full text-xs font-semibold select-none shrink-0
          hover:opacity-85 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
        style={user.avatar_url ? undefined : { background: 'var(--ink)', color: 'var(--ink-fg)' }}
      >
        {user.avatar_url
          ? <img src={user.avatar_url} alt="" className="w-full h-full object-cover" referrerPolicy="no-referrer" />
          : initials}
      </button>

      {menuOpen && (
        <div
          ref={menuRef}
          role="menu"
          aria-label="Account"
          onKeyDown={onMenuKeyDown}
          className="sh-pop sh-pop-in absolute right-0 top-full mt-2 z-40 w-60 origin-top-right"
        >
          <div className="px-3 py-2.5 border-b border-border">
            {user.name && <p className="text-[13px] font-semibold text-fg truncate leading-tight">{user.name}</p>}
            <p className="text-xs text-muted truncate leading-tight mt-0.5">{user.email}</p>
          </div>

          <div className="p-1">
            <button type="button" role="menuitem" className="sh-menu-item" onClick={() => { toggleTheme(); closeMenu(false) }}>
              {theme === 'dark'
                ? <Sun size={16} strokeWidth={1.5} className="text-subtle" aria-hidden="true" />
                : <Moon size={16} strokeWidth={1.5} className="text-subtle" aria-hidden="true" />}
              <span className="flex-1">{theme === 'dark' ? 'Light mode' : 'Dark mode'}</span>
            </button>

            <Link to="/settings" role="menuitem" className="sh-menu-item" onClick={() => closeMenu(false)}>
              <Settings size={16} strokeWidth={1.5} className="text-subtle" aria-hidden="true" />
              <span className="flex-1">Settings</span>
              <span className="inline-flex gap-1" aria-hidden="true"><kbd className="nubi-kbd">g</kbd><kbd className="nubi-kbd">s</kbd></span>
            </Link>

            <button type="button" role="menuitem" className="sh-menu-item" onClick={() => { closeMenu(false); openShortcuts() }}>
              <Keyboard size={16} strokeWidth={1.5} className="text-subtle" aria-hidden="true" />
              <span className="flex-1">Keyboard shortcuts</span>
              <kbd className="nubi-kbd" aria-hidden="true">?</kbd>
            </button>

            <div className="sh-menu-sep" role="separator" />

            <button type="button" role="menuitem" className="sh-menu-item" data-danger onClick={handleLogout}>
              <LogOut size={16} strokeWidth={1.5} aria-hidden="true" />
              <span className="flex-1">Sign out</span>
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// AppTopbar
// ---------------------------------------------------------------------------

/**
 * @param onMobileMenuOpen opens the mobile navigation drawer
 * @param actions the global panel descriptors (Notifications / Git / Chat), owned by AppShell:
 *   { id, Icon, label, active, onToggle, hidden?, badge? }
 */
export default function AppTopbar({
  onMobileMenuOpen,
  actions = [],
}: {
  onMobileMenuOpen: () => void
  actions?: RailItem[]
}) {
  const { setTopbarSlot } = useUi()

  return (
    <header className="sticky top-0 z-30 flex items-center gap-3 px-3 sm:px-4 shrink-0 h-12 bg-surface border-b border-border">
      {/* Left: mobile menu, mobile logo, breadcrumb */}
      <div className="flex items-center gap-2 min-w-0 shrink">
        <button type="button" onClick={onMobileMenuOpen} aria-label="Open navigation menu" className="sh-icon-btn md:hidden">
          <Menu size={16} strokeWidth={1.5} aria-hidden="true" />
        </button>
        <Link
          to="/"
          aria-label="Nubi, back to the landing page"
          className="md:hidden shrink-0 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Logo size={20} showName={false} />
        </Link>
        <Crumb />
      </div>

      {/* Center: the page-actions slot */}
      <div ref={setTopbarSlot} className="flex items-center gap-2 flex-1 min-w-0 overflow-x-auto" />

      {/* Right: search (mobile), panel switcher, account */}
      <div className="flex items-center gap-1.5 shrink-0">
        <button type="button" onClick={() => openSpotlight()} aria-label="Search" className="sh-icon-btn md:hidden">
          <Search size={16} strokeWidth={1.5} aria-hidden="true" />
        </button>

        <TopbarActions items={actions} />

        {actions.some(a => !a.hidden) && <div className="w-px h-5 bg-border mx-0.5" aria-hidden="true" />}

        <UserMenu />
      </div>
    </header>
  )
}
