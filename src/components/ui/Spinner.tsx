/**
 * Spinner — Nubi loading indicator primitive.
 *
 * A square-capped 270° arc over a faint full track, drawn in `currentColor`, so
 * it reads on the sheet, inside an ink button, and in dark mode without a
 * colour prop. Under prefers-reduced-motion it stops turning and pulses its
 * opacity slowly instead.
 *
 * Usage:
 *   <Spinner />
 *   <Spinner size="lg" />
 *   <Spinner size="sm" accent />
 *
 * Props:
 *   size    'xs' 14 | 'sm' 16 | 'md' 20 (default) | 'lg' 28 | 'xl' 40
 *   accent  boolean, uses --accent instead of the inherited text colour
 *   label   accessible name (default 'Loading'). Pass '' when a visible label
 *           already says it (e.g. inside a loading Button): the spinner is then
 *           aria-hidden.
 *   className, ...rest
 *
 * For button loading states use Button's `loading` prop.
 */

function cx(...parts: unknown[]) {
  return parts.filter(Boolean).join(' ')
}

const SIZE = {
  xs: 'nubi-spinner-xs',
  sm: 'nubi-spinner-sm',
  md: 'nubi-spinner-md',
  lg: 'nubi-spinner-lg',
  xl: 'nubi-spinner-xl',
}

export default function Spinner({
  size = 'md',
  accent = false,
  label = 'Loading',
  className = undefined,
  ...rest
}: {
  size?: keyof typeof SIZE
  accent?: boolean
  label?: string
  className?: string
} & Omit<React.HTMLAttributes<HTMLSpanElement>, 'className'>) {
  return (
    <span
      role={label ? 'status' : undefined}
      aria-label={label || undefined}
      aria-hidden={label ? undefined : true}
      className={cx('nubi-spinner', SIZE[size] ?? SIZE.md, accent && 'nubi-spinner-accent', className)}
      {...rest}
    >
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false">
        <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity=".22" strokeWidth="3" />
        <path d="M12 3a9 9 0 1 1-9 9" stroke="currentColor" strokeWidth="3" strokeLinecap="square" />
      </svg>
    </span>
  )
}
