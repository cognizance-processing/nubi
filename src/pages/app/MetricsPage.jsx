/**
 * MetricsPage — UI for the governed METRICS / semantic layer.
 *
 * Layout mirrors the QueriesPage two-pane shape (a left rail of metrics + a main
 * workspace) but is self-contained: a list of metrics on the left, and on the
 * right an editor form for the selected/new metric plus a "Preview" pane that
 * either RUNS the metric (Arrow via runMetricQuery) or shows the compiled SQL
 * (POST /metrics/{id}/sql).
 *
 * A metric is a GOVERNED definition: a measure (agg + expr), exactly one source
 * (base_table OR base_sql), allowed dimensions, an optional time dimension with
 * allowed grains, and a description. The form authors that definition; the
 * preview exercises it through the same governed paths the embed renderer uses.
 */

import { useState, useEffect, useCallback, useMemo } from 'react'
import { createPortal } from 'react-dom'
import {
  Sigma,
  Plus,
  Search,
  RefreshCw,
  Loader2,
  AlertCircle,
  Trash2,
  Play,
  FileCode2,
  Table2,
} from 'lucide-react'

import {
  listMetrics,
  getMetric,
  createMetric,
  updateMetric,
  deleteMetric,
  compileMetricSql,
} from '../../lib/metrics.js'
import { runMetricQuery } from '../../lib/metricRuntime.js'
import { arrowToRows, deriveColumns } from '../../components/dataTableUtils.js'
import { useProject } from '../../contexts/ProjectContext.jsx'
import { useCanWrite } from '../../contexts/OrgContext.jsx'
import { useUi } from '../../contexts/UiContext.jsx'

// ---------------------------------------------------------------------------
// Vocabularies (mirror app/metrics/models.py)
// ---------------------------------------------------------------------------

const AGG_FUNCS = ['sum', 'count', 'count_distinct', 'min', 'max', 'avg']
const MEASURE_TYPES = ['additive', 'semi_additive', 'non_additive']
const DIM_TYPES = ['text', 'number', 'bool', 'date', 'timestamp']
const ALL_GRAINS = ['hour', 'day', 'week', 'month', 'quarter', 'year']

// Shared control styles (kept self-contained; consistent with other pages).
const inputCls =
  'w-full h-8 text-sm px-2.5 bg-surface border border-border rounded-lg text-fg ' +
  'placeholder:text-muted/50 focus:outline-none focus:ring-2 focus:ring-ring/60 ' +
  'focus:border-ring/40 transition-colors'
const selectCls = inputCls + ' cursor-pointer'

function FieldLabel({ children, className = '' }) {
  return <label className={`block text-[11px] font-medium text-muted mb-1 ${className}`}>{children}</label>
}

// ---------------------------------------------------------------------------
// Blank draft + summary→draft normalisation
// ---------------------------------------------------------------------------

function blankDraft() {
  return {
    isNew: true,
    name: '',
    description: '',
    sourceKind: 'table', // 'table' | 'sql'
    base_table: '',
    base_sql: '',
    datastore_id: '',
    measure: { name: 'value', agg: 'sum', expr: '*', type: 'additive', format: '' },
    dimensions: [],
    time_dimension: { column: '', grains: [...ALL_GRAINS], default_grain: 'day' },
    hasTime: false,
  }
}

/** Normalise a full MetricDefinition (from getMetric) into the editor draft. */
function defToDraft(def) {
  if (!def) return blankDraft()
  const td = def.time_dimension
  return {
    isNew: false,
    id: def.id,
    name: def.name ?? '',
    description: def.description ?? '',
    sourceKind: def.base_sql ? 'sql' : 'table',
    base_table: def.base_table ?? '',
    base_sql: def.base_sql ?? '',
    datastore_id: def.datastore_id ?? '',
    measure: {
      name: def.measure?.name ?? 'value',
      agg: def.measure?.agg ?? 'sum',
      expr: def.measure?.expr ?? '*',
      type: def.measure?.type ?? 'additive',
      format: def.measure?.format ?? '',
    },
    dimensions: (def.dimensions ?? []).map(d => ({
      name: d.name ?? '',
      expr: d.expr ?? '',
      type: d.type ?? 'text',
    })),
    time_dimension: {
      column: td?.column ?? '',
      grains: Array.isArray(td?.grains) ? td.grains : [...ALL_GRAINS],
      default_grain: td?.default_grain ?? 'day',
    },
    hasTime: Boolean(td?.column),
  }
}

