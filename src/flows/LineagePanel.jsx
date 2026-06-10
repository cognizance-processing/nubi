/**
 * LineagePanel.jsx — column-level lineage viewer for a notebook cell or flow.
 *
 * Shows:
 *   - Which upstream cells / tables feed into this cell (input edges)
 *   - Which output columns this cell produces
 *   - Which downstream cells depend on this cell's outputs (column_flow)
 *
 * Data sources:
 *   - Per-cell ad-hoc lineage: POST /lineage/cell (called after each run)
 *   - Full flow lineage: GET /lineage/flow/{id} (called on demand when flow is saved)
 *
 * The panel can operate in two modes:
 *   "cell"  — shows edges for one cell (used inline in SqlCell / cell toolbar area)
 *   "flow"  — shows the full graph for the saved flow
 *
 * Props:
 *   mode          {'cell'|'flow'}
 *   cellKey       {string}               — required in cell mode
 *   cellSql       {string}               — SQL of the cell (cell mode)
 *   upstreamSqls  {Record<string,string>} — { [cellKey]: sql } for cell mode
 *   flowId        {string|null}          — required in flow mode
 *   spec          {object|null}          — current spec (for node labels)
 *   onClose       {Function}
 */

import { useState, useEffect, useCallback } from 'react'
import { X, Loader2, AlertCircle, ArrowRight, ArrowLeft, Columns, GitBranch } from 'lucide-react'
import { fetchCellLineage, fetchFlowLineage } from '../lib/notebooks.js'

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function CellBadge({ cellKey, highlight = false }) {
  return (
    <span className={[
      'inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold',
      highlight
        ? 'bg-primary/15 text-primary border border-primary/30'
        : 'bg-surface-2 text-muted border border-border/60',
    ].join(' ')}>
      {cellKey}
    </span>
  )
}

