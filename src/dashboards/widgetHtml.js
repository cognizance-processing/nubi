/**
 * widgetHtml.js — background/style builders + safe widget-HTML interpolation.
 *
 * Three concerns, all pure (no React, no DOM writes):
 *
 *   backgroundToCss(bg)   — DashboardSpec.background / widget.style.background
 *                           descriptor → React inline-style object.
 *   styleToCss(style)     — widget.style descriptor → whitelisted inline style.
 *   renderWidgetHtml(...)  — interpolate {{tokens}} from a widget's query result
 *                           then run the result through the dashboard sanitizer.
 *
 * SECURITY: all custom HTML passes through sanitizeDashboardHtml() (DOMPurify)
 * before it can reach innerHTML, and every interpolated DATA value is
 * HTML-escaped first so a cell value can never introduce markup. This keeps the
 * stored-XSS trust boundary that sanitize.js already enforces.
 */

import { sanitizeDashboardHtml } from './sanitize.js'

// ---------------------------------------------------------------------------
// Background descriptor → inline style
// ---------------------------------------------------------------------------

/**
 * @param {{ type?: 'solid'|'gradient'|'image', color?: string, from?: string,
 *   to?: string, angle?: number, imageUrl?: string, css?: string }} bg
 * @returns {object|undefined} React style object (or undefined when empty)
 */
export function backgroundToCss(bg) {
  if (!bg || typeof bg !== 'object') return undefined
  switch (bg.type) {
    case 'transparent':
      return { background: 'transparent' }
    case 'solid':
      return bg.color ? { background: bg.color } : undefined
    case 'gradient': {
      const from = bg.from || '#6366f1'
      const to = bg.to || '#ec4899'
      const angle = Number.isFinite(bg.angle) ? bg.angle : 135
      return { background: `linear-gradient(${angle}deg, ${from}, ${to})` }
    }
    case 'image':
      return bg.imageUrl && _safeUrl(bg.imageUrl)
        ? {
            backgroundImage: `url("${bg.imageUrl.replace(/"/g, '%22')}")`,
            backgroundSize: 'cover',
            backgroundPosition: 'center',
            backgroundRepeat: 'no-repeat',
          }
        : undefined
    case 'css':
      return parseCssString(bg.css)
    default:
      return undefined
  }
}

/** Reject javascript:/data:/vbscript: URLs for image backgrounds. */
function _safeUrl(url) {
  const v = String(url).trim().toLowerCase()
  return !(v.startsWith('javascript:') || v.startsWith('data:') || v.startsWith('vbscript:'))
}

// ---------------------------------------------------------------------------
// widget.style descriptor → whitelisted inline style
// ---------------------------------------------------------------------------

const STYLE_WHITELIST = new Set([
  'background', 'backgroundColor', 'backgroundImage', 'backgroundSize',
  'backgroundPosition', 'backgroundRepeat',
  'color', 'border', 'borderColor', 'borderWidth', 'borderStyle',
  'borderRadius', 'padding', 'margin', 'boxShadow', 'opacity', 'backdropFilter',
])

/**
 * Build a safe inline-style object for a widget card from a widget.style
 * descriptor: { background?, border?, padding?, borderRadius?, color?, css? }.
 * Any freeform `css` string is parsed and property-whitelisted.
 *
 * @param {object} style
 * @returns {object|undefined}
 */
export function styleToCss(style) {
  if (!style || typeof style !== 'object') return undefined
  const out = {}

  // background can be a plain color string or a background descriptor object
  if (style.background && typeof style.background === 'object') {
    Object.assign(out, backgroundToCss(style.background))
  } else if (typeof style.background === 'string' && style.background) {
    out.background = style.background
  }

  for (const k of ['border', 'padding', 'borderRadius', 'color', 'boxShadow']) {
    if (typeof style[k] === 'string' && style[k]) out[k] = style[k]
  }

  if (style.css) Object.assign(out, parseCssString(style.css))

  return Object.keys(out).length ? out : undefined
}

/**
 * Parse a freeform `prop: value; prop: value` CSS string into a whitelisted
 * React style object. Property names are camelCased; values containing
 * javascript:/expression() are dropped.
 *
 * @param {string} css
 * @returns {object}
 */
export function parseCssString(css) {
  const out = {}
  if (typeof css !== 'string') return out
  for (const decl of css.split(';')) {
    const idx = decl.indexOf(':')
    if (idx === -1) continue
    const rawProp = decl.slice(0, idx).trim()
    const value = decl.slice(idx + 1).trim()
    if (!rawProp || !value) continue
    const lowered = value.toLowerCase()
    if (lowered.includes('javascript:') || lowered.includes('expression(')) continue
    const prop = rawProp.replace(/-([a-z])/g, (_, c) => c.toUpperCase())
    if (STYLE_WHITELIST.has(prop)) out[prop] = value
  }
  return out
}

// ---------------------------------------------------------------------------
// Widget HTML template interpolation
// ---------------------------------------------------------------------------

function escapeHtml(v) {
  if (v == null) return ''
  return String(v)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/**
 * Resolve a single {{token}} expression against the widget's data context.
 *
 * Supported tokens:
 *   {{value}}        — the widget's headline value (extra.value, else first cell)
 *   {{col:NAME}}     — column NAME of the FIRST row
 *   {{row.N.NAME}}   — column NAME of row index N
 *   {{prop:NAME}}    — widget props[NAME]
 */
function resolveToken(expr, table, props, extra) {
  if (expr === 'value') {
    if (extra.value !== undefined) return extra.value
    return _cell(table, 0, _firstColName(table))
  }
  if (expr.startsWith('col:')) {
    return _cell(table, 0, expr.slice(4).trim())
  }
  if (expr.startsWith('prop:')) {
    return props?.[expr.slice(5).trim()]
  }
  const rowMatch = /^row\.(\d+)\.(.+)$/.exec(expr)
  if (rowMatch) {
    return _cell(table, Number(rowMatch[1]), rowMatch[2].trim())
  }
  return ''
}

function _firstColName(table) {
  return table?.schema?.fields?.[0]?.name ?? null
}

function _cell(table, rowIdx, colName) {
  if (!table || !colName) return ''
  const child = table.getChild(colName)
  if (!child || rowIdx >= table.numRows) return ''
  return child.get(rowIdx)
}

/**
 * Interpolate a widget HTML template with query data, then sanitize.
 *
 * @param {string} template — author HTML with {{tokens}}
 * @param {{ table?: object, props?: object, extra?: object }} ctx
 * @returns {string} sanitized, interpolated HTML (safe for innerHTML)
 */
export function renderWidgetHtml(template, { table = null, props = {}, extra = {} } = {}) {
  if (typeof template !== 'string' || !template) return ''
  const interpolated = template.replace(/\{\{\s*([^}]+?)\s*\}\}/g, (_, raw) =>
    escapeHtml(resolveToken(raw.trim(), table, props, extra)),
  )
  return sanitizeDashboardHtml(interpolated)
}
