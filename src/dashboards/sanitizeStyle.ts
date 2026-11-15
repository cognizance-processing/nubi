/**
 * Pure helpers for the HTML-widget sanitizer (no DOM needed, so they are unit
 * tested directly).
 *
 * An HTML widget is authored content that other people open. Two things it must
 * not be able to do, on top of "no script":
 *   - leave its card: `position` / `z-index` would let it cover the app's own
 *     controls (a fake login prompt, a hidden click target);
 *   - redirect a Nubi widget element: `backend` / `token` / `get-token` would
 *     point it at another server or hand it a credential.
 */

/** Attributes Nubi widget elements may carry inside authored HTML. */
export const WIDGET_ATTRS: string[] = [
  // nubi-kpi
  'query-id',
  'value-col',
  'label',
  'format',
  // nubi-table
  'limit',
  'columns',
  // nubi-chart
  'type',
  'x',
  'y',
  'color',
  // layout / styling (filtered by sanitizeInlineStyle)
  'style',
  'class',
  // standard safe HTML attrs (already allowed by DOMPurify, listed for clarity)
  'id',
  'title',
  'alt',
  'src',
  'href',
  'rel',
  'target',
  'colspan',
  'rowspan',
  'width',
  'height',
]

/** Never allowed in authored HTML: they come from the page's own context. */
export const FORBIDDEN_WIDGET_ATTRS: string[] = ['backend', 'token', 'get-token']

const BLOCKED_PROPERTIES = new Set(['position', 'z-index', 'behavior', '-moz-binding'])
const BLOCKED_VALUE = /url\s*\(|expression\s*\(|javascript:|@import|\\/i

/**
 * Return an inline style with the declarations removed that could take the
 * element out of its card or load/run something. Unknown or malformed
 * declarations that mention a blocked construct are dropped whole.
 */
export function sanitizeInlineStyle(style: unknown): string {
  if (typeof style !== 'string' || !style.trim()) return ''
  const kept: string[] = []
  for (const raw of style.split(';')) {
    const decl = raw.trim()
    if (!decl) continue
    const colon = decl.indexOf(':')
    if (colon <= 0) continue
    // Comments and escapes are ways to hide a property name or a function call.
    if (decl.includes('/*') || BLOCKED_VALUE.test(decl)) continue
    const property = decl.slice(0, colon).trim().toLowerCase()
    if (BLOCKED_PROPERTIES.has(property)) continue
    kept.push(decl)
  }
  return kept.join('; ')
}
