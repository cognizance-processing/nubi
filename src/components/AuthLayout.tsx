/**
 * AuthLayout — the Swiss split screen for Login and Register.
 *
 * Layout (lg+):
 *   ┌─ sheet ────────────────┬─ frame ─────────────────────────────────┐
 *   │ logo · theme · back    │ FIG. label                               │
 *   │                        │                                          │
 *   │ NUBI · SIGN IN ━━━━━━  │      AuthPoster: the landing hero's      │
 *   │ Title                  │      board in a browser tab, drawn once  │
 *   │ form                   │      in 2D (no three.js, no WebGL)       │
 *   │                        │ ━━━ facts row ━━━━━━━━━━━━━━━━━━━━━━━━━ │
 *   │ legal                  │ tagline                                  │
 *   └────────────────────────┴──────────────────────────────────────────┘
 * Below lg the art panel is hidden and the form takes the full width.
 *
 * Props:
 *   title      {string}    — form heading
 *   subtitle   {string}    — form sub-heading
 *   eyebrow    {string}    — ruled label above the heading ("SIGN IN")
 *   children   {ReactNode} — the form content
 *   footer     {ReactNode} — "Don't have an account? …" link
 *   artTagline {string}    — sentence under the figure
 *   art        {string}    — deprecated and ignored; the panel is always the static AuthPoster
 */

import { Link } from 'react-router-dom'
import { Sun, Moon, ArrowLeft } from 'lucide-react'
import Logo from './Logo.jsx'
import AuthPoster from './illustrations/AuthPoster'
import { useTheme } from '../contexts/ThemeContext.jsx'

const FACTS = [
  { value: '$0', label: 'per extra viewer' },
  { value: '1M', label: 'rows in this tab' },
  { value: '20+', label: 'connectors' },
]

export default function AuthLayout({
  title,
  subtitle,
  eyebrow = 'ACCOUNT',
  children,
  footer,
  artTagline = 'Dashboards that compute where they are read.',
}: {
  title: string
  subtitle?: string
  eyebrow?: string
  children: React.ReactNode
  footer?: React.ReactNode
  artTagline?: string
  /** @deprecated ignored: the art panel is always the static AuthPoster. */
  art?: string
}) {
  const { theme, toggleTheme } = useTheme()

  return (
    <div className="min-h-screen lg:h-screen flex bg-bg text-fg lg:overflow-hidden">

      {/* ══ Form sheet ══════════════════════════════════════════════════ */}
      <div className="flex-1 lg:flex-none lg:w-[44%] xl:w-[40%] flex flex-col min-h-0 bg-surface lg:border-r lg:border-[var(--frame-line)]">
        <header className="flex items-center gap-3 h-14 px-6 sm:px-10 border-b border-border shrink-0">
          <Link to="/" className="nubi-focus-ring rounded-sm" aria-label="Nubi home">
            <Logo size={22} showName />
          </Link>
          <div className="ml-auto flex items-center gap-1">
            <Link
              to="/"
              className="inline-flex items-center gap-1.5 h-8 px-2.5 text-[13px] text-muted hover:text-fg rounded-sm nubi-focus-ring"
            >
              <ArrowLeft size={14} aria-hidden="true" />
              Back to home
            </Link>
            <button
              type="button"
              onClick={toggleTheme}
              aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
              className="nubi-topbar-action"
            >
              {theme === 'dark' ? <Sun size={15} aria-hidden="true" /> : <Moon size={15} aria-hidden="true" />}
            </button>
          </div>
        </header>

        <div className="flex-1 min-h-0 overflow-y-auto flex flex-col px-6 sm:px-10 py-8">
          {/* my-auto centres the form when it fits and lets it start at the top (scrollable) when it does not */}
          <div className="w-full max-w-[400px] mx-auto my-auto nubi-animate-slide-up">
            <div className="flex items-center gap-2.5 pb-2.5 mb-6 border-b-2 border-[var(--ink)] font-mono text-[11.5px] tracking-[0.08em] text-subtle">
              <b className="font-medium text-fg">NUBI</b>
              <span>{eyebrow}</span>
              <span className="ml-auto">APACHE-2.0</span>
            </div>

            <h1 className="text-[2.25rem] sm:text-[2.5rem] font-semibold text-fg leading-[1.02] tracking-[-0.035em] [text-wrap:balance]">
              {title}
            </h1>
            {subtitle && <p className="mt-3 mb-7 text-[15px] text-muted leading-relaxed">{subtitle}</p>}
            {!subtitle && <div className="mb-7" />}

            {children}

            {footer && (
              <div className="mt-7 pt-4 border-t border-border text-sm text-muted">
                {footer}
              </div>
            )}
          </div>
        </div>

        <div className="px-6 sm:px-10 py-3.5 border-t border-border font-mono text-[11px] text-subtle shrink-0">
          By continuing you agree to the{' '}
          <Link to="/terms" className="text-fg underline underline-offset-2 hover:text-primary nubi-focus-ring rounded-sm">Terms</Link>
          {' '}and{' '}
          <Link to="/privacy" className="text-fg underline underline-offset-2 hover:text-primary nubi-focus-ring rounded-sm">Privacy Policy</Link>.
        </div>
      </div>

      {/* ══ Frame — static poster (lg+) ═════════════════════════════════ */}
      <aside className="hidden lg:flex flex-1 min-w-0 flex-col bg-bg overflow-y-auto nubi-auth-art" aria-label="About Nubi">
        <div className="flex items-center justify-between px-10 pt-7 pb-4 font-mono text-[11px] tracking-[0.08em] uppercase">
          <span className="text-fg">Fig. 1 · Your board, in a browser tab</span>
          <span className="text-subtle">computed in this tab</span>
        </div>

        <div className="flex-1 min-h-0 flex items-center px-10">
          <div className="w-full max-w-[900px] mx-auto">
            <AuthPoster className="block w-full aspect-[16/11] max-h-[calc(100vh-280px)] min-h-[280px] mx-auto" />
          </div>
        </div>

        <div className="px-10 pb-8 pt-4">
          <div className="max-w-[900px] mx-auto">
            <div className="grid grid-cols-3 border-t border-[var(--ink)]">
              {FACTS.map(f => (
                <div key={f.label} className="pt-2.5 pr-3">
                  <div className="text-[22px] font-medium tracking-[-0.03em] text-fg tabular-nums leading-tight">{f.value}</div>
                  <div className="font-mono text-[11px] text-subtle">{f.label}</div>
                </div>
              ))}
            </div>
            <p className="mt-5 text-[19px] font-semibold tracking-[-0.02em] text-fg leading-snug max-w-[40ch]">{artTagline}</p>
          </div>
        </div>
      </aside>
    </div>
  )
}
