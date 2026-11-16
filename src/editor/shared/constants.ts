/**
 * shared/constants.js — Inspector-level constants shared between DashboardEditor
 * These mirror the constants in DashboardEditor.jsx
 * so both editors stay in sync without diverging.
 */

export const DEMO_QUERY_IDS = [
  "demo_all",
  "demo_active",
  "demo_points_10k",
  "demo_points_100k",
];

// All chart types supported by the shared embed/widgets/chart-options.js builder.
// Order determines display order in the chart-type picker grid.
export const CHART_TYPES = [
  // Core cartesian
  "bar",
  "line",
  "area",
  "scatter",
  "bubble",
  "combo",
  // Circular
  "pie",
  "donut",
  "polar",
  "radialbar",
  // Flow / hierarchy / distribution
  "sankey",
  "funnel",
  "waterfall",
  "treemap",
  // Matrix / radial
  "heatmap",
  "radar",
  // Statistical / financial / forecast
  "boxplot",
  "gauge",
  "candlestick",
  "fan",
  // Geographic
  "map",
];

// Per-series type options (for multi-series / combo chart series rows in the editor)
export const SERIES_TYPES = ["bar", "line", "area", "scatter"];
export const FILTER_SUBTYPES = [
  "select",
  "multiselect",
  "list",
  "daterange",
  "text",
];
export const VARIABLE_TYPES = [
  "text",
  "number",
  "date",
  "daterange",
  "select",
  "multiselect",
];

// Conditional-formatting operators (mirror conditionalFormat.js evalRules)
export const FORMAT_OPS = [
  "eq",
  "ne",
  "gt",
  "gte",
  "lt",
  "lte",
  "between",
  "contains",
  "not_contains",
];

// Display text for FORMAT_OPS — UI only; specs keep storing the op id.
export const FORMAT_OP_LABELS: Record<string, string> = {
  eq: "Equal (=)",
  ne: "Not equal (!=)",
  gt: "Greater than (>)",
  gte: "Greater or equal (>=)",
  lt: "Less than (<)",
  lte: "Less or equal (<=)",
  between: "Between",
  contains: "Contains",
  not_contains: "Does not contain",
};
/** Label for an op id; unknown ids (hand-written specs) show as-is. */
export function formatOpLabel(op: string): string {
  return FORMAT_OP_LABELS[op] ?? op;
}
// Per-column value-format types (mirror conditionalFormat.js formatValue)
export const COLUMN_FORMAT_TYPES = ["number", "currency", "percent", "date"];
// columnFormats[col].numberFormat for type:'number' — [value, label]; '' = locale default
export const NUMBER_DISPLAY_FORMATS = [
  ["", "locale"],
  ["commas", "1,234"],
  ["spaces", "1 234"],
  ["abbreviate", "1.2 K"],
];
export const BACKGROUND_TYPES = [
  "none",
  "transparent",
  "solid",
  "gradient",
  "image",
  "css",
];
export const PIVOT_AGGS = ["sum", "avg", "count", "min", "max"];
