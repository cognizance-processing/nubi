/**
 * grid/index.js — Barrel for the headless CSS-Grid + dnd-kit grid engine.
 *
 * GridCanvas is the shared component that replaces react-grid-layout's
 * ResponsiveGridLayout (viewer) and GridLayout (editor). The geometry layer
 * (1-based pos <-> 0-based items, per-breakpoint layouts) still lives in
 * ../responsiveLayout.js and is imported directly by consumers.
 */

export { default as GridCanvas } from './GridCanvas.jsx'
export { DraggableItem } from './DraggableItem.jsx'

export { getBreakpointFromWidth, sortBreakpoints, DEFAULT_BREAKPOINTS } from './breakpoints.js'
export { compact, collides, getFirstCollision, getAllCollisions, resolveCollisions } from './compaction.js'
export { useGridInteraction, computeCellSize, pixelDeltaToCells } from './useGridInteraction.js'
export { useResize, applyResizeDelta, RESIZE_HANDLES } from './useResize.js'
