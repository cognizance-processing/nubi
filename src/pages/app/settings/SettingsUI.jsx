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
 *                           (single-column; used inside compact inline forms)
 *   <FieldRowGroup>/
 *   <FieldRow>             — label + description on the left, control on the
 *                           right on wide screens (single column on mobile).
 *                           Use inside a <SettingsCard> for the "one setting
 *                           per row" pattern instead of Field when the card
 *                           has room to breathe.
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
        <h2 className="font-display font-semibold text-xl text-fg tracking-tight">{title}</h2>
        {description && (
          <p className="text-sm text-muted mt-1 max-w-2xl leading-relaxed">{description}</p>
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
        <div className="px-5 sm:px-6 pt-4 pb-3.5 border-b border-border bg-surface-2/30">
          {title && (
            <h3 className="font-display font-semibold text-[0.9375rem] text-fg leading-tight">
              {title}
            </h3>
          )}
          {description && (
            <p className="text-xs text-muted mt-0.5 leading-relaxed">{description}</p>
          )}
        </div>
      )}
      <div className="px-5 sm:px-6 py-5">{children}</div>
      {footer && (
        <div className="px-5 sm:px-6 py-3 bg-surface-2/40 border-t border-border flex items-center gap-3 min-h-[3rem]">
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
  'w-full px-3 py-2 rounded-xl bg-bg border border-border text-sm text-fg placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-ring/50 focus:border-ring/60 transition-[border-color,box-shadow] duration-100'

export function Field({ label, htmlFor, hint, children }) {
  return (
    <div className="space-y-1.5">
      {label && (
        <label className="block text-xs font-medium text-muted uppercase tracking-wide" htmlFor={htmlFor}>
          {label}
        </label>
      )}
      {children}
      {hint && <p className="text-xs text-muted leading-relaxed">{hint}</p>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// FieldRow — the "one setting per row" two-column layout
// ---------------------------------------------------------------------------

/**
 * FieldRowGroup — wraps a set of <FieldRow> siblings inside a SettingsCard,
 * giving them a shared divider between rows (first/last rows lose their
 * outer padding so they sit flush with the card's own padding).
 */
export function FieldRowGroup({ children, className = '' }) {
  return <div className={['divide-y divide-border', className].join(' ')}>{children}</div>
}

/**
 * FieldRow — label + description on the left, control on the right, on
 * screens wide enough to afford it (lg+). Stacks to a single column (label
 * above control, like <Field>) on narrower screens. Meant to live inside a
 * <FieldRowGroup> inside a <SettingsCard> so a wide card doesn't leave a
 * form stranded in a narrow column with empty space beside it.
 */
export function FieldRow({ label, htmlFor, description, hint, children }) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,15rem)_1fr] gap-x-8 gap-y-2 py-5 first:pt-0 last:pb-0">
      <div className="min-w-0">
        {label && (
          <label className="block text-sm font-medium text-fg" htmlFor={htmlFor}>
            {label}
          </label>
        )}
        {description && (
          <p className="text-xs text-muted mt-1 leading-relaxed">{description}</p>
        )}
      </div>
      <div className="min-w-0 max-w-lg space-y-1.5">
        {children}
        {hint && <p className="text-xs text-muted leading-relaxed">{hint}</p>}
      </div>
    </div>
  )
}

export function PrimaryButton({ busy = false, children, className = '', ...props }) {
  return (
    <button
      {...props}
      className={[
        'inline-flex items-center justify-center gap-2 px-4 py-2 rounded-xl',
        'text-sm font-medium text-white',
        'transition-opacity duration-150',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
        className,
      ].join(' ')}
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
    <span className="inline-flex items-center gap-1.5 text-sm text-success">
      <CheckCircle size={14} aria-hidden="true" />
      {label}
    </span>
  )
}

export function ErrorText({ children }) {
  if (!children) return null
  return (
    <p className="text-sm text-danger" role="alert">
      {children}
    </p>
  )
}

// ---------------------------------------------------------------------------
// Toggle / switch
// ---------------------------------------------------------------------------

/**
 * Toggle — native checkbox styled as an accessible switch.
 * Wraps .nubi-switch + .nubi-switch-thumb from the design system CSS.
 */
export function Toggle({ checked, onChange, disabled, id, label }) {
  return (
    <label
      className="inline-flex items-center gap-2.5 cursor-pointer select-none"
      htmlFor={id}
    >
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => !disabled && onChange?.(!checked)}
        className="nubi-switch"
      >
        <span className="nubi-switch-thumb" />
      </button>
      {label && <span className="text-sm text-fg">{label}</span>}
    </label>
  )
}

// ---------------------------------------------------------------------------
// Danger zone
// ---------------------------------------------------------------------------

export function DangerZone({ children }) {
  return (
    <section
      className="rounded-2xl border border-red-200 dark:border-red-900/60 bg-surface overflow-hidden"
      aria-label="Danger zone"
    >
      <div className="px-5 sm:px-6 py-3 bg-red-50 dark:bg-red-950/25 border-b border-red-200 dark:border-red-900/60 flex items-center gap-2">
        <AlertTriangle size={13} className="text-red-600 dark:text-red-400 shrink-0" aria-hidden="true" />
        <h3 className="font-display font-semibold text-sm text-red-700 dark:text-red-400">Danger zone</h3>
      </div>
      <div className="px-5 sm:px-6 py-4 divide-y divide-border">{children}</div>
    </section>
  )
}

export function DangerRow({ title, description, children, extra }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4 py-3 first:pt-0 last:pb-0">
      <div className="min-w-0">
        <p className="text-sm font-medium text-fg leading-tight">{title}</p>
        {description && (
          <p className="text-xs text-muted mt-0.5 max-w-prose leading-relaxed">{description}</p>
        )}
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
      className={[
        'inline-flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium',
        'text-red-600 dark:text-red-400',
        'border border-red-200 dark:border-red-800/70',
        'hover:bg-red-50 dark:hover:bg-red-950/30',
        'transition-colors duration-100',
        'disabled:opacity-40 disabled:cursor-not-allowed',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400/50',
        className,
      ].join(' ')}
    >
      {busy && <Loader2 size={14} className="animate-spin" />}
      {children}
    </button>
  )
}
