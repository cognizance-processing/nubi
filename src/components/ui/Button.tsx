/**
 * Button — Nubi design-system button primitive (Swiss).
 *
 * Usage:
 *   <Button>Save</Button>                                   ink primary, 36px
 *   <Button variant="secondary" size="sm">Cancel</Button>   hairline, 32px
 *   <Button variant="danger" loading>Deleting…</Button>
 *   <Button variant="ghost" size="icon" aria-label="Close"><X /></Button>
 *
 * Variants: primary (ink fill) | secondary | ghost | outline | danger | accent
 *           (accent is deprecated and renders as primary: the one blue is for
 *           selection, focus and links, never a button fill).
 * Sizes:    xs 28 | sm 32 | md 36 (default) | lg 40 | xl 48 (marketing CTAs)
 *           icon-xs 28 | icon-sm 32 | icon 36 | icon-lg 40 (square: needs aria-label)
 * States:   hover (100ms), :active scale(.98), :focus-visible ring, disabled,
 *           loading (disabled + aria-busy, square-capped spinner, label stays so
 *           the width never jumps).
 *
 * asChild renders the first child as the element (not a <button>), useful for
 * link buttons: <Button asChild><a href="/foo">Go</a></Button>
 */

import { forwardRef, type ReactNode, type ButtonHTMLAttributes } from 'react'
import Spinner from './Spinner'

function cx(...parts: unknown[]) {
  return parts.filter(Boolean).join(' ')
}

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'outline' | 'accent'
type Size = 'xs' | 'sm' | 'md' | 'lg' | 'xl' | 'icon' | 'icon-xs' | 'icon-sm' | 'icon-lg'

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
  'icon-xs': 'nubi-btn-icon-xs',
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
      <Spinner size="xs" className="nubi-btn-spinner" label="" />
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
        'aria-disabled': isDisabled || undefined,
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
      aria-busy={loading || undefined}
      data-loading={loading || undefined}
      {...rest}
    >
      {inner}
    </button>
  )
})

Button.displayName = 'Button'
export default Button
