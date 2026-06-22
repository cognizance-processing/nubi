/**
 * Navbar — sticky marketing/app shell header.
 *
 * Left:   Logo (links to /)
 * Center: nav links — Docs, Reporting, Compare, Pricing, Portal (auth-only)
 * Right:  GitHub, CurrencySelector, ThemeToggle, Login/Register (or UserMenu)
 *
 * Polished: backdrop-blur glass effect, scroll-aware shadow, refined active
 * states, smooth mobile drawer with staggered link reveals.
 */

import { useState, useEffect, useRef } from 'react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { Sun, Moon, Menu, X, LogOut, LayoutDashboard, ChevronDown, Github } from 'lucide-react'
import { useTheme } from '../contexts/ThemeContext.jsx'
import { useAuth } from '../contexts/AuthContext.jsx'
import Logo from './Logo.jsx'
import CurrencySelector from './CurrencySelector.jsx'

const GITHUB_URL = 'https://github.com/nu-bi/nubi'

const NAV_LINKS = [
  { label: 'Docs',      to: '/docs' },
  { label: 'Reporting', to: '/reporting' },
  { label: 'Compare',   to: '/compare' },
  { label: 'Pricing',   to: '/pricing' },
  { label: 'Portal',    to: '/home', authOnly: true },
]

// ── Theme toggle ──────────────────────────────────────────────────────────────

function ThemeToggle() {
  const { theme, toggleTheme } = useTheme()
  const isDark = theme === 'dark'
  return (
    <button
      onClick={toggleTheme}
      aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      className="nubi-btn-icon nubi-btn nubi-btn-secondary focus-visible:ring-2 focus-visible:ring-ring"
    >
      {isDark
        ? <Sun size={15} strokeWidth={2} aria-hidden="true" />
        : <Moon size={15} strokeWidth={2} aria-hidden="true" />
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
        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl border border-border bg-surface text-sm text-fg hover:bg-surface-2 min-h-[36px] transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
        aria-label="User menu"
        aria-expanded={open}
        aria-haspopup="true"
      >
        <span
          className="flex items-center justify-center w-6 h-6 rounded-full text-[11px] font-bold text-white shrink-0"
          style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
          aria-hidden="true"
        >
          {initial}
        </span>
        <span className="hidden sm:inline max-w-[100px] truncate text-sm">{user.name || user.email}</span>
        <ChevronDown size={11} className="text-muted" aria-hidden="true" />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} aria-hidden="true" />
          <div
            className="absolute right-0 top-full mt-1.5 z-40 w-52 p-1 rounded-xl nubi-card nubi-animate-scale-in"
            role="menu"
          >
            <div className="px-3 py-2 mb-1 border-b border-border">
              <p className="text-xs text-muted truncate">{user.email}</p>
            </div>
            <Link
              to="/home"
              onClick={() => setOpen(false)}
              role="menuitem"
              className="flex items-center gap-2 w-full px-3 py-2.5 rounded-lg text-sm text-fg hover:bg-surface-2 transition-colors min-h-[40px]"
            >
              <LayoutDashboard size={14} className="text-muted" aria-hidden="true" />
              Portal
            </Link>
            <button
              onClick={handleLogout}
              role="menuitem"
              className="flex items-center gap-2 w-full px-3 py-2.5 rounded-lg text-sm text-fg hover:bg-surface-2 transition-colors text-left min-h-[40px]"
            >
              <LogOut size={14} className="text-muted" aria-hidden="true" />
              Sign out
            </button>
          </div>
        </>
      )}
    </div>
  )
}

// ── Desktop nav link ──────────────────────────────────────────────────────────

function DesktopNavLink({ to, label, scrollTo }) {
  const location = useLocation()

  if (scrollTo) {
    function handleClick(e) {
      if (location.pathname === '/') {
        e.preventDefault()
        const el = document.getElementById(scrollTo)
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }
    }
    return (
      <a
        href={`/#${scrollTo}`}
        onClick={handleClick}
        className="relative text-sm font-medium px-3 py-1.5 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
      >
        {label}
      </a>
    )
  }

  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `relative text-sm font-medium px-3 py-1.5 rounded-lg transition-colors
         ${isActive
           ? 'text-fg bg-surface-2'
           : 'text-muted hover:text-fg hover:bg-surface-2'
         }`
      }
    >
      {({ isActive }) => (
        <>
          {label}
          {isActive && (
            <span
              className="absolute inset-x-3 bottom-0 h-0.5 rounded-full"
              style={{ background: 'linear-gradient(90deg, #2456a6, #17b3a3)' }}
              aria-hidden="true"
            />
          )}
        </>
      )}
    </NavLink>
  )
}

// ── Main Navbar ───────────────────────────────────────────────────────────────

