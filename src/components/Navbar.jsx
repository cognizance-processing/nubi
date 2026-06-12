/**
 * Navbar — sticky app shell header.
 *
 * Left:   Logo (links to /)
 * Center: nav links — Docs, Compare, Pricing, and Portal (auth-only; the /home app hub)
 * Right:  theme toggle (Sun/Moon) + user menu (avatar+logout) or login/register
 *
 * Responsive: collapses to a hamburger menu on mobile/tablet (< lg).
 * Mobile drawer slides down with CSS transition; tap targets ≥ 44px.
 */

import { useState, useEffect, useRef } from 'react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { Sun, Moon, Menu, X, LogOut, LayoutDashboard, ChevronDown } from 'lucide-react'
import { useTheme } from '../contexts/ThemeContext.jsx'
import { useAuth } from '../contexts/AuthContext.jsx'
import Logo from './Logo.jsx'

// ── Nav link data ─────────────────────────────────────────────────────────────
// scrollTo: if set, clicking the link smooth-scrolls to that section ID on the
//           landing page (or navigates to /#id if not already on /).
const NAV_LINKS = [
  { label: 'Docs',    to: '/docs' },
  { label: 'Compare', to: '/compare' },
  { label: 'Pricing', to: '/pricing' },
  // One entry into the authenticated app (Playground / Editor / Dashboard all
  // live inside it) — the /home hub.
  { label: 'Portal',  to: '/home', authOnly: true },
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
        flex items-center justify-center w-11 h-11 rounded-lg
        text-muted hover:text-fg
        bg-surface-2 hover:bg-surface
        border border-border
        transition-colors duration-150
        focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1
      "
    >
      {isDark
        ? <Sun size={16} strokeWidth={2} />
        : <Moon size={16} strokeWidth={2} />
      }
    </button>
  )
}

