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
 */

/**
 * Board-local fields that must NOT be persisted with a library entry: they
 * describe where a widget sits on one particular board, not what it is.
 * `pos` is handled specially — its x/y are board-local, but its w/h are
 * authoring intent (a KPI tile and a wide chart are not the same size), so the
 * dimensions survive as `size` while the coordinates are dropped.
 */
const BOARD_LOCAL_KEYS = ['id', 'pos', 'tab_id']

/**
 * Strip a spec widget down to the reusable part.
 * @param {object} widget — a spec widget
 * @returns {object} the widget config to persist, with `size` in place of `pos`
 */
export function toLibraryConfig(widget) {
  const out = { ...(widget ?? {}) }
  const pos = widget?.pos
  for (const k of BOARD_LOCAL_KEYS) delete out[k]
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
