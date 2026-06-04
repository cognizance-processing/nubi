/**
 * Navbar — sticky app shell header.
 *
 * Left:   Logo (links to /)
 * Center: nav links — Docs, Compare, Playground, Editor + Dashboard (auth-only)
 * Right:  theme toggle (Sun/Moon) + user menu (avatar+logout) or login/register
 *
 * Responsive: collapses to a hamburger menu on mobile (< lg).
 */

import { useState } from 'react'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import { Sun, Moon, Menu, X, LogOut, LayoutDashboard, ChevronDown } from 'lucide-react'
import { useTheme } from '../contexts/ThemeContext.jsx'
import { useAuth } from '../contexts/AuthContext.jsx'
import Logo from './Logo.jsx'

// ── Nav link data ─────────────────────────────────────────────────────────────
const NAV_LINKS = [
  { label: 'Docs',       to: '/docs' },
  { label: 'Compare',    to: '/compare' },
  { label: 'Playground', to: '/playground', authOnly: true },
  { label: 'Editor',     to: '/editor',     authOnly: true },
  { label: 'Dashboard',  to: '/dashboard',  authOnly: true },
]

// ── Theme toggle button ───────────────────────────────────────────────────────
function ThemeToggle() {
  const { theme, toggleTheme } = useTheme()
  const isDark = theme === 'dark'

  return (
    <button
      onClick={toggleTheme}
      aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      className="
        flex items-center justify-center w-8 h-8 rounded-lg
        text-muted hover:text-fg
        bg-surface-2 hover:bg-surface
        border border-border
        transition-colors duration-150
        focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1
      "
    >
      {isDark
        ? <Sun size={15} strokeWidth={2} />
        : <Moon size={15} strokeWidth={2} />
      }
    </button>
  )
}

// ── User avatar / menu ────────────────────────────────────────────────────────
function UserMenu({ user, logout }) {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()

  const initial = (user.name || user.email || '?')[0].toUpperCase()

  async function handleLogout() {
    setOpen(false)
    await logout()
    navigate('/')
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(v => !v)}
        className="
          flex items-center gap-1.5 px-2 py-1 rounded-lg
          text-sm text-fg hover:bg-surface-2
          border border-border
          transition-colors duration-150
          focus:outline-none focus:ring-2 focus:ring-ring
        "
        aria-label="User menu"
        aria-expanded={open}
      >
        {/* Avatar circle */}
        <span
          className="flex items-center justify-center w-6 h-6 rounded-full text-xs font-semibold text-primary-fg"
          style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
          aria-hidden="true"
        >
          {initial}
        </span>
        <span className="hidden sm:inline max-w-[100px] truncate">{user.name || user.email}</span>
        <ChevronDown size={12} className="text-muted" />
      </button>

      {open && (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 z-30"
            onClick={() => setOpen(false)}
            aria-hidden="true"
          />
          {/* Dropdown */}
          <div className="
            absolute right-0 top-full mt-1 z-40
            w-48 py-1 rounded-xl
            bg-surface border border-border shadow-lg
          ">
            <div className="px-3 py-2 border-b border-border">
              <p className="text-xs text-muted truncate">{user.email}</p>
            </div>
            <Link
              to="/dashboard"
              onClick={() => setOpen(false)}
              className="flex items-center gap-2 px-3 py-2 text-sm text-fg hover:bg-surface-2 transition-colors"
            >
              <LayoutDashboard size={14} className="text-muted" />
              Dashboard
            </Link>
            <button
              onClick={handleLogout}
              className="flex items-center gap-2 w-full px-3 py-2 text-sm text-fg hover:bg-surface-2 transition-colors text-left"
            >
              <LogOut size={14} className="text-muted" />
              Log out
            </button>
          </div>
        </>
      )}
    </div>
  )
}

// ── Desktop nav link ──────────────────────────────────────────────────────────
function DesktopNavLink({ to, label }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `text-sm font-medium transition-colors duration-150 px-2 py-1 rounded-md
         ${isActive
           ? 'text-primary bg-surface-2'
           : 'text-muted hover:text-fg hover:bg-surface-2'
         }`
      }
    >
      {label}
    </NavLink>
  )
}

