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
import { RefreshCw, Plus, Check, ExternalLink } from 'lucide-react'

import { fetchPreaggSuggestions, fetchPreaggs, buildPreagg } from '../../lib/preagg.js'
import { useCanWrite } from '../../contexts/OrgContext.jsx'
import Button from '../../components/ui/Button.jsx'
import Tag from '../../components/ui/Tag.jsx'
import Skeleton from '../../components/ui/Skeleton.jsx'
import EmptyState from '../../components/ui/EmptyState.jsx'
import { EmptyQueries } from '../../components/illustrations'
import PageShell from '../../components/app/PageShell.jsx'
import { LoadError } from './appShared.jsx'

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
// ColumnList: a labelled run of column names (mono), used for the rollup shape
// ---------------------------------------------------------------------------

function ColumnList({ label, items, empty = 'none' }) {
  return (
    <div className="flex items-baseline gap-3 min-w-0">
      <span className="w-20 shrink-0 font-mono text-[11px] font-medium uppercase tracking-[0.06em] text-subtle">{label}</span>
      <div className="flex flex-wrap gap-1 min-w-0">
        {items && items.length > 0 ? (
          items.map((it) => (
            <Tag key={it} caps={false}>{it}</Tag>
          ))
        ) : (
          <span className="text-[12px] text-subtle">{empty}</span>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// SuggestionRow: one mined rollup candidate with a Build action
// ---------------------------------------------------------------------------

function SuggestionRow({ suggestion, canWrite, onBuild, buildState }) {
  const { table, dimensions, measures, filters, score, sample_count, est_bytes } = suggestion
  const building = buildState?.status === 'building'
  const built = buildState?.status === 'ok'
  const errored = buildState?.status === 'err'

  return (
    <li className="ap-row !items-start !py-4">
      <div className="ap-row-main">
        <div className="ap-row-title">
          <span className="ap-trunc font-mono">{table}</span>
          <Tag caps>score {formatCompact(score)}</Tag>
        </div>
        <p className="ap-meta mt-1">
          {Number(sample_count).toLocaleString()} hit{sample_count !== 1 ? 's' : ''} in the log
          <span aria-hidden="true"> · </span>~{formatBytes(est_bytes)} scanned
        </p>
        <div className="mt-3 flex flex-col gap-2">
          <ColumnList label="Group by" items={dimensions} empty="(none)" />
          <ColumnList label="Measures" items={measures} empty="(none)" />
          {filters && filters.length > 0 && <ColumnList label="Filters" items={filters} />}
        </div>
        {errored && (
          <p role="alert" className="mt-3 text-[13px] text-fg border-l-2 border-danger pl-2">
            {buildState?.message ?? 'Build failed.'} Nothing was changed.
          </p>
        )}
      </div>

      <div className="ap-actions !items-start">
        {canWrite ? (
          <Button
            size="sm"
            variant={built ? 'secondary' : 'primary'}
            onClick={() => onBuild(suggestion)}
            disabled={built}
            loading={building}
            title="Materialize and register this rollup"
          >
            {!building && (built ? <Check size={13} aria-hidden="true" /> : <Plus size={13} aria-hidden="true" />)}
            {building ? 'Building…' : built ? 'Built' : 'Build rollup'}
          </Button>
        ) : (
          <Tag title="Your role cannot build rollups">Read-only</Tag>
        )}
      </div>
    </li>
  )
}

// ---------------------------------------------------------------------------
// BuiltRollupRow: one active (built) rollup with its HIT count
// ---------------------------------------------------------------------------

function BuiltRollupRow({ rollup }) {
  const { rollup_id, table, source_table, dimensions, measures, rls_keys, datastore_id, hits } = rollup

  return (
    <li className="ap-row !items-start !py-4">
      <div className="ap-row-main">
        <div className="ap-row-title">
          <span className="ap-trunc font-mono">{table ?? rollup_id}</span>
          <Tag variant="success" dot>Active</Tag>
        </div>
        <p className="ap-meta mt-1 truncate">
          from {source_table}
          {datastore_id && <> <span aria-hidden="true">·</span> via {datastore_id}</>}
        </p>
        <div className="mt-3 flex flex-col gap-2">
          <ColumnList label="Group by" items={dimensions} empty="(none)" />
          <ColumnList label="Measures" items={measures} empty="(none)" />
          {rls_keys && rls_keys.length > 0 && <ColumnList label="RLS keys" items={rls_keys} />}
        </div>
        {rollup_id && (
          <p className="mt-3 ap-meta text-subtle truncate" title={rollup_id}>{rollup_id}</p>
        )}
      </div>
      <div className="text-right shrink-0" title="Queries routed to this rollup">
        <div className="ap-mono text-[20px] font-semibold leading-none text-fg">{Number(hits ?? 0).toLocaleString()}</div>
        <div className="ap-count mt-1">{hits === 1 ? 'hit' : 'hits'}</div>
      </div>
    </li>
  )
}

// ---------------------------------------------------------------------------
// RollupSkeleton: layout-matching placeholder for a row
// ---------------------------------------------------------------------------

function RollupSkeleton() {
  return (
    <li className="ap-row !items-start !py-4" aria-hidden="true">
      <div className="ap-row-main flex flex-col gap-2">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-3 w-56" />
        <Skeleton className="h-5 w-72 mt-2" />
        <Skeleton className="h-5 w-56" />
      </div>
      <Skeleton className="h-8 w-24 shrink-0" />
    </li>
  )
}

function Section({ index, title, count = undefined, hint = undefined, children }) {
  return (
    <section className="mt-8 first:mt-0" aria-labelledby={`pre-${index}`}>
      <div className="border-t-2 border-ink pt-3 pb-2 flex items-baseline gap-3">
        <span className="font-mono text-[11px] text-subtle">{index}</span>
        <h2 id={`pre-${index}`} className="text-[15px] font-semibold text-fg">{title}</h2>
        {count != null && <span className="ap-count">{count}</span>}
        {hint && <span className="text-[13px] text-muted hidden sm:inline">{hint}</span>}
      </div>
      {children}
    </section>
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
    <div className="h-full overflow-y-auto">
      <PageShell
        eyebrow="Query acceleration"
        title="Pre-aggregations"
        description="Rollups mined from your own query log. Matching queries read the rollup instead of the source."
        actions={
          <Button variant="secondary" onClick={load} loading={loading} title="Refresh suggestions and built rollups" aria-label="Refresh suggestions and built rollups">
            {!loading && <RefreshCw size={14} aria-hidden="true" />}
            <span className="hidden sm:inline">Refresh</span>
          </Button>
        }
      >
        <p className="text-[13px] leading-relaxed text-muted max-w-[68ch] mb-8">
          Nubi watches the queries that run, finds hot <code className="font-mono text-fg">GROUP BY</code> shapes,
          ranks them by frequency times scanned bytes, and materializes the winners as content-hashed rollup tables.
          Row-level security still holds because each rollup keeps its tenant key columns.{' '}
          <a
            href="/docs/pre-aggregations"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-[2px]"
          >
            Read the guide <ExternalLink size={11} aria-hidden="true" />
          </a>
        </p>

        {error && <LoadError title="Pre-aggregations could not load" error={error} onRetry={load} />}

        {!error && loading && (
          <div aria-live="polite" aria-label="Loading pre-aggregations">
            <Section index="01" title="Suggested rollups"><ul className="ap-list"><RollupSkeleton /><RollupSkeleton /></ul></Section>
            <Section index="02" title="Active rollups"><ul className="ap-list"><RollupSkeleton /></ul></Section>
          </div>
        )}

        {!error && !loading && (
          <>
            <Section index="01" title="Suggested rollups" count={openSuggestions.length} hint="Ranked by score">
              {openSuggestions.length > 0 ? (
                <ul className="ap-list">
                  {openSuggestions.map((s) => (
                    <SuggestionRow
                      key={s.cluster_key}
                      suggestion={s}
                      canWrite={canWrite}
                      onBuild={handleBuild}
                      buildState={buildStates[s.cluster_key]}
                    />
                  ))}
                </ul>
              ) : (
                <EmptyState
                  compact
                  art={<EmptyQueries className="w-28" />}
                  title="No suggestions yet"
                  description="A GROUP BY shape is suggested once it has run at least 3 times. Run your dashboards and check back."
                />
              )}
            </Section>

            <Section index="02" title="Active rollups" count={rollups.length} hint="Queries are routed here automatically">
              {rollups.length > 0 ? (
                <ul className="ap-list">
                  {rollups.map((r) => (
                    <BuiltRollupRow key={r.rollup_id} rollup={r} />
                  ))}
                </ul>
              ) : (
                <EmptyState
                  compact
                  art={<EmptyQueries className="w-28" />}
                  title="No rollups built yet"
                  description={canWrite
                    ? 'Build a suggestion above to materialize your first rollup. Matching queries will route to it.'
                    : 'A writer can build a suggestion to materialize a rollup. Matching queries will route to it.'}
                />
              )}
            </Section>
          </>
        )}
      </PageShell>
    </div>
  )
}
