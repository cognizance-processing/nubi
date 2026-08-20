/**
 * widgetLibraryShape.js — pure spec helpers for reusable LIBRARY WIDGETS.
 *
 * Lives here (next to widgetHtml.js) rather than in lib/widgetLibrary.js so the
 * shape logic stays free of the API client — src/lib/api.js reads
 * `import.meta.env`, which only exists under Vite, so anything importing it is
 * untestable under `node --test`. lib/widgetLibrary.js re-exports these.
 *
 * The contract: a library entry keeps everything that makes a widget what it
 * IS, and drops everything about WHERE it happened to live.
 *
 * ---------------------------------------------------------------------------
 * Reference-based reuse (ref / overrides)
 * ---------------------------------------------------------------------------
 * A spec widget may be a REFERENCE instead of fully inline:
 *
 *   { id, ref: '<library row id>', overrides: {...sparse...}, pos, tab_id }
 *
 * Resolution: `effective = deepMerge(libraryRow.config, overrides)`, then the
 * BOARD-LOCAL fields (id, pos, tab_id, placement, order, drawer,
 * drawer_group) are FORCED from the spec entry — never from the library row,
 * because those describe where THIS instance sits on THIS board, not what
 * the widget is.
 *
 * This mirrors the backend resolver 1:1 (backend/app/dashboards/widget_refs.py
 * `resolve_widget_refs`) — same merge rule, same forced-field list, same
 * broken-ref degrade (a `type: 'text'` placeholder rather than a crash).
 * Keep the two in lockstep if either changes.
 */

// ---------------------------------------------------------------------------
// toLibraryConfig / fromLibraryRow — save-to-library shape
// ---------------------------------------------------------------------------

/**
 * Board-local fields that must NOT be persisted with a library entry: they
 * describe where a widget sits on one particular board (or, for
 * placement/order/drawer/drawer_group, in what board-local SLOT), not what
 * the widget IS. Also the exact set of fields a `ref` widget's resolution
 * always takes from the spec entry, never from the library row — see
 * `resolveWidgetRef` below.
 * `pos` is handled specially — its x/y are board-local, but its w/h are
 * authoring intent (a KPI tile and a wide chart are not the same size), so the
 * dimensions survive as `size` while the coordinates are dropped.
 */
const BOARD_LOCAL_KEYS = ['id', 'pos', 'tab_id', 'placement', 'order', 'drawer', 'drawer_group']

/** Board-local fields, exported for callers that need the raw list (editor). */
export const BOARD_LOCAL_FIELDS = [...BOARD_LOCAL_KEYS]

/** Defaults applied to each board-local field when a spec entry omits it — mirrors backend `_BOARD_LOCAL_DEFAULTS`. */
export const BOARD_LOCAL_DEFAULTS = {
  tab_id: null,
  placement: 'grid',
  order: 0,
  drawer: false,
  drawer_group: null,
}

/**
 * Strip a spec widget down to the reusable part.
 * @param {object} widget — a spec widget
 * @returns {object} the widget config to persist, with `size` in place of `pos`
 */
export function toLibraryConfig(widget) {
  const out = { ...(widget ?? {}) }
  const pos = widget?.pos
  for (const k of BOARD_LOCAL_KEYS) delete out[k]
  // `ref`/`overrides` describe an unresolved reference, never a reusable
  // shape — a library row must never itself be (or point at) a reference.
  delete out.ref
  delete out.overrides
  if (pos && Number.isFinite(pos.w) && Number.isFinite(pos.h)) {
    out.size = { w: pos.w, h: pos.h }
  }
  return out
}

/**
 * Turn a library row back into a spec-widget body plus its remembered size.
 * The caller assigns the board-local `id` and `pos` — it owns id generation and
 * free-spot placement — so the returned widget deliberately has neither, and
 * `size` is handed back separately rather than leaking into the spec.
 * @param {object} row — a library row from listLibraryWidgets
 * @returns {{widget: object, size: {w:number,h:number}|null}}
 */