// ── Main Navbar ───────────────────────────────────────────────────────────────
export default function Navbar() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const { user, logout } = useAuth()

  // Filter links based on auth
  const visibleLinks = NAV_LINKS.filter(l => !l.authOnly || user)

  return (
    <header className="
      sticky top-0 z-50
      bg-surface/80 backdrop-blur-md
      border-b border-border
      shadow-sm
    ">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-14 flex items-center justify-between gap-4">

        {/* ── Left: Logo ────────────────────────────────────────────────── */}
        <Link to="/" aria-label="Nubi home" className="shrink-0">
          <Logo size={30} showName={true} />
        </Link>

        {/* ── Center: Desktop nav ───────────────────────────────────────── */}
        <nav className="hidden lg:flex items-center gap-1" aria-label="Main navigation">
          {visibleLinks.map(link => (
            <DesktopNavLink key={link.to} to={link.to} label={link.label} />
          ))}
        </nav>

        {/* ── Right: actions ────────────────────────────────────────────── */}
        <div className="flex items-center gap-2">
          <ThemeToggle />

          {/* Auth controls — desktop */}
          <div className="hidden sm:flex items-center gap-2">
            {user ? (
              <UserMenu user={user} logout={logout} />
            ) : (
              <>
                <Link
                  to="/login"
                  className="text-sm font-medium text-muted hover:text-fg transition-colors px-3 py-1.5 rounded-lg hover:bg-surface-2"
                >
                  Log in
                </Link>
                <Link
                  to="/register"
                  className="
                    text-sm font-medium px-3 py-1.5 rounded-lg
                    bg-primary text-primary-fg
                    hover:opacity-90
                    transition-opacity
                    shadow-sm
                  "
                >
                  Get started
                </Link>
              </>
            )}
          </div>

          {/* Hamburger — mobile */}
          <button
            className="lg:hidden flex items-center justify-center w-8 h-8 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
            onClick={() => setMobileOpen(v => !v)}
            aria-label="Toggle mobile menu"
            aria-expanded={mobileOpen}
          >
            {mobileOpen ? <X size={18} /> : <Menu size={18} />}
          </button>
        </div>
      </div>

      {/* ── Mobile menu ─────────────────────────────────────────────────── */}
      {mobileOpen && (
        <div className="lg:hidden bg-surface border-t border-border px-4 py-4 flex flex-col gap-1">
          {visibleLinks.map(link => (
            <NavLink
              key={link.to}
              to={link.to}
              onClick={() => setMobileOpen(false)}
              className={({ isActive }) =>
                `block px-3 py-2 rounded-lg text-sm font-medium transition-colors
                 ${isActive
                   ? 'bg-surface-2 text-primary'
                   : 'text-fg hover:bg-surface-2'
                 }`
              }
            >
              {link.label}
            </NavLink>
          ))}

          {/* Mobile auth */}
          <div className="mt-3 pt-3 border-t border-border flex flex-col gap-2">
            {user ? (
              <button
                onClick={async () => { setMobileOpen(false); await logout() }}
                className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-fg hover:bg-surface-2 transition-colors text-left"
              >
                <LogOut size={14} className="text-muted" />
                Log out ({user.name || user.email})
              </button>
            ) : (
              <>
                <Link
                  to="/login"
                  onClick={() => setMobileOpen(false)}
                  className="px-3 py-2 rounded-lg text-sm font-medium text-fg hover:bg-surface-2 transition-colors"
                >
                  Log in
                </Link>
                <Link
                  to="/register"
                  onClick={() => setMobileOpen(false)}
                  className="px-3 py-2 rounded-lg text-sm font-medium bg-primary text-primary-fg hover:opacity-90 transition-opacity text-center"
                >
                  Get started
                </Link>
              </>
            )}
          </div>
        </div>
      )}
    </header>
  )
}
