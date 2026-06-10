/**
 * PreaggregationsPanel — the "Pre-aggregations" surface inside the query section.
 *
 * Pre-aggregations are materialized rollup tables Nubi mines from your own
 * query log: hot GROUP BY shapes are ranked by frequency × scanned-bytes,
 * materialized once, content-hashed, and then matching queries are transparently
 * routed to the rollup (fewer scanned bytes, faster reads) — RLS still holds
 * because the rollup keeps its tenant key columns.
 *
 * This panel:
 *   - lists active (built) rollups: name, source table, dimensions, measures,
 *     and HIT count (how many queries have been routed to it),
 *   - shows mined suggestions ranked by score, each with a one-click Build
 *     action (writers only) that calls POST /preagg/build by cluster_key,
 *   - explains in-context what a pre-aggregation does and links to the docs.
 *
 * Empty / loading / error / read-only states are all handled inline.
 *
 * Data shapes match backend/app/routes/preagg.py + app/connectors/preagg.py:
 *   suggestion: { table, dimensions, measures, filters, score, sample_count,
 *                 est_bytes, cluster_key }
 *   built rollup: { rollup_id, table, source_table, dimensions, measures,
 *                   rls_keys, database, datastore_id, query_id, hits }
 */

import { useState, useEffect, useCallback } from 'react'
import {
  Boxes,
  Zap,
  RefreshCw,
  Loader2,
  AlertCircle,
  CheckCircle2,
  Layers,
  Database,
  Gauge,
  Sigma,
  Filter,
  Plus,
  Info,
  ExternalLink,
  Sparkles,
} from 'lucide-react'

import { fetchPreaggSuggestions, fetchPreaggs, buildPreagg } from '../../lib/preagg.js'
import { useCanWrite } from '../../contexts/OrgContext.jsx'

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

