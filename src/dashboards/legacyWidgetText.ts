/**
 * legacyWidgetText.js — pure label/caption derivation for the presentational
 * widgets carried over from the legacy estate (`breadcrumb`, `datedisplay`).
 *
 * Lives here rather than inside the widget components for the same reason
 * widgetLibraryShape.js does: those components import the query runtime, which
 * reaches `import.meta.env` and only exists under Vite, so anything importing
 * them is untestable under `node --test`. The interesting logic — how a filter
 * value becomes a readable crumb, how a date variable becomes a caption — is
 * all here and is covered by legacyWidgetText.test.mjs.
 */

import { resolveDateRange } from './inputs/helpers.js'

// ---------------------------------------------------------------------------
// Breadcrumb crumb labels
// ---------------------------------------------------------------------------

/**
 * Render any variable value as a short chip label.
 *
 * Handles the shapes Nubi filters actually write: a scalar (select/text), an
 * array (multiselect), `{mode, values}` (exclude-mode multiselect), and
 * `{from, to}` / `{preset}` (daterange).
 *
 * An empty result is meaningful, not a failure: it is how the breadcrumb knows
 * a variable holds no selection and therefore contributes no crumb.
 *
 * @param {unknown} value
 * @returns {string}
 */
export function chipLabel(value) {
  if (value == null || value === '') return ''
  if (Array.isArray(value)) {
    const vals = value.filter(v => v != null && v !== '').map(String)
    if (vals.length === 0) return ''
    if (vals.length <= 2) return vals.join(', ')
    return `${vals.slice(0, 2).join(', ')} +${vals.length - 2}`
  }
  if (typeof value === 'object') {
    if (Array.isArray(value.values)) {
      const inner = chipLabel(value.values)
      if (!inner) return ''
      return value.mode === 'exclude' ? `not ${inner}` : inner
    }
    // 'custom' is the picker's marker for "use the explicit bounds below" — the
    // word itself tells a viewer nothing, so fall through to from/to.
    if (value.preset && value.preset !== 'custom') return String(value.preset).replace(/_/g, ' ')
    if (value.from || value.to) return [value.from, value.to].filter(Boolean).join(' – ')
    const entries = Object.entries(value).filter(([, v]) => v != null && v !== '')
    if (!entries.length) return ''
    return entries.slice(0, 2).map(([k, v]) => `${k}: ${v}`).join(', ')
  }
  return String(value)
}

/** True when a variable currently holds no selection at all. */
export function isEmptyValue(value) {
  return chipLabel(value) === ''
}

// ---------------------------------------------------------------------------
// Date-display captions
// ---------------------------------------------------------------------------

const DATE_STYLES = {
  short: { year: '2-digit', month: 'numeric', day: 'numeric' },
  medium: { year: 'numeric', month: 'short', day: 'numeric' },
  long: { year: 'numeric', month: 'long', day: 'numeric' },
  month: { year: 'numeric', month: 'long' },
}

/**
 * Format one date-ish value.
 *
 * `raw` never parses, so a query column that already holds a formatted string
 * ("Jan–Mar FY25") survives untouched; `iso` only trims a timestamp down to its
 * day part. Anything unparseable is shown verbatim — a wrong-looking date is
 * still information, "Invalid Date" is not.
 *
 * @param {unknown} value
 * @param {string} [format]  'iso' | 'medium' | 'long' | 'short' | 'month' | 'raw'
 * @returns {string}
 */
export function formatDateValue(value, format) {
  if (value == null || value === '') return ''
  const str = String(value)
  if (format === 'raw') return str
  if (format === 'iso' || !format) {
    return /^\d{4}-\d{2}-\d{2}T/.test(str) ? str.slice(0, 10) : str
  }
  const style = DATE_STYLES[format]
  if (!style) return str
  const d = new Date(str)
  if (Number.isNaN(d.getTime())) return str
  return d.toLocaleDateString(undefined, style)
}

/**
 * Render a variable's value as a date caption. A daterange variable (object or
 * array) becomes "from – to"; anything else becomes a single date.
 *
 * A RELATIVE preset ("last 30 days") is resolved to concrete dates here — that
 * resolution is the whole reason this is a live widget rather than static text.
 *
 * @param {unknown} value
 * @param {string} format
 * @param {string} separator
 * @returns {string}
 */
export function captionForVariable(value, format, separator) {
  if (value == null || value === '') return ''
  if (Array.isArray(value)) {
    const parts = value.filter(v => v != null && v !== '').map(v => formatDateValue(v, format))
    return parts.join(` ${separator} `)
  }
  if (typeof value === 'object') {
    const { from, to } = resolveDateRange(value)
    if (!from && !to) return ''
    if (from && to) return `${formatDateValue(from, format)} ${separator} ${formatDateValue(to, format)}`
    return formatDateValue(from || to, format)
  }
  return formatDateValue(value, format)
}
