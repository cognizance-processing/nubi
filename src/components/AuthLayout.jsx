/**
 * AuthLayout — standalone split-screen shell for Login and Register pages.
 *
 * Layout:
 *   - Left panel (~48%, hidden below lg): rich brand artwork panel with
 *     navy→teal gradient, AuthArtwork illustration, logo, tagline, feature bullets.
 *   - Right panel (~52%): clean centered form area with logo (mobile-visible),
 *     theme toggle, the form (children), and a footer slot.
 *
 * Mobile: single column, compact brand strip at top, form below.
 *
 * Props:
 *   title      {string}   — form heading
 *   subtitle   {string}   — form sub-heading
 *   children   {ReactNode} — the form content
 *   footer     {ReactNode} — "Don't have an account? ..." link
 *   artTagline {string}   — override tagline shown on the artwork panel
 */

import { Link } from 'react-router-dom'
import Logo from './Logo.jsx'
import AuthArtwork from './illustrations/AuthArtwork.jsx'
import { useTheme } from '../contexts/ThemeContext.jsx'

// ── Theme toggle icon ──────────────────────────────────────────────────────

function SunIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="5" />
      <line x1="12" y1="1" x2="12" y2="3" />
      <line x1="12" y1="21" x2="12" y2="23" />
      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
      <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
      <line x1="1" y1="12" x2="3" y2="12" />
      <line x1="21" y1="12" x2="23" y2="12" />
      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
      <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
    </svg>
  )
}

function MoonIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  )
}

// ── Feature bullets shown on the art panel ────────────────────────────────

const FEATURES = [
  {
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
      </svg>
    ),
    text: 'Real-time analytics across all your data sources',
  },
  {
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
        <line x1="3" y1="9" x2="21" y2="9" />
        <line x1="9" y1="21" x2="9" y2="9" />
      </svg>
    ),
    text: 'Beautiful dashboards, no SQL required',
  },
  {
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      </svg>
    ),
    text: 'Enterprise-grade security and compliance',
  },
]

// ── Main component ─────────────────────────────────────────────────────────

