/**
 * responsiveLayout.js — Per-breakpoint dashboard layout helpers (shared by the
 * editor and the read-only viewer).
 *
 * Data model (back-compatible)
 * ----------------------------
 * `widget.pos` (1-based x,y,w,h + optional static/min/max) remains the canonical
 * DESKTOP (lg) layout. Existing specs are therefore unchanged.
 *
 * Per-breakpoint OVERRIDES for tablet (md) and mobile (sm) live on the spec:
 *
 *   spec.responsive = {
 *     md: { [widgetId]: { x, y, w, h } },   // 1-based, same units as widget.pos
 *     sm: { [widgetId]: { x, y, w, h } },
 *   }
 *
 * When a breakpoint has NO override for a widget, we fall back to the layout
 * derived from `widget.pos` (the previous behaviour): md inherits lg verbatim;
 * sm derives a stacked single-column layout. This keeps every old spec working
 * and means an author only "pays" for the widgets they actually customise.
 *
 * Breakpoint ↔ device mapping used by the editor:
 *   desktop → lg   tablet → md   mobile → sm
 */

export const DEVICE_TO_BREAKPOINT = { desktop: 'lg', tablet: 'md', mobile: 'sm' }
export const BREAKPOINT_TO_DEVICE = { lg: 'desktop', md: 'tablet', sm: 'mobile' }

/** Number of grid columns for the small (mobile) breakpoint. */
export const SM_COLS = 1

/** Read the override map for a breakpoint (md/sm). Always returns an object. */
export function overridesFor(spec, breakpoint) {
  return spec?.responsive?.[breakpoint] ?? {}
}

/** True if the breakpoint has at least one widget override on the spec. */
export function hasOverrides(spec, breakpoint) {
  return Object.keys(overridesFor(spec, breakpoint)).length > 0
}

/**
 * True if `widget` is hidden at `breakpoint`. Visibility is stored on the widget
 * as `widget.hidden = ['lg' | 'md' | 'sm', …]` (breakpoint tokens). A hidden
 * widget is omitted entirely from that breakpoint's layout + render.
 */
export function isHiddenAt(widget, breakpoint) {
  return Array.isArray(widget?.hidden) && widget.hidden.includes(breakpoint)
}

/**
 * Convert a 1-based pos ({x,y,w,h}+constraints) to a 0-based grid layout item.
 * `extra` is merged last (e.g. isDraggable/isResizable for the viewer).
 *
 * NOTE: historically named `posToRglItem` (RGL = react-grid-layout). Renamed to
 * `posToGridItem` during the dnd-kit / CSS-Grid migration — the item shape is
 * library-agnostic so the geometry layer outlives the grid library it feeds.
 */
export function posToGridItem(id, pos, { cols, minDefaults, extra } = {}) {
  const p = pos ?? { x: 1, y: 1, w: 4, h: 4 }
  const item = {
    i: id,
    x: Math.max(0, (p.x ?? 1) - 1),
    y: Math.max(0, (p.y ?? 1) - 1),
    w: cols != null ? Math.min(p.w ?? 4, cols) : (p.w ?? 4),
    h: p.h ?? 4,
  }
  if (minDefaults) {
    item.minW = p.minW ?? minDefaults.minW
    item.minH = p.minH ?? minDefaults.minH
  }
  if (p.static) item.static = true
  if (p.minW != null && !minDefaults) item.minW = p.minW
  if (p.minH != null && !minDefaults) item.minH = p.minH
  if (p.maxW != null) item.maxW = p.maxW
  if (p.maxH != null) item.maxH = p.maxH
  if (extra) Object.assign(item, extra)
  return item
}

/**
 * Convert a 0-based grid item back to a 1-based pos, preserving prev constraints.
 * Historically `rglItemToPos`; renamed for the dnd-kit / CSS-Grid migration.
 */
export function gridItemToPos(item, prevPos) {
  return {
    ...(prevPos ?? {}),
    x: item.x + 1,
    y: item.y + 1,
    w: item.w,
    h: item.h,
  }
}

/**
 * Effective pos for a widget at a breakpoint: the override if present, else the
 * canonical widget.pos. (For lg there are never overrides — pos is canonical.)
 */
export function effectivePos(widget, spec, breakpoint) {
  if (breakpoint === 'lg') return widget.pos
  const ov = overridesFor(spec, breakpoint)[widget.id]
  if (ov) return { ...(widget.pos ?? {}), ...ov }
  return widget.pos
}

/**
 * Build the `lg` RGL layout from canonical widget.pos.
 * `perWidget(widget)` may return per-item extras (constraints/min defaults).
 */
export function buildLgLayout(widgets, cols, perWidget) {
  return widgets.filter(w => !isHiddenAt(w, 'lg')).map(w => {
    const opts = perWidget ? perWidget(w) : {}
    return posToGridItem(w.id, w.pos, { cols, ...opts })
  })
}

/**
 * Build the `md` RGL layout: use spec.responsive.md override per widget when
 * present, else fall back to the lg-derived item (current behaviour).
 */
