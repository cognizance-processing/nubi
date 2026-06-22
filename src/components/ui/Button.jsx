/**
 * Button — Nubi design-system button primitive.
 *
 * Usage:
 *   <Button>Save</Button>
 *   <Button variant="secondary" size="sm">Cancel</Button>
 *   <Button variant="danger" loading>Deleting…</Button>
 *   <Button variant="ghost" size="icon" aria-label="Close"><X /></Button>
 *
 * Props:
 *   variant  'primary' | 'secondary' | 'ghost' | 'danger' | 'outline' | 'accent'
 *             default: 'primary'
 *   size     'xs' | 'sm' | 'md' | 'lg' | 'xl' | 'icon' | 'icon-sm' | 'icon-lg'
 *             default: 'md'
 *   loading  boolean — shows a spinner and disables interaction
 *   asChild  boolean — renders the first child as the element (not a <button>)
 *             useful for Link buttons: <Button asChild><a href="/foo">Go</a></Button>
 *   className, ...rest — forwarded to the element
 *
 * All buttons get focus-visible rings, transitions, and disabled states.
 */

import { forwardRef } from 'react'
import { Loader2 } from 'lucide-react'

function cx(...parts) {
  return parts.filter(Boolean).join(' ')
}

const VARIANT = {
  primary:   'nubi-btn-primary',
  secondary: 'nubi-btn-secondary',
  ghost:     'nubi-btn-ghost',
  danger:    'nubi-btn-danger',
  outline:   'nubi-btn-outline',
  accent:    'nubi-btn-accent',
}

const SIZE = {
  xs:      'nubi-btn-xs',
  sm:      'nubi-btn-sm',
  md:      'nubi-btn-md',
  lg:      'nubi-btn-lg',
  xl:      'nubi-btn-xl',
  icon:    'nubi-btn-icon',
  'icon-sm': 'nubi-btn-icon-sm',
  'icon-lg': 'nubi-btn-icon-lg',
}

const Button = forwardRef(function Button(
  {
    variant = 'primary',
    size = 'md',
    loading = false,
    asChild = false,
    className,
    disabled,
    children,
    ...rest
  },
  ref
) {
  const classes = cx(
    'nubi-btn',
    SIZE[size] ?? SIZE.md,
    VARIANT[variant] ?? VARIANT.primary,
    className,
  )

  const isDisabled = disabled || loading

  const inner = loading ? (
    <>
      <Loader2 size={14} className="nubi-btn-spinner" aria-hidden="true" />
      {children}
    </>
  ) : children

  if (asChild) {
    // Render the first child element with the button classes applied
    const child = children
    if (!child || typeof child !== 'object') return null
    return {
      ...child,
      props: {
        ...child.props,
        className: cx(classes, child.props?.className),
        ref,
      },
    }
  }

  return (
    <button
      ref={ref}
      className={classes}
      disabled={isDisabled}
      aria-disabled={isDisabled || undefined}
      {...rest}
    >
      {inner}
    </button>
  )
})

Button.displayName = 'Button'
export default Button
