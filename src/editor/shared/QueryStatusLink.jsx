/**
 * shared/QueryStatusLink.jsx — live status + deep link for a widget's bound query.
 *
 * Closes the dashboard→query half of the seam between the two editors. Before
 * this, a widget's only relationship to its query was a `query_id` string typed
 * into `QueryPicker` — you could not see whether that query actually returned
 * anything, nor open it. So a widget showing "Query failed" had no in-app route
 * to the SQL responsible.
 *
 * Every major BI tool offers this jump (Metabase "Edit question", Superset
 * "Edit chart", Looker "Explore from here"); the row also carries the live
 * row-count / error so you rarely need to take it.
 *
 * Props:
 *   queryId  string — the widget's query_id (or options_query_id).
 */

import { Link } from 'react-router-dom'
import { ArrowUpRight, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react'
import { useQuerySample } from './useInspectorData.js'

export default function QueryStatusLink({ queryId }) {
  // limit 1: we only need "did it run, and how many rows", not a sample.
  const { rowCount, loading, error } = useQuerySample(queryId, 1)

  if (!queryId) return null

  return (
    <div className="rounded-lg border border-border bg-surface-2/40 px-2.5 py-2 space-y-1.5">
      <div className="flex items-center gap-1.5 text-[11px]">
        {loading ? (
          <>
            <Loader2 size={11} className="animate-spin text-muted shrink-0" />
            <span className="text-muted">Checking…</span>
          </>
        ) : error ? (
          <>
            <AlertCircle size={11} className="text-danger shrink-0" />
            <span className="text-danger font-medium">Query failed</span>
          </>
        ) : (
          <>
            <CheckCircle2 size={11} className="text-success shrink-0" />
            <span className="text-fg/80">
              {rowCount.toLocaleString()} row{rowCount === 1 ? '' : 's'}
            </span>
          </>
        )}
      </div>

      {error && (
        <p className="text-[10px] leading-snug text-danger/90 break-words">{error}</p>
      )}

      <Link
        to={`/queries/${encodeURIComponent(queryId)}`}
        className="inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
      >
        Open in query editor
        <ArrowUpRight size={11} />
      </Link>
    </div>
  )
}
