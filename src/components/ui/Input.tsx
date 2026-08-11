/**
 * Input / Select / Textarea — Nubi form field primitives.
 *
 * ── Input ──────────────────────────────────────────────────────────────────
 * Usage:
 *   <Input placeholder="Search…" />
 *   <Input size="lg" error="Required" />
 *
 *   <Field label="Email" hint="We'll never share this" error={errors.email}>
 *     <Input type="email" {...register('email')} />
 *   </Field>
 *
 * Props (Input):
 *   size     'sm' | 'md' (default) | 'lg'
 *   error    string | boolean — applies error styling; string shown below field
 *   hint     string — helper text shown below field (when no error)
 *   label    string — shortcut: renders a Field wrapper with a label
 *             (alternative: use <Field> for full control)
 *
 * ── Field ──────────────────────────────────────────────────────────────────
 * Wrapper that composes label + input + hint/error in one unit.
 * Props: label, hint, error, required, id, className, children
 *
 * ── Select ─────────────────────────────────────────────────────────────────
 * Usage: <Select options={[{value,label}]} />
 * Props: same as Input + options array
 *
 * ── Textarea ───────────────────────────────────────────────────────────────
 * Usage: <Textarea rows={4} placeholder="Notes…" />
 * Same props as Input.
 */

import { forwardRef, useId, type ComponentPropsWithoutRef, type ReactNode } from 'react'

function cx(...parts) {
  return parts.filter(Boolean).join(' ')
}

// ── Field ──────────────────────────────────────────────────────────────────

interface FieldProps {
  label?: ReactNode
  hint?: ReactNode
  error?: ReactNode | boolean
  required?: boolean
  id?: string
  className?: string
  children?: ReactNode | ((props: { id: string; 'aria-describedby'?: string }) => ReactNode)
}

export function Field({
  label,
  hint,
  error,
  required,
  id: idProp,
  className,
  children,
}: FieldProps) {
  const autoId = useId()
  const id = idProp || autoId
  return (
    <div className={cx('nubi-field', className)}>
      {label && (
        <label htmlFor={id} className="nubi-label">
          {label}
          {required && <span className="text-red-500 ml-0.5" aria-hidden="true">*</span>}
        </label>
      )}
      {/* Clone child to inject id if it's a single element */}
      {typeof children === 'function'
        ? children({ id, 'aria-describedby': error ? `${id}-err` : hint ? `${id}-hint` : undefined })
        : children}
      {error && typeof error === 'string' && (
        <p id={`${id}-err`} role="alert" className="nubi-field-error">
          {error}
        </p>
      )}
      {!error && hint && (
        <p id={`${id}-hint`} className="nubi-hint">
          {hint}
        </p>
      )}
    </div>
  )
}

// ── Input ──────────────────────────────────────────────────────────────────

interface InputProps extends Omit<ComponentPropsWithoutRef<'input'>, 'size'> {
  size?: 'sm' | 'md' | 'lg'
  error?: ReactNode | boolean
  hint?: ReactNode
  label?: ReactNode
  required?: boolean
}

const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { size = 'md', error, hint, label, required, className, ...rest },
  ref
) {
  const sizeClass = size === 'sm' ? 'nubi-input-sm' : size === 'lg' ? 'nubi-input-lg' : 'nubi-input'
  const cls = cx(sizeClass, error && 'nubi-input-error', className)

  const input = <input ref={ref} className={cls} aria-invalid={error ? 'true' : undefined} {...rest} />

  // Shortcut: if label / hint / error provided inline, wrap in a Field
  if (label || hint || (error && typeof error === 'string')) {
    return (
      <Field label={label} hint={hint} error={error} required={required}>
        {input}
      </Field>
    )
  }
  return input
})

Input.displayName = 'Input'
export default Input

// ── Select ──────────────────────────────────────────────────────────────────

interface SelectProps extends Omit<ComponentPropsWithoutRef<'select'>, 'size'> {
  size?: 'sm' | 'md' | 'lg'
  error?: ReactNode | boolean
  hint?: ReactNode
  label?: ReactNode
  required?: boolean
  options?: (string | { value: any; label: ReactNode; disabled?: boolean })[]
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { size = 'md', error, hint, label, required, options = [], className, children, ...rest },
  ref
) {
  const sizeClass = size === 'sm' ? 'nubi-input-sm' : size === 'lg' ? 'nubi-input-lg' : 'nubi-input'
  const cls = cx('nubi-select', sizeClass, error && 'nubi-input-error', className)

  const el = (
    <select ref={ref} className={cls} aria-invalid={error ? 'true' : undefined} {...rest}>
      {options.map((o) =>
        typeof o === 'string'
          ? <option key={o} value={o}>{o}</option>
          : <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>
      )}
      {children}
    </select>
  )

  if (label || hint || (error && typeof error === 'string')) {
    return <Field label={label} hint={hint} error={error} required={required}>{el}</Field>
  }
  return el
})

Select.displayName = 'Select'

// ── Textarea ─────────────────────────────────────────────────────────────────

interface TextareaProps extends Omit<ComponentPropsWithoutRef<'textarea'>, 'size'> {
  size?: 'sm' | 'md' | 'lg'
  error?: ReactNode | boolean
  hint?: ReactNode
  label?: ReactNode
  required?: boolean
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { size = 'md', error, hint, label, required, className, ...rest },
  ref
) {
  const sizeClass = size === 'sm' ? 'nubi-input-sm' : size === 'lg' ? 'nubi-input-lg' : ''
  const cls = cx('nubi-textarea', sizeClass, error && 'nubi-input-error', className)

  const el = (
    <textarea ref={ref} className={cls} aria-invalid={error ? 'true' : undefined} {...rest} />
  )

  if (label || hint || (error && typeof error === 'string')) {
    return <Field label={label} hint={hint} error={error} required={required}>{el}</Field>
  }
  return el
})

Textarea.displayName = 'Textarea'
