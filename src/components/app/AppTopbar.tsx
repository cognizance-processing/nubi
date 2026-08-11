/**
 * AppTopbar — top navigation bar inside the authenticated AppShell.
 *
 * Left:   [mobile hamburger] + Logo (mobile only)
 * Center: per-page toolbar slot (pages portal their toolbar JSX here)
 * Right:  Global panel switcher (Notifications / Git / Chat) | User avatar menu
 *
 * The global switcher (Notifications / Git / Chat) is the single, consistent
 * home for the shell-level RHS panels — present on every authed page at every
 * breakpoint. AppShell owns the panel open/close state and passes the toggle
 * descriptors in via `actions`.
 *
 * Contracts:
 *   - Reads UiContext for setTopbarSlot (the center per-page toolbar portal)
 *   - Reads AuthContext for user, logout
 *   - Reads ThemeContext for theme, toggleTheme
 */

import { useState, useEffect, useRef } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  Menu,
  Sun,
  Moon,
  LogOut,
  Settings,
} from 'lucide-react'
import { useUi } from '../../contexts/UiContext.jsx'
import { useAuth } from '../../contexts/AuthContext.jsx'
import { useTheme } from '../../contexts/ThemeContext.jsx'
import {
  visibleRailItems,
  railItemAriaLabel,
  formatBadgeCount,
} from '../../shell/shellLogic.js'
import Logo from '../Logo.jsx'

// ---------------------------------------------------------------------------
// TopbarActions — the global panel switcher (Notifications / Git / Chat).
// ---------------------------------------------------------------------------