export function fromLibraryRow(row) {
  const config = row?.config && typeof row.config === 'object' ? row.config : {}
  const widget = toLibraryConfig(config)
  const size = widget.size ?? null
  delete widget.size
  return { widget, size }
}

// ---------------------------------------------------------------------------
// Generic deep-merge / diff helpers
// ---------------------------------------------------------------------------

function isPlainObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
}

/**
 * Sparse deep merge: only keys present in `overrides` win. Dict-valued keys
 * present in both `base` and `overrides` are merged key-by-key, recursively.
 * Any other value in `overrides` (including arrays) REPLACES the
 * corresponding `base` value wholesale — arrays never concatenate. Neither
 * input is mutated; a fresh object is returned. Mirrors backend `_deep_merge`
 * in `app/dashboards/widget_refs.py` exactly.
 * @param {object} base
 * @param {object} overrides
 * @returns {object}
 */
export function deepMergeOverrides(base, overrides) {
  const result = isPlainObject(base) ? { ...base } : {}
  if (!isPlainObject(overrides)) return result
  for (const [key, val] of Object.entries(overrides)) {
    if (isPlainObject(result[key]) && isPlainObject(val)) {
      result[key] = deepMergeOverrides(result[key], val)
    } else {
      result[key] = val
    }
  }
  return result
}

function deepEqual(a, b) {
  if (a === b) return true
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false
    return a.every((v, i) => deepEqual(v, b[i]))
  }
  if (isPlainObject(a) && isPlainObject(b)) {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)])
    for (const k of keys) {
      if (!deepEqual(a[k], b[k])) return false
    }
    return true
  }
  return false
}

/**
 * The inverse of `deepMergeOverrides`: given a library `base` config and an
 * `edited` effective config, compute the SPARSE overrides object that would
 * reproduce `edited` when merged onto `base`. A key equal to its base value
 * (recursively) is omitted — so re-editing a field back to the library's
 * value automatically clears the override, which is what powers "reset to
 * library value" for free (just re-run the diff after the edit).
 * @param {object} base
 * @param {object} edited
 * @returns {object}
 */
export function diffOverrides(base, edited) {
  const b = isPlainObject(base) ? base : {}
  const e = isPlainObject(edited) ? edited : {}
  const out = {}
  const keys = new Set([...Object.keys(b), ...Object.keys(e)])
  for (const key of keys) {
    if (!(key in e)) continue // absent in edited = "leave inherited" — never an explicit override
    const bv = b[key]
    const ev = e[key]
    if (isPlainObject(bv) && isPlainObject(ev)) {
      const nested = diffOverrides(bv, ev)
      if (Object.keys(nested).length > 0) out[key] = nested
    } else if (!deepEqual(bv, ev)) {
      out[key] = ev
    }
  }
  return out
}

// ---------------------------------------------------------------------------
// resolveWidgetRef — the resolver (mirrors backend resolve_widget_refs)
// ---------------------------------------------------------------------------

/** True when a spec widget is a reference (as opposed to fully inline). */
export function isRefWidget(widget) {
  return !!(widget && widget.ref != null && widget.ref !== '')
}

/**
 * A visible "broken reference" placeholder — a plain `text` widget (a type
 * that can never itself fail to render) so a dangling/deleted/malformed
 * `ref` degrades gracefully instead of crashing the render pipeline. Mirrors
 * backend `_broken_ref_widget` exactly (same type, same content string).
 */
function brokenRefWidget(specWidget) {
  return {
    id: specWidget?.id,
    type: 'text',
    ref: specWidget?.ref,
    tab_id: specWidget?.tab_id ?? BOARD_LOCAL_DEFAULTS.tab_id,
    pos: specWidget?.pos,
    placement: specWidget?.placement ?? BOARD_LOCAL_DEFAULTS.placement,
    order: specWidget?.order ?? BOARD_LOCAL_DEFAULTS.order,
    drawer: specWidget?.drawer ?? BOARD_LOCAL_DEFAULTS.drawer,
    drawer_group: specWidget?.drawer_group ?? BOARD_LOCAL_DEFAULTS.drawer_group,
    content: `Widget reference unavailable: ${specWidget?.ref}`,
    props: { broken_ref: true },
  }
}