/** Serialise the editor draft into a MetricDefinition body for the API. */
function draftToBody(draft) {
  const body = {
    name: draft.name.trim(),
    description: draft.description.trim(),
    measure: {
      name: draft.measure.name.trim() || 'value',
      agg: draft.measure.agg,
      expr: draft.measure.expr.trim() || '*',
      type: draft.measure.type,
      format: draft.measure.format.trim() || null,
    },
    dimensions: draft.dimensions
      .filter(d => d.name.trim())
      .map(d => ({
        name: d.name.trim(),
        expr: d.expr.trim() || null,
        type: d.type,
      })),
  }
  // Exactly one source.
  if (draft.sourceKind === 'sql') body.base_sql = draft.base_sql.trim()
  else body.base_table = draft.base_table.trim()
  if (draft.datastore_id.trim()) body.datastore_id = draft.datastore_id.trim()
  // Optional time dimension.
  if (draft.hasTime && draft.time_dimension.column.trim()) {
    body.time_dimension = {
      column: draft.time_dimension.column.trim(),
      grains: draft.time_dimension.grains.length ? draft.time_dimension.grains : [...ALL_GRAINS],
      default_grain: draft.time_dimension.default_grain,
    }
  }
  return body
}

// ---------------------------------------------------------------------------
// MetricListItem
// ---------------------------------------------------------------------------

function MetricListItem({ metric, isActive, onClick }) {
  const dims = Array.isArray(metric.dimensions) ? metric.dimensions : []
  const grains = Array.isArray(metric.time_grains) ? metric.time_grains : []
  return (
    <button
      onClick={onClick}
      className={[
        'w-full text-left px-3 py-2.5 rounded-lg transition-all border',
        isActive
          ? 'bg-primary/10 border-primary/20 text-fg'
          : 'hover:bg-surface-2 border-transparent text-fg/80 hover:text-fg',
      ].join(' ')}
    >
      <div className="flex items-start gap-2 min-w-0">
        <Sigma size={13} className={['shrink-0 mt-0.5', isActive ? 'text-primary' : 'text-muted'].join(' ')} />
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium truncate leading-tight">{metric.name || metric.id}</p>
          <p className="text-[10px] font-mono text-muted truncate mt-0.5">
            {metric.measure?.agg}({metric.measure?.expr})
          </p>
          {(dims.length > 0 || grains.length > 0) && (
            <div className="flex flex-wrap gap-1 mt-1">
              {dims.slice(0, 3).map(d => (
                <span key={d} className="px-1 py-0 rounded text-[9px] font-mono bg-surface-2 text-muted border border-border/60">
                  {d}
                </span>
              ))}
              {dims.length > 3 && <span className="text-[9px] text-muted">+{dims.length - 3}</span>}
              {grains.length > 0 && (
                <span className="px-1 py-0 rounded text-[9px] font-mono bg-primary/5 text-primary/70 border border-primary/15">
                  {grains.length} grain{grains.length === 1 ? '' : 's'}
                </span>
              )}
            </div>
          )}
        </div>
      </div>
    </button>
  )
}

// ---------------------------------------------------------------------------
// PreviewPane — run the metric (Arrow) or show the compiled SQL
// ---------------------------------------------------------------------------

