/**
 * format.js — date formatting helpers for the /admin pages.
 *
 * Lives outside AdminUI.jsx so that file only exports components
 * (react-refresh fast-refresh constraint).
 */

/** Format an ISO date as a short, locale date (no time). */
export function fmtDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString()
}

/** Format an ISO datetime as date + time. */
export function fmtDateTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
}

/** Cell text from any API value: strings pass through, objects are summarised as compact `k=v` pairs, empty objects vanish. */
export function cellText(v: unknown, max = 120): string {
  if (v == null) return ''
  let out = ''
  if (typeof v === 'string') out = v
  else if (typeof v === 'number' || typeof v === 'boolean') out = String(v)
  else if (typeof v === 'object') {
    const entries = Object.entries(v as Record<string, unknown>).filter(([, x]) => x != null && x !== '')
    out = entries
      .map(([k, x]) => `${k}=${typeof x === 'object' ? JSON.stringify(x) : String(x)}`)
      .join(' · ')
  }
  return out.length > max ? `${out.slice(0, max - 1)}…` : out
}
