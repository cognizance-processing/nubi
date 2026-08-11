/**
 * MetricWidget.jsx — big stat tile (value + delta + sparkline).
 *
 * Reuses KpiWidget's data + delta + sparkline capabilities; `metric` is a
 * distinct spec type so authors can pick a stat-tile from the palette. The
 * encoding/props shape is identical to KpiWidget:
 *
 *   encoding: { value, compare?, spark? }
 *   props:    { label, format, deltaFormat? }
 *
 * It simply delegates to KpiWidget — kept as a separate component so the
 * dispatch table and palette can present it independently and so future
 * metric-specific styling can diverge without touching KpiWidget.
 */

import KpiWidget from './KpiWidget.jsx'

export default function MetricWidget({ widget }) {
  return <KpiWidget widget={widget} />
}
