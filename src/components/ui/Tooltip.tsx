/**
 * Tooltip — Nubi hover/focus disclosure primitive (Swiss).
 *
 * An ink bubble, 12px, --shadow-pop. Opens after 300ms of hover (instantly on
 * keyboard focus), fades in over 120ms from scale(.97), and while the user is
 * moving between neighbouring tooltips the next one opens at once with no
 * animation. Esc dismisses (WCAG 1.4.13), and the bubble can be hovered without
 * closing. Flips above/below and clamps to the viewport.
 *
 * The bubble is portaled to document.body with position: fixed (placeTooltip /
 * useAnchorTracking in ./useFixedAnchor), so `overflow` ancestors such as the
 * horizontally scrolling editor toolbar can't clip it; it follows the trigger
 * on scroll and resize. It is not a DOM child of the trigger, so hover is
 * bridged explicitly: leaving the trigger or the bubble starts a short grace
 * timer that entering either one cancels.
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

import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { placeTooltip, useAnchorTracking, type TooltipPlacement, type TooltipSide } from './useFixedAnchor.js'

function cx(...parts: unknown[]) {
  return parts.filter(Boolean).join(' ')
}

/** Per-glyph optical centre in tooltip caps (font metrics differ: ⌘ high, ⇧ low). */
function tooltipKbdClass(k: string) {
  if (/^[⌘⌃⌥]$/.test(k)) return cx('nubi-kbd', 'nubi-kbd-glyph-high')
  if (/^[⇧↑↓←→]$/.test(k)) return cx('nubi-kbd', 'nubi-kbd-glyph-arrow')
  if (/^[⏎⌫↵]$/.test(k)) return cx('nubi-kbd', 'nubi-kbd-glyph-action')
  return 'nubi-kbd'
}

// Padding on the portaled wrapper: the visual gap, and the hover bridge between
// trigger and bubble. placeTooltip sits the wrapper flush against the trigger.
const PADDING: Record<TooltipSide, string> = {
  top: 'pb-2',
  bottom: 'pt-2',
  left: 'pr-2',
  right: 'pl-2',
}

// Module-level: when one tooltip closed less than 400ms ago, the next opens instantly
let lastClosed = 0
const WARM_MS = 400
// Pointer may travel trigger <-> bubble for this long without the tooltip closing
const GRACE_MS = 100
// The open tooltip. A neighbour that starts opening closes it at once, so two
// bubbles never overlap during the grace period and the neighbour opens warm.
let active: { owner: string; hide: () => void } | null = null

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
  side?: TooltipSide
  delay?: number
  disabled?: boolean
  className?: string
  children?: any
}) {
  const id = useId()
  const [visible, setVisible] = useState(false)
  const [instant, setInstant] = useState(false)
  const [pos, setPos] = useState<TooltipPlacement | null>(null)
  const openRef = useRef(false)
  const showTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const hideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const triggerRef = useRef<HTMLSpanElement | null>(null)
  const bubbleRef = useRef<HTMLSpanElement | null>(null)

  const clearShow = () => { if (showTimerRef.current) { clearTimeout(showTimerRef.current); showTimerRef.current = null } }
  const clearHide = () => { if (hideTimerRef.current) { clearTimeout(hideTimerRef.current); hideTimerRef.current = null } }

  const hideNow = () => {
    clearShow()
    clearHide()
    if (active?.owner === id) active = null
    if (openRef.current) { openRef.current = false; lastClosed = Date.now() }
    setVisible(false)
    setPos(null)
  }

  const show = (immediate = false) => {
    clearHide()
    if (openRef.current) return
    if (active && active.owner !== id) active.hide()
    clearShow()
    const warm = Date.now() - lastClosed < WARM_MS
    const open = () => {
      showTimerRef.current = null
      openRef.current = true
      active = { owner: id, hide: hideNow }
      setInstant(warm || immediate)
      setVisible(true)
    }
    if (immediate || warm) open()
    else showTimerRef.current = setTimeout(open, delay)
  }

  // Pointer left the trigger or the bubble: close unless it enters the other soon
  const hideSoon = () => {
    clearShow()
    if (!openRef.current) return
    clearHide()
    hideTimerRef.current = setTimeout(hideNow, GRACE_MS)
  }

  // Place against the trigger before paint, then follow it on scroll / resize
  useAnchorTracking(visible, () => {
    const trigger = triggerRef.current
    const bubble = bubbleRef.current
    if (!trigger || !bubble) return
    const p = placeTooltip(trigger.getBoundingClientRect(), bubble, side)
    setPos((prev) => (prev && prev.left === p.left && prev.top === p.top && prev.side === p.side ? prev : p))
  })

  // Esc dismisses while open
  useEffect(() => {
    if (!visible) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') hideNow() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible])

  useEffect(() => () => {
    if (showTimerRef.current) clearTimeout(showTimerRef.current)
    if (hideTimerRef.current) clearTimeout(hideTimerRef.current)
    if (active?.owner === id) active = null
  }, [id])

  if (disabled || !content) return children

  const keys = kbd ? (Array.isArray(kbd) ? kbd : kbd.split(' ')) : []
  const s = pos?.side ?? side

  return (
    <span
      ref={triggerRef}
      className={cx('relative inline-flex items-center', className)}
      onMouseEnter={() => show()}
      onMouseLeave={hideSoon}
      onFocus={() => show(true)}
      onBlur={hideNow}
      onClick={hideNow}
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

      {visible && createPortal(
        <span
          ref={bubbleRef}
          className={PADDING[s] ?? PADDING.top}
          style={pos
            ? { position: 'fixed', left: pos.left, top: pos.top, zIndex: 'var(--z-tooltip)' }
            : { position: 'fixed', left: 0, top: 0, visibility: 'hidden', zIndex: 'var(--z-tooltip)' }}
          onMouseEnter={clearHide}
          onMouseLeave={hideSoon}
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
                  <kbd key={i} className={tooltipKbdClass(k)}>{k}</kbd>
                ))}
              </span>
            )}
          </span>
        </span>,
        document.body,
      )}
    </span>
  )
}
