/**
 * Popover.jsx — dropdown content rendered in a React PORTAL.
 *
 * Why a portal: the grid wraps each cell in `overflow-hidden`, which clips any
 * dropdown that is absolutely positioned inside the widget card. Rendering the
 * panel into `document.body` escapes that clipping entirely (the known bug).
 *
 * The panel is anchored to the trigger's bounding rect (position: fixed), flips
 * above the trigger when there isn't enough room below, and closes on
 * outside-click / Escape. It re-measures on scroll & resize so it stays glued
 * to the trigger.
 *
 * Per-widget `style` / `custom_css` cannot reach the portal via DOM inheritance
 * (the panel lives outside the widget subtree), so callers pass a `styleVars`
 * object (CSS custom properties) which is applied directly to the panel.
 */

import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

const VIEWPORT_MARGIN = 8   // px gap from viewport edges
const TRIGGER_GAP = 4       // px gap between trigger and panel

/**
 * @param {{
 *   anchorRef: React.RefObject<HTMLElement>,  // the trigger element
 *   open: boolean,
 *   onClose: () => void,
 *   children: React.ReactNode,
 *   styleVars?: Record<string, string>,       // CSS vars to bridge across the portal
 *   matchWidth?: boolean,                      // panel min-width = trigger width
 *   maxHeight?: number,
 *   className?: string,
 *   role?: string,
 *   ariaLabel?: string,
 * }} props
 */
export default function Popover({
  anchorRef,
  open,
  onClose,
  children,
  styleVars,
  matchWidth = true,
  maxHeight = 360,
  className = '',
  role,
  ariaLabel,
}) {
  const panelRef = useRef(null)
  const [pos, setPos] = useState(null)

  // Measure the trigger and decide placement (below by default, above if tight).
  useLayoutEffect(() => {
    if (!open) return
    const anchor = anchorRef.current
    if (!anchor) return

    function measure() {
      const r = anchor.getBoundingClientRect()
      const vh = window.innerHeight
      const vw = window.innerWidth
      const spaceBelow = vh - r.bottom - VIEWPORT_MARGIN
      const spaceAbove = r.top - VIEWPORT_MARGIN
      const flipUp = spaceBelow < Math.min(maxHeight, 200) && spaceAbove > spaceBelow
      const avail = Math.max(120, (flipUp ? spaceAbove : spaceBelow) - TRIGGER_GAP)

      // Keep the panel within the viewport horizontally.
      let left = r.left
      const width = matchWidth ? r.width : undefined
      const panelW = panelRef.current?.offsetWidth ?? r.width
      if (left + panelW + VIEWPORT_MARGIN > vw) {
        left = Math.max(VIEWPORT_MARGIN, vw - panelW - VIEWPORT_MARGIN)
      }
      if (left < VIEWPORT_MARGIN) left = VIEWPORT_MARGIN

      setPos({
        left,
        top: flipUp ? undefined : r.bottom + TRIGGER_GAP,
        bottom: flipUp ? vh - r.top + TRIGGER_GAP : undefined,
        minWidth: width,
        maxHeight: Math.min(maxHeight, avail),
        flipUp,
      })
    }

    measure()
    window.addEventListener('resize', measure)
    // Capture scroll on any ancestor (grid scroll containers) so we re-anchor.
    window.addEventListener('scroll', measure, true)
    return () => {
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
    }
  }, [open, anchorRef, matchWidth, maxHeight])

  // Outside-click + Escape close.
  useEffect(() => {
    if (!open) return
    function onDoc(e) {
      const panel = panelRef.current
      const anchor = anchorRef.current
      if (panel && panel.contains(e.target)) return
      if (anchor && anchor.contains(e.target)) return
      onClose()
    }
    function onKey(e) {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
        anchorRef.current?.focus?.()
      }
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, onClose, anchorRef])

  if (!open || !pos) {
    // First frame: render hidden so we can measure the panel width, then place.
    if (open && !pos) {
      return createPortal(
        <div
          ref={panelRef}
          style={{ position: 'fixed', visibility: 'hidden', left: -9999, top: 0, ...styleVars }}
          className={className}
        >
          {children}
        </div>,
        document.body,
      )
    }
    return null
  }

  return createPortal(
    <div
      ref={panelRef}
      role={role}
      aria-label={ariaLabel}
      style={{
        position: 'fixed',
        left: pos.left,
        top: pos.top,
        bottom: pos.bottom,
        minWidth: pos.minWidth,
        maxHeight: pos.maxHeight,
        zIndex: 1000,
        ...styleVars,
      }}
      className={[
        'flex flex-col rounded-lg border border-border bg-surface shadow-xl overflow-hidden',
        className,
      ].join(' ')}
    >
      {children}
    </div>,
    document.body,
  )
}
