/**
 * Input / Select / Textarea / Field / Checkbox — Nubi form primitives (Swiss).
 *
 * Heights mirror Button so a field and a button on one row line up:
 *   xs 28 | sm 32 | md 36 (default) | lg 40 | xl 48
 * 1px --border-control edge (3:1, WCAG 1.4.11), 2px radius, sheet fill; hover
 * darkens the edge, focus turns it --ring and draws the 2px outline.
 *
 * ── Input ──────────────────────────────────────────────────────────────────
 *   <Input placeholder="Search…" />
 *   <Input size="lg" error="Required" />
 *   <Input icon={<Search size={16} />} placeholder="Search dashboards" />
 *   <Input type="password" trailing={<button aria-label="Show password">…</button>} />
 *   <Field label="Email" hint="We'll never share this" error={errors.email}>
 *     <Input type="email" {...register('email')} />
 *   </Field>
 *
 * Props (Input):
 *   size     'xs' | 'sm' | 'md' (default) | 'lg' | 'xl'
 *   icon     ReactNode, leading icon (16px). The left gutter exists ONLY when
 *            this is set, so a field without an icon is never indented.
 *   trailing ReactNode, trailing slot (a button, a unit). Right gutter only
 *            when set.
 *   error    string | boolean, error styling; a string is shown below the field
 *   hint     string, helper text (shown when there is no error)
 *   label    string, shortcut that wraps the control in a Field
 *
 * ── Field ──────────────────────────────────────────────────────────────────
 *   Label + control + hint/error as one unit. Props: label, hint, error,
 *   required, id, className, children (an element, or a render function that
 *   receives { id, 'aria-describedby' }).
 *
 * ── Select / Textarea ──────────────────────────────────────────────────────
 *   <Select options={[{value,label}]} />    <Textarea rows={4} />
 *   Same props as Input (no icon/trailing).
 *
 * ── Checkbox ───────────────────────────────────────────────────────────────
 *   <Checkbox checked={all} indeterminate={some} onChange={toggleAll} aria-label="Select all" />
 *   <Checkbox label="Remember me" />
 */

import { forwardRef, useEffect, useId, useRef, type ComponentPropsWithoutRef, type ReactNode } from 'react'

function cx(...parts: unknown[]) {
  return parts.filter(Boolean).join(' ')
}

