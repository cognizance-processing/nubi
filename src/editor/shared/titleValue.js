/**
 * shared/titleValue.js — read/write a chart's `config.title` in either form.
 *
 * Kept as plain .js (no JSX) so it is testable under `node --test`.
 *
 * Why this exists
 * ---------------
 * `config.title` is passed straight through to ECharts by the shared chart
 * builder (embed/widgets/chart-options.js → titleConfig), which deliberately
 * accepts BOTH forms:
 *
 *   'Revenue by region'                                   — string
 *   { text: 'Revenue by region', textStyle: {…}, left: … } — ECharts title object
 *
 * Boards imported from the legacy platform carry the object form (that's where
 * their per-title colour/size/alignment lives), so any editor field that treats
 * the title as a string will either render "[object Object]" or throw on
 * `.trim()`. Read through `titleText`, write through `withTitleText` — the
 * latter edits the object's `text` in place so styling survives a retype.
 */

/**
 * The human-readable title text, whichever form the title is stored in.
 * @param {unknown} title
 * @returns {string}
 */
export function titleText(title) {
  if (typeof title === 'string') return title
  if (title && typeof title === 'object' && !Array.isArray(title)) {
    const text = /** @type {{ text?: unknown }} */ (title).text
    if (typeof text === 'string') return text
    if (typeof text === 'number') return String(text)
  }
  return ''
}

/**
 * Set the title text, preserving an object title's other keys (textStyle,
 * left, subtext…). Returns `undefined` for a cleared string title so the key
 * drops out of the config entirely, matching what the editor wrote before.
 * @param {unknown} title  current title value
 * @param {string} text    new title text
 * @returns {string | Record<string, unknown> | undefined}
 */
export function withTitleText(title, text) {
  if (title && typeof title === 'object' && !Array.isArray(title)) {
    return { ...(/** @type {Record<string, unknown>} */ (title)), text }
  }
  return text || undefined
}