/**
 * Resolve one spec widget against the library rows available to the caller.
 * Inline widgets (no `ref`) pass through unchanged. A `ref` widget resolves
 * to `deepMergeOverrides(libraryRow.config, widget.overrides)` with
 * board-local fields forced from the spec entry. A missing/malformed library
 * row (deleted entry, wrong org, non-dict config, config with no `type`)
 * degrades to a visible broken placeholder — this function never throws.
 *
 * The resolved widget additionally carries `ref` and `overrides` (dropped
 * from the merge itself, then re-attached) so editor UI can inspect override
 * state and offer Detach without a second lookup; renderers ignore the extra
 * keys, they only read the widget-shape fields they already know about.
 *
 * @param {object} specWidget — a spec widget entry (inline or ref)
 * @param {object|null|undefined} libraryRow — the `widgets` library row for
 *   `specWidget.ref`, or nullish if it's missing/deleted
 * @returns {{ widget: object, broken: boolean }}
 */
export function resolveWidgetRef(specWidget, libraryRow) {
  if (!isRefWidget(specWidget)) {
    return { widget: specWidget, broken: false }
  }

  const config = libraryRow && isPlainObject(libraryRow.config) ? libraryRow.config : null
  if (!config) {
    return { widget: brokenRefWidget(specWidget), broken: true }
  }

  const merged = deepMergeOverrides(config, specWidget.overrides || {})
  // A stray ref/overrides/size on the library config (shouldn't happen, but
  // config is caller-controlled) must not leak into the effective widget.
  delete merged.ref
  delete merged.overrides
  delete merged.size

  // Force board-local fields from the SPEC entry, never the library row.
  merged.id = specWidget.id
  merged.pos = specWidget.pos
  merged.tab_id = specWidget.tab_id ?? BOARD_LOCAL_DEFAULTS.tab_id
  merged.placement = specWidget.placement ?? BOARD_LOCAL_DEFAULTS.placement
  merged.order = specWidget.order ?? BOARD_LOCAL_DEFAULTS.order
  merged.drawer = specWidget.drawer ?? BOARD_LOCAL_DEFAULTS.drawer
  merged.drawer_group = specWidget.drawer_group ?? BOARD_LOCAL_DEFAULTS.drawer_group

  if (typeof merged.type !== 'string') {
    return { widget: brokenRefWidget(specWidget), broken: true }
  }

  return {
    widget: { ...merged, ref: specWidget.ref, overrides: specWidget.overrides || {} },
    broken: false,
  }
}

// ---------------------------------------------------------------------------
// applyWidgetEdit — the inverse: fold an editor change back into overrides
// ---------------------------------------------------------------------------

/**
 * Given the CURRENT spec entry for a widget and the FULL effective widget an
 * inspector `onChange` produced (a resolved widget with one field changed),
 * compute the next spec entry to commit.
 *
 * For an inline widget (`specWidget.ref` unset) this is the identity
 * function — `editedEffectiveWidget` IS the new spec entry, matching today's
 * behaviour exactly (backward compatible).
 *
 * For a `ref` widget, the edit is folded into `overrides` via `diffOverrides`
 * against the library's own config — never written to the library row —
 * while board-local fields (pos/tab_id/placement/order/drawer/drawer_group)
 * are taken directly from the edit, since those are never part of overrides.
 *
 * @param {object} specWidget — the widget's CURRENT unresolved spec entry
 * @param {object} editedEffectiveWidget — the full widget an onChange handler produced
 * @param {object|null|undefined} libraryRow — the library row for specWidget.ref
 * @returns {object} the next spec entry to store in spec.widgets
 */