export function buildMdLayout(widgets, cols, spec, perWidget) {
  const ov = overridesFor(spec, 'md')
  return widgets.filter(w => !isHiddenAt(w, 'md')).map(w => {
    const opts = perWidget ? perWidget(w) : {}
    const pos = ov[w.id] ? { ...(w.pos ?? {}), ...ov[w.id] } : w.pos
    return posToGridItem(w.id, pos, { cols, ...opts })
  })
}

/**
 * Build the `sm` grid layout. If spec.responsive.sm has an override for a
 * widget, honour its y/h/w/x; otherwise derive a stacked layout from the widget
 * order (current behaviour).
 *
 * `smCols` is the number of columns the sm breakpoint renders at. It defaults to
 * SM_COLS (1) to preserve the historical hard-locked single-column mobile stack.
 * When sm has more than one column the derived stack still places each widget at
 * x=0 spanning ONE column (the safe, predictable default for an auto-generated
 * mobile layout); authors who want wider mobile widgets add explicit overrides.
 * Overrides are clamped to `smCols` so a stale wide override can't overflow.
 */
export function buildSmLayout(widgets, spec, perWidget, smCols = SM_COLS) {
  const ov = overridesFor(spec, 'sm')
  let cursorY = 0
  return widgets.filter(w => !isHiddenAt(w, 'sm')).map(w => {
    const opts = perWidget ? perWidget(w) : {}
    const o = ov[w.id]
    if (o) {
      return posToGridItem(w.id, { ...(w.pos ?? {}), ...o }, { cols: smCols, ...opts })
    }
    const h = w.pos?.h ?? 4
    const item = {
      i: w.id,
      x: 0,
      y: cursorY,
      w: 1,
      h,
      ...(opts.extra ?? {}),
    }
    cursorY += h
    return item
  })
}

/**
 * Full per-breakpoint layouts map ({ lg, md, sm }) with overrides + fallback
 * applied. `perWidget(widget)` returns posToGridItem options
 * (e.g. { minDefaults, extra }) so editor/viewer can inject their own
 * constraints / draggable flags.
 *
 * `colsByBp` optionally overrides the per-breakpoint column counts:
 *   { lg, md, sm }
 * Anything omitted falls back to the historical defaults — lg/md use `cols`
 * (the desktop column count) and sm uses SM_COLS (1) — so existing callers that
 * pass only `(spec, cols, perWidget)` get identical behaviour.
 */
export function buildResponsiveLayouts(spec, cols, perWidget, colsByBp = {}) {
  const widgets = spec?.widgets ?? []
  const lgCols = colsByBp.lg ?? cols
  const mdCols = colsByBp.md ?? cols
  const smCols = colsByBp.sm ?? SM_COLS
  return {
    lg: buildLgLayout(widgets, lgCols, perWidget),
    md: buildMdLayout(widgets, mdCols, spec, perWidget),
    sm: buildSmLayout(widgets, spec, perWidget, smCols),
  }
}

/**
 * Write a committed RGL layout back into the spec for a SINGLE active breakpoint.
 *
 *   lg → updates widget.pos (canonical desktop) for moved widgets.
 *   md/sm → updates spec.responsive[bp][widgetId] only — never touches other
 *           breakpoints or untouched widgets.
 *
 * Returns a NEW spec (immutable). Only widgets present in `layout` are updated.
 */
export function applyLayoutCommit(spec, breakpoint, layout) {
  const byId = new Map(layout.map(l => [l.i, l]))

  if (breakpoint === 'lg') {
    return {
      ...spec,
      widgets: spec.widgets.map(w => {
        const item = byId.get(w.id)
        if (!item) return w
        return { ...w, pos: gridItemToPos(item, w.pos) }
      }),
    }
  }

  // md / sm: write only this breakpoint's override map, and only for widgets
  // whose geometry actually CHANGED vs their current effective layout — so
  // dragging one widget on tablet does not freeze every other widget into an
  // override (untouched widgets keep inheriting the desktop/fallback layout).
  const prevResponsive = spec.responsive ?? {}
  const prevBp = prevResponsive[breakpoint] ?? {}
  const nextBp = { ...prevBp }
  let changed = false
  for (const w of spec.widgets) {
    const item = byId.get(w.id)
    if (!item) continue
    const next = { x: item.x + 1, y: item.y + 1, w: item.w, h: item.h }
    const cur = effectivePos(w, spec, breakpoint) ?? {}
    if (cur.x === next.x && cur.y === next.y && cur.w === next.w && cur.h === next.h) continue
    nextBp[w.id] = next
    changed = true
  }
  if (!changed) return spec
  return {
    ...spec,
    responsive: { ...prevResponsive, [breakpoint]: nextBp },
  }
}

/**
 * Clear all overrides for a breakpoint (md/sm) → that size reverts to being
 * inherited/derived from the desktop layout. No-op for lg.
 */
export function clearBreakpointOverrides(spec, breakpoint) {
  if (breakpoint === 'lg' || !spec.responsive?.[breakpoint]) return spec
  const nextResponsive = { ...spec.responsive }
  delete nextResponsive[breakpoint]
  const cleaned = Object.keys(nextResponsive).length ? nextResponsive : undefined
  return { ...spec, responsive: cleaned }
}
