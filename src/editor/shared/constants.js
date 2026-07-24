/**
 * shared/constants.js — Inspector-level constants shared between DashboardEditor
 * These mirror the constants in DashboardEditor.jsx
 * so both editors stay in sync without diverging.
 */

export const DEMO_QUERY_IDS = ['demo_all', 'demo_active', 'demo_points_10k', 'demo_points_100k']

// All chart types supported by the shared embed/widgets/chart-options.js builder.
// Order determines display order in the chart-type picker grid.
export const CHART_TYPES = [
  // Core cartesian
  'bar', 'line', 'area', 'scatter', 'bubble', 'combo',
  // Circular
  'pie', 'donut',
  // Flow / hierarchy / distribution
  'sankey', 'funnel', 'waterfall', 'treemap',
  // Matrix / radial
  'heatmap', 'radar',
  // Statistical / financial / forecast
  'boxplot', 'gauge', 'candlestick', 'fan',
  // Geographic
  'map',
]

// Per-series type options (for multi-series / combo chart series rows in the editor)
export const SERIES_TYPES = ['bar', 'line', 'area', 'scatter']
export const FILTER_SUBTYPES = ['select', 'multiselect', 'list', 'daterange', 'text']
export const VARIABLE_TYPES = ['text', 'number', 'date', 'daterange', 'select', 'multiselect']

// Conditional-formatting operators (mirror conditionalFormat.js evalRules)
export const FORMAT_OPS = ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between', 'contains']
// Per-column value-format types (mirror conditionalFormat.js formatValue)
export const COLUMN_FORMAT_TYPES = ['number', 'currency', 'percent', 'date']
export const BACKGROUND_TYPES = ['none', 'transparent', 'solid', 'gradient', 'image', 'css']
export const PIVOT_AGGS = ['sum', 'avg', 'count', 'min', 'max']
