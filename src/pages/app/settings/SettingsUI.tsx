/**
 * SettingsUI — shared building blocks for the settings pages.
 *
 * Gives every settings section the same anatomy:
 *
 *   <SettingsPageHeader>  — the page's ONE h1: a mono scope eyebrow (from the
 *                           layout), title, one-line description, optional
 *                           actions, closed by the 2px ink rule
 *   <SettingsCard>        — a hairline panel under a 2px ink rule, optional
 *                           header, optional footer bar (where Save lives)
 *   <Field>               — label + control + hint (single column)
 *   <FieldRowGroup>/
 *   <FieldRow>            — label + description on the left, control on the
 *                           right on wide screens, stacked on mobile
 *   <PrimaryButton>       — the one ink primary, with a busy spinner
 *   <SavedBadge>          — transient "Saved" confirmation
 *   <ErrorText>           — inline error message
 *   <Toggle>              — switch (role=switch)
 *   <DangerZone>          — hairline panel with a 2px danger rule
 *   <DangerRow>/<DangerButton>
 *
 * Purely presentational: no data fetching, no business logic. Exports and
 * props are stable: other squads' settings pages import from here.
 */

import { createContext, useContext } from 'react'
import { CheckCircle } from 'lucide-react'
import Button from '../../../components/ui/Button.jsx'
import '../../../components/app/appPatterns.css'
import '../../../shell/shell.css'

/** The layout publishes the current scope ("Organization · Acme") for the eyebrow. */
export const SettingsScopeContext = createContext<string | undefined>(undefined)

// ---------------------------------------------------------------------------
// Page header
// ---------------------------------------------------------------------------

export function SettingsPageHeader({ title, description = undefined, children = undefined }) {
  const scope = useContext(SettingsScopeContext)
  return (
    <header className="sh-head" style={{ marginBottom: 24 }}>
      {scope && <p className="sh-eyebrow">{scope}</p>}
      <div className="sh-head-row">
        <div className="sh-head-text">
          <h1 className="sh-title">{title}</h1>
          {description && <p className="sh-desc">{description}</p>}
        </div>
        {children && <div className="sh-head-actions">{children}</div>}
      </div>
    </header>
  )
}

// ---------------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------------

export function SettingsCard({ title = undefined, description = undefined, children = undefined, footer = undefined }) {
  const hasHeader = Boolean(title || description)
  return (
    <section className="ap-panel mb-6">
      {hasHeader && (
        <div className="ap-panel-head" style={{ paddingInline: 20 }}>
          {title && <h2 className="ap-panel-title">{title}</h2>}
          {description && <p className="ap-panel-sub" style={{ lineHeight: 1.5 }}>{description}</p>}
        </div>
      )}
      <div className="px-5 py-5">{children}</div>
      {footer && (
        <div className="flex min-h-[3rem] items-center gap-3 border-t border-border bg-surface-2 px-5 py-3">
          {footer}
        </div>
      )}
    </section>
  )
}

// ---------------------------------------------------------------------------
// Form bits
// ---------------------------------------------------------------------------

/** Class for a bare <input>/<select>/<textarea> in a settings form. */
export const inputCls = 'nubi-input'

export function Field({ label = undefined, htmlFor = undefined, hint = undefined, children }) {
  return (
    <div className="nubi-field">
      {label && <label className="nubi-label" htmlFor={htmlFor}>{label}</label>}
      {children}
      {hint && <p className="nubi-hint m-0">{hint}</p>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// FieldRow — the "one setting per row" two-column layout
// ---------------------------------------------------------------------------

/**
 * FieldRowGroup — wraps a set of <FieldRow> siblings inside a SettingsCard,
 * giving them a shared hairline between rows.
 */
export function FieldRowGroup({ children, className = '' }) {
  return <div className={['divide-y divide-border', className].join(' ')}>{children}</div>
}

/**
 * FieldRow — label + description on the left, control on the right, on
 * screens wide enough to afford it (lg+). Stacks to a single column on
 * narrower screens.
 */
export function FieldRow({ label = undefined, htmlFor = undefined, description = undefined, hint = undefined, children }) {
  return (
    <div className="grid grid-cols-1 gap-x-8 gap-y-2 py-5 first:pt-0 last:pb-0 lg:grid-cols-[minmax(0,13rem)_1fr]">
      <div className="min-w-0">
        {label && <label className="nubi-label" htmlFor={htmlFor}>{label}</label>}
        {description && <p className="mb-0 mt-1 text-xs leading-relaxed text-muted">{description}</p>}
      </div>
      <div className="min-w-0 max-w-lg space-y-1.5">
        {children}
        {hint && <p className="nubi-hint m-0">{hint}</p>}
      </div>
    </div>
  )
}

/** The one ink primary. Native button props pass through. */
export function PrimaryButton({ busy = false, children, className = '', ...props }) {
  return (
    <Button {...(props as any)} loading={busy} className={className}>
      {children}
    </Button>
  )
}

export function SavedBadge({ show, label = 'Saved' }) {
  if (!show) return null
  return (
    <span className="inline-flex items-center gap-1.5 text-sm" role="status" style={{ color: 'var(--success-fg)' }}>
      <CheckCircle size={14} aria-hidden="true" />
      {label}
    </span>
  )
}

export function ErrorText({ children }) {
  if (!children) return null
  return (
    <p className="nubi-field-error m-0 text-sm" role="alert">
      {children}
    </p>
  )
}

// ---------------------------------------------------------------------------
// Toggle / switch
// ---------------------------------------------------------------------------

/** Toggle — a switch (role=switch): ink when on. */
export function Toggle({ checked, onChange, disabled = false, id = undefined, label = undefined }) {
  return (
    <label className="inline-flex cursor-pointer select-none items-center gap-2.5" htmlFor={id}>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => !disabled && onChange?.(!checked)}
        className="ap-switch disabled:opacity-50"
      >
        <span className="ap-switch-knob" />
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
      className="mb-6 border border-border bg-surface"
      style={{ borderTop: '2px solid var(--danger)', borderRadius: '0 0 2px 2px' }}
      aria-label="Danger zone"
    >
      <div className="flex items-center gap-2 border-b border-border px-5 py-3">
        <span className="nubi-dot nubi-dot-danger" aria-hidden="true" />
        <h2 className="m-0 text-sm font-semibold" style={{ color: 'var(--danger-fg)' }}>Danger zone</h2>
      </div>
      <div className="divide-y divide-border px-5 py-4">{children}</div>
    </section>
  )
}

export function DangerRow({ title, description = undefined, children, extra = undefined }) {
  return (
    <div className="flex flex-col gap-4 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <p className="m-0 text-sm font-semibold leading-tight text-fg">{title}</p>
        {description && (
          <p className="mb-0 mt-1 max-w-prose text-xs leading-relaxed text-muted">{description}</p>
        )}
        {extra}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  )
}

/** A destructive action: outlined in the danger colour, so one click cannot hurt. */
export function DangerButton({ busy = false, children, className = '', ...props }) {
  return (
    <Button
      type="button"
      variant="outline"
      {...(props as any)}
      loading={busy}
      className={['text-danger', className].join(' ')}
      style={{ borderColor: 'var(--danger)', color: 'var(--danger-fg)' }}
    >
      {children}
    </Button>
  )
}