export default function AuthLayout({
  title,
  subtitle,
  children,
  footer,
  artTagline = 'Transform your data into insight',
}) {
  const { theme, toggleTheme } = useTheme()

  return (
    <div className="min-h-screen lg:h-screen flex bg-bg text-fg lg:overflow-hidden">

      {/* ══ LEFT — Brand artwork panel (lg+) ══════════════════════════════ */}
      <div
        className="hidden lg:flex lg:w-[48%] xl:w-[46%] flex-col relative overflow-hidden"
        style={{
          background: 'linear-gradient(150deg, #0e1729 0%, #1b2363 28%, #2456a6 62%, #17b3a3 88%, #2dd4bf 100%)',
        }}
      >
        {/* Subtle noise texture overlay */}
        <div
          className="absolute inset-0 pointer-events-none"
          style={{ opacity: 0.03, backgroundImage: 'url("data:image/svg+xml,%3Csvg width=\'200\' height=\'200\' xmlns=\'http://www.w3.org/2000/svg\'%3E%3Cfilter id=\'n\'%3E%3CfeTurbulence type=\'fractalNoise\' baseFrequency=\'0.9\' numOctaves=\'4\'/%3E%3C/filter%3E%3Crect width=\'100%25\' height=\'100%25\' filter=\'url(%23n)\'/%3E%3C/svg%3E")' }}
        />

        {/* Top bar — logo + back link */}
        <div className="relative z-10 flex items-center justify-between px-10 pt-8 pb-0">
          <Link to="/" className="inline-flex items-center gap-2 group">
            <img
              src="/nubi.png"
              alt="Nubi"
              width={36}
              height={36}
              style={{ width: 36, height: 36, objectFit: 'contain' }}
              draggable={false}
            />
            <span
              className="font-display font-semibold tracking-tight text-xl select-none"
              style={{ color: '#ffffff', opacity: 0.95 }}
            >
              Nubi
            </span>
          </Link>
          <Link
            to="/"
            className="flex items-center gap-1.5 text-sm font-medium transition-opacity hover:opacity-70"
            style={{ color: 'rgba(255,255,255,0.65)' }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <line x1="19" y1="12" x2="5" y2="12" />
              <polyline points="12 19 5 12 12 5" />
            </svg>
            Back
          </Link>
        </div>

        {/* Artwork — fills the middle, shrinks to fit */}
        <div className="relative z-10 flex-1 min-h-0 flex items-center justify-center px-8 py-4">
          <AuthArtwork className="w-full max-w-[400px] max-h-full drop-shadow-2xl" />
        </div>

        {/* Bottom — tagline + feature bullets */}
        <div className="relative z-10 px-10 pb-10">
          <h2
            className="font-display font-bold text-2xl xl:text-3xl leading-tight mb-3"
            style={{ color: '#ffffff' }}
          >
            {artTagline}
          </h2>
          <p
            className="text-sm mb-6 leading-relaxed"
            style={{ color: 'rgba(255,255,255,0.65)' }}
          >
            Connect any data source. Query with plain English. Ship insights in minutes.
          </p>

          <ul className="space-y-3">
            {FEATURES.map(({ icon, text }, i) => (
              <li key={i} className="flex items-center gap-3">
                <span
                  className="flex-shrink-0 w-7 h-7 rounded-lg flex items-center justify-center"
                  style={{
                    background: 'rgba(255,255,255,0.12)',
                    color: '#2dd4bf',
                    backdropFilter: 'blur(4px)',
                  }}
                >
                  {icon}
                </span>
                <span className="text-sm leading-snug" style={{ color: 'rgba(255,255,255,0.8)' }}>
                  {text}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {/* ══ RIGHT — Form panel ════════════════════════════════════════════ */}
      <div className="flex-1 flex flex-col min-h-0">

        {/* Mobile brand strip (visible below lg) */}
        <div
          className="lg:hidden flex items-center justify-between px-5 py-4"
          style={{
            background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)',
          }}
        >
          <Link to="/" className="inline-flex items-center gap-2">
            <img
              src="/nubi.png"
              alt="Nubi"
              width={30}
              height={30}
              style={{ width: 30, height: 30, objectFit: 'contain' }}
              draggable={false}
            />
            <span className="font-display font-semibold text-lg text-white tracking-tight">
              Nubi
            </span>
          </Link>
          <button
            type="button"
            onClick={toggleTheme}
            aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            className="p-2.5 rounded-lg transition-colors min-w-[44px] min-h-[44px] flex items-center justify-center"
            style={{ color: 'rgba(255,255,255,0.85)', background: 'rgba(255,255,255,0.1)' }}
          >
            {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
          </button>
        </div>

        {/* Desktop top bar — theme toggle only */}
        <div className="hidden lg:flex items-center justify-end px-8 py-6">
          <button
            type="button"
            onClick={toggleTheme}
            aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            className="p-2.5 rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors min-w-[44px] min-h-[44px] flex items-center justify-center"
          >
            {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
          </button>
        </div>

        {/* Form area — centered vertically; scrolls internally only if it ever
            overflows (so the PAGE never scrolls on desktop). */}
        <div className="flex-1 min-h-0 overflow-y-auto flex flex-col items-center justify-center px-5 py-6 sm:px-8">
          <div className="w-full max-w-[400px]">

            {/* Logo — desktop (hidden on lg because artwork panel has it) */}
            <div className="hidden lg:flex justify-center mb-6">
              <Link to="/">
                <Logo size={40} showName />
              </Link>
            </div>

            {/* Heading */}
            <div className="mb-7 text-center lg:text-left">
              <h1 className="text-2xl sm:text-3xl font-bold font-display text-fg leading-tight">
                {title}
              </h1>
              {subtitle && (
                <p className="mt-2 text-sm text-muted">
                  {subtitle}
                </p>
              )}
            </div>

            {/* Form content slot */}
            {children}

            {/* Footer link slot */}
            {footer && (
              <div className="mt-6 text-center text-sm text-muted">
                {footer}
              </div>
            )}
          </div>
        </div>

        {/* Bottom footer note */}
        <div className="px-6 py-4 text-center">
          <p className="text-xs text-muted">
            By continuing, you agree to our{' '}
            <span className="text-primary hover:opacity-80 cursor-pointer transition-opacity">
              Terms
            </span>{' '}
            and{' '}
            <span className="text-primary hover:opacity-80 cursor-pointer transition-opacity">
              Privacy Policy
            </span>
          </p>
        </div>

      </div>
    </div>
  )
}
