/**
 * DatasetProfileView — per-column statistics for a Nubi dataset.
 *
 * Calls GET /api/v1/datasets/{id}/profile and renders:
 *   - row count + sampled indicator
 *   - per-column stats table: name, type, null rate (bar), distinct, min, max
 *
 * Used from DataExplorerPage (Profile tab on a table with a known dataset_id)
 * and can be embedded anywhere that has a `datasetId` prop.
 */

import { useState, useEffect, useCallback } from 'react'
import { BarChart2, Loader2, AlertCircle, RefreshCw } from 'lucide-react'
import { get } from '../../lib/api.js'
import EmptyState from '../../components/ui/EmptyState.jsx'
import Skeleton from '../../components/ui/Skeleton.jsx'
import Badge from '../../components/ui/Badge.jsx'
import Button from '../../components/ui/Button.jsx'

// ---------------------------------------------------------------------------
// Null rate bar
// ---------------------------------------------------------------------------

function NullRateBar({ rate }) {
  const pct = Math.round((rate ?? 0) * 100)
  const color =
    pct === 0 ? 'bg-success'
    : pct < 20 ? 'bg-warning'
    : 'bg-danger'

  return (
    <div className="flex items-center gap-2 min-w-[90px]">
      <div className="flex-1 h-1.5 rounded-full bg-surface-2 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-300 ${color}`}
          style={{ width: `${Math.max(pct, pct > 0 ? 2 : 0)}%` }}
        />
      </div>
      <span className="text-[11px] font-mono text-muted w-9 text-right shrink-0 tabular-nums">
        {pct}%
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Type badge
// ---------------------------------------------------------------------------

function TypeBadge({ type }) {
  return (
    <Badge variant="default" size="sm" className="font-mono">
      {type ?? '—'}
    </Badge>
  )
}

// ---------------------------------------------------------------------------
// DatasetProfileView
// ---------------------------------------------------------------------------

export default function DatasetProfileView({ datasetId }) {
  const [profile, setProfile] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    if (!datasetId) return
    setLoading(true)
    setError(null)
    try {
      const data = await get(`/datasets/${datasetId}/profile`)
      setProfile(data)
    } catch (err) {
      setError(err?.message || 'Failed to load profile.')
    } finally {
      setLoading(false)
    }
  }, [datasetId])

  useEffect(() => {
    load()
  }, [load])

  if (!datasetId) {
    return (
      <div className="flex flex-col items-center justify-center h-full">
        <EmptyState
          icon={<BarChart2 size={24} />}
          title="Select a dataset to profile"
          description="Open a dataset and click the Profile tab to see per-column statistics."
        />
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex flex-col h-full overflow-hidden" aria-busy="true">
        <div className="shrink-0 flex items-center gap-3 px-4 py-3 border-b border-border bg-surface">
          <Loader2 size={15} className="text-primary shrink-0 animate-spin" />
          <span className="text-sm text-muted">Computing profile…</span>
        </div>
        <div className="flex-1 overflow-hidden p-4 space-y-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="flex items-center gap-4">
              <Skeleton className="h-3.5 w-32 rounded" />
              <Skeleton className="h-3.5 w-14 rounded" />
              <Skeleton className="h-1.5 flex-1 max-w-[140px] rounded-full" />
              <Skeleton className="h-3.5 w-12 rounded ml-auto" />
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full">
        <EmptyState
          icon={<AlertCircle size={24} />}
          title="Couldn't load profile"
          description={error}
          action={(
            <Button variant="outline" size="sm" onClick={load}>
              <RefreshCw size={12} /> Retry
            </Button>
          )}
        />
      </div>
    )
  }

  if (!profile) return null

  const columns = profile.columns ?? []

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="shrink-0 flex items-center gap-3 px-4 py-3 border-b border-border bg-surface">
        <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-primary/10 shrink-0">
          <BarChart2 size={15} className="text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-fg">Column profile</span>
            {profile.sampled && (
              <Badge variant="warning" size="sm">
                Sampled
              </Badge>
            )}
          </div>
          <p className="text-xs text-muted mt-0.5 flex items-center gap-1.5 flex-wrap tabular-nums">
            {columns.length} column{columns.length === 1 ? '' : 's'}
            {profile.row_count != null && (
              <>
                <span className="text-muted/40" aria-hidden="true">·</span>
                {profile.row_count.toLocaleString()} rows
                {profile.sampled && (
                  <span className="text-muted/60">(sampled to {(profile.sample_rows ?? 0).toLocaleString()})</span>
                )}
              </>
            )}
          </p>
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={load}
          disabled={loading}
          title="Refresh profile"
          aria-label="Refresh profile"
        >
          {loading
            ? <Loader2 size={13} className="animate-spin" />
            : <RefreshCw size={13} />
          }
        </Button>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {columns.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16">
            <EmptyState compact title="No columns to profile" />
          </div>
        ) : (
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="sticky top-0 z-10 bg-surface border-b border-border">
                <th className="text-left px-4 py-2.5 font-semibold text-muted text-[11px] uppercase tracking-wider">Column</th>
                <th className="text-left px-3 py-2.5 font-semibold text-muted text-[11px] uppercase tracking-wider">Type</th>
                <th className="text-left px-3 py-2.5 font-semibold text-muted text-[11px] uppercase tracking-wider w-40">Null rate</th>
                <th className="text-right px-3 py-2.5 font-semibold text-muted text-[11px] uppercase tracking-wider">Distinct</th>
                <th className="text-right px-3 py-2.5 font-semibold text-muted text-[11px] uppercase tracking-wider">Min</th>
                <th className="text-right px-3 py-2.5 font-semibold text-muted text-[11px] uppercase tracking-wider">Max</th>
              </tr>
            </thead>
            <tbody>
              {columns.map((col, i) => (
                <tr
                  key={col.name}
                  className={[
                    'border-b border-border/50 hover:bg-surface-2/30 transition-colors',
                    i % 2 === 0 ? 'bg-surface' : 'bg-surface-2/10',
                  ].join(' ')}
                >
                  <td className="px-4 py-2.5 font-mono font-medium text-fg">
                    {col.name}
                  </td>
                  <td className="px-3 py-2.5">
                    <TypeBadge type={col.type} />
                  </td>
                  <td className="px-3 py-2.5">
                    <NullRateBar rate={col.null_rate} />
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-muted">
                    {col.distinct_count != null
                      ? col.distinct_count.toLocaleString()
                      : '—'}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-muted max-w-[120px]">
                    <span className="truncate block" title={col.min ?? '—'}>
                      {col.min ?? '—'}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-muted max-w-[120px]">
                    <span className="truncate block" title={col.max ?? '—'}>
                      {col.max ?? '—'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