export default function Navbar() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const [scrolled, setScrolled] = useState(false)
  const { user, logout } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()

  // Close mobile menu on route change
  const [lastPath, setLastPath] = useState(location.pathname)
  if (lastPath !== location.pathname) {
    setLastPath(location.pathname)
    setMobileOpen(false)
  }

  // Scroll-aware shadow
  useEffect(() => {
    function onScroll() { setScrolled(window.scrollY > 8) }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  const visibleLinks = NAV_LINKS.filter(l => !l.authOnly || user)

  function handleMobileScrollLink(e, scrollTo) {
    setMobileOpen(false)
    if (location.pathname === '/') {
      e.preventDefault()
      const el = document.getElementById(scrollTo)
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }

  return (
    <header className={`nubi-navbar${scrolled ? ' scrolled' : ''}`}>
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-14 flex items-center justify-between gap-3">

        {/* ── Logo ─────────────────────────────────────────────────────── */}
        <Link to="/" aria-label="Nubi home" className="shrink-0 flex items-center nubi-focus-ring rounded-lg">
          <Logo size={30} showName={true} />
        </Link>

        {/* ── Desktop nav ──────────────────────────────────────────────── */}
        <nav className="hidden lg:flex items-center gap-0.5 flex-1 justify-center" aria-label="Main navigation">
          {visibleLinks.map(link => (
            <DesktopNavLink key={link.label} to={link.to} label={link.label} scrollTo={link.scrollTo} />
          ))}
        </nav>

        {/* ── Right actions ─────────────────────────────────────────────── */}
        <div className="flex items-center gap-1.5 shrink-0">
          {/* GitHub */}
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Nubi on GitHub"
            title="Star Nubi on GitHub"
            className="nubi-btn-icon nubi-btn nubi-btn-secondary"
          >
            <Github size={15} strokeWidth={2} aria-hidden="true" />
          </a>

          <CurrencySelector />
          <ThemeToggle />

          {/* Auth controls — desktop */}
          <div className="hidden lg:flex items-center gap-2">
            {user ? (
              <UserMenu user={user} logout={logout} />
            ) : (
              <>
                <Link
                  to="/login"
                  className="text-sm font-medium text-muted hover:text-fg transition-colors px-3 py-1.5 rounded-lg hover:bg-surface-2 min-h-[36px] flex items-center nubi-focus-ring"
                >
                  Log in
                </Link>
                <Link
                  to="/register"
                  className="nubi-btn nubi-btn-primary nubi-btn-sm"
                >
                  Get started
                </Link>
              </>
            )}
          </div>

          {/* Hamburger — mobile */}
          <button
            className="lg:hidden nubi-btn nubi-btn-ghost nubi-btn-icon"
            onClick={() => setMobileOpen(v => !v)}
            aria-label={mobileOpen ? 'Close menu' : 'Open menu'}
            aria-expanded={mobileOpen}
            aria-controls="mobile-nav"
          >
            {mobileOpen
              ? <X size={18} aria-hidden="true" />
              : <Menu size={18} aria-hidden="true" />
            }
          </button>
        </div>
      </div>

      {/* ── Mobile drawer ────────────────────────────────────────────────── */}
      <div
        id="mobile-nav"
        role="region"
        aria-label="Mobile navigation"
        className={`
          lg:hidden overflow-hidden bg-surface border-t border-border
          transition-all duration-300 ease-in-out
          ${mobileOpen ? 'max-h-[600px] opacity-100' : 'max-h-0 opacity-0'}
        `}
      >
        <div className="px-4 py-3 flex flex-col gap-0.5">
          {visibleLinks.map((link, i) =>
            link.scrollTo ? (
              <a
                key={link.label}
                href={`/#${link.scrollTo}`}
                onClick={(e) => handleMobileScrollLink(e, link.scrollTo)}
                className="flex items-center px-3 py-3 rounded-xl text-sm font-medium text-fg hover:bg-surface-2 transition-colors min-h-[44px]"
                style={{ animationDelay: `${i * 40}ms` }}
              >
                {link.label}
              </a>
            ) : (
              <NavLink
                key={link.to}
                to={link.to}
                onClick={() => setMobileOpen(false)}
                className={({ isActive }) =>
                  `flex items-center px-3 py-3 rounded-xl text-sm font-medium transition-colors min-h-[44px]
                   ${isActive
                     ? 'bg-surface-2 text-primary font-semibold'
                     : 'text-fg hover:bg-surface-2'
                   }`
                }
                style={{ animationDelay: `${i * 40}ms` }}
              >
                {link.label}
              </NavLink>
            )
          )}

          {/* Mobile auth */}
          <div className="mt-3 pt-3 border-t border-border flex flex-col gap-2">
            {user ? (
              <>
                <div className="px-3 py-2 flex items-center gap-2.5">
                  <span
                    className="flex items-center justify-center w-7 h-7 rounded-full text-[11px] font-bold text-white shrink-0"
                    style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
                    aria-hidden="true"
                  >
                    {(user.name || user.email || '?')[0].toUpperCase()}
                  </span>
                  <span className="text-xs text-muted truncate">{user.name || user.email}</span>
                </div>
                <Link
                  to="/home"
                  onClick={() => setMobileOpen(false)}
                  className="flex items-center gap-2 px-3 py-3 rounded-xl text-sm font-medium text-fg hover:bg-surface-2 transition-colors min-h-[44px]"
                >
                  <LayoutDashboard size={15} className="text-muted" aria-hidden="true" />
                  Portal
                </Link>
                <button
                  onClick={async () => { setMobileOpen(false); await logout(); navigate('/') }}
                  className="flex items-center gap-2 px-3 py-3 rounded-xl text-sm font-medium text-fg hover:bg-surface-2 transition-colors text-left min-h-[44px] w-full"
                >
                  <LogOut size={15} className="text-muted" aria-hidden="true" />
                  Sign out
                </button>
              </>
            ) : (
              <>
                <Link
                  to="/login"
                  onClick={() => setMobileOpen(false)}
                  className="nubi-btn nubi-btn-secondary nubi-btn-lg w-full justify-center"
                >
                  Log in
                </Link>
                <Link
                  to="/register"
                  onClick={() => setMobileOpen(false)}
                  className="nubi-btn nubi-btn-primary nubi-btn-lg w-full justify-center"
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
