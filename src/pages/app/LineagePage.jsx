/**
 * LineagePage — full workspace data lineage DAG view.
 *
 * Fetches GET /lineage/dag for the whole-workspace graph.
 * Selecting a node fetches GET /lineage/dag/{node_id}?hops=N for its
 * neighbourhood (default hops=2).
 *
 * Since there's no heavy graph library, this renders as a list-based DAG view
 * using badge/chip patterns similar to LineagePanel.jsx:
 *   - Left panel: searchable node list
 *   - Right panel: selected node's upstream / downstream neighbours
 */

import { useState, useEffect, useCallback, useMemo } from 'react'
import { GitBranch, Loader2, AlertCircle, Search, ArrowRight, ArrowLeft, X, RefreshCw, Columns3 } from 'lucide-react'
import { get } from '../../lib/api.js'
import Button from '../../components/ui/Button.jsx'
import Skeleton from '../../components/ui/Skeleton.jsx'

// ---------------------------------------------------------------------------
// Small reusable chips (match LineagePanel visual language)
// ---------------------------------------------------------------------------

function NodeBadge({ node, highlight = false, onClick }) {
  const label = node?.label ?? node?.id ?? '?'
  const kind = node?.kind ?? node?.type ?? ''
  const cls = [
    'inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs font-mono font-semibold border cursor-pointer transition-colors select-none',
    highlight
      ? 'bg-primary/15 text-primary border-primary/30 hover:bg-primary/25'
      : 'bg-surface-2 text-fg border-border hover:bg-surface-2/80 hover:border-primary/30',
  ].join(' ')

  return (
    <button className={cls} onClick={onClick} title={`${label}${kind ? ` (${kind})` : ''}`}>
      {kind && (
        <span className="text-[9px] uppercase tracking-wider font-semibold opacity-60 shrink-0">{kind}</span>
      )}
      <span className="truncate max-w-[160px]">{label}</span>
    </button>
  )
}

function KindChip({ kind }) {
  return (
    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider bg-surface-2 text-muted border border-border/60">
      {kind}
    </span>
  )
}

// ---------------------------------------------------------------------------
// NeighbourhoodPanel — upstream / downstream for selected node
// ---------------------------------------------------------------------------

