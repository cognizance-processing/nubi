/**
 * SettingsUI — shared building blocks for the settings pages.
 *
 * Gives every settings section the same anatomy (the pattern used by
 * Vercel/Linear settings):
 *
 *   <SettingsPageHeader>  — section title + one-line description
 *   <SettingsCard>        — bordered card, optional header, optional footer
 *                           bar (where the Save button lives)
 *   <Field>               — label + control + hint with consistent spacing
 *   <PrimaryButton>       — brand-gradient action button with busy spinner
 *   <SavedBadge>          — transient "Saved" confirmation
 *   <ErrorText>           — inline error message
 *   <DangerZone>          — red-bordered card for destructive actions
 *   <DangerRow>           — title/description + action slot inside DangerZone
 *
 * Purely presentational — no data fetching, no business logic.
 */

import { Loader2, CheckCircle, AlertTriangle } from 'lucide-react'

// ---------------------------------------------------------------------------
// Page header
// ---------------------------------------------------------------------------

export function SettingsPageHeader({ title, description, children }) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4 mb-6">
      <div className="min-w-0">
        <h2 className="font-display font-semibold text-xl text-fg">{title}</h2>
        {description && (
          <p className="text-sm text-muted mt-1 max-w-2xl">{description}</p>
        )}
      </div>
      {children && <div className="shrink-0">{children}</div>}
    </header>
  )
}

// ---------------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------------

export function SettingsCard({ title, description, children, footer }) {
  const hasHeader = Boolean(title || description)
  return (
    <section className="rounded-2xl border border-border bg-surface overflow-hidden">
      {hasHeader && (
        <div className="px-5 sm:px-6 pt-5 pb-4 border-b border-border">
          {title && <h3 className="font-semibold text-sm text-fg">{title}</h3>}
          {description && <p className="text-xs text-muted mt-1">{description}</p>}
        </div>
      )}
      <div className="px-5 sm:px-6 py-5">{children}</div>
      {footer && (
        <div className="px-5 sm:px-6 py-3.5 bg-surface-2/50 border-t border-border flex items-center gap-3">
          {footer}
        </div>
      )}
    </section>
  )
}

// ---------------------------------------------------------------------------
// Form bits
// ---------------------------------------------------------------------------

export const inputCls =
  'w-full px-3 py-2 rounded-xl bg-bg border border-border text-sm text-fg placeholder:text-muted focus:outline-none focus:border-primary'

export function Field({ label, htmlFor, hint, children }) {
  return (
    <div className="space-y-1.5">
      {label && (
        <label className="block text-xs font-medium text-muted" htmlFor={htmlFor}>
          {label}
        </label>
      )}
      {children}
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  )
}

export function PrimaryButton({ busy = false, children, className = '', ...props }) {
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-2 px-4 py-2 rounded-xl text-sm font-medium text-white transition-opacity disabled:opacity-50 disabled:cursor-not-allowed ${className}`}
      style={{ background: 'linear-gradient(135deg, #2456a6, #17b3a3)' }}
    >
      {busy && <Loader2 size={14} className="animate-spin" />}
      {children}
    </button>
  )
}

export function SavedBadge({ show, label = 'Saved' }) {
  if (!show) return null
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400">
      <CheckCircle size={15} />
      {label}
    </span>
  )
}

export function ErrorText({ children }) {
  if (!children) return null
  return <p className="text-sm text-red-600 dark:text-red-400">{children}</p>
}

// ---------------------------------------------------------------------------
// Danger zone
// ---------------------------------------------------------------------------

export function DangerZone({ children }) {
  return (
    <section className="rounded-2xl border border-red-200 dark:border-red-900 bg-surface overflow-hidden">
      <div className="px-5 sm:px-6 py-3.5 bg-red-50 dark:bg-red-950/30 border-b border-red-200 dark:border-red-900 flex items-center gap-2">
        <AlertTriangle size={14} className="text-red-600 dark:text-red-400 shrink-0" />
        <h3 className="font-semibold text-sm text-red-700 dark:text-red-400">Danger zone</h3>
      </div>
      <div className="px-5 sm:px-6 py-5 divide-y divide-border">{children}</div>
    </section>
  )
}

export function DangerRow({ title, description, children, extra }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4 py-1">
      <div className="min-w-0">
        <p className="text-sm font-medium text-fg">{title}</p>
        {description && <p className="text-xs text-muted mt-0.5 max-w-prose">{description}</p>}
        {extra}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  )
}

export function DangerButton({ busy = false, children, className = '', ...props }) {
  return (
    <button
      type="button"
      {...props}
      className={`inline-flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${className}`}
    >
      {busy && <Loader2 size={15} className="animate-spin" />}
      {children}
    </button>
  )
}