/** Human-readable byte count, e.g. 117560 → "114.8 KB". */
function formatBytes(n) {
  const bytes = Number(n) || 0
  if (bytes <= 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = bytes
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v >= 10 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`
}

/** Compact integer, e.g. 4820000 → "4.8M". */
function formatCompact(n) {
  const num = Number(n) || 0
  return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(num)
}

// ---------------------------------------------------------------------------
// ColumnChips — render a labelled list of dimension/measure/key strings
// ---------------------------------------------------------------------------

function ColumnChips({ icon: Icon, label, items, tone = 'muted', empty = 'none' }) {
  const toneClass = {
    muted: 'bg-surface-2 text-muted border-border/60',
    primary: 'bg-primary/10 text-primary border-primary/20',
    indigo: 'bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border-indigo-500/20',
    amber: 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20',
  }[tone]

  return (
    <div className="flex items-start gap-1.5 min-w-0">
      <span className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider text-muted/70 shrink-0 mt-0.5">
        <Icon size={10} />
        {label}
      </span>
      <div className="flex flex-wrap gap-1 min-w-0">
        {items && items.length > 0 ? (
          items.map((it) => (
            <span
              key={it}
              className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono border ${toneClass}`}
            >
              {it}
            </span>
          ))
        ) : (
          <span className="text-[10px] text-muted/50 italic">{empty}</span>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// SuggestionCard — one mined rollup candidate with a Build action
// ---------------------------------------------------------------------------

function SuggestionCard({ suggestion, canWrite, onBuild, buildState }) {
  const { table, dimensions, measures, filters, score, sample_count, est_bytes } = suggestion
  const building = buildState?.status === 'building'
  const built = buildState?.status === 'ok'
  const errored = buildState?.status === 'err'

  return (
    <div className="rounded-2xl border border-border bg-surface p-4 shadow-sm transition-shadow hover:shadow-md">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-amber-500/10 text-amber-600 dark:text-amber-400">
          <Sparkles size={16} />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="inline-flex items-center gap-1 text-sm font-semibold text-fg font-display">
              <Database size={13} className="text-muted" />
              {table}
            </span>
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-semibold rounded-full bg-primary/10 text-primary border border-primary/20">
              <Gauge size={9} /> score {formatCompact(score)}
            </span>
          </div>

          {/* Cost / frequency stats */}
          <div className="mt-1.5 flex items-center gap-3 flex-wrap text-[11px] text-muted">
            <span className="inline-flex items-center gap-1">
              <Zap size={10} className="text-amber-500" />
              {Number(sample_count).toLocaleString()} hit{sample_count !== 1 ? 's' : ''} in the log
            </span>
            <span className="inline-flex items-center gap-1">
              <Database size={10} />
              ~{formatBytes(est_bytes)} scanned
            </span>
          </div>

          {/* Shape */}
          <div className="mt-3 flex flex-col gap-2">
            <ColumnChips icon={Layers} label="group by" items={dimensions} tone="indigo" empty="(none)" />
            <ColumnChips icon={Sigma} label="measures" items={measures} tone="primary" empty="(none)" />
            {filters && filters.length > 0 && (
              <ColumnChips icon={Filter} label="filters" items={filters} tone="muted" />
            )}
          </div>
        </div>

        {/* Build action */}
        <div className="shrink-0 flex flex-col items-end gap-1.5">
          {canWrite ? (
            <button
              onClick={() => onBuild(suggestion)}
              disabled={building || built}
              className="inline-flex items-center gap-1.5 h-8 px-3 text-xs font-semibold rounded-lg bg-primary text-primary-fg hover:opacity-90 disabled:opacity-60 disabled:cursor-not-allowed transition-opacity"
              title="Materialize and register this rollup"
            >
              {building ? (
                <Loader2 size={12} className="animate-spin" />
              ) : built ? (
                <CheckCircle2 size={12} />
              ) : (
                <Plus size={12} />
              )}
              {building ? 'Building…' : built ? 'Built' : 'Build'}
            </button>
          ) : (
            <span className="text-[10px] text-muted/70 select-none" title="Read-only access">
              Read-only
            </span>
          )}
        </div>
      </div>

      {errored && (
        <p className="mt-2 text-[11px] text-rose-500 flex items-center gap-1">
          <AlertCircle size={10} /> {buildState?.message ?? 'Build failed.'}
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// BuiltRollupCard — one active (built) rollup with its HIT count
// ---------------------------------------------------------------------------

function BuiltRollupCard({ rollup }) {
  const { rollup_id, table, source_table, dimensions, measures, rls_keys, datastore_id, hits } = rollup

  return (
    <div className="rounded-2xl border border-border bg-surface p-4 shadow-sm">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
          <Boxes size={16} />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-fg font-display truncate">{table ?? rollup_id}</span>
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-semibold rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
              <CheckCircle2 size={9} /> active
            </span>
            <span
              className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-semibold rounded-full bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border border-indigo-500/20"
              title="Queries routed to this rollup"
            >
              <Zap size={9} /> {Number(hits ?? 0).toLocaleString()} hit{hits !== 1 ? 's' : ''}
            </span>
          </div>

          <p className="mt-1 text-[11px] text-muted flex items-center gap-1 min-w-0">
            <Database size={10} className="shrink-0" />
            from <span className="font-mono text-fg/80">{source_table}</span>
            {datastore_id && (
              <span className="text-muted/60 truncate">· via {datastore_id}</span>
            )}
          </p>

          <div className="mt-3 flex flex-col gap-2">
            <ColumnChips icon={Layers} label="group by" items={dimensions} tone="indigo" empty="(none)" />
            <ColumnChips icon={Sigma} label="measures" items={measures} tone="primary" empty="(none)" />
            {rls_keys && rls_keys.length > 0 && (
              <ColumnChips icon={Filter} label="rls keys" items={rls_keys} tone="amber" />
            )}
          </div>

          {rollup_id && (
            <p className="mt-2 text-[10px] font-mono text-muted/60 truncate" title={rollup_id}>
              {rollup_id}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// SectionHeader
// ---------------------------------------------------------------------------

function SectionHeader({ icon: Icon, title, count, hint }) {
  return (
    <div className="flex items-baseline gap-2 mb-3">
      <h3 className="text-sm font-semibold text-fg font-display flex items-center gap-1.5">
        <Icon size={14} className="text-primary" />
        {title}
      </h3>
      {count != null && (
        <span className="text-[11px] font-mono text-muted">{count}</span>
      )}
      {hint && <span className="text-[11px] text-muted/70">· {hint}</span>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// PreaggregationsPanel
// ---------------------------------------------------------------------------

export default function PreaggregationsPanel() {
  const canWrite = useCanWrite()

  const [suggestions, setSuggestions] = useState([])
  const [rollups, setRollups] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // Per-suggestion build state keyed by cluster_key:
  // { [cluster_key]: { status: 'building'|'ok'|'err', message?: string } }
  const [buildStates, setBuildStates] = useState({})

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [sug, built] = await Promise.all([fetchPreaggSuggestions(), fetchPreaggs()])
      setSuggestions(sug)
      setRollups(built)
    } catch (err) {
      // The lib helpers swallow errors and return [], so this is belt-and-braces.
      setError(err?.message ?? 'Failed to load pre-aggregations.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const handleBuild = useCallback(async (suggestion) => {
    const key = suggestion.cluster_key
    setBuildStates((prev) => ({ ...prev, [key]: { status: 'building' } }))
    try {
      // Build by cluster_key — the backend resolves table/dimensions/measures
      // from the mined candidate so we don't have to re-send the shape.
      await buildPreagg({ cluster_key: key })
      setBuildStates((prev) => ({ ...prev, [key]: { status: 'ok' } }))
      // Refresh the built-rollups list so the new rollup shows up immediately.
      const built = await fetchPreaggs()
      setRollups(built)
    } catch (err) {
      const message =
        err?.status === 403
          ? 'You need writer access to build a rollup.'
          : err?.message ?? 'Build failed.'
      setBuildStates((prev) => ({ ...prev, [key]: { status: 'err', message } }))
    }
  }, [])

  // Hide suggestions that already have a built rollup with the same shape so the
  // list reflects what's still actionable.
  const builtSignatures = new Set(
    rollups.map((r) => `${r.source_table}|${[...(r.dimensions ?? [])].sort().join(',')}`),
  )
  const openSuggestions = suggestions.filter((s) => {
    if (buildStates[s.cluster_key]?.status === 'ok') return false
    const sig = `${s.table}|${[...(s.dimensions ?? [])].sort().join(',')}`
    return !builtSignatures.has(sig)
  })

  return (
    <div className="h-full overflow-y-auto bg-bg">
      <div className="mx-auto max-w-4xl px-4 py-5 sm:px-6">

        {/* ── Header ───────────────────────────────────────────────────── */}
        <div className="flex items-start gap-3">
          <div
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl"
            style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
          >
            <Boxes size={22} className="text-white" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-semibold font-display text-fg">Pre-aggregations</h2>
            <p className="text-sm text-muted mt-0.5">
              Auto rollups, mined from your query log.
            </p>
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="h-8 px-2.5 flex items-center gap-1.5 text-[11px] font-medium rounded-lg border border-border bg-surface text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40 transition-colors shrink-0"
            title="Refresh suggestions and built rollups"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            <span className="hidden sm:inline">Refresh</span>
          </button>
        </div>

        {/* ── What is this? explainer ──────────────────────────────────── */}
        <div className="mt-4 rounded-2xl border border-border bg-surface-2/40 p-4">
          <div className="flex items-start gap-2.5">
            <Info size={15} className="text-primary shrink-0 mt-0.5" />
            <div className="text-[12px] leading-relaxed text-muted">
              <span className="text-fg font-medium">Pre-aggregations accelerate repeated queries.</span>{' '}
              Nubi watches the queries that actually run, finds the hot{' '}
              <code className="font-mono text-fg/80 bg-surface px-1 rounded">GROUP BY</code> shapes,
              ranks them by <span className="text-fg/80">frequency × scanned-bytes</span>, and materializes
              the winners as content-hashed rollup tables. Matching queries are then transparently routed to
              the rollup — fewer bytes scanned, faster dashboards and embeds — while row-level security still
              holds because the rollup keeps its tenant key columns. No cubes to hand-define; the suggestions
              below are mined automatically.
              <a
                href="/docs/pre-aggregations"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-0.5 ml-1 text-primary hover:underline"
              >
                Learn more <ExternalLink size={10} />
              </a>
            </div>
          </div>
        </div>

        {/* ── Error banner ─────────────────────────────────────────────── */}
        {error && (
          <div className="mt-4 flex items-center gap-2 rounded-xl bg-rose-500/5 border border-rose-500/20 px-3 py-2 text-xs text-rose-600 dark:text-rose-400">
            <AlertCircle size={13} />
            {error}
          </div>
        )}

        {/* ── Loading ──────────────────────────────────────────────────── */}
        {loading && (
          <div className="mt-8 flex items-center justify-center gap-2 text-sm text-muted">
            <Loader2 size={16} className="animate-spin" />
            Loading pre-aggregations…
          </div>
        )}

        {!loading && (
          <>
            {/* ── Suggestions ──────────────────────────────────────────── */}
            <section className="mt-6">
              <SectionHeader
                icon={Sparkles}
                title="Suggested rollups"
                count={openSuggestions.length}
                hint="ranked by score"
              />

              {openSuggestions.length > 0 ? (
                <div className="flex flex-col gap-3">
                  {openSuggestions.map((s) => (
                    <SuggestionCard
                      key={s.cluster_key}
                      suggestion={s}
                      canWrite={canWrite}
                      onBuild={handleBuild}
                      buildState={buildStates[s.cluster_key]}
                    />
                  ))}
                </div>
              ) : (
                <div className="rounded-2xl border border-dashed border-border bg-surface-2/30 px-6 py-10 text-center">
                  <Sparkles size={22} className="mx-auto mb-2 text-muted/40" />
                  <p className="text-sm text-fg font-medium">No suggestions yet</p>
                  <p className="text-xs text-muted mt-1 max-w-md mx-auto">
                    Run aggregating queries a few times and Nubi will mine the hot{' '}
                    <code className="font-mono text-fg/70">GROUP BY</code> shapes here. A pattern must
                    appear at least 3 times before it&apos;s suggested.
                  </p>
                </div>
              )}
            </section>

            {/* ── Active rollups ───────────────────────────────────────── */}
            <section className="mt-8 pb-4">
              <SectionHeader
                icon={Boxes}
                title="Active rollups"
                count={rollups.length}
                hint="transparently routed to"
              />

              {rollups.length > 0 ? (
                <div className="flex flex-col gap-3">
                  {rollups.map((r) => (
                    <BuiltRollupCard key={r.rollup_id} rollup={r} />
                  ))}
                </div>
              ) : (
                <div className="rounded-2xl border border-dashed border-border bg-surface-2/30 px-6 py-10 text-center">
                  <Boxes size={22} className="mx-auto mb-2 text-muted/40" />
                  <p className="text-sm text-fg font-medium">No rollups built yet</p>
                  <p className="text-xs text-muted mt-1 max-w-md mx-auto">
                    {canWrite
                      ? 'Build a suggestion above to materialize your first rollup. Once built, matching queries route to it automatically.'
                      : 'A writer can build a suggestion above to materialize a rollup. Once built, matching queries route to it automatically.'}
                  </p>
                </div>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  )
}
