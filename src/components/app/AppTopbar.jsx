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
//
// A horizontal cluster of square icon toggles living in the topbar's right
// zone, present on EVERY authed surface at ALL breakpoints — the single,
// consistent home for the shell-level RHS panels. Replaces the former
// full-height right-edge rail; the panels themselves still slide in from the
// right (full-width on mobile, max-w-md on desktop). Items are filtered by
// their `hidden` flag (e.g. Chat hides when a page owns its own chat UI).
// ---------------------------------------------------------------------------

function TopbarActions({ items }) {
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
            'relative w-9 h-9 flex items-center justify-center rounded-lg border transition-colors duration-150',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1',
            active
              ? 'bg-primary text-primary-fg border-primary shadow-sm'
              : 'bg-surface text-muted border-border hover:text-fg hover:bg-surface-2',
          ].join(' ')}
        >
          <Icon size={16} strokeWidth={2} />
          {badge > 0 && (
            <span
              className="absolute -top-1 -right-1 min-w-[16px] h-4 px-1 flex items-center justify-center rounded-full bg-red-500 text-white text-[10px] font-bold leading-none shadow"
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
        className="
          flex items-center justify-center overflow-hidden
          w-9 h-9 rounded-full
          text-xs font-bold text-white
          transition-opacity duration-150 hover:opacity-85
          focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-surface
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
          <div className="
            absolute right-0 top-full mt-1.5 z-40
            w-52 p-1.5 rounded-xl
            bg-surface border border-border shadow-lg shadow-black/10
          ">
            {/* User info */}
            <div className="px-2 pt-1 pb-2 mb-1 border-b border-border">
              {user.name && (
                <p className="text-sm font-semibold text-fg truncate">{user.name}</p>
              )}
              <p className="text-xs text-muted truncate">{user.email}</p>
            </div>

            {/* Theme toggle */}
            <button
              onClick={toggleTheme}
              className="
                flex items-center gap-2.5 w-full px-2 py-2.5 rounded-lg text-sm text-fg
                hover:bg-surface-2 transition-colors text-left min-h-[40px]
              "
            >
              {theme === 'dark'
                ? <Sun size={14} className="text-muted shrink-0" />
                : <Moon size={14} className="text-muted shrink-0" />}
              {theme === 'dark' ? 'Light mode' : 'Dark mode'}
            </button>

            {/* Settings link */}
            <Link
              to="/settings"
              onClick={() => setOpen(false)}
              className="
                flex items-center gap-2.5 w-full px-2 py-2.5 rounded-lg text-sm text-fg
                hover:bg-surface-2 transition-colors min-h-[40px]
              "
            >
              <Settings size={14} className="text-muted shrink-0" />
              Settings
            </Link>

            {/* Divider */}
            <div className="border-t border-border my-1" />

            {/* Sign out */}
            <button
              onClick={handleLogout}
              className="
                flex items-center gap-2.5 w-full px-2 py-2.5 rounded-lg text-sm
                text-red-500 dark:text-red-400
                hover:bg-red-50 dark:hover:bg-red-950/30
                transition-colors text-left min-h-[40px]
              "
            >
              <LogOut size={14} className="shrink-0" />
              Sign out
            </button>
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
      px-3 h-14 shrink-0
      bg-surface/90 backdrop-blur-sm
      border-b border-border
    ">
      {/* ── Far left: mobile hamburger / logo ── */}
      <div className="flex items-center gap-2 shrink-0">
        {/* Mobile hamburger */}
        <button
          onClick={onMobileMenuOpen}
          aria-label="Open navigation menu"
          className="
            md:hidden flex items-center justify-center w-9 h-9 rounded-lg
            text-muted hover:text-fg hover:bg-surface-2
            transition-colors duration-150
            focus:outline-none focus:ring-2 focus:ring-ring
          "
        >
          <Menu size={18} />
        </button>

        {/* Logo — mobile only (sidebar hidden); links to the landing page */}
        <Link to="/" aria-label="Nubi — back to landing page" className="md:hidden shrink-0">
          <Logo size={24} showName={false} />
        </Link>
      </div>

      {/* ── Center: per-page toolbar slot (e.g. the dashboard editor) ── */}
      <div
        ref={setTopbarSlot}
        className="flex items-center gap-2 flex-1 min-w-0 overflow-x-auto"
      />

      {/* ── Right: global panel switcher + user avatar (far right) ── */}
      <div className="flex items-center gap-1 shrink-0">
        {/* Notifications / Git / Chat — the single home for shell RHS panels,
            on every page at every breakpoint. */}
        <TopbarActions items={actions} />

        {/* Divider between the panel switcher and the account menu. */}
        <div className="w-px h-6 bg-border mx-1" aria-hidden="true" />

        <UserMenu />
      </div>
    </header>
  )
}
