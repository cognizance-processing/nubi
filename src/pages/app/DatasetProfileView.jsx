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

// ---------------------------------------------------------------------------
// Null rate bar
// ---------------------------------------------------------------------------

function NullRateBar({ rate }) {
  const pct = Math.round((rate ?? 0) * 100)
  const color =
    pct === 0 ? 'bg-emerald-500'
    : pct < 5  ? 'bg-yellow-400'
    : pct < 20 ? 'bg-orange-400'
    : 'bg-red-500'

  return (
    <div className="flex items-center gap-2 min-w-[80px]">
      <div className="flex-1 h-1.5 rounded-full bg-surface-2 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-[11px] font-mono text-muted w-9 text-right shrink-0">
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
    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-medium bg-surface-2 text-muted border border-border/50">
      {type ?? '—'}
    </span>
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
      <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-6 py-12">
        <BarChart2 size={28} className="text-muted/30" />
        <p className="text-sm font-medium text-fg">Select a dataset to profile</p>
        <p className="text-xs text-muted max-w-xs">
          Open a dataset and click the Profile tab to see per-column statistics.
        </p>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 justify-center py-12 text-xs text-muted">
        <Loader2 size={14} className="animate-spin" /> Computing profile…
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-center gap-3 py-10 text-center px-6">
        <AlertCircle size={20} className="text-danger" />
        <p className="text-xs text-danger">{error}</p>
        <button
          onClick={load}
          className="text-xs text-primary hover:underline flex items-center gap-1"
        >
          <RefreshCw size={11} /> Retry
        </button>
      </div>
    )
  }

  if (!profile) return null

  const columns = profile.columns ?? []

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="shrink-0 flex items-center gap-3 px-4 py-3 border-b border-border bg-surface">
        <BarChart2 size={15} className="text-primary shrink-0" />
        <div className="flex-1 min-w-0">
          <span className="text-sm font-semibold text-fg">Column profile</span>
          {profile.row_count != null && (
            <span className="ml-2 text-xs text-muted">
              {profile.row_count.toLocaleString()} rows
              {profile.sampled && (
                <span className="ml-1 text-muted/60">(sampled to {(profile.sample_rows ?? 0).toLocaleString()})</span>
              )}
            </span>
          )}
        </div>
        <button
          onClick={load}
          disabled={loading}
          title="Refresh profile"
          aria-label="Refresh profile"
          className="h-7 w-7 flex items-center justify-center rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-50 transition-colors"
        >
          {loading
            ? <Loader2 size={13} className="animate-spin" />
            : <RefreshCw size={13} />
          }
        </button>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {columns.length === 0 ? (
          <p className="text-xs text-muted text-center py-8">No columns to profile.</p>
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
