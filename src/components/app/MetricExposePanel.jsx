/**
 * MetricExposePanel — the query editor's "Expose as metric" section.
 *
 * An optional, collapsible panel that, when enabled, declares a `config.metric`
 * block on the query (the unification contract — see
 * docs/query-metric-unification.md §1 & §5). When disabled, NO block is written
 * and the query stays a plain query.
 *
 * Fully controlled: the parent (QueryWorkspace) owns the `draft` and receives
 * the next draft via `onChange`. All pure logic (slug derivation, build/parse,
 * validation) lives in metricBlock.logic.js so this file is render-only.
 */

import { useMemo } from 'react'
import {
  Sigma,
  ChevronDown,
  ChevronRight,
  Plus,
  Trash2,
  AlertCircle,
  Info,
} from 'lucide-react'

import {
  AGG_FUNCS,
  MEASURE_TYPES,
  MEASURE_FORMATS,
  DIM_TYPES,
  ALL_GRAINS,
  deriveSlug,
  validateMetricDraft,
} from '../../pages/app/metricBlock.logic.js'

// Shared control styles — mirror the legacy MetricsPage form + Tailwind tokens.
const inputCls =
  'w-full h-8 text-sm px-2.5 bg-surface border border-border rounded-lg text-fg ' +
  'placeholder:text-muted/50 focus:outline-none focus:ring-2 focus:ring-ring/60 ' +
  'focus:border-ring/40 transition-colors'
const selectCls = inputCls + ' cursor-pointer'

function FieldLabel({ children, className = '' }) {
  return <label className={`block text-[11px] font-medium text-muted mb-1 ${className}`}>{children}</label>
}

