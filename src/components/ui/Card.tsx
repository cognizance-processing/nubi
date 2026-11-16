/**
 * Card — Nubi surface container primitive (Swiss: rules, not shadows).
 *
 * Usage:
 *   <Card>Content</Card>
 *   <Card raised>Ruled card (2px ink top rule)</Card>
 *   <Card interactive onClick={…}>Clickable card (border turns ink on hover)</Card>
 *   <Card interactive as="a" href="/d/1">Link card</Card>
 *
 *   <Card>
 *     <CardHeader>
 *       <CardTitle>Title</CardTitle>
 *       <CardDescription>Subtitle</CardDescription>
 *     </CardHeader>
 *     <CardBody>Content</CardBody>
 *     <CardFooter>
 *       <Button>Save</Button>
 *     </CardFooter>
 *   </Card>
 *
 * Props (Card):
 *   raised       boolean — a 2px ink top rule (cards have no shadow at rest)
 *   interactive  boolean — hover turns the border ink, never lifts or scales;
 *                with onClick on a div it is keyboard focusable (Enter / Space)
 *   as           element type (default 'div')
 *   noPadding    boolean — removes default body padding (for custom layouts)
 *   className, ...rest
 *
 * Sub-components: CardHeader, CardTitle, CardDescription, CardBody, CardFooter
 */

import type { ElementType } from 'react'

function cx(...parts) {
  return parts.filter(Boolean).join(' ')
}

export default function Card({
  raised = false,
  interactive = false,
  noPadding = false,
  as: Comp = 'div' as ElementType,
  className,
  children,
  onKeyDown,
  ...rest
}: {
  raised?: boolean
  interactive?: boolean
  noPadding?: boolean
  as?: ElementType
  className?: string
  children?: React.ReactNode
  [key: string]: any
}) {
  const base = interactive
    ? 'nubi-card-interactive'
    : raised
    ? 'nubi-card-raised'
    : 'nubi-card'

  // A clickable <div> must be reachable and operable from the keyboard
  const clickable = interactive && Comp === 'div' && typeof rest.onClick === 'function'
  const a11y = clickable
    ? {
        role: 'button',
        tabIndex: 0,
        onKeyDown: (e: React.KeyboardEvent<HTMLElement>) => {
          onKeyDown?.(e)
          if (e.defaultPrevented || e.target !== e.currentTarget) return
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            e.currentTarget.click()
          }
        },
      }
    : { onKeyDown }

  return (
    <Comp className={cx(base, !noPadding && 'nubi-card-body', className)} {...a11y} {...rest}>
      {children}
    </Comp>
  )
}

export function CardHeader({ className, children, ...rest }: { className?: string; children?: React.ReactNode; [k: string]: any }) {
  return (
    <div className={cx('nubi-card-header', className)} {...rest}>
      {children}
    </div>
  )
}

export function CardTitle({ className, as: Tag = 'h3' as ElementType, children, ...rest }: { className?: string; as?: ElementType; children?: React.ReactNode; [k: string]: any }) {
  return (
    <Tag className={cx('text-section text-fg', className)} {...rest}>
      {children}
    </Tag>
  )
}

export function CardDescription({ className, children, ...rest }: { className?: string; children?: React.ReactNode; [k: string]: any }) {
  return (
    <p className={cx('text-small text-muted', className)} {...rest}>
      {children}
    </p>
  )
}

export function CardBody({ className, children, ...rest }: { className?: string; children?: React.ReactNode; [k: string]: any }) {
  return (
    <div className={cx('nubi-card-body', className)} {...rest}>
      {children}
    </div>
  )
}

export function CardFooter({ className, children, ...rest }: { className?: string; children?: React.ReactNode; [k: string]: any }) {
  return (
    <div className={cx('nubi-card-footer', className)} {...rest}>
      {children}
    </div>
  )
}
