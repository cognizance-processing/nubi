/**
 * GridCanvas.jsx — THE shared headless grid renderer.
 *
 * Replaces BOTH react-grid-layout entrypoints the dashboard used:
 *   - ResponsiveGridLayout (read-only viewer in SpecRenderer)
 *   - GridLayout           (editor canvas in DashboardEditor)
 *
 * It renders a REAL CSS Grid — `display:grid; grid-template-columns: repeat(cols,
 * 1fr); grid-auto-rows: <rowHeight>px; gap: <gap>px` — and places each child by
 * inline `gridColumn: <x+1> / span <w>; gridRow: <y+1> / span <h>` (the +1 maps
 * the 0-based item coords to CSS Grid's 1-based line numbers). No absolute
 * positioning, no width math for placement: the browser's grid engine lays it out.
 *
 * Interaction (drag + 8-handle resize) is layered on top via the headless hooks
 * (useGridInteraction + useResize). During a gesture a translucent GHOST cell
 * shows the target position, and faint column GUIDES appear — both reimplemented
 * here with this component's OWN classnames (.grid-canvas, .grid-dragging,
 * .grid-ghost, .grid-resize-handle, .grid-reorder) so we no longer depend on any
 * RGL CSS.
 *
 * ── FROZEN PROP CONTRACT (consumers code against this verbatim) ───────────────
 *   layout            Array   0-based items [{ i, x, y, w, h, static?, minW?,
 *                             minH?, maxW?, maxH? }] (REQUIRED)
 *   cols              number  column count for the active breakpoint (default 12)
 *   rowHeight         number  px height of one grid row (default 60)
 *   gap               number  px gap between cells, both axes (default 12)
 *   padding           {x,y} | [x,y]  container padding in px (default {x:0,y:0})
 *   draggable         bool    enable drag-to-move (default false)
 *   resizable         bool    enable 8-handle resize (default false)
 *   mode              'grid' | 'reorder'   'grid' = free 2-D placement (default);
 *                             'reorder' = single-axis vertical stack reordering
 *                             for the mobile (sm) breakpoint.
 *   compaction        'free' | 'vertical' | 'horizontal' | 'none' (default 'free')
 *   dense             bool    back-fill gaps when packing (default false)
 *   zoom              number  CSS scale of the host device frame (default 1) —
 *                             drag/resize pixel deltas are divided by this.
 *   selectedId        string  id of the currently selected item (for styling)
 *   dragHandle        string  CSS selector for the drag handle (default
 *                             '.drag-handle'); only pointer-downs inside a match
 *                             start a drag.
 *   renderItem(item)  fn → ReactNode   renders ONE item's body. REQUIRED.
 *   onInteractionStart()         fired when a drag/resize gesture begins.
 *   onInteractionEnd()           fired when it ends (commit or cancel).
 *   onLayoutCommit(finalLayout)  fired with the SAME 0-based {i,x,y,w,h} array
 *                                RGL emitted, so the editor's commitLayout works
 *                                unchanged. NEVER fires in read-only usage
 *                                (draggable & resizable both false).
 *   className         string  extra class on the grid element.
 *   style             object  extra inline style on the grid element.
 *
 * Read-only viewer usage: pass draggable=false, resizable=false; onLayoutCommit
 * is never invoked. Editor usage: pass draggable/resizable true, a zoom, a
 * selectedId, and onLayoutCommit=commitLayout.
 */

import { useMemo, useState, useCallback } from 'react'
import { DndContext, closestCenter } from '@dnd-kit/core'
import { useGridInteraction, computeCellSize } from './useGridInteraction.js'
import { useResize, RESIZE_HANDLES } from './useResize.js'
import { compact } from './compaction.js'
import { DraggableItem } from './DraggableItem.jsx'

/** Normalize padding prop ({x,y} | [x,y] | number) → { x, y }. */
function normalizePadding(padding) {
  if (padding == null) return { x: 0, y: 0 }
  if (Array.isArray(padding)) return { x: padding[0] ?? 0, y: padding[1] ?? 0 }
  if (typeof padding === 'number') return { x: padding, y: padding }
  return { x: padding.x ?? 0, y: padding.y ?? 0 }
}

