/**
 * shared/chartMeta.tsx — Chart-type presentation metadata, shared by every
 * surface that offers a chart-type picker.
 *
 * Lives apart from ChartConfig.tsx so the query workspace's widget preview can
 * offer the SAME set of types, icons, labels and encoding slots as the board
 * editor's inspector — one list, no drift.
 *
 *   CHART_ICONS   — lucide icon per chart_type (picker grids)
 *   CHART_LABELS  — human-readable label per chart_type
 *   TYPE_ENCODING — the encoding slots each chart_type consumes, in the order
 *                   a picker should present them. `optional: true` entries may
 *                   be left unset without breaking the chart.
 */

import {
  BarChart3, Layers, Map as MapIcon,
  LineChart, BarChartHorizontal, ScatterChart, AreaChart, PieChart, Gauge,
  Grid3x3, GitCommitHorizontal, TrendingDown, Waves, LayoutDashboard,
  Radar, CandlestickChart, Wind, CircleDashed,
} from 'lucide-react'

// Lucide icon map for the chart-type picker grid
export const CHART_ICONS = {
  bar:         BarChart3,
  line:        LineChart,
  area:        AreaChart,
  scatter:     ScatterChart,
  bubble:      ScatterChart,
  pie:         PieChart,
  donut:       PieChart,
  polar:       PieChart,
  radialbar:   CircleDashed,
  sankey:      GitCommitHorizontal,
  funnel:      TrendingDown,
  waterfall:   Waves,
  treemap:     LayoutDashboard,
  heatmap:     Grid3x3,
  radar:       Radar,
  boxplot:     BarChartHorizontal,
  gauge:       Gauge,
  candlestick: CandlestickChart,
  fan:         Wind,
  combo:       Layers,
  map:         MapIcon,
}

// Human-readable labels for chart types
export const CHART_LABELS = {
  bar:         'Bar',
  line:        'Line',
  area:        'Area',
  scatter:     'Scatter',
  bubble:      'Bubble',
  pie:         'Pie',
  donut:       'Donut',
  polar:       'Polar',
  radialbar:   'Radial bar',
  sankey:      'Sankey',
  funnel:      'Funnel',
  waterfall:   'Waterfall',
  treemap:     'Treemap',
  heatmap:     'Heatmap',
  radar:       'Radar',
  boxplot:     'Box Plot',
  gauge:       'Gauge',
  candlestick: 'Candle',
  fan:         'Fan',
  combo:       'Combo',
  map:         'Map',
}

// Per-type encoding field definitions
// Each entry: { key, label, required?, hint? }
export const TYPE_ENCODING = {
  // A choropleth needs the region NAME column (matched against the registered
  // GeoJSON feature names) and the value that colours it.
  map:         [{ key: 'name', label: 'Region column' }, { key: 'value', label: 'Value column' }],
  bar:         [{ key: 'x', label: 'X (category)' }, { key: 'y', label: 'Y (value)' }, { key: 'color', label: 'Group / color', optional: true }],
  line:        [{ key: 'x', label: 'X' }, { key: 'y', label: 'Y' }, { key: 'y2', label: 'Y2 (secondary axis)', optional: true }, { key: 'color', label: 'Group / color', optional: true }],
  area:        [{ key: 'x', label: 'X' }, { key: 'y', label: 'Y' }, { key: 'color', label: 'Group / color', optional: true }],
  scatter:     [{ key: 'x', label: 'X' }, { key: 'y', label: 'Y' }, { key: 'color', label: 'Group / color', optional: true }],
  bubble:      [{ key: 'x', label: 'X' }, { key: 'y', label: 'Y' }, { key: 'size', label: 'Size column', optional: true }, { key: 'color', label: 'Group / color', optional: true }],
  pie:         [{ key: 'x', label: 'Category column' }, { key: 'y', label: 'Value column' }],
  donut:       [{ key: 'x', label: 'Category column' }, { key: 'y', label: 'Value column' }],
  polar:       [{ key: 'x', label: 'Category column' }, { key: 'y', label: 'Value column' }],
  radialbar:   [{ key: 'x', label: 'Ring label column' }, { key: 'y', label: 'Value column' }],
  sankey:      [{ key: 'source', label: 'Source column' }, { key: 'target', label: 'Target column' }, { key: 'value', label: 'Value column' }],
  funnel:      [{ key: 'x', label: 'Stage / label column' }, { key: 'y', label: 'Value column' }],
  waterfall:   [{ key: 'x', label: 'Category column' }, { key: 'y', label: 'Delta (change) column' }],
  treemap:     [{ key: 'x', label: 'Name column' }, { key: 'y', label: 'Size (value) column' }, { key: 'color', label: 'Group column', optional: true }],
  heatmap:     [{ key: 'x', label: 'X column' }, { key: 'color', label: 'Y column' }, { key: 'value', label: 'Value column' }],
  radar:       [{ key: 'x', label: 'Indicator column' }, { key: 'y', label: 'Value column' }, { key: 'color', label: 'Series / group', optional: true }],
  boxplot:     [{ key: 'x', label: 'Category column' }, { key: 'y', label: 'Values column (raw — auto-boxed)' }],
  gauge:       [{ key: 'y', label: 'Value column' }],
  candlestick: [{ key: 'x', label: 'Date / time column' }, { key: 'open', label: 'Open column' }, { key: 'close', label: 'Close column' }, { key: 'low', label: 'Low column' }, { key: 'high', label: 'High column' }],
  fan:         [{ key: 'x', label: 'X (time) column' }, { key: 'y', label: 'Forecast (midline) column' }, { key: 'lower', label: 'Lower bound column', optional: true }, { key: 'upper', label: 'Upper bound column', optional: true }],
}