export default function MetricExposePanel({ draft, onChange, collapsed, onToggleCollapsed, canWrite = true }) {
  const errors = useMemo(() => validateMetricDraft(draft), [draft])
  const m = draft.measure

  // ── Mutators — always emit a fresh draft object ──────────────────────────
  const patch = (p) => onChange({ ...draft, ...p })
  const patchMeasure = (p) => onChange({ ...draft, measure: { ...draft.measure, ...p } })
  const patchTime = (p) => onChange({ ...draft, time: { ...draft.time, ...p } })

  const setEnabled = (enabled) => {
    // Enabling with no slug yet → seed one from the measure name (or keep the
    // query-name suggestion already on the draft).
    if (enabled && !draft.slug) {
      onChange({ ...draft, enabled, slug: deriveSlug(draft.measure?.name) || draft.slug })
    } else {
      onChange({ ...draft, enabled })
    }
  }

  const setName = (name) => {
    // Auto-derive the slug from the measure name until the user edits it by hand.
    if (!draft.slugEdited) {
      onChange({ ...draft, measure: { ...draft.measure, name }, slug: deriveSlug(name) })
    } else {
      onChange({ ...draft, measure: { ...draft.measure, name } })
    }
  }

  const setSlug = (slug) => onChange({ ...draft, slug, slugEdited: true })

  const addDimension = () =>
    onChange({ ...draft, dimensions: [...draft.dimensions, { name: '', expr: '', type: 'text' }] })
  const updateDimension = (i, p) =>
    onChange({ ...draft, dimensions: draft.dimensions.map((d, j) => (j === i ? { ...d, ...p } : d)) })
  const removeDimension = (i) =>
    onChange({ ...draft, dimensions: draft.dimensions.filter((_, j) => j !== i) })

  const toggleGrain = (g) => {
    const grains = draft.time.grains.includes(g)
      ? draft.time.grains.filter((x) => x !== g)
      : [...draft.time.grains, g]
    patchTime({ grains })
  }

  // rls_keys as a comma-separated text field (round-trips to an array).
  const rlsText = (draft.rls_keys ?? []).join(', ')
  const setRlsText = (text) =>
    patch({ rls_keys: text.split(',').map((s) => s.trim()).filter(Boolean) })

  return (
    <div className="rounded-xl border border-border bg-surface overflow-hidden shadow-sm">
      {/* Header — toggle + enable switch */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-surface-2/60 min-h-[44px]">
        <button
          onClick={onToggleCollapsed}
          className="h-7 w-7 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface transition-colors shrink-0"
          title={collapsed ? 'Expand' : 'Collapse'}
        >
          {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
        </button>
        <Sigma size={13} className="text-primary shrink-0" />
        <span className="text-xs font-semibold text-fg">Expose as metric</span>
        {draft.enabled && (
          <span className="shrink-0 inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded bg-primary/10 text-primary border border-primary/20 font-mono">
            {draft.slug || '…'}
          </span>
        )}
        <div className="flex-1" />
        <label className="flex items-center gap-1.5 text-[11px] text-muted cursor-pointer select-none shrink-0">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            disabled={!canWrite}
            className="accent-primary w-3.5 h-3.5"
          />
          {draft.enabled ? 'Enabled' : 'Off'}
        </label>
      </div>

      {!collapsed && (
        <div className="p-4 space-y-4">
          {/* Helper — the base-grain rule */}
          <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-primary/5 border border-primary/15 text-[11px] text-muted">
            <Info size={13} className="shrink-0 mt-0.5 text-primary/70" />
            <span>
              Write the SQL at <span className="font-medium text-fg">row / base grain</span> — select the
              dimension and raw measure columns, with <span className="font-medium text-fg">no GROUP BY</span>.
              The metric layer owns the aggregation, dimensions and time grains declared here.
            </span>
          </div>

          {!draft.enabled ? (
            <p className="text-[11px] text-muted/70">
              Enable to make this query consumable as a governed metric by dashboards, watches and AI —
              with declared dimensions, an aggregated measure and allowed time grains. Leave it off for a
              plain query.
            </p>
          ) : (
            <>
              {/* Measure */}
              <fieldset className="space-y-3 border border-border rounded-xl p-3">
                <legend className="px-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted">Measure</legend>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <FieldLabel>Name *</FieldLabel>
                    <input
                      className={inputCls}
                      value={m.name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder="revenue"
                      disabled={!canWrite}
                    />
                    {errors.measureName && (
                      <p className="mt-1 text-[10px] text-rose-500 flex items-center gap-1">
                        <AlertCircle size={9} /> {errors.measureName}
                      </p>
                    )}
                  </div>
                  <div>
                    <FieldLabel>Aggregation</FieldLabel>
                    <select className={selectCls} value={m.agg} onChange={(e) => patchMeasure({ agg: e.target.value })} disabled={!canWrite}>
                      {AGG_FUNCS.map((a) => <option key={a} value={a}>{a}</option>)}
                    </select>
                  </div>
                </div>
                <div>
                  <FieldLabel>{m.agg === 'count' ? 'Expression (use * for count)' : 'Expression (column or SQL expr)'}</FieldLabel>
                  <input
                    className={inputCls}
                    value={m.expr}
                    onChange={(e) => patchMeasure({ expr: e.target.value })}
                    placeholder={m.agg === 'count' ? '*' : 'amount'}
                    disabled={!canWrite}
                  />
                  {errors.expr && (
                    <p className="mt-1 text-[10px] text-rose-500 flex items-center gap-1">
                      <AlertCircle size={9} /> {errors.expr}
                    </p>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <FieldLabel>Additivity</FieldLabel>
                    <select className={selectCls} value={m.type} onChange={(e) => patchMeasure({ type: e.target.value })} disabled={!canWrite}>
                      {MEASURE_TYPES.map((t) => <option key={t} value={t}>{t.replace('_', '-')}</option>)}
                    </select>
                  </div>
                  <div>
                    <FieldLabel>Format</FieldLabel>
                    <select className={selectCls} value={m.format} onChange={(e) => patchMeasure({ format: e.target.value })} disabled={!canWrite}>
                      {MEASURE_FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}
                    </select>
                  </div>
                </div>
              </fieldset>

              {/* Identity — slug + description */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <FieldLabel>Slug (stable metric id)</FieldLabel>
                  <input
                    className={inputCls + ' font-mono'}
                    value={draft.slug}
                    onChange={(e) => setSlug(e.target.value)}
                    placeholder="revenue"
                    disabled={!canWrite}
                  />
                  {errors.slug && (
                    <p className="mt-1 text-[10px] text-rose-500 flex items-center gap-1">
                      <AlertCircle size={9} /> {errors.slug}
                    </p>
                  )}
                </div>
                <div>
                  <FieldLabel>Description</FieldLabel>
                  <input
                    className={inputCls}
                    value={draft.description}
                    onChange={(e) => patch({ description: e.target.value })}
                    placeholder="Total revenue from paid orders"
                    disabled={!canWrite}
                  />
                </div>
              </div>

              {/* Dimensions */}
              <fieldset className="space-y-2 border border-border rounded-xl p-3">
                <legend className="px-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted">Dimensions</legend>
                {draft.dimensions.length === 0 && (
                  <p className="text-[11px] text-muted/70">No dimensions yet — add the columns this metric can group by.</p>
                )}
                {draft.dimensions.map((dim, i) => (
                  <div key={i} className="flex items-end gap-2">
                    <div className="flex-1">
                      <FieldLabel>Name</FieldLabel>
                      <input className={inputCls} value={dim.name} onChange={(e) => updateDimension(i, { name: e.target.value })} placeholder="region" disabled={!canWrite} />
                    </div>
                    <div className="flex-1">
                      <FieldLabel>Expr (optional)</FieldLabel>
                      <input className={inputCls} value={dim.expr} onChange={(e) => updateDimension(i, { expr: e.target.value })} placeholder="upper(region)" disabled={!canWrite} />
                    </div>
                    <div className="w-28">
                      <FieldLabel>Type</FieldLabel>
                      <select className={selectCls} value={dim.type} onChange={(e) => updateDimension(i, { type: e.target.value })} disabled={!canWrite}>
                        {DIM_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </div>
                    {canWrite && (
                      <button onClick={() => removeDimension(i)} className="h-8 w-8 shrink-0 flex items-center justify-center rounded-lg text-muted hover:text-rose-500 hover:bg-rose-500/5 transition-colors" title="Remove dimension">
                        <Trash2 size={13} />
                      </button>
                    )}
                  </div>
                ))}
                {canWrite && (
                  <button
                    onClick={addDimension}
                    className="w-full h-8 flex items-center justify-center gap-1.5 text-xs font-medium rounded-lg border border-dashed border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors"
                  >
                    <Plus size={13} /> Add dimension
                  </button>
                )}
              </fieldset>

              {/* Time dimension */}
              <fieldset className="space-y-3 border border-border rounded-xl p-3">
                <legend className="px-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted">Time</legend>
                <label className="flex items-center gap-2 text-xs text-fg/80">
                  <input type="checkbox" checked={draft.hasTime} onChange={(e) => patch({ hasTime: e.target.checked })} disabled={!canWrite} className="accent-primary" />
                  This metric has a time dimension
                </label>
                {draft.hasTime && (
                  <>
                    <div>
                      <FieldLabel>Time column</FieldLabel>
                      <input className={inputCls} value={draft.time.column} onChange={(e) => patchTime({ column: e.target.value })} placeholder="order_date" disabled={!canWrite} />
                    </div>
                    <div>
                      <FieldLabel>Allowed grains</FieldLabel>
                      <div className="flex flex-wrap gap-1.5">
                        {ALL_GRAINS.map((g) => {
                          const on = draft.time.grains.includes(g)
                          return (
                            <button
                              key={g}
                              type="button"
                              onClick={() => canWrite && toggleGrain(g)}
                              className={[
                                'px-2 py-1 rounded-md text-[11px] font-medium border transition-colors',
                                on ? 'bg-primary/10 border-primary/30 text-primary' : 'bg-surface border-border text-muted hover:text-fg',
                              ].join(' ')}
                            >
                              {g}
                            </button>
                          )
                        })}
                      </div>
                    </div>
                    <div>
                      <FieldLabel>Default grain</FieldLabel>
                      <select className={selectCls} value={draft.time.default_grain} onChange={(e) => patchTime({ default_grain: e.target.value })} disabled={!canWrite}>
                        {(draft.time.grains.length ? draft.time.grains : ALL_GRAINS).map((g) => (
                          <option key={g} value={g}>{g}</option>
                        ))}
                      </select>
                    </div>
                  </>
                )}
              </fieldset>

              {/* Governance — RLS keys */}
              <div>
                <FieldLabel>RLS keys (comma-separated tenant/row-security columns)</FieldLabel>
                <input
                  className={inputCls + ' font-mono'}
                  value={rlsText}
                  onChange={(e) => setRlsText(e.target.value)}
                  placeholder="tenant_id, org_id"
                  disabled={!canWrite}
                />
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
