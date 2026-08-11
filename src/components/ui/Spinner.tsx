/**
 * Spinner — Nubi loading indicator primitive.
 *
 * Usage:
 *   <Spinner />
 *   <Spinner size="lg" />
 *   <Spinner size="sm" accent />
 *
 * Props:
 *   size    'xs' | 'sm' | 'md' (default) | 'lg' | 'xl'
 *   accent  boolean — uses teal accent color instead of primary
 *   label   string — accessible sr-only label (default 'Loading')
 *   className, ...rest
 *
 * For button loading states use Button's loading prop instead.
 */

function cx(...parts) {
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
}) {
  return (
    <span
      role="status"
      aria-label={label}
      className={cx(
        SIZE[size] ?? SIZE.md,
        accent && 'nubi-spinner-accent',
        'shrink-0',
        className,
      )}
      {...rest}
    >
      <span className="sr-only">{label}</span>
    </span>
  )
}
