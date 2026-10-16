/**
 * Navbar — the public site header, built to the landing header's spec.
 *
 * 56px, lockup left, text links, ⌘K search, ghost "Sign in", ink "Start free".
 * A hairline appears under the bar once the page scrolls; the current route
 * sits on a 2px ink rule. Below lg the links move into a full-width drawer.
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { Sun, Moon, Menu, X, LogOut, Search, ArrowRight, LayoutDashboard } from 'lucide-react'
import { useTheme } from '../contexts/ThemeContext.jsx'
import { useAuth } from '../contexts/AuthContext.jsx'
import Logo from './Logo.jsx'
import Brand from './marketing/Brand.jsx'
import MarketingStyles from './marketing/MarketingStyles.jsx'
import PublicPalette, { openPublicSearch, isMacLike } from './marketing/PublicPalette.jsx'

export const GITHUB_URL = 'https://github.com/nu-bi/nubi'

type Link_ = { label: string; to: string; hash?: string }

const NAV_LINKS: Link_[] = [
  { label: 'Product', to: '/' },
  { label: 'Live demo', to: '/', hash: '#live' },
  { label: 'Docs', to: '/docs' },
  { label: 'Pricing', to: '/pricing' },
  { label: 'Compare', to: '/compare' },
]

const CSS = `
.mk-nav { position: sticky; top: 0; z-index: 40; background: var(--surface); border-bottom: 1px solid transparent; }
.mk-nav[data-scrolled="true"] { border-bottom-color: var(--border); }
.mk-nav-in { display: flex; align-items: center; gap: 12px; height: 56px; }
@media (min-width: 1024px) { .mk-nav-in { gap: 28px; } }
.mk-skip { position: absolute; left: 8px; top: -48px; z-index: 60; padding: 10px 14px; background: var(--ink); color: var(--ink-fg); font: 600 14px var(--font-ui); border-radius: 2px; text-decoration: none; }
.mk-skip:focus { top: 8px; }
.mk-brand { display: inline-flex; align-items: center; color: var(--text); text-decoration: none; border-radius: 2px; }
.mk-links { display: none; align-items: stretch; gap: 4px; height: 100%; }
@media (min-width: 1024px) { .mk-links { display: flex; } }
.mk-link-item { position: relative; display: inline-flex; align-items: center; padding: 0 12px; font-size: 14px; font-weight: 500; color: var(--text-muted); text-decoration: none; }
.mk-link-item:hover { color: var(--text); }
.mk-link-item[aria-current="page"] { color: var(--text); }
.mk-link-item[aria-current="page"]::after { content: ''; position: absolute; left: 12px; right: 12px; bottom: -1px; height: 2px; background: var(--ink); }
.mk-nav-r { margin-left: auto; display: flex; align-items: center; gap: 4px; min-width: 0; }
@media (min-width: 640px) { .mk-nav-r { gap: 8px; } }
.mk-kbtn { display: none; align-items: center; gap: 10px; height: 34px; padding: 0 10px; min-width: 200px; border: 1px solid var(--frame-line); background: var(--surface-2); color: var(--text-muted); font: 13px var(--font-ui); cursor: pointer; border-radius: 2px; }
.mk-kbtn:hover { border-color: var(--ink); color: var(--text); }
.mk-kbtn span { flex: 1; text-align: left; }
@media (min-width: 1100px) { .mk-kbtn { display: inline-flex; } }
.mk-icon-btn { display: inline-flex; align-items: center; justify-content: center; width: 36px; height: 36px; color: var(--text-muted); background: transparent; border: 0; border-radius: 2px; cursor: pointer; text-decoration: none; }
.mk-icon-btn:hover { color: var(--text); background: var(--frame-hover); }
.mk-only-lg { display: none; }
@media (min-width: 1024px) { .mk-only-lg { display: inline-flex; } .mk-not-lg { display: none !important; } }
@media (min-width: 1100px) { .mk-search-icon { display: none; } }
.mk-drawer { position: fixed; z-index: 39; left: 0; right: 0; top: 56px; bottom: 0; background: var(--surface); border-top: 2px solid var(--ink); overflow-y: auto; overscroll-behavior: contain; }
.mk-drawer nav a { display: flex; align-items: center; gap: 16px; min-height: 56px; padding: 0 16px; font-size: 22px; font-weight: 600; letter-spacing: -.02em; color: var(--text); text-decoration: none; border-bottom: 1px solid var(--border); }
.mk-drawer nav a small { font: 500 11px var(--font-mono); letter-spacing: .08em; color: var(--text-muted); width: 24px; }
.mk-drawer nav a[aria-current="page"] { box-shadow: inset 2px 0 0 var(--ink); }
.mk-drawer nav a svg { margin-left: auto; color: var(--text-muted); }
.mk-drawer .mk-dr-act { display: grid; gap: 10px; padding: 20px 16px; }
.mk-drawer .mk-dr-foot { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 16px 28px; border-top: 1px solid var(--border); }
.mk-user { position: relative; }
.mk-avatar { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; background: var(--ink); color: var(--ink-fg); font: 600 12px var(--font-ui); border-radius: 2px; }
.mk-menu { position: absolute; right: 0; top: calc(100% + 8px); width: 220px; background: var(--surface); border: 1px solid var(--ink); border-radius: 4px; box-shadow: var(--shadow-pop, var(--shadow-lg)); padding: 4px; z-index: 45; }
.mk-menu .mk-menu-id { padding: 8px 10px; font: 12px var(--font-mono); color: var(--text-muted); border-bottom: 1px solid var(--border); margin-bottom: 4px; overflow: hidden; text-overflow: ellipsis; }
.mk-menu a, .mk-menu button { display: flex; align-items: center; gap: 10px; width: 100%; min-height: 36px; padding: 0 10px; font: 500 14px var(--font-ui); color: var(--text); background: transparent; border: 0; border-radius: 2px; cursor: pointer; text-decoration: none; text-align: left; }
.mk-menu a:hover, .mk-menu button:hover { background: var(--frame-hover); }
`

function ThemeButton({ className = 'mk-icon-btn' }: { className?: string }) {
  const { theme, toggleTheme } = useTheme()
  const dark = theme === 'dark'
  return (
    <button type="button" onClick={toggleTheme} className={className} aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'}>
      {dark ? <Sun size={16} strokeWidth={1.75} aria-hidden="true" /> : <Moon size={16} strokeWidth={1.75} aria-hidden="true" />}
    </button>
  )
}

function GithubLink({ className = 'mk-icon-btn' }: { className?: string }) {
  return (
    <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer" className={className} aria-label="Nubi on GitHub">
      <Brand slug="github" name="GitHub" size={18} mode="mono" />
    </a>
  )
}

function UserMenu({ user, logout }: { user: any; logout: () => Promise<void> }) {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const ref = useRef<HTMLDivElement>(null)
  const initial = (user.name || user.email || '?')[0].toUpperCase()

  useEffect(() => {
    if (!open) return
    const down = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', down)
    document.addEventListener('keydown', key)
    return () => { document.removeEventListener('mousedown', down); document.removeEventListener('keydown', key) }
  }, [open])

  return (
    <div className="mk-user" ref={ref}>
      <button type="button" className="mk-icon-btn" style={{ width: 'auto', padding: '0 4px' }} onClick={() => setOpen(v => !v)} aria-label="Account menu" aria-expanded={open} aria-haspopup="menu">
        <span className="mk-avatar" aria-hidden="true">{initial}</span>
      </button>
      {open && (
        <div className="mk-menu" role="menu">
          <div className="mk-menu-id">{user.email}</div>
          <Link to="/home" role="menuitem" onClick={() => setOpen(false)}><LayoutDashboard size={15} strokeWidth={1.75} aria-hidden="true" />Open app</Link>
          <button type="button" role="menuitem" onClick={async () => { setOpen(false); await logout(); navigate('/') }}><LogOut size={15} strokeWidth={1.75} aria-hidden="true" />Sign out</button>
        </div>
      )}
    </div>
  )
}

function isActive(l: Link_, pathname: string) {
  if (l.hash) return false
  if (l.to === '/') return false // the landing has its own header
  return pathname === l.to || pathname.startsWith(l.to + '/')
}

export default function Navbar() {
  const [open, setOpen] = useState(false)
  const [scrolled, setScrolled] = useState(false)
  const { user, logout } = useAuth()
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const menuBtn = useRef<HTMLButtonElement>(null)
  const mac = isMacLike()

  // Close the drawer on navigation.
  const [lastPath, setLastPath] = useState(pathname)
  if (lastPath !== pathname) { setLastPath(pathname); setOpen(false) }

  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 4)
    on()
    window.addEventListener('scroll', on, { passive: true })
    return () => window.removeEventListener('scroll', on)
  }, [])

  // Drawer: lock page scroll, Esc closes, wide screens reset it.
  useEffect(() => {
    if (!open) return
    const prev = document.documentElement.style.overflow
    document.documentElement.style.overflow = 'hidden'
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); menuBtn.current?.focus() } }
    const mq = window.matchMedia('(min-width: 1024px)')
    const wide = () => { if (mq.matches) setOpen(false) }
    document.addEventListener('keydown', key)
    mq.addEventListener('change', wide)
    return () => { document.documentElement.style.overflow = prev; document.removeEventListener('keydown', key); mq.removeEventListener('change', wide) }
  }, [open])

  const goHash = useCallback((e: React.MouseEvent, l: Link_) => {
    if (!l.hash) return
    e.preventDefault()
    setOpen(false)
    navigate({ pathname: l.to, hash: l.hash })
    setTimeout(() => document.getElementById(l.hash!.slice(1))?.scrollIntoView({ behavior: 'smooth' }), 120)
  }, [navigate])

  return (
    <header className="mk-nav" data-scrolled={scrolled || open ? 'true' : 'false'}>
      <MarketingStyles />
      <style href="nubi-navbar" precedence="default">{CSS}</style>
      <PublicPalette />
      <a href="#main" className="mk-skip">Skip to content</a>

      <div className="mk-wrap">
        <div className="mk-nav-in">
          <Link to="/" className="mk-brand" aria-label="Nubi home"><Logo size={26} showName /></Link>

          <nav className="mk-links" aria-label="Main">
            {NAV_LINKS.map(l => (
              <Link
                key={l.label}
                to={{ pathname: l.to, hash: l.hash }}
                onClick={e => goHash(e, l)}
                className="mk-link-item"
                aria-current={isActive(l, pathname) ? 'page' : undefined}
              >
                {l.label}
              </Link>
            ))}
          </nav>

          <div className="mk-nav-r">
            <button type="button" className="mk-kbtn" onClick={openPublicSearch} aria-label={`Search (${mac ? 'Command' : 'Control'} K)`}>
              <Search size={14} strokeWidth={1.75} aria-hidden="true" />
              <span>Search or jump to…</span>
              <kbd className="mk-kbd">{mac ? '⌘K' : 'Ctrl K'}</kbd>
            </button>
            <button type="button" className="mk-icon-btn mk-search-icon" onClick={openPublicSearch} aria-label="Search">
              <Search size={17} strokeWidth={1.75} aria-hidden="true" />
            </button>
            <GithubLink className="mk-icon-btn mk-only-lg" />
            {user ? (
              <>
                <Link to="/home" className="nubi-btn nubi-btn-primary nubi-btn-sm mk-only-lg">Open app</Link>
                <span className="mk-only-lg"><UserMenu user={user} logout={logout} /></span>
              </>
            ) : (
              <>
                <Link to="/login" className="nubi-btn nubi-btn-ghost nubi-btn-sm mk-only-lg">Sign in</Link>
                <Link to="/register" className="nubi-btn nubi-btn-primary nubi-btn-sm">Start free</Link>
              </>
            )}
            <button
              ref={menuBtn}
              type="button"
              className="mk-icon-btn mk-not-lg"
              onClick={() => setOpen(v => !v)}
              aria-label={open ? 'Close menu' : 'Open menu'}
              aria-expanded={open}
              aria-controls="mk-drawer"
            >
              {open ? <X size={20} strokeWidth={1.75} aria-hidden="true" /> : <Menu size={20} strokeWidth={1.75} aria-hidden="true" />}
            </button>
          </div>
        </div>
      </div>

      {open && (
        <div id="mk-drawer" className="mk-drawer" role="dialog" aria-modal="true" aria-label="Menu">
          <nav aria-label="Mobile">
            {NAV_LINKS.map((l, i) => (
              <Link
                key={l.label}
                to={{ pathname: l.to, hash: l.hash }}
                onClick={e => goHash(e, l)}
                aria-current={isActive(l, pathname) ? 'page' : undefined}
              >
                <small>{String(i + 1).padStart(2, '0')}</small>
                {l.label}
                <ArrowRight size={18} strokeWidth={1.5} aria-hidden="true" />
              </Link>
            ))}
            <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
              <small>{String(NAV_LINKS.length + 1).padStart(2, '0')}</small>GitHub<ArrowRight size={18} strokeWidth={1.5} aria-hidden="true" />
            </a>
          </nav>
          <div className="mk-dr-act">
            {user ? (
              <>
                <Link to="/home" className="nubi-btn nubi-btn-primary nubi-btn-xl">Open app</Link>
                <button type="button" className="nubi-btn nubi-btn-secondary nubi-btn-xl" onClick={async () => { setOpen(false); await logout(); navigate('/') }}>Sign out</button>
              </>
            ) : (
              <>
                <Link to="/register" className="nubi-btn nubi-btn-primary nubi-btn-xl">Start free</Link>
                <Link to="/login" className="nubi-btn nubi-btn-secondary nubi-btn-xl">Sign in</Link>
              </>
            )}
          </div>
          <div className="mk-dr-foot">
            <button type="button" className="nubi-btn nubi-btn-ghost nubi-btn-sm" onClick={() => { setOpen(false); openPublicSearch() }}>
              <Search size={15} strokeWidth={1.75} aria-hidden="true" />Search docs
            </button>
            <ThemeButton />
          </div>
        </div>
      )}
    </header>
  )
}