function TopbarActions({ items }: { items: { id: string; Icon: any; label: string; active?: boolean; onToggle: () => void; badge?: number; hidden?: boolean }[] }) {
  const visible = visibleRailItems(items || [])
  if (visible.length === 0) return null

  return (
    <div
      className="flex items-center gap-1"
      role="toolbar"
      aria-label="Panels"
      data-testid="app-right-rail"
    >
      {visible.map(({ id, Icon, label, active, onToggle, badge }) => (
        <button
          key={id}
          type="button"
          onClick={onToggle}
          aria-label={railItemAriaLabel({ active, label, badge })}
          aria-pressed={active}
          title={label}
          data-testid={`rail-toggle-${id}`}
          className={[
            'nubi-topbar-action',
            active ? 'active' : '',
          ].join(' ')}
        >
          <Icon size={15} strokeWidth={2} aria-hidden="true" />
          {badge > 0 && (
            <span
              className="absolute -top-1 -right-1 min-w-[1rem] h-4 px-1 flex items-center justify-center rounded-full bg-red-500 text-white text-[9px] font-bold leading-none shadow-sm"
              aria-hidden="true"
            >
              {formatBadgeCount(badge)}
            </span>
          )}
        </button>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// User avatar dropdown
// ---------------------------------------------------------------------------

function UserMenu() {
  const { user, logout } = useAuth()
  const { theme, toggleTheme } = useTheme()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  // Close on outside click
  useEffect(() => {
    if (!open) return
    function onDown(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  // Close on Escape (matches the Escape-to-close behaviour of every other
  // dropdown/dialog in the shell — WorkspaceSwitcher, NotificationCenter, etc).
  useEffect(() => {
    if (!open) return
    function onKey(e) {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  if (!user) return null

  const initials = (user.name || user.email || '?')
    .split(' ')
    .map(s => s[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()

  async function handleLogout() {
    setOpen(false)
    await logout()
    navigate('/login')
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(v => !v)}
        aria-label="User account menu"
        aria-expanded={open}
        aria-haspopup="menu"
        className="
          flex items-center justify-center overflow-hidden
          w-8 h-8 rounded-full
          text-xs font-bold text-white
          ring-2 ring-transparent
          transition-[opacity,box-shadow] duration-150
          hover:opacity-85
          focus-visible:outline-none focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface
          select-none shrink-0
        "
        style={user.avatar_url ? undefined : { background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
      >
        {user.avatar_url
          ? <img src={user.avatar_url} alt="" className="w-full h-full object-cover" referrerPolicy="no-referrer" />
          : initials}
      </button>

      {open && (
        <>
          <div
            className="fixed inset-0 z-30"
            onClick={() => setOpen(false)}
            aria-hidden="true"
          />
          <div
            role="menu"
            className="
              absolute right-0 top-full mt-2 z-40
              w-52 rounded-nubi-xl
              bg-surface border border-border
              shadow-nubi-lg
              overflow-hidden
              nubi-animate-scale-in origin-top-right
            "
          >
            {/* User info */}
            <div className="px-3 pt-2.5 pb-2.5 border-b border-border">
              {user.name && (
                <p className="text-sm font-semibold text-fg truncate leading-tight">{user.name}</p>
              )}
              <p className="text-xs text-muted truncate leading-tight mt-0.5">{user.email}</p>
            </div>

            <div className="p-1">
              {/* Theme toggle */}
              <button
                onClick={toggleTheme}
                role="menuitem"
                className="nubi-dropdown-item"
              >
                {theme === 'dark'
                  ? <Sun size={14} className="shrink-0 text-muted" aria-hidden="true" />
                  : <Moon size={14} className="shrink-0 text-muted" aria-hidden="true" />}
                {theme === 'dark' ? 'Light mode' : 'Dark mode'}
              </button>

              {/* Settings link */}
              <Link
                to="/settings"
                onClick={() => setOpen(false)}
                role="menuitem"
                className="nubi-dropdown-item"
              >
                <Settings size={14} className="shrink-0 text-muted" aria-hidden="true" />
                Settings
              </Link>

              <div className="nubi-dropdown-divider" />

              {/* Sign out */}
              <button
                onClick={handleLogout}
                role="menuitem"
                className="nubi-dropdown-item danger"
              >
                <LogOut size={14} className="shrink-0" aria-hidden="true" />
                Sign out
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// AppTopbar
// ---------------------------------------------------------------------------

/**
 * @param {{ onMobileMenuOpen: Function, actions?: Array }} props
 *   actions — the global panel toggle descriptors (Notifications / Git / Chat),
 *   built and owned by AppShell. Item shape:
 *   { id, Icon, label, active, onToggle, hidden?, badge? } — see TopbarActions.
 */
export default function AppTopbar({ onMobileMenuOpen, actions = [] }) {
  const { setTopbarSlot } = useUi()

  return (
    <header className="
      sticky top-0 z-30
      flex items-center gap-3
      px-3 sm:px-4 shrink-0
      bg-surface/90 backdrop-blur-md
      border-b border-border
      shadow-[0_1px_0_var(--border)]
    "
    style={{ height: '3.25rem' }}
    >
      {/* ── Far left: mobile hamburger / logo ── */}
      <div className="flex items-center gap-2 shrink-0">
        {/* Mobile hamburger */}
        <button
          onClick={onMobileMenuOpen}
          aria-label="Open navigation menu"
          className="
            md:hidden flex items-center justify-center w-8 h-8 rounded-lg
            text-muted hover:text-fg hover:bg-surface-2
            transition-colors duration-100
            focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring
          "
        >
          <Menu size={17} aria-hidden="true" />
        </button>

        {/* Logo — mobile only (sidebar hidden); links to the landing page */}
        <Link
          to="/"
          aria-label="Nubi — back to landing page"
          className="md:hidden shrink-0 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Logo size={22} showName={false} />
        </Link>
      </div>

      {/* ── Center: per-page toolbar slot (e.g. the dashboard editor) ── */}
      <div
        ref={setTopbarSlot}
        className="flex items-center gap-2 flex-1 min-w-0 overflow-x-auto"
      />

      {/* ── Right: global panel switcher + user avatar (far right) ── */}
      <div className="flex items-center gap-1.5 shrink-0">
        {/* Notifications / Git / Chat — the single home for shell RHS panels,
            on every page at every breakpoint. */}
        <TopbarActions items={actions} />

        {/* Divider between the panel switcher and the account menu. */}
        {actions.filter(a => !a.hidden).length > 0 && (
          <div className="w-px h-5 bg-border mx-0.5" aria-hidden="true" />
        )}

        <UserMenu />
      </div>
    </header>
  )
}
