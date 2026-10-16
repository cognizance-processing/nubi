/**
 * Tooltip — Nubi hover/focus disclosure primitive (Swiss).
 *
 * An ink bubble, 12px, --shadow-pop. Opens after 300ms of hover (instantly on
 * keyboard focus), fades in over 120ms from scale(.97), and while the user is
 * moving between neighbouring tooltips the next one opens at once with no
 * animation. Esc dismisses (WCAG 1.4.13), and the bubble can be hovered without
 * closing. Flips above/below and clamps to the viewport.
 *
 * Usage:
 *   <Tooltip content="Save changes">
 *     <Button variant="ghost" size="icon" aria-label="Save"><Save /></Button>
 *   </Tooltip>
 *
 *   <Tooltip content="Search" kbd={['⌘', 'K']} side="bottom">…</Tooltip>
 *   <Tooltip content="Go to Dashboards" kbd="G D">…</Tooltip>   (string = one key group)
 *
 * Props:
 *   content   string | ReactNode — the tooltip text/content
 *   kbd       string | string[] — keycap(s) shown after the text
 *   side      'top' (default) | 'bottom' | 'left' | 'right'
 *   delay     number (ms) — hover delay before showing (default 300)
 *   disabled  boolean — skip rendering the tooltip
 *   className — applied to the trigger wrapper
 *   children  — a single React element
 *
 * Accessibility: aria-describedby is wired while open. A tooltip is a label
 * supplement, never the only accessible name: icon buttons still need aria-label.
 */

import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react'

function cx(...parts: unknown[]) {
  return parts.filter(Boolean).join(' ')
}

const POSITION = {
  top:    'bottom-full left-1/2 -translate-x-1/2 pb-2',
  bottom: 'top-full left-1/2 -translate-x-1/2 pt-2',
  left:   'right-full top-1/2 -translate-y-1/2 pr-2',
  right:  'left-full top-1/2 -translate-y-1/2 pl-2',
}

// Module-level: when one tooltip closed less than 400ms ago, the next opens instantly
let lastClosed = 0
const WARM_MS = 400

export default function Tooltip({
  content,
  kbd,
  side = 'top',
  delay = 300,
  disabled = false,
  className,
  children,
}: {
  content?: ReactNode
  kbd?: string | string[]
  side?: 'top' | 'bottom' | 'left' | 'right'
  delay?: number
  disabled?: boolean
  className?: string
  children?: any
}) {
  const id = useId()
  const [visible, setVisible] = useState(false)
  const [instant, setInstant] = useState(false)
  const [placed, setPlaced] = useState<{ side: typeof side; dx: number }>({ side, dx: 0 })
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const bubbleRef = useRef<HTMLSpanElement | null>(null)

  const show = (immediate = false) => {
    if (timerRef.current) clearTimeout(timerRef.current)
    const warm = Date.now() - lastClosed < WARM_MS
    const open = () => { setInstant(warm || immediate); setVisible(true) }
    if (immediate || warm) open()
    else timerRef.current = setTimeout(open, delay)
  }
  const hide = () => {
    if (timerRef.current) clearTimeout(timerRef.current)
    setVisible((v) => { if (v) lastClosed = Date.now(); return false })
  }

  // Clamp to the viewport / flip once the bubble is measurable
  useLayoutEffect(() => {
    if (!visible) { setPlaced({ side, dx: 0 }); return }
    const el = bubbleRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    let s = side
    if (side === 'top' && r.top < 4) s = 'bottom'
    else if (side === 'bottom' && r.bottom > window.innerHeight - 4) s = 'top'
    else if (side === 'left' && r.left < 4) s = 'right'
    else if (side === 'right' && r.right > window.innerWidth - 4) s = 'left'
    let dx = 0
    if (s === 'top' || s === 'bottom') {
      if (r.left < 8) dx = 8 - r.left
      else if (r.right > window.innerWidth - 8) dx = window.innerWidth - 8 - r.right
    }
    if (s !== placed.side || dx !== placed.dx) setPlaced({ side: s, dx })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, side])

  // Esc dismisses while open
  useEffect(() => {
    if (!visible) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') hide() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [visible])

  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current) }, [])

  if (disabled || !content) return children

  const keys = kbd ? (Array.isArray(kbd) ? kbd : kbd.split(' ')) : []
  const s = placed.side

  return (
    <span
      className={cx('relative inline-flex', className)}
      onMouseEnter={() => show()}
      onMouseLeave={hide}
      onFocus={() => show(true)}
      onBlur={hide}
    >
      {typeof children === 'object' && children !== null
        ? {
            ...children,
            props: {
              ...children.props,
              'aria-describedby': visible ? id : children.props?.['aria-describedby'],
            },
          }
        : children}

      {visible && (
        <span
          ref={bubbleRef}
          className={cx('absolute z-tooltip', POSITION[s] ?? POSITION.top)}
          style={placed.dx ? { marginLeft: placed.dx } : undefined}
        >
          <span
            id={id}
            role="tooltip"
            className="nubi-tooltip-bubble nubi-tooltip-pop whitespace-nowrap"
            data-instant={instant || undefined}
            data-side={s}
          >
            {content}
            {keys.length > 0 && (
              <span className="inline-flex items-center gap-1" aria-hidden="true">
                {keys.map((k, i) => (
                  <kbd key={i} className="nubi-kbd">{k}</kbd>
                ))}
              </span>
            )}
          </span>
        </span>
      )}
    </span>
  )
}