function ColChip({ name }) {
  return (
    <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-mono bg-blue-500/8 text-blue-600 dark:text-blue-400 border border-blue-500/20">
      {name}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Cell mode — edges for one cell
// ---------------------------------------------------------------------------

function CellLineageContent({ cellKey, cellSql, upstreamSqls }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [data, setData] = useState(null)

  const load = useCallback(async () => {
    if (!cellSql?.trim()) {
      setData(null)
      return
    }
    setLoading(true)
    setError(null)
    const res = await fetchCellLineage({
      sql: cellSql,
      cell_key: cellKey,
      upstream_cells: upstreamSqls ?? {},
    })
    setLoading(false)
    if (!res || res.edges == null) {
      setError('No lineage data returned.')
    } else {
      setData(res)
    }
  }, [cellKey, cellSql, upstreamSqls])

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cellKey, cellSql])

  if (loading) {
    return (
      <div className="flex items-center gap-2 px-4 py-6 text-xs text-muted">
        <Loader2 size={13} className="animate-spin" />
        Analysing column lineage…
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center gap-2 px-4 py-4 text-xs text-red-500">
        <AlertCircle size={12} />
        {error}
      </div>
    )
  }

  if (!data || data.edges.length === 0) {
    return (
      <div className="px-4 py-4 text-xs text-muted text-center">
        No column-level lineage found for this cell.{' '}
        {!cellSql?.trim() && 'Add SQL and run the cell first.'}
      </div>
    )
  }

  // Group edges by output_col
  const byOutput = {}
  for (const edge of data.edges) {
    if (!byOutput[edge.output_col]) byOutput[edge.output_col] = []
    byOutput[edge.output_col].push(edge)
  }

  return (
    <div className="px-3 py-3 space-y-2">
      {Object.entries(byOutput).map(([outputCol, edges]) => (
        <div key={outputCol} className="rounded-lg border border-border/60 bg-surface-2/30 overflow-hidden">
          <div className="flex items-center gap-1.5 px-2.5 py-1.5 bg-surface-2/60 border-b border-border/40">
            <Columns size={10} className="text-blue-500 shrink-0" />
            <span className="text-[11px] font-semibold text-fg">{outputCol}</span>
          </div>
          <div className="px-2.5 py-1.5 space-y-1">
            {edges.map((edge, i) => (
              <div key={i} className="flex items-center gap-1.5 text-[11px]">
                <ArrowLeft size={10} className="text-muted shrink-0" />
                {edge.from_table ? (
                  <span className="font-mono text-muted/70">{edge.from_table}</span>
                ) : edge.source_name ? (
                  <CellBadge cellKey={edge.source_name} />
                ) : (
                  <span className="italic text-muted/50">unknown</span>
                )}
                {edge.from_col && edge.from_col !== outputCol && (
                  <>
                    <span className="text-muted/40">.</span>
                    <ColChip name={edge.from_col} />
                  </>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Flow mode — full graph for saved flow
// ---------------------------------------------------------------------------

function FlowLineageContent({ flowId, spec }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [data, setData] = useState(null)

  const load = useCallback(async () => {
    if (!flowId) return
    setLoading(true)
    setError(null)
    const res = await fetchFlowLineage(flowId)
    setLoading(false)
    if (res.lineage == null) {
      setError((res.issues ?? []).join('; ') || 'No lineage data returned.')
    } else {
      setData(res)
    }
  }, [flowId])

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flowId])

  if (!flowId) {
    return (
      <div className="px-4 py-4 text-xs text-muted text-center">
        Save the notebook first to see full flow lineage.
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 px-4 py-6 text-xs text-muted">
        <Loader2 size={13} className="animate-spin" />
        Loading flow lineage…
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center gap-2 px-4 py-4 text-xs text-red-500">
        <AlertCircle size={12} />
        {error}
      </div>
    )
  }

  if (!data?.lineage) {
    return (
      <div className="px-4 py-4 text-xs text-muted text-center">
        No lineage data available.
      </div>
    )
  }

  const { nodes, edges } = data.lineage
  const taskLabels = {}
  for (const task of spec?.tasks ?? []) {
    taskLabels[task.key] = task.key
  }

  // Filter to only edges that cross cells (from_cell is set)
  const crossCellEdges = edges.filter(e => e.from_cell)

  // Group edges by from_cell -> to_cell pair
  const pairMap = {}
  for (const edge of crossCellEdges) {
    const pairKey = `${edge.from_cell}::${edge.to_cell}`
    if (!pairMap[pairKey]) pairMap[pairKey] = { from_cell: edge.from_cell, to_cell: edge.to_cell, cols: [] }
    const entry = `${edge.from_col} → ${edge.to_col}`
    if (!pairMap[pairKey].cols.includes(entry)) pairMap[pairKey].cols.push(entry)
  }

  const nodeKeys = Object.keys(nodes)

  if (nodeKeys.length === 0) {
    return (
      <div className="px-4 py-4 text-xs text-muted text-center">
        No SQL cells to trace.
      </div>
    )
  }

  return (
    <div className="px-3 py-3 space-y-3">
      {/* Cell-level dependency list */}
      {Object.values(pairMap).length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-muted px-0.5">
            Cross-cell column dependencies
          </p>
          {Object.values(pairMap).map((pair, i) => (
            <div
              key={i}
              className="flex items-start gap-2 rounded-lg border border-border/60 bg-surface-2/30 px-2.5 py-2"
            >
              <CellBadge cellKey={pair.from_cell} />
              <ArrowRight size={11} className="text-muted shrink-0 mt-0.5" />
              <CellBadge cellKey={pair.to_cell} />
              <div className="flex-1 min-w-0">
                <div className="flex flex-wrap gap-1 mt-0.5">
                  {pair.cols.map((c, j) => (
                    <span
                      key={j}
                      className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-blue-500/8 text-blue-600 dark:text-blue-400 border border-blue-500/15"
                    >
                      {c}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Per-cell output columns */}
      <div className="space-y-1.5">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-muted px-0.5">
          Cell outputs
        </p>
        {nodeKeys.map(key => {
          const node = nodes[key]
          return (
            <div key={key} className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg border border-border/40 bg-surface-2/20">
              <CellBadge cellKey={key} />
              <span className="text-[10px] text-muted/60 font-mono shrink-0">{node.kind}</span>
              <div className="flex flex-wrap gap-1 min-w-0">
                {(node.outputs ?? []).length === 0 ? (
                  <span className="text-[10px] italic text-muted/40">no outputs traced</span>
                ) : (
                  node.outputs.map(col => <ColChip key={col} name={col} />)
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// LineagePanel (exported)
// ---------------------------------------------------------------------------

export default function LineagePanel({
  mode = 'cell',
  cellKey,
  cellSql,
  upstreamSqls,
  flowId,
  spec,
  onClose,
}) {
  return (
    <div className="rounded-xl border border-border bg-surface shadow-md overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 bg-surface-2/50 border-b border-border">
        <GitBranch size={12} className="text-blue-500 shrink-0" />
        <span className="text-[11px] font-semibold text-fg">
          {mode === 'flow' ? 'Flow Lineage' : 'Cell Lineage'}
        </span>
        {mode === 'cell' && cellKey && (
          <CellBadge cellKey={cellKey} />
        )}
        <div className="flex-1" />
        {onClose && (
          <button
            onClick={onClose}
            className="w-5 h-5 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface-2 transition-colors"
            title="Close lineage panel"
          >
            <X size={11} />
          </button>
        )}
      </div>

      {/* Body */}
      {mode === 'cell' ? (
        <CellLineageContent
          cellKey={cellKey}
          cellSql={cellSql}
          upstreamSqls={upstreamSqls}
        />
      ) : (
        <FlowLineageContent flowId={flowId} spec={spec} />
      )}
    </div>
  )
}
