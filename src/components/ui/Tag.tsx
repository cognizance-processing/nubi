/**
 * Tag — Nubi's one inline label. Replaces every pill, chip and badge.
 *
 * Square (2px radius), hairline border, Geist Mono 11px. Colour is carried by a
 * 6px square dot and the text, never by a tinted fill; the only filled variant
 * is `ink`. Interactive tags (as="button" | "a") get the focus ring and an ink
 * border on hover. `onRemove` turns it into a removable filter chip.
 *
 * Usage:
 *   <Tag>Draft</Tag>
 *   <Tag variant="success" dot>Live</Tag>
 *   <Tag variant="danger" dot>Not in prod</Tag>
 *   <Tag variant="ink">Most popular</Tag>
 *   <Tag size="md" caps={false} onRemove={() => clear('region')}>Region is EMEA</Tag>
 *
 * Props:
 *   variant  'neutral' (default) | 'ink' | 'accent' | 'danger' | 'success' | 'warning'
 *   dot      boolean, 6px square dot in the variant colour
 *   size     'sm' 20px (default) | 'md' 24px (filter chip)
 *   caps     boolean, uppercase + tracking (default true). Use false for
 *            user-authored names (folder names, engines, column names).
 *   onRemove () => void, adds an x button labelled "Remove <text>"
 *   as       'span' (default) | 'button' | 'a'
 */

import { X } from 'lucide-react'
import type { ElementType, ReactNode } from 'react'

function cx(...parts: unknown[]) {
  return parts.filter(Boolean).join(' ')
}

export type TagVariant = 'neutral' | 'ink' | 'accent' | 'danger' | 'success' | 'warning'

const VARIANT: Record<TagVariant, string> = {
  neutral: '',
  ink: 'nubi-tag-ink',
  accent: 'nubi-tag-accent',
  danger: 'nubi-tag-danger',
  success: 'nubi-tag-success',
  warning: 'nubi-tag-warning',
}

export interface TagProps {
  variant?: TagVariant
  dot?: boolean
  size?: 'sm' | 'md'
  caps?: boolean
  onRemove?: () => void
  as?: ElementType
  className?: string
  children?: ReactNode
  [key: string]: unknown
}

export default function Tag({
  variant = 'neutral',
  dot = false,
  size = 'sm',
  caps = true,
  onRemove,
  as: Comp = 'span',
  className,
  children,
  ...rest
}: TagProps) {
  const label = typeof children === 'string' ? children : undefined
  return (
    <Comp
      className={cx(
        'nubi-tag',
        VARIANT[variant] ?? '',
        size === 'md' && 'nubi-tag-md',
        !caps && 'nubi-tag-nocaps',
        className,
      )}
      {...(Comp === 'button' ? { type: 'button' } : null)}
      {...rest}
    >
      {dot && <span className="nubi-tag-dot" aria-hidden="true" />}
      {children}
      {onRemove && (
        <button
          type="button"
          className="nubi-tag-remove"
          aria-label={label ? `Remove ${label}` : 'Remove'}
          onClick={(e) => { e.stopPropagation(); onRemove() }}
        >
          <X size={12} strokeWidth={2} aria-hidden="true" />
        </button>
      )}
    </Comp>
  )
}