function NeighbourhoodPanel({ nodeId, hops, onSelectNode }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    if (!nodeId) return
    setLoading(true)
    setError(null)
    setData(null)
    try {
      const res = await get(`/lineage/dag/${encodeURIComponent(nodeId)}?hops=${hops}`)
      setData(res)
    } catch (err) {
      setError(err?.message || 'Failed to load neighbourhood.')
    } finally {
      setLoading(false)
    }
  }, [nodeId, hops])

  useEffect(() => {
    load()
  }, [load])

  if (!nodeId) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-6 py-12 gap-2">
        <GitBranch size={28} className="text-muted/30" />
        <p className="text-sm font-medium text-fg">Select a node</p>
        <p className="text-xs text-muted max-w-xs">
          Choose a node from the list to see its upstream and downstream connections.
        </p>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="space-y-5 px-4 py-4" aria-busy="true" aria-label="Loading neighbourhood">
        <div className="space-y-2">
          <Skeleton className="h-2.5 w-16 rounded" />
          <Skeleton className="h-4 w-40 rounded" />
        </div>
        <div className="space-y-2">
          <Skeleton className="h-2.5 w-24 rounded" />
          <div className="flex flex-wrap gap-1.5">
            <Skeleton className="h-6 w-20 rounded-lg" />
            <Skeleton className="h-6 w-24 rounded-lg" />
            <Skeleton className="h-6 w-16 rounded-lg" />
          </div>
        </div>
        <div className="space-y-2">
          <Skeleton className="h-2.5 w-28 rounded" />
          <div className="flex flex-wrap gap-1.5">
            <Skeleton className="h-6 w-24 rounded-lg" />
            <Skeleton className="h-6 w-16 rounded-lg" />
          </div>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-start gap-2 px-4 py-6 text-xs text-red-500">
        <div className="flex items-center gap-1.5">
          <AlertCircle size={13} /> {error}
        </div>
        <Button variant="outline" size="xs" onClick={load}>Try again</Button>
      </div>
    )
  }

  if (!data) return null

  // Normalise response shape: support { nodes, edges } or { upstream, downstream, node }
  const nodes = data.nodes ?? {}
  const edges = data.edges ?? []
  const upstreamNodes = data.upstream ?? []
  const downstreamNodes = data.downstream ?? []
  const selectedNode = data.node ?? nodes[nodeId] ?? { id: nodeId }

  // Derive upstream/downstream from edges if not provided directly
  let upstream = upstreamNodes
  let downstream = downstreamNodes
  if (upstream.length === 0 && downstream.length === 0 && edges.length > 0) {
    const upIds = new Set()
    const downIds = new Set()
    for (const edge of edges) {
      if (edge.to === nodeId || edge.target === nodeId) upIds.add(edge.from ?? edge.source)
      if (edge.from === nodeId || edge.source === nodeId) downIds.add(edge.to ?? edge.target)
    }
    upstream = [...upIds].map(id => nodes[id] ?? { id })
    downstream = [...downIds].map(id => nodes[id] ?? { id })
  }

  return (
    <div className="space-y-5 px-4 py-4 overflow-y-auto h-full">
      {/* Selected node */}
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-wider text-muted mb-2">Selected</p>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-semibold text-fg font-mono">
            {selectedNode?.label ?? selectedNode?.id ?? nodeId}
          </span>
          {selectedNode?.kind && <KindChip kind={selectedNode.kind} />}
        </div>
        {selectedNode?.schema && (
          <p className="text-xs text-muted mt-1 font-mono">{selectedNode.schema}</p>
        )}
      </div>

      {/* Upstream */}
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-wider text-muted mb-2 flex items-center gap-1.5">
          <ArrowLeft size={10} className="text-blue-500 dark:text-blue-400" />
          Upstream ({upstream.length})
        </p>
        {upstream.length === 0 ? (
          <p className="text-xs italic text-muted/50">No upstream nodes within {hops} hops.</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {upstream.map((n) => {
              const id = n?.id ?? n
              const nodeObj = typeof n === 'object' ? n : { id }
              return (
                <NodeBadge
                  key={id}
                  node={nodeObj}
                  onClick={() => onSelectNode(id)}
                />
              )
            })}
          </div>
        )}
      </div>

      {/* Downstream */}
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-wider text-muted mb-2 flex items-center gap-1.5">
          <ArrowRight size={10} className="text-emerald-500 dark:text-emerald-400" />
          Downstream ({downstream.length})
        </p>
        {downstream.length === 0 ? (
          <p className="text-xs italic text-muted/50">No downstream nodes within {hops} hops.</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {downstream.map((n) => {
              const id = n?.id ?? n
              const nodeObj = typeof n === 'object' ? n : { id }
              return (
                <NodeBadge
                  key={id}
                  node={nodeObj}
                  onClick={() => onSelectNode(id)}
                />
              )
            })}
          </div>
        )}
      </div>

      {/* All edges (detail) */}
      {edges.length > 0 && (
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted mb-2">
            Edges in neighbourhood ({edges.length})
          </p>
          <div className="space-y-1">
            {edges.slice(0, 50).map((edge, i) => {
              const from = edge.from ?? edge.source ?? '?'
              const to = edge.to ?? edge.target ?? '?'
              return (
                <div
                  key={i}
                  className="flex items-center gap-1.5 text-[11px] font-mono text-muted rounded-lg bg-surface-2/30 px-2.5 py-1.5 border border-border/40"
                >
                  <button
                    onClick={() => onSelectNode(from)}
                    className="text-blue-600 dark:text-blue-400 hover:underline truncate max-w-[120px]"
                    title={from}
                  >
                    {from}
                  </button>
                  <ArrowRight size={9} className="text-muted/50 shrink-0" />
                  <button
                    onClick={() => onSelectNode(to)}
                    className="text-emerald-600 dark:text-emerald-400 hover:underline truncate max-w-[120px]"
                    title={to}
                  >
                    {to}
                  </button>
                  {edge.label && (
                    <span className="text-muted/50 shrink-0 ml-1">· {edge.label}</span>
                  )}
                </div>
              )
            })}
            {edges.length > 50 && (
              <p className="text-[10px] text-muted/60 italic">… and {edges.length - 50} more edges</p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ColumnLineagePanel — column provenance chain for a selected node + column
// ---------------------------------------------------------------------------

function ColumnLineagePanel({ nodeId, column, hops, onSelectNode }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    if (!nodeId || !column) return
    setLoading(true)
    setError(null)
    setData(null)
    try {
      const res = await get(
        `/lineage/columns/${encodeURIComponent(nodeId)}?column=${encodeURIComponent(column)}&hops=${hops}`
      )
      setData(res)
    } catch (err) {
      setError(err?.message || 'Failed to load column lineage.')
    } finally {
      setLoading(false)
    }
  }, [nodeId, column, hops])

  useEffect(() => {
    load()
  }, [load])

  if (!nodeId || !column) return null

  return (
    <div className="mt-4 border-t border-border/60 pt-4 space-y-2">
      <div className="flex items-center gap-1.5">
        <Columns3 size={12} className="text-primary shrink-0" />
        <p className="text-[10px] font-semibold uppercase tracking-wider text-muted">
          Column provenance: <span className="font-mono text-fg">{column}</span>
        </p>
        <button
          onClick={load}
          disabled={loading}
          aria-label="Refresh column provenance"
          title="Refresh column provenance"
          className="ml-auto h-5 w-5 flex items-center justify-center text-muted hover:text-fg transition-colors disabled:opacity-40"
        >
          {loading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
        </button>
      </div>

      {loading && (
        <div className="flex items-center gap-1.5 text-xs text-muted">
          <Loader2 size={11} className="animate-spin" /> Loading provenance…
        </div>
      )}

      {error && (
        <div className="flex items-center gap-1.5 text-xs text-red-500">
          <AlertCircle size={11} /> {error}
        </div>
      )}

      {data && (
        <div className="space-y-1">
          {(data.path ?? []).length === 0 ? (
            <p className="text-xs italic text-muted/50">No provenance path found.</p>
          ) : (
            <>
              {(data.path ?? []).map((hop, i) => (
                <div
                  key={i}
                  className="flex items-start gap-2 text-[11px] font-mono rounded-lg bg-surface-2/30 px-2.5 py-1.5 border border-border/40"
                >
                  <span className="text-[9px] text-muted/40 font-sans mt-0.5 shrink-0 w-3">{i}</span>
                  <div className="flex-1 min-w-0">
                    <button
                      className="text-blue-600 dark:text-blue-400 hover:underline truncate max-w-[120px] block"
                      onClick={() => onSelectNode(hop.node)}
                      title={hop.node}
                    >
                      {hop.node}
                    </button>
                    <span className="text-fg/80">.{hop.column}</span>
                    {hop.alias && (
                      <span className="ml-1.5 text-[9px] font-sans text-amber-600 dark:text-amber-400 bg-amber-500/10 px-1 rounded">alias</span>
                    )}
                    {hop.select_star && (
                      <span className="ml-1.5 text-[9px] font-sans text-muted bg-surface-2 px-1 rounded">SELECT *</span>
                    )}
                  </div>
                  {i < (data.path ?? []).length - 1 && (
                    <ArrowLeft size={9} className="text-muted/40 shrink-0 mt-1" />
                  )}
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// LineagePage
// ---------------------------------------------------------------------------

const HOPS_OPTIONS = [1, 2, 3, 5]

export default function LineagePage() {
  // Workspace DAG
  const [dag, setDag] = useState(null)
  const [dagLoading, setDagLoading] = useState(true)
  const [dagError, setDagError] = useState(null)

  // Node selection
  const [selectedId, setSelectedId] = useState(null)
  const [hops, setHops] = useState(2)

  // Column selection for column-level lineage
  const [selectedColumn, setSelectedColumn] = useState(null)
  const [columnInput, setColumnInput] = useState('')

  // Search
  const [search, setSearch] = useState('')

  const loadDag = useCallback(async () => {
    setDagLoading(true)
    setDagError(null)
    try {
      const res = await get('/lineage/dag')
      setDag(res)
    } catch (err) {
      setDagError(err?.message || 'Failed to load lineage graph.')
    } finally {
      setDagLoading(false)
    }
  }, [])

  useEffect(() => {
    loadDag()
  }, [loadDag])

  // Normalise node list from DAG response
  const allNodes = useMemo(() => {
    if (!dag) return []
    if (Array.isArray(dag.nodes)) return dag.nodes
    if (dag.nodes && typeof dag.nodes === 'object') {
      return Object.entries(dag.nodes).map(([id, n]) => ({
        id,
        ...(typeof n === 'object' ? n : { label: String(n) }),
      }))
    }
    return []
  }, [dag])

  const filteredNodes = useMemo(() => {
    if (!search.trim()) return allNodes
    const q = search.toLowerCase()
    return allNodes.filter((n) => {
      const label = (n?.label ?? n?.id ?? '').toLowerCase()
      const kind = (n?.kind ?? '').toLowerCase()
      return label.includes(q) || kind.includes(q) || (n?.id ?? '').toLowerCase().includes(q)
    })
  }, [allNodes, search])

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Page header */}
      <div className="shrink-0 flex flex-wrap items-center gap-3 px-4 sm:px-6 py-4 border-b border-border bg-surface">
        <div className="flex items-center gap-2">
          <GitBranch size={18} className="text-primary shrink-0" />
          <h1 className="text-base font-semibold text-fg font-display">Data Lineage</h1>
        </div>

        {dag && (
          <span className="text-xs text-muted">
            {allNodes.length} node{allNodes.length !== 1 ? 's' : ''}
            {dag.edges?.length != null ? `, ${dag.edges.length} edge${dag.edges.length !== 1 ? 's' : ''}` : ''}
          </span>
        )}

        <div className="flex-1 min-w-0" />

        {/* Hops selector */}
        <div className="flex items-center gap-1.5 shrink-0">
          <label className="text-xs text-muted font-medium shrink-0">Hops</label>
          <select
            value={hops}
            onChange={(e) => setHops(Number(e.target.value))}
            className="h-7 px-2 text-xs bg-surface border border-border rounded-lg text-fg focus:outline-none focus:ring-1 focus:ring-ring cursor-pointer"
          >
            {HOPS_OPTIONS.map(h => (
              <option key={h} value={h}>{h}</option>
            ))}
          </select>
        </div>

        {/* Refresh */}
        <button
          onClick={loadDag}
          disabled={dagLoading}
          title="Refresh graph"
          aria-label="Refresh lineage graph"
          className="h-7 w-7 flex items-center justify-center rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-50 transition-colors"
        >
          {dagLoading
            ? <Loader2 size={13} className="animate-spin" />
            : <RefreshCw size={13} />
          }
        </button>
      </div>

      {/* Body: node list + neighbourhood panel */}
      <div className="flex flex-1 min-h-0 overflow-hidden">

        {/* Left: node list */}
        <div className="w-48 sm:w-64 shrink-0 border-r border-border flex flex-col overflow-hidden">
          {/* Search */}
          <div className="p-3 border-b border-border shrink-0">
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search nodes…"
                className="w-full h-8 pl-8 pr-8 text-xs bg-surface-2 border border-border rounded-lg text-fg placeholder:text-muted/50 focus:outline-none focus:ring-1 focus:ring-ring"
              />
              {search && (
                <button
                  onClick={() => setSearch('')}
                  aria-label="Clear search"
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-fg transition-colors"
                >
                  <X size={12} />
                </button>
              )}
            </div>
          </div>

          {/* Node list body */}
          <div className="flex-1 overflow-y-auto">
            {dagLoading ? (
              <div className="px-3 py-2 space-y-2" aria-busy="true" aria-label="Loading nodes">
                {[1, 2, 3, 4, 5, 6].map((i) => (
                  <Skeleton key={i} className="h-6 w-full rounded-lg" />
                ))}
              </div>
            ) : dagError ? (
              <div className="flex flex-col items-start gap-2 px-4 py-4 text-xs text-red-500">
                <div className="flex items-center gap-1.5">
                  <AlertCircle size={12} /> {dagError}
                </div>
                <Button variant="outline" size="xs" onClick={loadDag}>Try again</Button>
              </div>
            ) : filteredNodes.length === 0 ? (
              <div className="px-4 py-6 text-xs text-center text-muted">
                {search ? 'No nodes match your search.' : 'No nodes in the lineage graph yet.'}
              </div>
            ) : (
              <div className="py-1">
                {filteredNodes.map((node) => {
                  const id = node?.id ?? node
                  const label = node?.label ?? id
                  const kind = node?.kind ?? node?.type ?? ''
                  const isSelected = id === selectedId
                  return (
                    <button
                      key={id}
                      onClick={() => setSelectedId(id)}
                      title={`${label}${kind ? ` (${kind})` : ''}`}
                      className={[
                        'w-full text-left px-3 py-2 text-xs transition-colors',
                        'flex items-center gap-2 min-w-0',
                        isSelected
                          ? 'bg-primary/10 text-primary font-semibold'
                          : 'text-fg hover:bg-surface-2 text-muted hover:text-fg',
                      ].join(' ')}
                    >
                      {kind && (
                        <span className={[
                          'shrink-0 text-[9px] uppercase tracking-wider font-semibold px-1 py-0.5 rounded border',
                          isSelected
                            ? 'bg-primary/10 text-primary border-primary/20'
                            : 'bg-surface-2 text-muted border-border/60',
                        ].join(' ')}>
                          {kind}
                        </span>
                      )}
                      <span className="truncate font-mono">{label}</span>
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        </div>

        {/* Right: neighbourhood detail */}
        <div className="flex-1 min-w-0 overflow-hidden bg-surface-2/20 overflow-y-auto">
          <NeighbourhoodPanel
            nodeId={selectedId}
            hops={hops}
            onSelectNode={(id) => { setSelectedId(id); setSelectedColumn(null); setColumnInput('') }}
          />

          {/* Column-level lineage — input + provenance chain */}
          {selectedId && (
            <div className="px-4 pb-6">
              <div className="flex items-center gap-2">
                <Columns3 size={13} className="text-muted shrink-0" />
                <input
                  type="text"
                  value={columnInput}
                  onChange={e => setColumnInput(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') setSelectedColumn(columnInput.trim() || null) }}
                  placeholder="Column name (Enter to trace)"
                  className="flex-1 h-7 px-2 text-xs font-mono bg-surface border border-border rounded-lg text-fg placeholder:text-muted/50 focus:outline-none focus:ring-1 focus:ring-ring"
                />
                <button
                  onClick={() => setSelectedColumn(columnInput.trim() || null)}
                  className="h-7 px-2.5 text-xs rounded-lg border border-border bg-surface-2 hover:bg-surface text-muted hover:text-fg transition-colors"
                >
                  Trace
                </button>
                {selectedColumn && (
                  <button
                    onClick={() => { setSelectedColumn(null); setColumnInput('') }}
                    aria-label="Clear column selection"
                    className="h-7 w-7 flex items-center justify-center rounded-lg border border-border text-muted hover:text-fg transition-colors"
                  >
                    <X size={12} />
                  </button>
                )}
              </div>
              {selectedColumn && (
                <ColumnLineagePanel
                  nodeId={selectedId}
                  column={selectedColumn}
                  hops={hops}
                  onSelectNode={(id) => { setSelectedId(id); setSelectedColumn(null); setColumnInput('') }}
                />
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
