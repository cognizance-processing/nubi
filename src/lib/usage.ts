/**
 * usage.js — API client for the open-core USAGE surface.
 *
 * Usage is read-only metering visibility: how much an org has consumed this
 * period, paired with the configured soft limit ("used / limit / %").  It is
 * deliberately BILLING-FREE — charging/wallets live in the EE tree; usage
 * counters are core.  The numbers are aggregated server-side from the core
 * ``usage_events`` table (populated off the hot path by the metering sink), so
 * this client just transports them.
 *
 * Endpoints mirror backend/app/routes/usage.py (paths under /api/v1):
 *   GET /usage?period=          usageSummary
 *   GET /usage/series?metric=&period=   usageSeries
 *
 * Both read helpers degrade gracefully: on a transport/auth error they return a
 * safe empty shape so the page can still render its empty/error state.
 *
 * Summary shape:
 *   {
 *     period: 'day'|'week'|'month',
 *     period_start, period_end,    // ISO strings
 *     metrics: [
 *       { id, label, unit, used, limit|null, pct|null },
 *       ...
 *     ]
 *   }
 *
 * Series shape:
 *   { metric, label, unit, period, bucket: 'hour'|'day',
 *     points: [{ t: ISO, value: number }, ...] }
 */

import { get } from './api.js'

const PERIODS = ['day', 'week', 'month']

function normPeriod(period) {
  return PERIODS.includes(period) ? period : 'month'
}

/**
 * Fetch the org's current-period usage summary.
 * @param {'day'|'week'|'month'} [period]
 * @returns {Promise<{period:string, period_start:string, period_end:string, metrics:Array}>}
 */
export async function usageSummary(period = 'month') {
  try {
    const data = await get(`/usage?period=${encodeURIComponent(normPeriod(period))}`)
    return {
      period: data?.period ?? period,
      period_start: data?.period_start ?? null,
      period_end: data?.period_end ?? null,
      metrics: Array.isArray(data?.metrics) ? data.metrics : [],
    }
  } catch {
    return { period, period_start: null, period_end: null, metrics: [] }
  }
}

/**
 * Fetch a per-bucket time series for one usage metric.
 * @param {string} metric    A usage-metric id (e.g. 'queries', 'compute_units').
 * @param {'day'|'week'|'month'} [period]
 * @returns {Promise<{metric:string, label:string, unit:string, period:string, bucket:string, points:Array}>}
 */
export async function usageSeries(metric, period = 'month') {
  try {
    const qs = `metric=${encodeURIComponent(metric)}&period=${encodeURIComponent(normPeriod(period))}`
    const data = await get(`/usage/series?${qs}`)
    return {
      metric: data?.metric ?? metric,
      label: data?.label ?? metric,
      unit: data?.unit ?? '',
      period: data?.period ?? period,
      bucket: data?.bucket ?? 'day',
      points: Array.isArray(data?.points) ? data.points : [],
    }
  } catch {
    return { metric, label: metric, unit: '', period, bucket: 'day', points: [] }
  }
}

/**
 * Format a usage value for display given its unit. Bytes get human sizes;
 * counts/CU/tokens get locale grouping; GB gets one decimal.
 * @param {number} value
 * @param {string} unit
 * @returns {string}
 */
export function formatUsage(value, unit) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  if (unit === 'bytes') return formatBytes(n)
  if (unit === 'GB') return `${n.toLocaleString(undefined, { maximumFractionDigits: 1 })} GB`
  if (unit === 'CU') return `${Math.round(n).toLocaleString()} CU`
  if (unit === 'tokens') return Math.round(n).toLocaleString()
  // count and anything else
  return Math.round(n).toLocaleString()
}

/** Human-readable byte size (1024-based). */
export function formatBytes(bytes) {
  const n = Number(bytes)
  if (!Number.isFinite(n) || n <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1)
  const v = n / Math.pow(1024, i)
  return `${v.toLocaleString(undefined, { maximumFractionDigits: i === 0 ? 0 : 1 })} ${units[i]}`
}
