/**
 * compaction.js — Library-agnostic grid compaction + collision math.
 *
 * Ported from react-grid-layout's compaction utilities, trimmed to exactly what
 * the dashboard needs. Operates on the SAME 0-based item array the rest of the
 * grid uses: each item is { i, x, y, w, h, static?, ... }. Items are NEVER
 * mutated in place — every function returns a fresh array of fresh item objects.
 *
 * Compaction modes (match spec.layout.compaction):
 *   'free'       → identity. Widgets stay exactly where authored (this was RGL's
 *                  `noCompactor`). The dashboard default.
 *   'vertical'   → pack items upward, resolving collisions (RGL 'vertical').
 *   'horizontal' → pack items leftward, resolving collisions (RGL 'horizontal').
 *   'none'       → identity, BUT overlaps are explicitly allowed (no collision
 *                  resolution at all). Distinct from 'free' only conceptually —
 *                  both leave geometry untouched; callers may use 'none' to also
 *                  disable interactive collision pushing.
 *
 * `dense` (RGL's "compactType + allowOverlap" cousin): when packing, try to back-
 * fill gaps left of/above an item so the grid is densely packed rather than
 * strictly order-preserving.
 */

/** Deep-ish clone of an item (shallow is enough — values are primitives). */
function cloneItem(item) {
  return { ...item }
}

/** True if two items overlap on the grid (AABB intersection test). */
export function collides(a, b) {
  if (a.i === b.i) return false          // an item never collides with itself
  if (a.x + a.w <= b.x) return false     // a is fully left of b
  if (a.x >= b.x + b.w) return false     // a is fully right of b
  if (a.y + a.h <= b.y) return false     // a is fully above b
  if (a.y >= b.y + b.h) return false     // a is fully below b
  return true                            // boxes intersect
}

/** First item in `layout` that collides with `item`, or undefined. */
export function getFirstCollision(layout, item) {
  for (const other of layout) {
    if (collides(item, other)) return other
  }
  return undefined
}

/** All items in `layout` colliding with `item` (excluding itself). */
export function getAllCollisions(layout, item) {
  return layout.filter(other => collides(item, other))
}

/**
 * Sort items so compaction is deterministic & order-preserving.
 *   vertical   → top-to-bottom, then left-to-right.
 *   horizontal → left-to-right, then top-to-bottom.
 */
function sortLayoutItems(layout, mode) {
  const items = layout.slice()
  if (mode === 'horizontal') {
    items.sort((a, b) => (a.x - b.x) || (a.y - b.y) || compareId(a, b))
  } else {
    items.sort((a, b) => (a.y - b.y) || (a.x - b.x) || compareId(a, b))
  }
  return items
}

function compareId(a, b) {
  if (a.i === b.i) return 0
  return a.i > b.i ? 1 : -1
}

/**
 * Move a single item up (vertical) or left (horizontal) as far as it can go
 * without colliding with anything already placed in `compareWith`, and without
 * crossing the grid edge. With `dense`, it scans from 0 to find the EARLIEST gap;
 * otherwise it only moves toward the boundary until it hits something.
 */
function compactItem(compareWith, item, mode, cols, dense) {
  const it = cloneItem(item)
  if (mode === 'vertical') {
    if (dense) {
      // Dense: try every y from 0 upward, take the smallest with no collision.
      it.y = 0
      while (getFirstCollision(compareWith, it)) it.y++
    } else {
      // Sparse: slide up until just before a collision (or the top).
      while (it.y > 0 && !getFirstCollision(compareWith, { ...it, y: it.y - 1 })) {
        it.y--
      }
      // Then resolve any residual collision by sliding back down.
      while (getFirstCollision(compareWith, it)) it.y++
    }
  } else if (mode === 'horizontal') {
    if (dense) {
      it.x = 0
      while (it.x + it.w <= cols && getFirstCollision(compareWith, it)) it.x++
    } else {
      while (it.x > 0 && !getFirstCollision(compareWith, { ...it, x: it.x - 1 })) {
        it.x--
      }
      while (it.x + it.w <= cols && getFirstCollision(compareWith, it)) it.x++
    }
  }
  return it
}

/**
 * Compact a layout.
 *
 * @param {Array} layout  0-based item array
 * @param {object} opts
 * @param {'free'|'vertical'|'horizontal'|'none'} opts.mode
 * @param {number} opts.cols  total column count (clamps horizontal packing)
 * @param {boolean} [opts.dense]  back-fill gaps when packing
 * @returns {Array} a NEW item array (input untouched)
 */
export function compact(layout, { mode = 'free', cols = 12, dense = false } = {}) {
  // 'free' and 'none' are both identity for geometry purposes — widgets keep the
  // positions the author committed. Return clones so callers can mutate freely.
  if (mode === 'free' || mode === 'none') {
    return layout.map(cloneItem)
  }

  // Statics are obstacles that never move; seed the "placed" set with them so
  // movable items compact AROUND them.
  const sorted = sortLayoutItems(layout, mode)
  const placed = []
  const out = []

  for (const item of sorted) {
    if (item.static) {
      const s = cloneItem(item)
      placed.push(s)
      out.push(s)
      continue
    }
    const compacted = compactItem(placed, item, mode, cols, dense)
    placed.push(compacted)
    out.push(compacted)
  }

  // Preserve the caller's original item ORDER (sortLayoutItems reordered for the
  // packing pass only) so React keys / array indices stay stable.
  const byId = new Map(out.map(it => [it.i, it]))
  return layout.map(orig => byId.get(orig.i) ?? cloneItem(orig))
}

/**
 * Resolve collisions for ONE moved item against the rest of the layout by
 * pushing colliding movable items out of the way (used during interactive drag
 * when compaction is vertical/horizontal). Returns a NEW layout. For 'free' /
 * 'none' this is a no-op (overlaps allowed) — the caller keeps the ghost as-is.
 */
export function resolveCollisions(layout, movedItem, { mode = 'free', cols = 12, dense = false } = {}) {
  if (mode === 'free' || mode === 'none') return layout.map(cloneItem)

  // Replace the moved item, then push collisions and recompact everything else.
  const next = layout.map(it => (it.i === movedItem.i ? cloneItem(movedItem) : cloneItem(it)))
  const collisionsWithMoved = getAllCollisions(next, movedItem).filter(it => !it.static)
  for (const c of collisionsWithMoved) {
    // Bump the colliding item just past the moved item along the pack axis, then
    // let compact() tidy the whole board.
    if (mode === 'vertical') c.y = movedItem.y + movedItem.h
    else c.x = Math.min(movedItem.x + movedItem.w, cols - c.w)
  }
  return compact(next, { mode, cols, dense })
}