type Size = 'xs' | 'sm' | 'md' | 'lg' | 'xl'
const SIZE_CLASS: Record<Size, string> = {
  xs: 'nubi-input-xs',
  sm: 'nubi-input-sm',
  md: 'nubi-input-md',
  lg: 'nubi-input-lg',
  xl: 'nubi-input-xl',
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
          {required && <span className="text-danger ml-0.5" aria-hidden="true">*</span>}
        </label>
      )}
      {typeof children === 'function'
        ? children({ id, 'aria-describedby': error ? `${id}-err` : hint ? `${id}-hint` : undefined })
        : children}
      {error && typeof error !== 'boolean' && (
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
  size?: Size
  error?: ReactNode | boolean
  hint?: ReactNode
  label?: ReactNode
  required?: boolean
  icon?: ReactNode
  trailing?: ReactNode
}

const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { size = 'md', error, hint, label, required, icon, trailing, className, id, ...rest },
  ref
) {
  const autoId = useId()
  const inputId = id || autoId
  const cls = cx('nubi-input', SIZE_CLASS[size] ?? SIZE_CLASS.md, error && 'nubi-input-error', className)

  const field = !!(label || hint || (error && typeof error !== 'boolean'))
  const describedBy = field ? (error ? `${inputId}-err` : hint ? `${inputId}-hint` : undefined) : undefined

  const control = (
    <input
      ref={ref}
      id={inputId}
      className={cls}
      aria-invalid={error ? 'true' : undefined}
      aria-describedby={describedBy}
      required={required}
      {...rest}
    />
  )

  const input = icon || trailing ? (
    <span
      className="nubi-input-wrap"
      data-size={size}
      data-icon={icon ? '' : undefined}
      data-trailing={trailing ? '' : undefined}
    >
      {icon && <span className="nubi-input-icon" aria-hidden="true">{icon}</span>}
      {control}
      {trailing && <span className="nubi-input-trailing">{trailing}</span>}
    </span>
  ) : control

  // Shortcut: if label / hint / error provided inline, wrap in a Field
  if (field) {
    return (
      <Field label={label} hint={hint} error={error} required={required} id={inputId}>
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
  size?: Size
  error?: ReactNode | boolean
  hint?: ReactNode
  label?: ReactNode
  required?: boolean
  options?: (string | { value: any; label: ReactNode; disabled?: boolean })[]
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { size = 'md', error, hint, label, required, options = [], className, children, id, ...rest },
  ref
) {
  const autoId = useId()
  const selectId = id || autoId
  const cls = cx('nubi-input', SIZE_CLASS[size] ?? SIZE_CLASS.md, 'nubi-select', error && 'nubi-input-error', className)
  const field = !!(label || hint || (error && typeof error !== 'boolean'))

  const el = (
    <select
      ref={ref}
      id={selectId}
      className={cls}
      aria-invalid={error ? 'true' : undefined}
      aria-describedby={field ? (error ? `${selectId}-err` : hint ? `${selectId}-hint` : undefined) : undefined}
      {...rest}
    >
      {options.map((o) =>
        typeof o === 'string'
          ? <option key={o} value={o}>{o}</option>
          : <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>
      )}
      {children}
    </select>
  )

  if (field) {
    return <Field label={label} hint={hint} error={error} required={required} id={selectId}>{el}</Field>
  }
  return el
})

Select.displayName = 'Select'

// ── Textarea ─────────────────────────────────────────────────────────────────

interface TextareaProps extends Omit<ComponentPropsWithoutRef<'textarea'>, 'size'> {
  size?: Size
  error?: ReactNode | boolean
  hint?: ReactNode
  label?: ReactNode
  required?: boolean
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { size = 'md', error, hint, label, required, className, id, ...rest },
  ref
) {
  const autoId = useId()
  const areaId = id || autoId
  // Textarea height is content-driven; the size only sets type size + padding
  const cls = cx('nubi-input', SIZE_CLASS[size] ?? SIZE_CLASS.md, 'nubi-textarea', error && 'nubi-input-error', className)
  const field = !!(label || hint || (error && typeof error !== 'boolean'))

  const el = (
    <textarea
      ref={ref}
      id={areaId}
      className={cls}
      aria-invalid={error ? 'true' : undefined}
      aria-describedby={field ? (error ? `${areaId}-err` : hint ? `${areaId}-hint` : undefined) : undefined}
      {...rest}
    />
  )

  if (field) {
    return <Field label={label} hint={hint} error={error} required={required} id={areaId}>{el}</Field>
  }
  return el
})

Textarea.displayName = 'Textarea'

// ── Checkbox ─────────────────────────────────────────────────────────────────

interface CheckboxProps extends Omit<ComponentPropsWithoutRef<'input'>, 'type' | 'size'> {
  indeterminate?: boolean
  label?: ReactNode
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { indeterminate = false, label, className, ...rest },
  ref
) {
  const inner = useRef<HTMLInputElement | null>(null)
  useEffect(() => {
    if (inner.current) inner.current.indeterminate = indeterminate
  }, [indeterminate])

  const box = (
    <input
      type="checkbox"
      ref={(node) => {
        inner.current = node
        if (typeof ref === 'function') ref(node)
        else if (ref) (ref as { current: HTMLInputElement | null }).current = node
      }}
      className={cx('nubi-check', className)}
      aria-checked={indeterminate ? 'mixed' : undefined}
      {...rest}
    />
  )
  if (!label) return box
  return (
    <label className="inline-flex items-center gap-2 text-small text-fg cursor-pointer select-none">
      {box}
      {label}
    </label>
  )
})

Checkbox.displayName = 'Checkbox'