// ── User avatar / menu ────────────────────────────────────────────────────────
function UserMenu({ user, logout }) {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const menuRef = useRef(null)

  const initial = (user.name || user.email || '?')[0].toUpperCase()

  async function handleLogout() {
    setOpen(false)
    await logout()
    navigate('/')
  }

  // Close on outside click
  useEffect(() => {
    if (!open) return
    function onDown(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  return (
    <div className="relative" ref={menuRef}>
      <button
        onClick={() => setOpen(v => !v)}
        className="
          flex items-center gap-1.5 px-2 py-1 rounded-lg
          text-sm text-fg hover:bg-surface-2
          border border-border
          min-h-[44px]
          transition-colors duration-150
          focus:outline-none focus:ring-2 focus:ring-ring
        "
        aria-label="User menu"
        aria-expanded={open}
      >
        {/* Avatar circle */}
        <span
          className="flex items-center justify-center w-6 h-6 rounded-full text-xs font-semibold text-primary-fg shrink-0"
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
              to="/home"
              onClick={() => setOpen(false)}
              className="flex items-center gap-2 px-3 py-2.5 text-sm text-fg hover:bg-surface-2 transition-colors min-h-[44px]"
            >
              <LayoutDashboard size={14} className="text-muted" />
              Portal
            </Link>
            <button
              onClick={handleLogout}
              className="flex items-center gap-2 w-full px-3 py-2.5 text-sm text-fg hover:bg-surface-2 transition-colors text-left min-h-[44px]"
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

// ── Scroll-aware nav link ─────────────────────────────────────────────────────
// For links with scrollTo, clicking while on "/" scrolls to the section.
// If not on "/", navigates to "/" then the browser follows the hash.
function DesktopNavLink({ to, label, scrollTo }) {
  const location = useLocation()

  if (scrollTo) {
    function handleClick(e) {
      if (location.pathname === '/') {
        e.preventDefault()
        const el = document.getElementById(scrollTo)
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }
      // else let the link navigate to /#scrollTo naturally
    }

    return (
      <a
        href={`/#${scrollTo}`}
        onClick={handleClick}
        className="text-sm font-medium transition-colors duration-150 px-2 py-1 rounded-md text-muted hover:text-fg hover:bg-surface-2"
      >
        {label}
      </a>
    )
  }

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
  const location = useLocation()
  const navigate = useNavigate()

  // Close mobile menu on route change — "adjust state during render" pattern
  // (no effect, no cascading re-render).
  const [lastPath, setLastPath] = useState(location.pathname)
  if (lastPath !== location.pathname) {
    setLastPath(location.pathname)
    setMobileOpen(false)
  }

  // Filter links based on auth
  const visibleLinks = NAV_LINKS.filter(l => !l.authOnly || user)

  function handleMobileScrollLink(e, scrollTo) {
    setMobileOpen(false)
    if (location.pathname === '/') {
      e.preventDefault()
      const el = document.getElementById(scrollTo)
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
    // else navigate to /#id naturally
  }

  return (
    <header
      className="
        sticky top-0 z-50
        bg-surface
        border-b border-border
        shadow-sm
      "
    >
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-14 flex items-center justify-between gap-3">

        {/* ── Left: Logo ────────────────────────────────────────────────── */}
        <Link to="/" aria-label="Nubi home" className="shrink-0">
          <Logo size={30} showName={true} />
        </Link>

        {/* ── Center: Desktop nav ───────────────────────────────────────── */}
        <nav className="hidden lg:flex items-center gap-1 flex-1 justify-center" aria-label="Main navigation">
          {visibleLinks.map(link => (
            <DesktopNavLink key={link.label} to={link.to} label={link.label} scrollTo={link.scrollTo} />
          ))}
        </nav>

        {/* ── Right: actions ────────────────────────────────────────────── */}
        <div className="flex items-center gap-2 shrink-0">
          <ThemeToggle />

          {/* Auth controls — desktop only */}
          <div className="hidden lg:flex items-center gap-2">
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
                    text-sm font-medium px-3 py-2 rounded-lg
                    bg-primary text-primary-fg
                    hover:opacity-90
                    transition-opacity
                    shadow-sm min-h-[36px] flex items-center
                  "
                >
                  Get started
                </Link>
              </>
            )}
          </div>

          {/* Hamburger — visible below lg ── */}
          <button
            className="
              lg:hidden flex items-center justify-center
              w-11 h-11 rounded-lg
              text-muted hover:text-fg hover:bg-surface-2
              transition-colors duration-150
              focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1
            "
            onClick={() => setMobileOpen(v => !v)}
            aria-label={mobileOpen ? 'Close menu' : 'Open menu'}
            aria-expanded={mobileOpen}
            aria-controls="mobile-nav"
          >
            {mobileOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </div>

      {/* ── Mobile menu drawer ──────────────────────────────────────────── */}
      {/*  CSS max-height transition for open/close animation              */}
      <div
        id="mobile-nav"
        role="region"
        aria-label="Mobile navigation"
        className={`
          lg:hidden
          overflow-hidden
          bg-surface border-t border-border
          transition-all duration-300 ease-in-out
          ${mobileOpen ? 'max-h-[600px] opacity-100' : 'max-h-0 opacity-0'}
        `}
      >
        <div className="px-4 py-3 flex flex-col gap-1">
          {visibleLinks.map(link =>
            link.scrollTo ? (
              <a
                key={link.label}
                href={`/#${link.scrollTo}`}
                onClick={(e) => handleMobileScrollLink(e, link.scrollTo)}
                className="
                  flex items-center px-3 py-3 rounded-lg
                  text-sm font-medium
                  text-fg hover:bg-surface-2
                  transition-colors duration-150
                  min-h-[44px]
                "
              >
                {link.label}
              </a>
            ) : (
              <NavLink
                key={link.to}
                to={link.to}
                onClick={() => setMobileOpen(false)}
                className={({ isActive }) =>
                  `flex items-center px-3 py-3 rounded-lg text-sm font-medium transition-colors duration-150 min-h-[44px]
                   ${isActive
                     ? 'bg-surface-2 text-primary'
                     : 'text-fg hover:bg-surface-2'
                   }`
                }
              >
                {link.label}
              </NavLink>
            )
          )}

          {/* Mobile auth actions */}
          <div className="mt-2 pt-3 border-t border-border flex flex-col gap-2">
            {user ? (
              <>
                <div className="px-3 py-2 text-xs text-muted truncate">
                  Signed in as {user.name || user.email}
                </div>
                <Link
                  to="/home"
                  onClick={() => setMobileOpen(false)}
                  className="
                    flex items-center gap-2 px-3 py-3 rounded-lg
                    text-sm font-medium text-fg hover:bg-surface-2
                    transition-colors duration-150 min-h-[44px]
                  "
                >
                  <LayoutDashboard size={15} className="text-muted" />
                  Portal
                </Link>
                <button
                  onClick={async () => { setMobileOpen(false); await logout(); navigate('/') }}
                  className="
                    flex items-center gap-2 px-3 py-3 rounded-lg
                    text-sm font-medium text-fg hover:bg-surface-2
                    transition-colors duration-150 text-left min-h-[44px] w-full
                  "
                >
                  <LogOut size={15} className="text-muted" />
                  Log out
                </button>
              </>
            ) : (
              <>
                <Link
                  to="/login"
                  onClick={() => setMobileOpen(false)}
                  className="
                    flex items-center justify-center px-4 py-3 rounded-lg
                    text-sm font-medium text-fg
                    border border-border hover:bg-surface-2
                    transition-colors duration-150 min-h-[44px]
                  "
                >
                  Log in
                </Link>
                <Link
                  to="/register"
                  onClick={() => setMobileOpen(false)}
                  className="
                    flex items-center justify-center px-4 py-3 rounded-lg
                    text-sm font-semibold
                    bg-primary text-primary-fg
                    hover:opacity-90 transition-opacity
                    min-h-[44px]
                  "
                >
                  Get started free
                </Link>
              </>
            )}
          </div>
        </div>
      </div>
    </header>
  )
}