export default function GridCanvas({
  layout,
  cols = 12,
  rowHeight = 60,
  gap = 12,
  padding,
  width,
  draggable = false,
  resizable = false,
  mode = 'grid',
  compaction = 'free',
  dense = false,
  zoom = 1,
  selectedId = null,
  dragHandle = '.drag-handle',
  renderItem,
  onInteractionStart,
  onInteractionEnd,
  onLayoutCommit,
  className = '',
  style,
}) {
  const pad = useMemo(() => normalizePadding(padding), [padding])

  // Width is needed only for cell math (zoom-compensated pixel→cell). If a host
  // doesn't pass one, fall back to a sane default; CSS Grid itself doesn't need
  // it because columns are `1fr`.
  const effectiveWidth = width ?? 1200

  // Local ghost for resize (drag ghost lives inside useGridInteraction).
  const [resizeGhost, setResizeGhost] = useState(null)

  // Wrap the caller's commit so packing modes ('vertical'/'horizontal') run the
  // committed geometry through compact() before it leaves the grid. 'free' and
  // 'none' are identity, so the editor's commitLayout receives the EXACT same
  // 0-based {i,x,y,w,h} array it always did (back-compat). dense back-fills gaps.
  const commitWithCompaction = useCallback((finalLayout) => {
    if (!onLayoutCommit) return
    const packed = compaction === 'free' || compaction === 'none'
      ? finalLayout
      : compact(finalLayout, { mode: compaction, cols, dense })
    onLayoutCommit(packed)
  }, [onLayoutCommit, compaction, cols, dense])

  // ── Drag interaction (dnd-kit) ──────────────────────────────────────────────
  const interactionEnabled = draggable && mode !== 'reorder'
  const {
    sensors,
    ghostLayout: dragGhost,
    activeId,
    handleDragStart,
    handleDragMove,
    handleDragEnd,
    handleDragCancel,
  } = useGridInteraction({
    layout,
    width: effectiveWidth,
    cols,
    rowHeight,
    gap,
    padX: pad.x,
    zoom,
    onLayoutCommit: commitWithCompaction,
    onInteractionStart,
    onInteractionEnd,
  })

  // ── Resize interaction (custom pointer handles) ─────────────────────────────
  const { startResize } = useResize({
    layout,
    width: effectiveWidth,
    cols,
    rowHeight,
    gap,
    padX: pad.x,
    zoom,
    onGhost: setResizeGhost,
    onLayoutCommit: commitWithCompaction,
    onInteractionStart,
    onInteractionEnd,
  })

  // The layout to RENDER: a live ghost while dragging/resizing, else the prop.
  const displayLayout = resizeGhost ?? dragGhost ?? layout
  const isInteracting = !!(resizeGhost || dragGhost)

  // The "ghost cell" target indicator follows the active item during a gesture.
  const ghostItem = useMemo(() => {
    if (!isInteracting) return null
    if (resizeGhost) {
      // Resize: the ghost is whichever item differs from the base layout.
      const base = new Map(layout.map(it => [it.i, it]))
      return resizeGhost.find(it => {
        const b = base.get(it.i)
        return b && (b.x !== it.x || b.y !== it.y || b.w !== it.w || b.h !== it.h)
      }) ?? null
    }
    // Drag: the ghost is the actively-dragged item, located in the live layout.
    return displayLayout.find(it => it.i === activeId) ?? null
  }, [isInteracting, activeId, resizeGhost, displayLayout, layout])

  // ── Reorder mode (mobile single-axis stack) ─────────────────────────────────
  // In 'reorder' mode we ignore 2-D geometry and treat the layout as an ordered
  // vertical stack: dragging swaps order, then we re-derive a stacked layout and
  // commit it. This is the mobile-friendly path; the editor opts in for sm.
  const reorderMode = mode === 'reorder'

  const handleReorderCommit = useCallback((orderedIds) => {
    // Re-stack: x=0, w=1 (or item.w if author set one), cumulative y by height.
    let cursorY = 0
    const byId = new Map(layout.map(it => [it.i, it]))
    const restacked = orderedIds.map(id => {
      const it = byId.get(id)
      const h = it?.h ?? 4
      const next = { ...it, x: 0, y: cursorY }
      cursorY += h
      return next
    })
    commitWithCompaction(restacked)
  }, [layout, commitWithCompaction])

  // ── CSS Grid container style ────────────────────────────────────────────────
  const gridStyle = useMemo(() => ({
    display: 'grid',
    gridTemplateColumns: `repeat(${Math.max(1, cols)}, 1fr)`,
    gridAutoRows: `${rowHeight}px`,
    gap: `${gap}px`,
    padding: `${pad.y}px ${pad.x}px`,
    position: 'relative',
    // Column guides: a faint repeating gradient shown only while interacting.
    // One column pitch ≈ (usableWidth/cols + gap); we expose it as a CSS var the
    // .grid-dragging class consumes (see index.css). Falls back gracefully.
    ...(isInteracting ? { '--grid-col-w': `${columnPitch(effectiveWidth, cols, gap, pad.x)}px` } : {}),
    ...style,
  }), [cols, rowHeight, gap, pad.x, pad.y, isInteracting, effectiveWidth, style])

  const containerClass = [
    'grid-canvas',
    isInteracting ? 'grid-dragging' : '',
    reorderMode ? 'grid-reorder' : '',
    className,
  ].filter(Boolean).join(' ')

  // ── Item renderer (shared by both modes) ────────────────────────────────────
  const renderGridItem = useCallback((item) => {
    const isActive = item.i === activeId || (ghostItem && item.i === ghostItem.i)
    const itemStyle = {
      gridColumn: `${item.x + 1} / span ${item.w}`,
      gridRow: `${item.y + 1} / span ${item.h}`,
      position: 'relative',
      minWidth: 0,
      minHeight: 0,
    }
    return (
      <DraggableItem
        key={item.i}
        item={item}
        style={itemStyle}
        draggable={interactionEnabled && !item.static}
        dragHandle={dragHandle}
        selected={item.i === selectedId}
        active={isActive}
        resizable={resizable && !item.static}
        resizeHandles={RESIZE_HANDLES}
        onResizeHandleDown={startResize}
      >
        {renderItem(item)}
      </DraggableItem>
    )
  }, [interactionEnabled, dragHandle, selectedId, resizable, startResize, renderItem, activeId, ghostItem])

  // The ghost target cell (placeholder), placed by the same grid coords.
  const ghostCell = ghostItem ? (
    <div
      className="grid-ghost"
      aria-hidden
      style={{
        gridColumn: `${ghostItem.x + 1} / span ${ghostItem.w}`,
        gridRow: `${ghostItem.y + 1} / span ${ghostItem.h}`,
        pointerEvents: 'none',
      }}
    />
  ) : null

  // Read-only / no-interaction fast path: plain CSS Grid, no DndContext overhead.
  if (!interactionEnabled && !resizable && !reorderMode) {
    return (
      <div className={containerClass} style={gridStyle}>
        {layout.map(renderGridItem)}
      </div>
    )
  }

  // Reorder mode wraps in its own simple drag context (still dnd-kit) but commits
  // via order, not geometry.
  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragStart={handleDragStart}
      onDragMove={reorderMode ? undefined : handleDragMove}
      onDragEnd={reorderMode
        ? (e) => {
            // Commit the new order, THEN clear the shared drag-hook state.
            // handleDragCancel() resets activeId/ghostLayout (so the stale
            // pre-reorder snapshot stops winning over the freshly committed
            // layout) and fires onInteractionEnd exactly once.
            handleReorderEnd(e, layout, handleReorderCommit)
            handleDragCancel()
          }
        : handleDragEnd}
      onDragCancel={handleDragCancel}
    >
      <div className={containerClass} style={gridStyle}>
        {ghostCell}
        {displayLayout.map(renderGridItem)}
      </div>
    </DndContext>
  )
}

/** Approx px width of one column + gutter, for the drag-time column guides. */
function columnPitch(width, cols, gap, padX) {
  const safeCols = Math.max(1, cols)
  const { cellW } = computeCellSize({ width, cols: safeCols, rowHeight: 0, gap, padX })
  return cellW + gap
}

/**
 * Reorder-mode drag end: compute the new order from the active/over ids and
 * hand the ordered id list to the caller's commit. Kept tiny & dependency-free
 * (no SortableContext) because the mobile stack is a single vertical list.
 * State reset + onInteractionEnd are handled by the caller (via handleDragCancel).
 */
function handleReorderEnd(event, layout, commit) {
  const activeId = event?.active?.id
  const overId = event?.over?.id
  if (!activeId || !overId || activeId === overId) return
  const ids = layout.map(it => it.i)
  const from = ids.indexOf(activeId)
  const to = ids.indexOf(overId)
  if (from === -1 || to === -1) return
  const reordered = ids.slice()
  reordered.splice(to, 0, reordered.splice(from, 1)[0])
  commit(reordered)
}
