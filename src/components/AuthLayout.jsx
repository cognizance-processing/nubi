/**
 * AuthLayout — standalone split-screen shell for Login and Register pages.
 *
 * Layout:
 *   - Left panel (~48%, hidden below lg): rich brand artwork panel with
 *     navy→teal gradient, AuthArtwork illustration, logo, tagline, feature bullets.
 *   - Right panel (~52%): clean centered form area with logo (desktop),
 *     theme toggle, the form (children), and a footer slot.
 *
 * Mobile: single column; compact brand strip at top, form below.
 *
 * Polished: glass noise layer, staggered form entrance animation,
 * full a11y focus ring, refined spacing, dark mode parity.
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

// ── Icons ─────────────────────────────────────────────────────────────────────

function SunIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="5" />
      <line x1="12" y1="1" x2="12" y2="3" /><line x1="12" y1="21" x2="12" y2="23" />
      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" /><line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
      <line x1="1" y1="12" x2="3" y2="12" /><line x1="21" y1="12" x2="23" y2="12" />
      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" /><line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
    </svg>
  )
}

function MoonIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  )
}

function BackArrow() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="19" y1="12" x2="5" y2="12" />
      <polyline points="12 19 5 12 12 5" />
    </svg>
  )
}

// ── Feature bullets on the art panel ─────────────────────────────────────────

const FEATURES = [
  {
    icon: (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
      </svg>
    ),
    text: 'Real-time analytics across all your data sources',
  },
  {
    icon: (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
        <line x1="3" y1="9" x2="21" y2="9" />
        <line x1="9" y1="21" x2="9" y2="9" />
      </svg>
    ),
    text: 'Beautiful embeddable dashboards, viewers always free',
  },
  {
    icon: (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      </svg>
    ),
    text: 'JWT-based RLS. Auth lives in your code, not a vendor UI',
  },
]

// ── Art panel mini-metric strip ──────────────────────────────────────────────

const METRICS = [
  { value: '≈ $0', label: 'per view' },
  { value: '∞',   label: 'viewer seats' },
  { value: '25+',  label: 'connectors' },
]

// ── Main component ─────────────────────────────────────────────────────────────

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
        className="hidden lg:flex lg:w-[46%] xl:w-[44%] flex-col relative overflow-hidden"
        style={{
          background: 'linear-gradient(150deg, #080e22 0%, #0f1635 20%, #1b2363 42%, #2456a6 68%, #17b3a3 88%, #2dd4bf 100%)',
        }}
      >
        {/* Subtle dot-grid texture */}
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            backgroundImage:
              'radial-gradient(circle at 1px 1px, rgba(255,255,255,0.07) 1px, transparent 1.5px)',
            backgroundSize: '28px 28px',
          }}
          aria-hidden="true"
        />
        {/* Noise overlay */}
        <div
          className="absolute inset-0 pointer-events-none mix-blend-overlay"
          style={{
            opacity: 0.04,
            backgroundImage: "url(\"data:image/svg+xml,%3Csvg width='200' height='200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")",
          }}
          aria-hidden="true"
        />
        {/* Glowing orb accents */}
        <div
          className="absolute -top-40 -left-32 w-[28rem] h-[28rem] rounded-full pointer-events-none"
          style={{ background: 'radial-gradient(circle, rgba(45,212,191,0.18) 0%, transparent 65%)' }}
          aria-hidden="true"
        />
        <div
          className="absolute bottom-20 -right-20 w-[22rem] h-[22rem] rounded-full pointer-events-none"
          style={{ background: 'radial-gradient(circle, rgba(36,86,166,0.25) 0%, transparent 65%)' }}
          aria-hidden="true"
        />

        {/* Top bar */}
        <div className="relative z-10 flex items-center justify-between px-10 pt-8">
          <Link to="/" className="inline-flex items-center gap-2.5 group nubi-focus-ring rounded-lg px-1 py-0.5">
            <img
              src="/nubi.png"
              alt="Nubi"
              width={34}
              height={34}
              style={{ width: 34, height: 34, objectFit: 'contain' }}
              draggable={false}
            />
            <span
              className="font-display font-semibold tracking-tight text-xl select-none"
              style={{ color: 'rgba(255,255,255,0.95)' }}
            >
              Nubi
            </span>
          </Link>
          <Link
            to="/"
            className="flex items-center gap-1.5 text-sm font-medium transition-all hover:opacity-80 px-3 py-2 rounded-lg"
            style={{ color: 'rgba(255,255,255,0.55)', background: 'rgba(255,255,255,0.07)' }}
            aria-label="Back to home"
          >
            <BackArrow />
            Back
          </Link>
        </div>

        {/* Artwork */}
        <div className="relative z-10 flex-1 min-h-0 flex items-center justify-center px-8 py-6">
          <AuthArtwork className="w-full max-w-[380px] max-h-full drop-shadow-2xl" />
        </div>

        {/* Bottom — tagline + metrics + bullets */}
        <div className="relative z-10 px-10 pb-10">
          {/* Metric strip */}
          <div className="flex gap-5 mb-6 pb-6 border-b" style={{ borderColor: 'rgba(255,255,255,0.12)' }}>
            {METRICS.map(m => (
              <div key={m.label}>
                <p className="font-display font-bold text-lg leading-none" style={{ color: '#ffffff' }}>
                  {m.value}
                </p>
                <p className="font-mono text-[10px] mt-0.5" style={{ color: 'rgba(255,255,255,0.5)' }}>
                  {m.label}
                </p>
              </div>
            ))}
          </div>

          <h2
            className="font-display font-bold text-xl xl:text-2xl leading-tight mb-2"
            style={{ color: '#ffffff' }}
          >
            {artTagline}
          </h2>
          <p className="text-sm mb-5 leading-relaxed" style={{ color: 'rgba(255,255,255,0.58)' }}>
            Connect any warehouse. Query with SQL or plain English. Ship embedded analytics in minutes.
          </p>

          <ul className="space-y-2.5">
            {FEATURES.map(({ icon, text }, i) => (
              <li key={i} className="flex items-center gap-3">
                <span
                  className="flex-shrink-0 w-6 h-6 rounded-md flex items-center justify-center"
                  style={{ background: 'rgba(45,212,191,0.18)', color: '#2dd4bf' }}
                >
                  {icon}
                </span>
                <span className="text-sm leading-snug" style={{ color: 'rgba(255,255,255,0.75)' }}>
                  {text}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {/* ══ RIGHT — Form panel ════════════════════════════════════════════ */}
      <div className="flex-1 flex flex-col min-h-0">

        {/* Mobile brand strip */}
        <div
          className="lg:hidden flex items-center justify-between px-5 py-3.5"
          style={{ background: 'linear-gradient(135deg, #0f1635, #1b2363, #2456a6, #17b3a3)' }}
        >
          <Link to="/" className="inline-flex items-center gap-2">
            <img src="/nubi.png" alt="Nubi" width={28} height={28} style={{ width: 28, height: 28, objectFit: 'contain' }} draggable={false} />
            <span className="font-display font-semibold text-[17px] text-white tracking-tight">Nubi</span>
          </Link>
          <button
            type="button"
            onClick={toggleTheme}
            aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            className="p-2 rounded-lg flex items-center justify-center min-w-[40px] min-h-[40px]"
            style={{ color: 'rgba(255,255,255,0.85)', background: 'rgba(255,255,255,0.12)' }}
          >
            {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
          </button>
        </div>

        {/* Desktop top bar — theme toggle only */}
        <div className="hidden lg:flex items-center justify-end px-8 pt-6 pb-2">
          <button
            type="button"
            onClick={toggleTheme}
            aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            className="nubi-btn nubi-btn-secondary nubi-btn-icon"
          >
            {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
          </button>
        </div>

        {/* Form area */}
        <div className="flex-1 min-h-0 overflow-y-auto flex flex-col items-center justify-center px-5 py-8 sm:px-8">
          <div className="w-full max-w-[400px] nubi-animate-slide-up">

            {/* Logo — desktop only (art panel has it on mobile) */}
            <div className="hidden lg:flex justify-center mb-7">
              <Link to="/" className="nubi-focus-ring rounded-lg p-1">
                <Logo size={38} showName />
              </Link>
            </div>

            {/* Heading */}
            <div className="mb-6 text-center lg:text-left">
              <h1 className="font-display text-[1.625rem] sm:text-3xl font-bold text-fg leading-tight tracking-tight">
                {title}
              </h1>
              {subtitle && (
                <p className="mt-2 text-sm text-muted leading-relaxed">
                  {subtitle}
                </p>
              )}
            </div>

            {/* Form content */}
            {children}

            {/* Footer link */}
            {footer && (
              <div className="mt-6 text-center text-sm text-muted">
                {footer}
              </div>
            )}
          </div>
        </div>

        {/* Legal note */}
        <div className="px-6 py-4 text-center border-t border-border/50">
          <p className="text-xs text-muted/70">
            By continuing, you agree to our{' '}
            <Link to="/terms" className="text-primary hover:opacity-80 transition-opacity nubi-focus-ring rounded">
              Terms
            </Link>{' '}
            and{' '}
            <Link to="/privacy" className="text-primary hover:opacity-80 transition-opacity nubi-focus-ring rounded">
              Privacy Policy
            </Link>
          </p>
        </div>
      </div>
    </div>
  )
}
