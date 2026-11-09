/**
 * useFixedAnchor — helpers for floating UI rendered in a portal with
 * `position: fixed`, so `overflow` ancestors (e.g. the horizontally scrolling
 * editor toolbar / topbar slot) can't clip it.
 *
 * Same approach as dashboards/inputs/Popover and PageShell's DropdownMenu:
 * measure the trigger's bounding rect, place the panel, and re-measure on
 * scroll (capture phase, so any ancestor scroller counts) and resize.
 *
 *   useAnchorTracking(open, measure)
 *     Runs `measure` once on open (in a layout effect, before paint), then at
 *     most once per animation frame while anything scrolls and debounced on
 *     resize. `measure` may change every render; the latest one is used.
 *
 *   placeDropdown(anchorRect, panelEl, { align })
 *     Below the trigger by default, flipped above when there is more room
 *     there; clamped to the viewport; height capped. align 'right' lines up the
 *     panel's right edge with the trigger's (the old `right-0 top-full`).
 *
 *   placeTooltip(anchorRect, bubbleEl, side)
 *     Tooltips (ui/Tooltip). Sits flush against the requested side of the
 *     trigger (the bubble element's own padding is the gap / hover bridge),
 *     flips to the opposite side only when it would come within 4px of the
 *     viewport edge, and is centred on the trigger — clamped 8px inside the
 *     viewport horizontally for top/bottom. No height cap.
 */

import { useLayoutEffect, useRef } from 'react'

export const VIEWPORT_MARGIN = 8   // px gap from viewport edges
export const TRIGGER_GAP = 4       // px gap between trigger and panel
const DEFAULT_MAX_HEIGHT = 480

export function useAnchorTracking(open: boolean, measure: () => void) {
  const measureRef = useRef(measure)
  // Keep the latest measure without re-subscribing. Declared first, so it runs
  // before the open effect below within the same commit.
  useLayoutEffect(() => { measureRef.current = measure })

  useLayoutEffect(() => {
    if (!open) return
    measureRef.current()

    let raf = 0
    let resizeTimer: ReturnType<typeof setTimeout> | 0 = 0
    const onScroll = () => {
      if (raf) cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => { raf = 0; measureRef.current() })
    }
    const onResize = () => {
      if (resizeTimer) clearTimeout(resizeTimer)
      resizeTimer = setTimeout(() => { resizeTimer = 0; measureRef.current() }, 100)
    }
    window.addEventListener('resize', onResize)
    window.addEventListener('scroll', onScroll, true)
    return () => {
      window.removeEventListener('resize', onResize)
      window.removeEventListener('scroll', onScroll, true)
      if (raf) cancelAnimationFrame(raf)
      if (resizeTimer) clearTimeout(resizeTimer)
    }
  }, [open])
}

export type DropdownPlacement = {
  left: number
  top?: number
  bottom?: number
  maxHeight: number
}

export function placeDropdown(
  r: DOMRect,
  panel: HTMLElement,
  { align = 'left', maxHeight: cap = DEFAULT_MAX_HEIGHT }: { align?: 'left' | 'right'; maxHeight?: number } = {},
): DropdownPlacement {
  const vw = window.innerWidth
  const vh = window.innerHeight
  const panelW = panel.offsetWidth
  // scrollHeight is the natural content height even once maxHeight applies.
  const wanted = Math.min(panel.scrollHeight, cap)
  const spaceBelow = vh - r.bottom - TRIGGER_GAP - VIEWPORT_MARGIN
  const spaceAbove = r.top - TRIGGER_GAP - VIEWPORT_MARGIN
  const flipUp = spaceBelow < wanted && spaceAbove > spaceBelow
  const maxHeight = Math.max(120, Math.min(cap, flipUp ? spaceAbove : spaceBelow))

  let left = align === 'right' ? r.right - panelW : r.left
  left = Math.min(left, vw - panelW - VIEWPORT_MARGIN)
  left = Math.max(VIEWPORT_MARGIN, left)

  return {
    left,
    top: flipUp ? undefined : r.bottom + TRIGGER_GAP,
    bottom: flipUp ? vh - r.top + TRIGGER_GAP : undefined,
    maxHeight,
  }
}

/** Inline style for a portaled panel: hidden until the first measure lands. */
export function fixedPanelStyle(pos: DropdownPlacement | null, zIndex = 'var(--z-popover)') {
  return pos
    ? { position: 'fixed' as const, left: pos.left, top: pos.top, bottom: pos.bottom, maxHeight: pos.maxHeight, overflowY: 'auto' as const, zIndex }
    : { position: 'fixed' as const, left: 0, top: 0, visibility: 'hidden' as const, zIndex }
}

export type TooltipSide = 'top' | 'bottom' | 'left' | 'right'
export type TooltipPlacement = { left: number; top: number; side: TooltipSide }

const TOOLTIP_FLIP_EDGE = 4   // px: flip when the bubble would come closer than this to the edge

export function placeTooltip(r: DOMRect, el: HTMLElement, side: TooltipSide): TooltipPlacement {
  const vw = window.innerWidth
  const vh = window.innerHeight
  const w = el.offsetWidth
  const h = el.offsetHeight

  let s = side
  if (side === 'top' && r.top - h < TOOLTIP_FLIP_EDGE) s = 'bottom'
  else if (side === 'bottom' && r.bottom + h > vh - TOOLTIP_FLIP_EDGE) s = 'top'
  else if (side === 'left' && r.left - w < TOOLTIP_FLIP_EDGE) s = 'right'
  else if (side === 'right' && r.right + w > vw - TOOLTIP_FLIP_EDGE) s = 'left'

  if (s === 'top' || s === 'bottom') {
    let left = r.left + r.width / 2 - w / 2
    left = Math.max(VIEWPORT_MARGIN, Math.min(left, vw - w - VIEWPORT_MARGIN))
    return { left, top: s === 'top' ? r.top - h : r.bottom, side: s }
  }
  return { left: s === 'left' ? r.left - w : r.right, top: r.top + r.height / 2 - h / 2, side: s }
}
