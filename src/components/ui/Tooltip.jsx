/**
 * Tooltip — Nubi hover/focus disclosure primitive.
 *
 * Pure CSS-first, no external positioning libraries. Uses CSS custom property
 * `--tooltip-offset` for fine-tuning offset. The tooltip shows on hover AND
 * focus-visible so keyboard users are covered.
 *
 * Usage:
 *   <Tooltip content="Save changes">
 *     <Button variant="ghost" size="icon"><Save /></Button>
 *   </Tooltip>
 *
 *   <Tooltip content="Requires Pro plan" side="right">
 *     <span><Badge variant="info">Pro</Badge></span>
 *   </Tooltip>
 *
 * Props:
 *   content   string | ReactNode — the tooltip text/content
 *   side      'top' (default) | 'bottom' | 'left' | 'right'
 *   delay     number (ms) — hover delay before showing (default 300)
 *   disabled  boolean — skip rendering tooltip
 *   className — applied to the trigger wrapper
 *   children  — must be a single React element that accepts ref
 *
 * Accessibility: aria-describedby wired automatically.
 */

import { useId, useRef, useState } from 'react'

function cx(...parts) {
  return parts.filter(Boolean).join(' ')
}

const POSITION = {
  top:    'bottom-full left-1/2 -translate-x-1/2 mb-2',
  bottom: 'top-full  left-1/2 -translate-x-1/2 mt-2',
  left:   'right-full top-1/2 -translate-y-1/2 mr-2',
  right:  'left-full  top-1/2 -translate-y-1/2 ml-2',
}

export default function Tooltip({
  content,
  side = 'top',
  delay = 300,
  disabled = false,
  className,
  children,
}) {
  const id = useId()
  const [visible, setVisible] = useState(false)
  const timerRef = useRef(null)

  if (disabled || !content) return children

  const show = () => {
    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => setVisible(true), delay)
  }
  const hide = () => {
    clearTimeout(timerRef.current)
    setVisible(false)
  }

  return (
    <span
      className={cx('relative inline-flex', className)}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      {/* Trigger */}
      {typeof children === 'object' && children !== null
        ? {
            ...children,
            props: {
              ...children.props,
              'aria-describedby': visible ? id : undefined,
            },
          }
        : children}

      {/* Bubble */}
      {visible && (
        <span
          id={id}
          role="tooltip"
          className={cx(
            'nubi-tooltip-bubble absolute whitespace-nowrap pointer-events-none',
            'nubi-animate-fade-in',
            POSITION[side] ?? POSITION.top,
          )}
        >
          {content}
        </span>
      )}
    </span>
  )
}
