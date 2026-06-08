/**
 * useResize.js — Custom 8-handle resize for the headless grid.
 *
 * RGL bundled resize via react-resizable; we reimplement it with raw pointer
 * events so it shares the grid's own cell math and the editor's zoom
 * compensation. Handles: n, s, e, w, ne, nw, se, sw.
 *
 * A handle is dragged in screen pixels; we divide by `zoom` (the device-frame CSS
 * scale) and convert to a cell delta using the SAME geometry as dragging
 * (computeCellSize / pixelDeltaToCells). The delta adjusts x/y/w/h depending on
 * which edges the handle controls, then everything is clamped to the item's
 * min/max constraints and to the grid bounds [0, cols].
 *
 * Emits a live "ghost" layout during the drag (via onGhost) and commits the
 * final layout on pointer up (via onLayoutCommit). For 'free'/'none' compaction
 * the caller leaves the committed geometry untouched; packing modes recompact
 * after the commit at the GridCanvas level.
 */

import { useCallback, useRef } from 'react'
import { computeCellSize, pixelDeltaToCells } from './useGridInteraction.js'

/** Which edges each handle moves: each entry → {left,right,top,bottom} booleans. */
const HANDLE_EDGES = {
  n:  { top: true },
  s:  { bottom: true },
  e:  { right: true },
  w:  { left: true },
  ne: { top: true, right: true },
  nw: { top: true, left: true },
  se: { bottom: true, right: true },
  sw: { bottom: true, left: true },
}

/** All supported handle tokens, in the editor's historical order. */
export const RESIZE_HANDLES = ['s', 'e', 'se', 'sw', 'ne', 'n', 'w', 'nw']

function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v))
}

/**
 * Apply a (dCol,dRow) cell delta to `item` for a given handle, honouring
 * min/max constraints and grid bounds. Pure — returns a new item.
 */
export function applyResizeDelta(item, handle, dCol, dRow, cols) {
  const edges = HANDLE_EDGES[handle] ?? {}
  let { x, y, w, h } = item
  const minW = item.minW ?? 1
  const minH = item.minH ?? 1
  const maxW = item.maxW ?? Infinity
  const maxH = item.maxH ?? Infinity

  if (edges.right) {
    // Grow/shrink from the right edge: width changes, x fixed.
    w = clamp(w + dCol, minW, Math.min(maxW, cols - x))
  }
  if (edges.left) {
    // Drag the left edge: x and w move together, keeping the right edge fixed.
    const right = x + w
    const newX = clamp(x + dCol, 0, right - minW)
    // Respect maxW too: don't let the left edge run away past the max width.
    const clampedX = Math.max(newX, right - maxW)
    x = clampedX
    w = right - x
  }
  if (edges.bottom) {
    h = clamp(h + dRow, minH, maxH)
  }
  if (edges.top) {
    const bottom = y + h
    const newY = clamp(y + dRow, 0, bottom - minH)
    const clampedY = Math.max(newY, bottom - maxH)
    y = clampedY
    h = bottom - y
  }
  return { ...item, x, y, w, h }
}

/**
 * @param {object} cfg
 * @param {Array}  cfg.layout
 * @param {number} cfg.width      UNSCALED design width
 * @param {number} cfg.cols
 * @param {number} cfg.rowHeight
 * @param {number} cfg.gap
 * @param {number} [cfg.padX]
 * @param {number} [cfg.zoom]
 * @param {(ghost:Array|null)=>void} cfg.onGhost      live ghost during resize
 * @param {(finalLayout:Array)=>void} cfg.onLayoutCommit
 * @param {()=>void} [cfg.onInteractionStart]
 * @param {()=>void} [cfg.onInteractionEnd]
 *
 * @returns {{ startResize: (e, itemId, handle) => void }}
 *   startResize is wired to onPointerDown of each handle element.
 */
export function useResize(cfg) {
  const {
    layout,
    width,
    cols,
    rowHeight,
    gap,
    padX = 0,
    zoom = 1,
    onGhost,
    onLayoutCommit,
    onInteractionStart,
    onInteractionEnd,
  } = cfg

  // Mutable gesture state — kept in a ref so the global pointer listeners (added
  // for the duration of one resize) always see fresh values without re-binding.
  const stateRef = useRef(null)

  const startResize = useCallback((e, itemId, handle) => {
    e.preventDefault()
    e.stopPropagation()
    const item = layout.find(it => it.i === itemId)
    if (!item || item.static) return

    const cellSize = computeCellSize({ width, cols, rowHeight, gap, padX })
    const startX = e.clientX
    const startY = e.clientY
    const baseLayout = layout.map(it => ({ ...it }))

    stateRef.current = {
      itemId, handle, startX, startY, cellSize,
      baseItem: { ...item },
      ghost: baseLayout,
    }
    onInteractionStart?.()

    const onMove = (ev) => {
      const st = stateRef.current
      if (!st) return
      const dx = ev.clientX - st.startX
      const dy = ev.clientY - st.startY
      const { dCol, dRow } = pixelDeltaToCells(dx, dy, { ...st.cellSize, zoom })
      const resized = applyResizeDelta(st.baseItem, st.handle, dCol, dRow, cols)
      const ghost = baseLayout.map(it => (it.i === st.itemId ? resized : it))
      st.ghost = ghost
      onGhost?.(ghost)
    }

    const onUp = () => {
      const st = stateRef.current
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onCancel)
      const finalLayout = st?.ghost ?? null
      stateRef.current = null
      onGhost?.(null)
      onInteractionEnd?.()
      if (finalLayout) onLayoutCommit?.(finalLayout)
    }

    const onCancel = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onCancel)
      stateRef.current = null
      onGhost?.(null)
      onInteractionEnd?.()
    }

    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onCancel)
  }, [layout, width, cols, rowHeight, gap, padX, zoom, onGhost, onLayoutCommit, onInteractionStart, onInteractionEnd])

  return { startResize }
}
