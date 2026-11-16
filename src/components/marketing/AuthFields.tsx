/**
 * Form parts for the public auth pages (sign in, create account, onboarding,
 * consent): labelled fields with inline validation, a password field with a
 * visibility toggle and strength meter, the Google SSO button, banners.
 *
 * Errors are wired for assistive tech (aria-invalid, aria-describedby, role
 * alert on the banner), shown after a field is left or the form is submitted,
 * and the first invalid field takes focus on submit.
 */
import { useId, useState, type ChangeEvent, type InputHTMLAttributes, type ReactNode } from 'react'
import { Eye, EyeOff, AlertCircle, CheckCircle2 } from 'lucide-react'
import Brand from './Brand.jsx'

import { validators, check, passwordScore, type Validator } from './formLogic.js'
export { validators, check, passwordScore }
export type { Validator }

/** Focus the first invalid control inside a form, by the ids it was given. */
export function focusFirst(ids: string[]) {
  for (const id of ids) {
    const el = document.getElementById(id)
    if (el) { el.focus(); return }
  }
}

export function Field({
  id, label, hint, error, optional, aside, children,
}: {
  id: string
  label: string
  hint?: ReactNode
  error?: string | null
  optional?: boolean
  /** Right side of the label row, for example "Forgot password?" */
  aside?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="mk-field">
      <div className="mk-field-row">
        <label htmlFor={id} className="mk-flabel">{label}{optional && <span className="mk-opt"> · optional</span>}</label>
        {aside}
      </div>
      {children}
      {error
        ? <p id={`${id}-err`} className="mk-ferr" role="alert">{error}</p>
        : hint ? <p id={`${id}-hint`} className="mk-fhint">{hint}</p> : null}
    </div>
  )
}

type TextProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'onChange' | 'value' | 'id'> & {
  id: string
  label: string
  value: string
  onChange: (v: string) => void
  error?: string | null
  hint?: ReactNode
  optional?: boolean
  aside?: ReactNode
  onBlurValue?: (v: string) => void
}

export function TextField({ id, label, value, onChange, error, hint, optional, aside, onBlurValue, ...rest }: TextProps) {
  return (
    <Field id={id} label={label} error={error} hint={hint} optional={optional} aside={aside}>
      <input
        id={id}
        className="mk-input"
        value={value}
        onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(e.target.value)}
        onBlur={e => onBlurValue?.(e.target.value)}
        aria-invalid={error ? 'true' : undefined}
        aria-describedby={error ? `${id}-err` : hint ? `${id}-hint` : undefined}
        {...rest}
      />
    </Field>
  )
}

const STRENGTH = ['', 'Weak', 'Fair', 'Good', 'Strong']
export function PasswordField({
  id, label = 'Password', value, onChange, error, hint, aside, meter = false, autoComplete, placeholder, disabled, onBlurValue,
}: {
  id: string
  label?: string
  value: string
  onChange: (v: string) => void
  error?: string | null
  hint?: ReactNode
  aside?: ReactNode
  meter?: boolean
  autoComplete?: string
  placeholder?: string
  disabled?: boolean
  onBlurValue?: (v: string) => void
}) {
  const [show, setShow] = useState(false)
  const score = passwordScore(value)
  const meterId = useId()
  return (
    <Field id={id} label={label} error={error} hint={hint} aside={aside}>
      <span className="mk-control">
        <input
          id={id}
          className="mk-input mk-pr"
          type={show ? 'text' : 'password'}
          value={value}
          onChange={e => onChange(e.target.value)}
          onBlur={e => onBlurValue?.(e.target.value)}
          autoComplete={autoComplete}
          placeholder={placeholder}
          disabled={disabled}
          aria-invalid={error ? 'true' : undefined}
          aria-describedby={[error ? `${id}-err` : hint ? `${id}-hint` : '', meter && value ? meterId : ''].filter(Boolean).join(' ') || undefined}
        />
        <button type="button" className="mk-eye" onClick={() => setShow(v => !v)} aria-label={show ? 'Hide password' : 'Show password'} aria-pressed={show}>
          {show ? <EyeOff size={17} strokeWidth={1.5} aria-hidden="true" /> : <Eye size={17} strokeWidth={1.5} aria-hidden="true" />}
        </button>
      </span>
      {meter && value && (
        <div id={meterId} aria-live="polite">
          <div className="mk-meter" aria-hidden="true">{[1, 2, 3, 4].map(i => <i key={i} className={score >= i ? 'on' : undefined} />)}</div>
          <p className="mk-fhint mk-mono" style={{ marginTop: 6 }}>Strength: {STRENGTH[score] || 'Too short'}</p>
        </div>
      )}
    </Field>
  )
}

export function SsoButton({ onClick, disabled, children }: { onClick: () => void; disabled?: boolean; children: ReactNode }) {
  return (
    <button type="button" className="mk-sso" onClick={onClick} disabled={disabled}>
      <Brand slug="google" name="Google" mode="brand" size={18} decorative />
      {children}
    </button>
  )
}

export function OrDivider({ children }: { children: ReactNode }) {
  return <div className="mk-or" role="separator"><span>{children}</span></div>
}

export function Banner({ children, kind = 'error', id }: { children: ReactNode; kind?: 'error' | 'ok'; id?: string }) {
  return (
    <div id={id} role={kind === 'error' ? 'alert' : 'status'} className={kind === 'ok' ? 'mk-banner mk-ok' : 'mk-banner'}>
      {kind === 'error' ? <AlertCircle size={16} strokeWidth={1.75} aria-hidden="true" /> : <CheckCircle2 size={16} strokeWidth={1.75} aria-hidden="true" />}
      <span>{children}</span>
    </div>
  )
}

export function Checkbox({
  id, checked, onChange, title, children, disabled,
}: { id: string; checked: boolean; onChange: (v: boolean) => void; title: string; children?: ReactNode; disabled?: boolean }) {
  return (
    <label htmlFor={id} className="mk-check">
      <input id={id} type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} disabled={disabled} />
      <span><b>{title}</b>{children && <span>{children}</span>}</span>
    </label>
  )
}

export function SubmitLabel({ pending, idle, busy }: { pending: boolean; idle: string; busy: string }) {
  return pending ? <><span className="mk-spin" aria-hidden="true" />{busy}</> : <>{idle}</>
}
