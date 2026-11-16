/**
 * Badge — legacy name for Tag. Same look (square, hairline, mono 11px), kept so
 * existing imports keep working. New code: import Tag from './Tag'.
 *
 * Usage:
 *   <Badge>Draft</Badge>
 *   <Badge variant="success" dot>Active</Badge>
 *   <Badge variant="danger" size="sm">Error</Badge>
 *
 * Props:
 *   variant  'default' | 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'accent' | 'ink'
 *   size     'sm' | 'md' (default) | 'lg'
 *   dot      boolean, prepends a 6px square dot in the status colour
 *   className, ...rest
 */

function cx(...parts) {
  return parts.filter(Boolean).join(' ')
}

const VARIANT = {
  default:  '',
  primary:  'nubi-badge-primary',
  success:  'nubi-badge-success',
  warning:  'nubi-badge-warning',
  danger:   'nubi-badge-danger',
  info:     'nubi-badge-info',
  accent:   'nubi-badge-accent',
  ink:      'nubi-badge-ink',
}

const SIZE = {
  sm: 'nubi-badge-sm',
  md: '',
  lg: 'nubi-badge-lg',
}

export default function Badge({
  variant = 'default',
  size = 'md',
  dot = false,
  className = undefined,
  children,
  ...rest
}: {
  variant?: keyof typeof VARIANT | (string & {})
  size?: keyof typeof SIZE | (string & {})
  dot?: boolean
  className?: string
  children?: React.ReactNode
  [key: string]: unknown
}) {
  return (
    <span
      className={cx('nubi-badge', VARIANT[variant] ?? '', SIZE[size] ?? '', className)}
      {...rest}
    >
      {dot && <span className="nubi-badge-dot" aria-hidden="true" />}
      {children}
    </span>
  )
}
