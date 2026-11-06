/**
 * kpiDelta.ts — the signed percentage shown beside a KPI's direction triangle.
 *
 * Kept separate from KpiWidget so the arithmetic can be tested without React.
 */

export type KpiTrend = {
  /** Display text such as "6.50%" or "-21.25%"; the sign is the only marker. */
  text: string,
  direction: 'up' | 'down' | 'flat',
}

export function computeTrend(value: unknown, baseline: unknown, decimals = 2): KpiTrend | null {
  if (value == null || baseline == null) return null
  const v = Number(value)
  const b = Number(baseline)
  if (Number.isNaN(v) || Number.isNaN(b)) return null
  const diff = v - b
  const direction = diff > 0 ? 'up' : diff < 0 ? 'down' : 'flat'
  if (b === 0) return { text: diff === 0 ? `${(0).toFixed(decimals)}%` : '—', direction }
  return { text: `${((diff / Math.abs(b)) * 100).toFixed(decimals)}%`, direction }
}
