/**
 * MetricPicker — a reusable control for binding to a governed METRIC.
 *
 * Given the list of metrics (the compact summary rows from listMetrics —
 * { id, name, measure, dimensions:[name], time_grains:[grain], description }),
 * it lets the user:
 *   1. pick a metric,
 *   2. choose a SUBSET of that metric's allowed dimensions,
 *   3. choose a time grain (constrained to the metric's allowed grains),
 *
 * and emits a binding object shaped like the backend MetricBinding /
 * MetricQuery contract:
 *
 *   { metric_id, dimensions: string[], time_grain: string|null, filters: [] }
 *
 * It is fully controlled: it never holds binding state itself. The parent owns
 * `value` (the current binding or null) and receives the next binding via
 * `onChange`. Selecting a different metric resets dimensions/time_grain to a
 * clean binding so the picker can never emit a dim/grain the new metric
 * disallows. `filters` are passed through untouched (the picker doesn't author
 * filters — that stays the parent's concern).
 */

import { useMemo } from 'react'

// Shared field-control styles (mirror DashboardEditor's selectCls intent but
// kept self-contained so the picker can be dropped into any panel).
const selectCls =
  'w-full h-8 text-sm border border-border rounded-lg px-2.5 bg-surface text-fg ' +
  'focus:outline-none focus:ring-2 focus:ring-ring/60 focus:border-ring/40 ' +
  'hover:border-border/80 transition-colors cursor-pointer'

function Label({ children }) {
  return <label className="block text-[11px] font-medium text-muted mb-1">{children}</label>
}

/**
 * @param {{
 *   metrics: Array<{ id, name, dimensions?: string[], time_grains?: string[] }>,
 *   value: { metric_id?: string, dimensions?: string[], time_grain?: string|null, filters?: any[] } | null,
 *   onChange: (binding: { metric_id: string, dimensions: string[], time_grain: string|null, filters: any[] } | null) => void,
 *   allowClear?: boolean,
 * }} props
 */
export default function MetricPicker({ metrics = [], value, onChange, allowClear = true }) {
  const metricId = value?.metric_id ?? ''
  const selected = useMemo(
    () => metrics.find(m => m.id === metricId) ?? null,
    [metrics, metricId],
  )

  // The metric's allowed dimensions / grains (defensive against shape drift).
  const allowedDims = useMemo(() => {
    const dims = selected?.dimensions ?? []
    // dimensions may be plain names (summary shape) or {name,...} (full def).
    return dims.map(d => (typeof d === 'string' ? d : d?.name)).filter(Boolean)
  }, [selected])

  const allowedGrains = useMemo(() => {
    if (!selected) return []
    if (Array.isArray(selected.time_grains)) return selected.time_grains
    const grains = selected.time_dimension?.grains
    return Array.isArray(grains) ? grains : []
  }, [selected])

  const selectedDims = Array.isArray(value?.dimensions) ? value.dimensions : []
  const timeGrain = value?.time_grain ?? ''

  // ── Mutators — always emit the full binding shape ─────────────────────────

  function selectMetric(id) {
    if (!id) {
      onChange(null)
      return
    }
    // Fresh binding: drop any dims/grain that the previous metric allowed.
    onChange({ metric_id: id, dimensions: [], time_grain: null, filters: [] })
  }

  function toggleDim(name) {
    const next = selectedDims.includes(name)
      ? selectedDims.filter(d => d !== name)
      : [...selectedDims, name]
    onChange({
      metric_id: metricId,
      dimensions: next,
      time_grain: value?.time_grain ?? null,
      filters: Array.isArray(value?.filters) ? value.filters : [],
    })
  }

  function setTimeGrain(grain) {
    onChange({
      metric_id: metricId,
      dimensions: selectedDims,
      time_grain: grain || null,
      filters: Array.isArray(value?.filters) ? value.filters : [],
    })
  }

  return (
    <div className="space-y-3">
      {/* ── Metric select ── */}
      <div>
        <Label>Metric</Label>
        <p className="text-[10px] text-muted/70 -mt-0.5 mb-1">Queries exposed as metrics</p>
        <select
          className={selectCls}
          value={metricId}
          onChange={e => selectMetric(e.target.value)}
        >
          <option value="">{allowClear ? '— none —' : 'Select a metric…'}</option>
          {metrics.map(m => (
            <option key={m.id} value={m.id}>
              {m.name || m.id}
            </option>
          ))}
        </select>
      </div>

      {selected && (
        <>
          {/* ── Dimensions (subset of the metric's allowed dims) ── */}
          <div>
            <Label>Group by dimensions</Label>
            {allowedDims.length === 0 ? (
              <p className="text-[11px] text-muted/70">This metric declares no dimensions.</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {allowedDims.map(name => {
                  const on = selectedDims.includes(name)
                  return (
                    <button
                      key={name}
                      type="button"
                      onClick={() => toggleDim(name)}
                      className={[
                        'px-2 py-1 rounded-md text-[11px] font-medium border transition-colors',
                        on
                          ? 'bg-primary/10 border-primary/30 text-primary'
                          : 'bg-surface border-border text-muted hover:text-fg hover:bg-surface-2',
                      ].join(' ')}
                    >
                      {name}
                    </button>
                  )
                })}
              </div>
            )}
          </div>

          {/* ── Time grain (constrained to allowed grains) ── */}
          <div>
            <Label>Time grain</Label>
            {allowedGrains.length === 0 ? (
              <p className="text-[11px] text-muted/70">This metric has no time dimension.</p>
            ) : (
              <select
                className={selectCls}
                value={timeGrain}
                onChange={e => setTimeGrain(e.target.value)}
              >
                <option value="">— none —</option>
                {allowedGrains.map(g => (
                  <option key={g} value={g}>{g}</option>
                ))}
              </select>
            )}
          </div>
        </>
      )}
    </div>
  )
}