function PreviewPane({ metricId, draft }) {
  const [mode, setMode] = useState('run') // 'run' | 'sql'
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [rows, setRows] = useState(null)
  const [columns, setColumns] = useState([])
  const [sql, setSql] = useState(null)

  // Derive a sensible default query from the draft: first dim + default grain.
  const previewQuery = useMemo(() => {
    const dims = draft.dimensions.filter(d => d.name.trim()).slice(0, 1).map(d => d.name.trim())
    const time_grain = draft.hasTime && draft.time_dimension.column.trim()
      ? draft.time_dimension.default_grain
      : null
    return { dimensions: dims, time_grain, filters: [], limit: 50 }
  }, [draft])

  const runPreview = useCallback(async () => {
    if (!metricId) return
    setBusy(true); setError(null); setRows(null); setSql(null)
    try {
      if (mode === 'sql') {
        const out = await compileMetricSql(metricId, previewQuery)
        setSql(out)
      } else {
        const { table } = await runMetricQuery({ metric_id: metricId, ...previewQuery })
        setColumns(deriveColumns(table))
        setRows(arrowToRows(table))
      }
    } catch (err) {
      setError(err?.message ?? 'Preview failed')
    } finally {
      setBusy(false)
    }
  }, [metricId, mode, previewQuery])

  if (!metricId) {
    return (
      <p className="text-[11px] text-muted/70">
        Save the metric to preview its results or compiled SQL.
      </p>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="flex items-center rounded-lg border border-border overflow-hidden">
          <button
            onClick={() => setMode('run')}
            className={[
              'h-8 px-2.5 flex items-center gap-1.5 text-[11px] font-medium transition-colors',
              mode === 'run' ? 'bg-primary/10 text-primary' : 'bg-surface text-muted hover:text-fg',
            ].join(' ')}
          >
            <Table2 size={12} /> Run
          </button>
          <button
            onClick={() => setMode('sql')}
            className={[
              'h-8 px-2.5 flex items-center gap-1.5 text-[11px] font-medium border-l border-border transition-colors',
              mode === 'sql' ? 'bg-primary/10 text-primary' : 'bg-surface text-muted hover:text-fg',
            ].join(' ')}
          >
            <FileCode2 size={12} /> SQL
          </button>
        </div>
        <button
          onClick={runPreview}
          disabled={busy}
          className="h-8 px-3 flex items-center gap-1.5 text-xs font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
          {mode === 'sql' ? 'Compile' : 'Run'}
        </button>
      </div>

      {error && (
        <div className="flex items-start gap-2 px-3 py-2 bg-rose-500/5 border border-rose-500/20 rounded-lg text-xs text-rose-600 dark:text-rose-400">
          <AlertCircle size={13} className="shrink-0 mt-0.5" />
          <span className="break-words">{error}</span>
        </div>
      )}

      {/* Compiled SQL */}
      {mode === 'sql' && sql && (
        <div className="space-y-2">
          <pre className="text-[11px] font-mono bg-surface-2 border border-border rounded-lg p-3 overflow-x-auto whitespace-pre-wrap text-fg/90">
            {sql.sql}
          </pre>
          {sql.params && Object.keys(sql.params).length > 0 && (
            <pre className="text-[10px] font-mono bg-surface-2/60 border border-border rounded-lg p-2 overflow-x-auto text-muted">
              params: {JSON.stringify(sql.params, null, 2)}
            </pre>
          )}
        </div>
      )}

      {/* Result rows */}
      {mode === 'run' && rows && (
        rows.length === 0 ? (
          <p className="text-[11px] text-muted text-center py-4">No rows.</p>
        ) : (
          <div className="overflow-auto border border-border rounded-lg max-h-72">
            <table className="w-full text-[11px]">
              <thead className="bg-surface-2 sticky top-0">
                <tr>
                  {columns.map(c => (
                    <th key={c.key} className="text-left font-medium text-muted px-2 py-1.5 border-b border-border whitespace-nowrap">
                      {c.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 100).map((row, i) => (
                  <tr key={i} className="border-b border-border/50 last:border-0">
                    {columns.map(c => (
                      <td key={c.key} className="px-2 py-1.5 text-fg/90 whitespace-nowrap font-mono">
                        {row[c.key] === null || row[c.key] === undefined ? '—' : String(row[c.key])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// MetricEditor — the create/edit form for one metric
// ---------------------------------------------------------------------------

function MetricEditor({ draft, setDraft, onSave, onDelete, saving, canWrite, saveError }) {
  const m = draft.measure

  const update = patch => setDraft(d => ({ ...d, ...patch }))
  const updateMeasure = patch => setDraft(d => ({ ...d, measure: { ...d.measure, ...patch } }))
  const updateTime = patch => setDraft(d => ({ ...d, time_dimension: { ...d.time_dimension, ...patch } }))

  const addDimension = () =>
    setDraft(d => ({ ...d, dimensions: [...d.dimensions, { name: '', expr: '', type: 'text' }] }))
  const updateDimension = (i, patch) =>
    setDraft(d => ({ ...d, dimensions: d.dimensions.map((dim, j) => (j === i ? { ...dim, ...patch } : dim)) }))
  const removeDimension = i =>
    setDraft(d => ({ ...d, dimensions: d.dimensions.filter((_, j) => j !== i) }))

  const toggleGrain = g =>
    setDraft(d => {
      const grains = d.time_dimension.grains.includes(g)
        ? d.time_dimension.grains.filter(x => x !== g)
        : [...d.time_dimension.grains, g]
      return { ...d, time_dimension: { ...d.time_dimension, grains } }
    })

  return (
    <div className="max-w-2xl mx-auto p-4 sm:p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold font-display text-fg">
          {draft.isNew ? 'New metric' : draft.name || draft.id}
        </h2>
        {!draft.isNew && canWrite && (
          <button
            onClick={onDelete}
            className="flex items-center gap-1 text-xs px-2 h-7 rounded-lg border border-transparent text-muted hover:text-rose-500 hover:border-rose-300 hover:bg-rose-500/5 transition-colors"
          >
            <Trash2 size={13} /> Delete
          </button>
        )}
      </div>

      {/* Identity */}
      <div className="space-y-3">
        <div>
          <FieldLabel>Name</FieldLabel>
          <input
            className={inputCls}
            value={draft.name}
            onChange={e => update({ name: e.target.value })}
            placeholder="Revenue"
            disabled={!canWrite}
          />
        </div>
        <div>
          <FieldLabel>Description</FieldLabel>
          <input
            className={inputCls}
            value={draft.description}
            onChange={e => update({ description: e.target.value })}
            placeholder="Total revenue from completed orders"
            disabled={!canWrite}
          />
        </div>
      </div>

      {/* Measure */}
      <fieldset className="space-y-3 border border-border rounded-xl p-4">
        <legend className="px-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted">Measure</legend>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <FieldLabel>Output name</FieldLabel>
            <input className={inputCls} value={m.name} onChange={e => updateMeasure({ name: e.target.value })} placeholder="revenue" disabled={!canWrite} />
          </div>
          <div>
            <FieldLabel>Aggregation</FieldLabel>
            <select className={selectCls} value={m.agg} onChange={e => updateMeasure({ agg: e.target.value })} disabled={!canWrite}>
              {AGG_FUNCS.map(a => <option key={a} value={a}>{a}</option>)}
            </select>
          </div>
        </div>
        <div>
          <FieldLabel>Expression {m.agg === 'count' ? '(use * for count)' : '(column or SQL expr)'}</FieldLabel>
          <input className={inputCls} value={m.expr} onChange={e => updateMeasure({ expr: e.target.value })} placeholder={m.agg === 'count' ? '*' : 'amount'} disabled={!canWrite} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <FieldLabel>Additivity</FieldLabel>
            <select className={selectCls} value={m.type} onChange={e => updateMeasure({ type: e.target.value })} disabled={!canWrite}>
              {MEASURE_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <FieldLabel>Format (optional)</FieldLabel>
            <input className={inputCls} value={m.format} onChange={e => updateMeasure({ format: e.target.value })} placeholder="currency" disabled={!canWrite} />
          </div>
        </div>
      </fieldset>

      {/* Source */}
      <fieldset className="space-y-3 border border-border rounded-xl p-4">
        <legend className="px-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted">Source</legend>
        <div className="flex items-center rounded-lg border border-border overflow-hidden w-fit">
          {['table', 'sql'].map(k => (
            <button
              key={k}
              type="button"
              onClick={() => canWrite && update({ sourceKind: k })}
              className={[
                'h-8 px-3 text-[11px] font-medium transition-colors',
                k === 'sql' ? 'border-l border-border' : '',
                draft.sourceKind === k ? 'bg-primary/10 text-primary' : 'bg-surface text-muted hover:text-fg',
              ].join(' ')}
            >
              {k === 'table' ? 'Base table' : 'Base SQL'}
            </button>
          ))}
        </div>
        {draft.sourceKind === 'table' ? (
          <div>
            <FieldLabel>Base table</FieldLabel>
            <input className={inputCls} value={draft.base_table} onChange={e => update({ base_table: e.target.value })} placeholder="orders" disabled={!canWrite} />
          </div>
        ) : (
          <div>
            <FieldLabel>Base SQL (trusted SELECT used as a subquery)</FieldLabel>
            <textarea
              className={inputCls + ' h-24 py-2 font-mono resize-y'}
              value={draft.base_sql}
              onChange={e => update({ base_sql: e.target.value })}
              placeholder="SELECT * FROM orders WHERE status = 'paid'"
              disabled={!canWrite}
            />
          </div>
        )}
        <div>
          <FieldLabel>Datastore id (optional — blank uses the demo connector)</FieldLabel>
          <input className={inputCls} value={draft.datastore_id} onChange={e => update({ datastore_id: e.target.value })} placeholder="" disabled={!canWrite} />
        </div>
      </fieldset>

      {/* Dimensions */}
      <fieldset className="space-y-3 border border-border rounded-xl p-4">
        <legend className="px-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted">Dimensions</legend>
        {draft.dimensions.length === 0 && (
          <p className="text-[11px] text-muted/70">No dimensions yet. Add allowed grouping columns.</p>
        )}
        {draft.dimensions.map((dim, i) => (
          <div key={i} className="flex items-end gap-2">
            <div className="flex-1">
              <FieldLabel>Name</FieldLabel>
              <input className={inputCls} value={dim.name} onChange={e => updateDimension(i, { name: e.target.value })} placeholder="country" disabled={!canWrite} />
            </div>
            <div className="flex-1">
              <FieldLabel>Expr (optional)</FieldLabel>
              <input className={inputCls} value={dim.expr} onChange={e => updateDimension(i, { expr: e.target.value })} placeholder="upper(country)" disabled={!canWrite} />
            </div>
            <div className="w-28">
              <FieldLabel>Type</FieldLabel>
              <select className={selectCls} value={dim.type} onChange={e => updateDimension(i, { type: e.target.value })} disabled={!canWrite}>
                {DIM_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
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
      <fieldset className="space-y-3 border border-border rounded-xl p-4">
        <legend className="px-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted">Time dimension</legend>
        <label className="flex items-center gap-2 text-xs text-fg/80">
          <input type="checkbox" checked={draft.hasTime} onChange={e => update({ hasTime: e.target.checked })} disabled={!canWrite} className="accent-primary" />
          This metric has a time dimension
        </label>
        {draft.hasTime && (
          <>
            <div>
              <FieldLabel>Time column</FieldLabel>
              <input className={inputCls} value={draft.time_dimension.column} onChange={e => updateTime({ column: e.target.value })} placeholder="created_at" disabled={!canWrite} />
            </div>
            <div>
              <FieldLabel>Allowed grains</FieldLabel>
              <div className="flex flex-wrap gap-1.5">
                {ALL_GRAINS.map(g => {
                  const on = draft.time_dimension.grains.includes(g)
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
              <select className={selectCls} value={draft.time_dimension.default_grain} onChange={e => updateTime({ default_grain: e.target.value })} disabled={!canWrite}>
                {(draft.time_dimension.grains.length ? draft.time_dimension.grains : ALL_GRAINS).map(g => (
                  <option key={g} value={g}>{g}</option>
                ))}
              </select>
            </div>
          </>
        )}
      </fieldset>

      {/* Save */}
      {saveError && (
        <div className="flex items-start gap-2 px-3 py-2 bg-rose-500/5 border border-rose-500/20 rounded-lg text-xs text-rose-600 dark:text-rose-400">
          <AlertCircle size={13} className="shrink-0 mt-0.5" />
          <span className="break-words">{saveError}</span>
        </div>
      )}
      {canWrite && (
        <button
          onClick={onSave}
          disabled={saving}
          className="h-9 px-4 flex items-center gap-2 text-sm font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          {saving ? <Loader2 size={14} className="animate-spin" /> : null}
          {draft.isNew ? 'Create metric' : 'Save changes'}
        </button>
      )}

      {/* Preview */}
      <fieldset className="space-y-3 border border-border rounded-xl p-4">
        <legend className="px-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted">Preview</legend>
        <PreviewPane metricId={draft.isNew ? null : draft.id} draft={draft} />
      </fieldset>
    </div>
  )
}

// ---------------------------------------------------------------------------
// MetricsPage
// ---------------------------------------------------------------------------

export default function MetricsPage() {
  const { activeProject } = useProject()
  const projectId = activeProject?.id
  const canWrite = useCanWrite()
  const { topbarSlot } = useUi()

  const [metrics, setMetrics] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')

  // Active draft (the metric being edited / created). null = nothing selected.
  const [draft, setDraft] = useState(null)
  const [activeId, setActiveId] = useState(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const rows = await listMetrics()
      setMetrics(rows)
    } catch (err) {
      setError(err?.message ?? 'Failed to load metrics')
      setMetrics([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [projectId]) // eslint-disable-line react-hooks/exhaustive-deps

  const selectMetric = useCallback(async (summary) => {
    setActiveId(summary.id)
    setSaveError(null)
    // Fetch the full definition (the list carries only a compact summary).
    const def = await getMetric(summary.id)
    setDraft(defToDraft(def ?? summary))
  }, [])

  const newMetric = useCallback(() => {
    setActiveId(null)
    setSaveError(null)
    setDraft(blankDraft())
  }, [])

  const handleSave = useCallback(async () => {
    if (!draft) return
    setSaving(true); setSaveError(null)
    try {
      const body = draftToBody(draft)
      const saved = draft.isNew
        ? await createMetric(body)
        : await updateMetric(draft.id, body)
      const nextDraft = defToDraft(saved)
      setDraft(nextDraft)
      setActiveId(saved.id)
      await load()
    } catch (err) {
      setSaveError(err?.message ?? 'Save failed')
    } finally {
      setSaving(false)
    }
  }, [draft, load])

  const handleDelete = useCallback(async () => {
    if (!draft || draft.isNew) return
    if (!window.confirm(`Delete metric "${draft.name || draft.id}"?`)) return
    await deleteMetric(draft.id)
    setDraft(null)
    setActiveId(null)
    await load()
  }, [draft, load])

  const filtered = useMemo(() => {
    if (!search) return metrics
    const q = search.toLowerCase()
    return metrics.filter(m =>
      (m.name ?? '').toLowerCase().includes(q) || (m.id ?? '').toLowerCase().includes(q),
    )
  }, [metrics, search])

  return (
    <div className="flex h-[calc(100vh-var(--shell-header-h,56px))] overflow-hidden bg-bg">
      {/* Topbar title */}
      {topbarSlot && createPortal(
        <div className="flex items-center gap-1.5 w-full min-w-0">
          <Sigma size={15} className="text-primary shrink-0" />
          <span className="text-sm font-semibold font-display text-fg truncate">Metrics</span>
        </div>,
        topbarSlot,
      )}

      {/* ── Left rail ── */}
      <aside className="w-64 shrink-0 border-r border-border bg-surface-2/40 flex flex-col overflow-hidden">
        {canWrite ? (
          <div className="shrink-0 px-2 py-2">
            <button
              onClick={newMetric}
              className="w-full h-8 flex items-center justify-center gap-1.5 text-xs font-medium rounded-lg border border-dashed border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors"
            >
              <Plus size={13} /> New metric
            </button>
          </div>
        ) : (
          <div className="shrink-0 px-2 py-2">
            <p className="text-[10px] text-muted/70 text-center py-1.5 rounded-lg border border-dashed border-border">Read-only access</p>
          </div>
        )}

        <div className="shrink-0 px-2 pb-2 flex items-center gap-1.5">
          <div className="relative flex-1">
            <Search size={11} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search metrics…"
              className="w-full h-7 pl-7 pr-2.5 text-[11px] bg-surface border border-border rounded-lg text-fg placeholder:text-muted/50 focus:outline-none focus:ring-1 focus:ring-ring"
            />
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="h-7 w-7 shrink-0 flex items-center justify-center rounded-lg border border-border bg-surface text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40 transition-colors"
            title="Refresh"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-0.5">
          {loading && metrics.length === 0 && (
            <div className="flex items-center gap-2 text-[11px] text-muted py-4 justify-center">
              <Loader2 size={12} className="animate-spin" /> Loading…
            </div>
          )}
          {!loading && filtered.length === 0 && (
            <div className="text-[11px] text-muted text-center py-6">
              <Sigma size={20} className="mx-auto mb-2 opacity-30" />
              {search ? `No metrics match "${search}"` : 'No metrics yet'}
            </div>
          )}
          {filtered.map(m => (
            <MetricListItem
              key={m.id}
              metric={m}
              isActive={activeId === m.id}
              onClick={() => selectMetric(m)}
            />
          ))}
        </div>
      </aside>

      {/* ── Main ── */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {error && (
          <div className="shrink-0 flex items-center gap-2 px-4 py-2 bg-rose-500/5 border-b border-rose-500/20 text-xs text-rose-600 dark:text-rose-400">
            <AlertCircle size={12} /> {error}
          </div>
        )}
        <div className="flex-1 min-h-0 overflow-y-auto">
          {draft ? (
            <MetricEditor
              draft={draft}
              setDraft={setDraft}
              onSave={handleSave}
              onDelete={handleDelete}
              saving={saving}
              canWrite={canWrite}
              saveError={saveError}
            />
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center gap-4 text-center px-6 py-20">
              <div className="flex items-center justify-center w-14 h-14 rounded-2xl" style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}>
                <Sigma size={24} className="text-white" />
              </div>
              <div>
                <h2 className="text-lg font-semibold font-display text-fg mb-1">Semantic layer</h2>
                <p className="text-sm text-muted max-w-xs">
                  Define governed metrics — a measure, allowed dimensions and grains — that
                  dashboards and embeds can query safely. Select a metric on the left or create one.
                </p>
              </div>
              {canWrite && (
                <button onClick={newMetric} className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 transition-opacity">
                  <Plus size={15} /> New metric
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
