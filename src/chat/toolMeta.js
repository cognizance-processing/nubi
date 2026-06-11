/**
 * toolMeta — shared, presentation-only helpers for rendering AI tool calls.
 *
 * Kept in a plain (non-JSX) module so it can export functions/constants
 * without tripping react-refresh's "only export components" rule. Consumed by
 * <ToolCard> and by the global chat panel (toolLabel for pin titles).
 */

import {
  Wrench, BarChart3, Database, Search, Code2, Table2, Layers,
} from 'lucide-react'

// name → icon + friendly label + accent colours
const TOOL_META = {
  generate_sql:          { icon: Code2,     label: 'Generate SQL',     color: 'text-brand-blue', bg: 'bg-brand-blue/10' },
  run_query:             { icon: Database,  label: 'Run query',        color: 'text-brand-teal', bg: 'bg-brand-teal/10' },
  create_dashboard:      { icon: BarChart3, label: 'Create dashboard', color: 'text-brand-blue', bg: 'bg-brand-blue/10' },
  edit_dashboard:        { icon: BarChart3, label: 'Edit dashboard',   color: 'text-brand-blue', bg: 'bg-brand-blue/10' },
  propose_dashboard_spec:{ icon: Layers,    label: 'Propose dashboard',color: 'text-brand-blue', bg: 'bg-brand-blue/10' },
  get_schema:            { icon: Table2,    label: 'Inspect schema',   color: 'text-accent',     bg: 'bg-accent/10' },
  list_queries:          { icon: Search,    label: 'List queries',     color: 'text-accent',     bg: 'bg-accent/10' },
  default:               { icon: Wrench,    label: null,               color: 'text-muted',      bg: 'bg-surface-2' },
}

export const getToolMeta = (name) => TOOL_META[name] ?? TOOL_META.default
export const toolLabel = (name) =>
  getToolMeta(name).label ?? (name ? name.replace(/_/g, ' ') : 'tool')

export const truncate = (s, n = 64) => (s && s.length > n ? s.slice(0, n) + '…' : s || '')

/** Coerce a possibly-stringified JSON tool output into an object/value. */
export function coerce(value) {
  if (typeof value !== 'string') return value
  const t = value.trim()
  if (t.startsWith('{') || t.startsWith('[')) {
    try { return JSON.parse(t) } catch { return value }
  }
  return value
}

/** A one-line summary shown on a collapsed tool card header. */
export function toolSummary(tool, result) {
  const value = coerce(result)
  if (value == null || typeof value !== 'object') return value ? truncate(String(value), 56) : ''
  if (value.error) return truncate(String(value.error), 56)
  if (tool === 'generate_sql' && value.sql) return truncate(value.sql, 56)
  if (tool === 'run_query' && value.row_count != null) return `${value.row_count} rows · ${(value.columns || []).length} cols`
  if (value.widget_count != null) return `${value.widget_count} widgets`
  if (Array.isArray(value.widgets)) return `${value.widgets.length} widgets`
  if (value.title) return truncate(String(value.title), 56)
  return ''
}
