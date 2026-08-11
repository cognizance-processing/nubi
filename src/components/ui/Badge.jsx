/**
 * Badge / Chip — Nubi inline label primitive.
 *
 * Usage:
 *   <Badge>Draft</Badge>
 *   <Badge variant="success">Active</Badge>
 *   <Badge variant="danger" size="sm">Error</Badge>
 *   <Badge variant="primary" dot>Live</Badge>
 *
 * Props:
 *   variant  'default' | 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'accent'
 *             default: 'default'
 *   size     'sm' | 'md' (default) | 'lg'
 *   dot      boolean — prepends a small color-matched dot
 *   className, ...rest
 */

function cx(...parts) {
  return parts.filter(Boolean).join(' ')
}

const VARIANT = {
  default:  'nubi-badge-default',
  primary:  'nubi-badge-primary',
  success:  'nubi-badge-success',
  warning:  'nubi-badge-warning',
  danger:   'nubi-badge-danger',
  info:     'nubi-badge-info',
  accent:   'nubi-badge-accent',
}

const SIZE = {
  sm: 'nubi-badge-sm',
  md: '',       // base .nubi-badge already has md sizing
  lg: 'nubi-badge-lg',
}

export default function Badge({
  variant = 'default',
  size = 'md',
  dot = false,
  className = undefined,
  children,
  ...rest
}) {
  return (
    <span
      className={cx(
        VARIANT[variant] ?? VARIANT.default,
        SIZE[size] ?? '',
        className,
      )}
      {...rest}
    >
      {dot && <span className="nubi-badge-dot" aria-hidden="true" />}
      {children}
    </span>
  )
}
