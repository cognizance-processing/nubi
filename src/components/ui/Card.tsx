/**
 * Card — Nubi surface container primitive.
 *
 * Usage:
 *   <Card>Content</Card>
 *   <Card raised>Elevated card</Card>
 *   <Card interactive onClick={…}>Clickable card</Card>
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
 *   raised       boolean — stronger shadow
 *   interactive  boolean — cursor pointer + hover lift
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
  className,
  children,
  ...rest
}) {
  const base = interactive
    ? 'nubi-card-interactive'
    : raised
    ? 'nubi-card-raised'
    : 'nubi-card'

  return (
    <div className={cx(base, !noPadding && 'nubi-card-body', className)} {...rest}>
      {children}
    </div>
  )
}

export function CardHeader({ className, children, ...rest }) {
  return (
    <div className={cx('nubi-card-header', className)} {...rest}>
      {children}
    </div>
  )
}

export function CardTitle({ className, as: Tag = 'h3' as ElementType, children, ...rest }) {
  return (
    <Tag className={cx('font-display font-semibold text-base text-fg', className)} {...rest}>
      {children}
    </Tag>
  )
}

export function CardDescription({ className, children, ...rest }) {
  return (
    <p className={cx('text-sm text-muted', className)} {...rest}>
      {children}
    </p>
  )
}

export function CardBody({ className, children, ...rest }) {
  return (
    <div className={cx('nubi-card-body', className)} {...rest}>
      {children}
    </div>
  )
}

export function CardFooter({ className, children, ...rest }) {
  return (
    <div className={cx('nubi-card-footer', className)} {...rest}>
      {children}
    </div>
  )
}
