/**
 * useGridInteraction.js — dnd-kit-backed drag interaction for the headless grid.
 *
 * Replaces react-grid-layout's internal drag handling. It wraps @dnd-kit/core
 * sensors and converts a pointer PIXEL delta into a grid CELL delta, producing a
 * live "ghost" layout while a widget is being dragged. On pointer up it hands the
 * final layout to the caller to commit.
 *
 * ── Zoom compensation (replaces RGL's createScaledStrategy) ───────────────────
 * The editor renders the grid inside a CSS `transform: scale(zoom)` device frame.
 * dnd-kit reports deltas in *screen* pixels, which are already multiplied by that
 * scale. To map a screen delta back to the grid's own (unscaled) coordinate space
 * we DIVIDE the pixel delta by `zoom` before converting to cells. The read-only
 * viewer passes zoom = 1, so this is a no-op there.
 *
 * ── Cell geometry ─────────────────────────────────────────────────────────────
 *   cellW = (width - 2*padX - (cols-1)*gap) / cols   // usable px per column
 *   cellH = rowHeight + gap                          // px per row (incl. gutter)
 * A pixel delta (dx,dy) becomes a cell delta by rounding (dx/cellW, dy/cellH).
 */

import { useCallback, useMemo, useRef, useState } from 'react'
import {
  useSensor,
  useSensors,
  PointerSensor,
  TouchSensor,
} from '@dnd-kit/core'

/**
 * Compute usable cell width/height in px for the current grid geometry.
 * Exported so useResize can share the exact same math.
 */
export function computeCellSize({ width, cols, rowHeight, gap, padX = 0 }) {
  const safeCols = Math.max(1, cols)
  const cellW = (width - 2 * padX - (safeCols - 1) * gap) / safeCols
  const cellH = rowHeight + gap
  return { cellW: cellW > 0 ? cellW : 1, cellH: cellH > 0 ? cellH : 1 }
}

/**
 * Convert a pixel delta to a grid cell delta, compensating for the CSS zoom.
 * Exported & shared with useResize so drag and resize round identically.
 */
export function pixelDeltaToCells(dx, dy, { cellW, cellH, zoom = 1 }) {
  const z = zoom || 1
  return {
    dCol: Math.round(dx / z / cellW),
    dRow: Math.round(dy / z / cellH),
  }
}

/**
 * @param {object} cfg
 * @param {Array}  cfg.layout      current 0-based item array
 * @param {number} cfg.width       grid container width (px, UNSCALED design width)
 * @param {number} cfg.cols        column count for the active breakpoint
 * @param {number} cfg.rowHeight   row height (px)
 * @param {number} cfg.gap         gap between cells (px)
 * @param {number} [cfg.padX]      horizontal padding (px)
 * @param {number} [cfg.zoom]      CSS scale of the device frame (default 1)
 * @param {(finalLayout:Array)=>void} [cfg.onLayoutCommit]
 * @param {()=>void} [cfg.onInteractionStart]
 * @param {()=>void} [cfg.onInteractionEnd]
 *
 * @returns {{
 *   sensors: import('@dnd-kit/core').SensorDescriptor[],
 *   ghostLayout: Array|null,   // live layout during a drag, else null
 *   activeId: string|null,
 *   handleDragStart: Function,
 *   handleDragMove: Function,
 *   handleDragEnd: Function,
 *   handleDragCancel: Function,
 * }}
 */
export function useGridInteraction(cfg) {
  const {
    layout,
    width,
    cols,
    rowHeight,
    gap,
    padX = 0,
    zoom = 1,
    onLayoutCommit,
    onInteractionStart,
    onInteractionEnd,
  } = cfg

  // PointerSensor: small activation distance so a click on the handle doesn't
  // start a drag, but a 4px move does. TouchSensor: a 200ms press-and-hold delay
  // (with 8px tolerance) so a one-finger swipe still SCROLLS the page instead of
  // dragging a widget — only a deliberate long-press grabs.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 200, tolerance: 8 } }),
  )

  const [activeId, setActiveId] = useState(null)
  const [ghostLayout, setGhostLayout] = useState(null)

  // Keep the layout snapshot at drag-start stable for the whole gesture so
  // re-renders mid-drag don't move the ghost out from under the pointer.
  const dragStartLayoutRef = useRef(null)

  const cellSize = useMemo(
    () => computeCellSize({ width, cols, rowHeight, gap, padX }),
    [width, cols, rowHeight, gap, padX],
  )

  const handleDragStart = useCallback((event) => {
    const id = event?.active?.id
    if (id == null) return
    setActiveId(id)
    dragStartLayoutRef.current = layout.map(it => ({ ...it }))
    setGhostLayout(dragStartLayoutRef.current)
    onInteractionStart?.()
  }, [layout, onInteractionStart])

  const handleDragMove = useCallback((event) => {
    const start = dragStartLayoutRef.current
    if (!start) return
    const id = event?.active?.id
    const delta = event?.delta ?? { x: 0, y: 0 }
    const { dCol, dRow } = pixelDeltaToCells(delta.x, delta.y, { ...cellSize, zoom })

    const next = start.map(it => {
      if (it.i !== id || it.static) return it
      // Clamp x into [0, cols - w] and y into [0, ∞) so the ghost never leaves
      // the grid horizontally or floats above row 0.
      const x = clamp(it.x + dCol, 0, Math.max(0, cols - it.w))
      const y = Math.max(0, it.y + dRow)
      return { ...it, x, y }
    })
    setGhostLayout(next)
  }, [cellSize, cols, zoom])

  const finishDrag = useCallback((commit) => {
    const finalLayout = ghostLayout
    setActiveId(null)
    setGhostLayout(null)
    dragStartLayoutRef.current = null
    onInteractionEnd?.()
    // Emit the SAME 0-based {i,x,y,w,h,...} array RGL emitted so the editor's
    // commitLayout works unchanged. Only commit on a real drop, not a cancel.
    if (commit && finalLayout) onLayoutCommit?.(finalLayout)
  }, [ghostLayout, onInteractionEnd, onLayoutCommit])

  const handleDragEnd = useCallback(() => finishDrag(true), [finishDrag])
  const handleDragCancel = useCallback(() => finishDrag(false), [finishDrag])

  return {
    sensors,
    ghostLayout,
    activeId,
    handleDragStart,
    handleDragMove,
    handleDragEnd,
    handleDragCancel,
  }
}

function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v))
}