export function applyWidgetEdit(specWidget, editedEffectiveWidget, libraryRow) {
  if (!isRefWidget(specWidget)) return editedEffectiveWidget

  const config = libraryRow && isPlainObject(libraryRow.config) ? libraryRow.config : {}
  const base = { ...config }
  delete base.size

  const editedConfig = {}
  for (const [k, v] of Object.entries(editedEffectiveWidget || {})) {
    if (k === 'ref' || k === 'overrides' || BOARD_LOCAL_KEYS.includes(k)) continue
    editedConfig[k] = v
  }

  const overrides = diffOverrides(base, editedConfig)
  const next = { id: specWidget.id, ref: specWidget.ref, overrides }
  for (const k of BOARD_LOCAL_KEYS) {
    if (k === 'id') continue
    if (editedEffectiveWidget && editedEffectiveWidget[k] !== undefined) next[k] = editedEffectiveWidget[k]
    else if (specWidget[k] !== undefined) next[k] = specWidget[k]
  }
  return next
}

// ---------------------------------------------------------------------------
// Override introspection — powers the inspector's "overridden vs inherited"
// per-field display and "reset to library value" action.
// ---------------------------------------------------------------------------

/**
 * Flatten a (possibly nested) overrides object into leaf `{ path, value }`
 * entries, e.g. `{ props: { label: 'X' }, query_id: 'q2' }` →
 * `[{ path: 'props.label', value: 'X' }, { path: 'query_id', value: 'q2' }]`.
 * @param {object} overrides
 * @param {string} [prefix]
 * @returns {Array<{ path: string, value: unknown }>}
 */
export function flattenOverridePaths(overrides, prefix = '') {
  const out: Array<{ path: string; value: unknown }> = []
  if (!isPlainObject(overrides)) return out
  for (const [k, v] of Object.entries(overrides)) {
    const path = prefix ? `${prefix}.${k}` : k
    if (isPlainObject(v)) {
      if (Object.keys(v).length === 0) continue
      out.push(...flattenOverridePaths(v, path))
    } else {
      out.push({ path, value: v })
    }
  }
  return out
}

/**
 * Remove one leaf override by dot-path (e.g. `'props.label'`), pruning any
 * parent object left empty. Returns a NEW overrides object; does not mutate
 * the input. Used by the inspector's per-field "reset to library value".
 * @param {object} overrides
 * @param {string} path
 * @returns {object}
 */
export function removeOverridePath(overrides, path) {
  const clone = JSON.parse(JSON.stringify(overrides ?? {}))
  const parts = String(path).split('.')

  function unset(obj, keys) {
    if (!isPlainObject(obj)) return
    const [head, ...rest] = keys
    if (rest.length === 0) {
      delete obj[head]
      return
    }
    if (!isPlainObject(obj[head])) return
    unset(obj[head], rest)
    if (Object.keys(obj[head]).length === 0) delete obj[head]
  }

  unset(clone, parts)
  return clone
}

/** Friendly labels for the override paths the built-in widget types actually use. */
const FRIENDLY_OVERRIDE_LABELS = {
  query_id: 'Query',
  chart_type: 'Chart type',
  content: 'Content',
  subtype: 'Filter type',
  target_var: 'Target variable',
  options_query_id: 'Options query',
  style: 'Style',
  'config.title': 'Title',
  'props.label': 'Label',
  'props.format': 'Format',
  'props.accentColor': 'Accent color',
  'props.icon': 'Icon',
  'props.limit': 'Row limit',
  'encoding.value': 'Value column',
  'encoding.x': 'X axis',
  'encoding.y': 'Y axis',
  'encoding.color': 'Color',
  'encoding.compare': 'Comparison column',
  'encoding.spark': 'Sparkline column',
}

/**
 * Human-readable label for an override leaf path, e.g. `'props.label'` →
 * `'Label'`. Falls back to a humanized last path segment for anything not in
 * the known-fields map, so unrecognised/custom fields still render sensibly.
 * @param {string} path
 * @returns {string}
 */
export function humanizeOverridePath(path) {
  if (FRIENDLY_OVERRIDE_LABELS[path]) return FRIENDLY_OVERRIDE_LABELS[path]
  const last = String(path).split('.').pop() ?? path
  return last
    .replace(/_/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/^./, (c) => c.toUpperCase())
}
