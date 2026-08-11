/**
 * Button — Nubi design-system button primitive.
 *
 * Usage:
 *   <Button>Save</Button>
 *   <Button variant="secondary" size="sm">Cancel</Button>
 *   <Button variant="danger" loading>Deleting…</Button>
 *   <Button variant="ghost" size="icon" aria-label="Close"><X /></Button>
 *
 * asChild renders the first child as the element (not a <button>) — useful
 * for Link buttons: <Button asChild><a href="/foo">Go</a></Button>
 *
 * All buttons get focus-visible rings, transitions, and disabled states.
 */

import { forwardRef, type ReactNode, type ButtonHTMLAttributes } from 'react'
import { Loader2 } from 'lucide-react'

function cx(...parts: unknown[]) {
  return parts.filter(Boolean).join(' ')
}

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'outline' | 'accent'
type Size = 'xs' | 'sm' | 'md' | 'lg' | 'xl' | 'icon' | 'icon-sm' | 'icon-lg'

const VARIANT: Record<Variant, string> = {
  primary:   'nubi-btn-primary',
  secondary: 'nubi-btn-secondary',
  ghost:     'nubi-btn-ghost',
  danger:    'nubi-btn-danger',
  outline:   'nubi-btn-outline',
  accent:    'nubi-btn-accent',
}

const SIZE: Record<Size, string> = {
  xs:      'nubi-btn-xs',
  sm:      'nubi-btn-sm',
  md:      'nubi-btn-md',
  lg:      'nubi-btn-lg',
  xl:      'nubi-btn-xl',
  icon:    'nubi-btn-icon',
  'icon-sm': 'nubi-btn-icon-sm',
  'icon-lg': 'nubi-btn-icon-lg',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  loading?: boolean
  asChild?: boolean
  children?: ReactNode
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'primary',
    size = 'md',
    loading = false,
    asChild = false,
    className = undefined,
    disabled = undefined,
    children = undefined,
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
    // Render the first child element with the button classes applied.
    // Not React.cloneElement: that reads ref.current synchronously during
    // render, which React 19's ref-as-prop model flags as unsafe. This
    // constructs the merged element manually instead.
    const child = children as any
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
