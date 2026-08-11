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
