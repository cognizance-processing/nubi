/**
 * WorkqueuePage — /workqueue
 *
 * "Needs attention" inbox — three grouped sections:
 *
 *   (a) Alerts       — active watches (GET /watches).
 *                      Watches that have fired show a "breached" chip;
 *                      all active watches are surfaced as potential alerts
 *                      since the backend does not persist a separate "fired"
 *                      field — the config.enabled flag is used instead.
 *
 *   (b) Flow runs    — recent failed / running runs per flow (GET /flows then
 *                      GET /flows/{id}/runs for each, capped to avoid N+1).
 *
 *   (c) Stale data   — datasets with status=stale from GET /health/freshness.
 *
 * Empty state: "All clear — nothing needs attention" when every group is empty.
 *
 * All groups handle loading / empty / error independently so a single 404
 * never collapses the whole page.
 */

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Bell,
  Workflow,
  Database,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Clock,
  ChevronRight,
  RefreshCw,
  Loader2,
  GitBranch,
} from 'lucide-react'
import { useOrg } from '../../contexts/OrgContext.jsx'
import * as api from '../../lib/api.js'
import { PageRoot, PageHeader } from '../../components/app/PageShell.jsx'

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

async function fetchList(path) {
  try {
    const data = await api.get(path)
    if (Array.isArray(data)) return data
    for (const key of ['items', 'data', 'boards', 'queries', 'connectors', 'flows', 'results', 'datasets', 'watches']) {
      if (Array.isArray(data?.[key])) return data[key]
    }
    return []
  } catch {
    return []
  }
}

function relativeTime(date) {
  const diff = Date.now() - date.getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  if (days < 30) return `${days}d ago`
  return date.toLocaleDateString()
}

// ─────────────────────────────────────────────────────────────────────────────
// Scoped animation styles
// ─────────────────────────────────────────────────────────────────────────────

const ANIMATION_STYLE = `
  @keyframes wq-shimmer { 0% { background-position:-400px 0 } 100% { background-position:400px 0 } }
  .wq-skeleton {
    background: linear-gradient(90deg, var(--surface-2,#eef2f7) 25%, var(--border,#e2e8f0) 50%, var(--surface-2,#eef2f7) 75%);
    background-size: 800px 100%; animation: wq-shimmer 1.4s ease-in-out infinite; border-radius:.5rem;
  }
  @keyframes wq-reveal { from { opacity:0; transform: translateY(6px) } to { opacity:1; transform:none } }
  .wq-reveal { opacity:0; animation: wq-reveal .4s cubic-bezier(.16,1,.3,1) forwards; }
  @media (prefers-reduced-motion: reduce) { .wq-reveal { animation: none; opacity:1 } }
`

// ─────────────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────────────

/** Status chip used on item rows. */
function StatusChip({ label, variant }) {
  const styles = {
    red:    'bg-red-500/10 text-red-600 dark:text-red-400',
    amber:  'bg-amber-500/10 text-amber-600 dark:text-amber-400',
    blue:   'bg-blue-500/10 text-blue-600 dark:text-blue-400',
    muted:  'bg-surface-2 text-muted',
  }
  return (
    <span className={`inline-flex items-center text-xs font-medium px-2 py-0.5 rounded-full shrink-0 ${styles[variant] ?? styles.muted}`}>
      {label}
    </span>
  )
}

/** Single row inside a group. */
function ItemRow({ icon: Icon, iconVariant = 'muted', title, subtitle, meta, chips, href, delay }) {
  const iconStyles = {
    red:    'bg-red-500/10 text-red-600 dark:text-red-400',
    amber:  'bg-amber-500/10 text-amber-600 dark:text-amber-400',
    blue:   'bg-blue-500/10 text-blue-600 dark:text-blue-400',
    muted:  'bg-surface-2 text-muted',
  }

  const inner = (
    <div
      style={{ animationDelay: `${delay}ms` }}
      className="wq-reveal group flex items-center gap-3 p-4 rounded-xl border border-border bg-surface
        hover:border-primary/40 hover:shadow-sm transition-all duration-150 min-h-[52px] w-full text-left"
    >
      <div className={`flex items-center justify-center w-9 h-9 rounded-lg shrink-0 ${iconStyles[iconVariant]}`}>
        <Icon size={16} />
      </div>
      <div className="flex-1 min-w-0">
        <p className="font-display font-medium text-sm text-fg truncate">{title}</p>
        {subtitle && <p className="text-xs text-muted mt-0.5 truncate">{subtitle}</p>}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {chips?.map((c) => <StatusChip key={c.label} label={c.label} variant={c.variant} />)}
        {meta && (
          <span className="text-xs text-muted hidden sm:flex items-center gap-1">
            <Clock size={10} />{meta}
          </span>
        )}
        {href && (
          <ChevronRight size={14} className="text-muted opacity-0 group-hover:opacity-100 transition-opacity" />
        )}
      </div>
    </div>
  )

  if (href) return (
    <Link to={href} className="block rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
      {inner}
    </Link>
  )
  return inner
}

/** Group skeleton loader. */
function GroupSkeleton({ rows = 3 }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="wq-skeleton h-[52px] w-full rounded-xl" />
      ))}
    </div>
  )
}

/** Section header with a count badge. */
function GroupHeader({ icon: Icon, title, count, description }) {
  return (
    <div className="flex items-center gap-3 mb-3">
      <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-surface-2 shrink-0">
        <Icon size={15} className="text-muted" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <h2 className="font-display font-semibold text-base text-fg">{title}</h2>
          {count != null && (
            <span className={[
              'inline-flex items-center justify-center min-w-[20px] h-5 rounded-full text-xs font-medium tabular-nums px-1.5',
              count > 0 ? 'bg-red-500/10 text-red-600 dark:text-red-400' : 'bg-surface-2 text-muted',
            ].join(' ')}>
              {count}
            </span>
          )}
        </div>
        {description && <p className="text-xs text-muted">{description}</p>}
      </div>
    </div>
  )
}

function EmptyGroup({ label }) {
  return (
    <div className="flex items-center gap-3 py-3 px-4 rounded-xl border border-dashed border-border bg-surface-2/30">
      <CheckCircle2 size={16} className="text-emerald-500 shrink-0" />
      <p className="text-sm text-muted">{label}</p>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Main page
// ─────────────────────────────────────────────────────────────────────────────

export default function WorkqueuePage() {
  const { activeOrg } = useOrg()

  // (a) Watches / Alerts
  const [watchesLoading, setWatchesLoading] = useState(true)
  const [watches, setWatches] = useState([])

  // (b) Flow runs (failed + running)
  const [runsLoading, setRunsLoading] = useState(true)
  const [badRuns, setBadRuns] = useState([])

  // (c) Stale datasets
  const [stalenessLoading, setStalenessLoading] = useState(true)
  const [staleDatasets, setStaleDatasets] = useState([])

  // (d) Schema drift
  const [driftLoading, setDriftLoading] = useState(true)
  const [driftItems, setDriftItems] = useState([])

  // ── (a) Watches ──────────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    async function loadWatches() {
      setWatchesLoading(true)
      const list = await fetchList('/watches')
      if (cancelled) return
      // Surface all watches — the backend does not expose a "fired" timestamp
      // on the list endpoint. We show active (enabled) watches as potential alerts.
      // Watches whose config.enabled is explicitly false are hidden.
      const active = list.filter((w) => w.config?.enabled !== false)
      setWatches(active)
      setWatchesLoading(false)
    }
    loadWatches()
    return () => { cancelled = true }
  }, [activeOrg?.id])

  // ── (b) Flow runs ────────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    async function loadFlowRuns() {
      setRunsLoading(true)
      const flows = await fetchList('/flows')
      if (cancelled) return

      // Fetch recent runs for up to 10 flows in parallel (cap to avoid hammering).
      const flowsToCheck = flows.slice(0, 10)
      const runLists = await Promise.all(
        flowsToCheck.map((f) =>
          fetchList(`/flows/${f.id}/runs?limit=5`).then((runs) =>
            runs.map((r) => ({ ...r, _flow_name: f.name || f.title || f.id })),
          ),
        ),
      )
      if (cancelled) return

      const allRuns = runLists.flat()
      // Keep only failed or running runs, sorted newest first.
      const attention = allRuns
        .filter((r) => r.state === 'failed' || r.state === 'running')
        .sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0))
        .slice(0, 20)

      setBadRuns(attention)
      setRunsLoading(false)
    }
    loadFlowRuns()
    return () => { cancelled = true }
  }, [activeOrg?.id])

  // ── (c) Stale datasets ───────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    async function loadStaleness() {
      setStalenessLoading(true)
      const list = await fetchList('/health/freshness')
      if (cancelled) return
      setStaleDatasets(list.filter((d) => d.status === 'stale'))
      setStalenessLoading(false)
    }
    loadStaleness()
    return () => { cancelled = true }
  }, [activeOrg?.id])

  // ── (d) Schema drift ─────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    async function loadDrift() {
      setDriftLoading(true)
      const list = await fetchList('/health/drift')
      if (cancelled) return
      setDriftItems(list)
      setDriftLoading(false)
    }
    loadDrift()
    return () => { cancelled = true }
  }, [activeOrg?.id])

  const totalIssues = watches.length + badRuns.length + staleDatasets.length + driftItems.length
  const allClear = !watchesLoading && !runsLoading && !stalenessLoading && !driftLoading && totalIssues === 0

  return (
    <PageRoot>
      <style>{ANIMATION_STYLE}</style>

      <PageHeader
        title="Workqueue"
        subtitle="Items that need your attention — alerts, failed runs, and stale data."
      />

      {/* ── All-clear state ─────────────────────────────────────────────────── */}
      {allClear && (
        <div className="wq-reveal mt-10 flex flex-col items-center justify-center py-20 text-center gap-4">
          <div className="flex items-center justify-center w-16 h-16 rounded-2xl bg-emerald-500/10">
            <CheckCircle2 size={30} className="text-emerald-500" />
          </div>
          <div>
            <p className="font-display font-semibold text-xl text-fg">All clear</p>
            <p className="text-sm text-muted mt-1.5 max-w-xs">Nothing needs attention right now. Check back after your next flow run.</p>
          </div>
        </div>
      )}

      {/* ── Groups ──────────────────────────────────────────────────────────── */}
      {!allClear && (
        <div className="mt-6 space-y-8">

          {/* (a) Alerts — active watches */}
          <section aria-labelledby="alerts-heading">
            <GroupHeader
              icon={Bell}
              title="Alerts"
              count={watchesLoading ? null : watches.length}
              description="Active metric watches"
            />
            {watchesLoading ? (
              <GroupSkeleton rows={2} />
            ) : watches.length > 0 ? (
              <div className="space-y-3">
                {watches.map((w, i) => (
                  <ItemRow
                    key={w.id}
                    icon={Bell}
                    iconVariant="amber"
                    title={w.name || w.id}
                    subtitle={w.metric_id ? `Metric: ${w.metric_id}` : undefined}
                    chips={[{ label: 'active', variant: 'amber' }]}
                    href="/watches"
                    delay={i * 50}
                  />
                ))}
              </div>
            ) : (
              <EmptyGroup label="No active alerts" />
            )}
          </section>

          {/* (b) Flow runs — failed or running */}
          <section aria-labelledby="runs-heading">
            <GroupHeader
              icon={Workflow}
              title="Flow runs"
              count={runsLoading ? null : badRuns.length}
              description="Failed and in-progress runs"
            />
            {runsLoading ? (
              <GroupSkeleton rows={3} />
            ) : badRuns.length > 0 ? (
              <div className="space-y-3">
                {badRuns.map((r, i) => {
                  const isRunning = r.state === 'running'
                  const ts = r.started_at || r.created_at
                  return (
                    <ItemRow
                      key={r.id}
                      icon={isRunning ? Loader2 : XCircle}
                      iconVariant={isRunning ? 'blue' : 'red'}
                      title={r._flow_name || r.flow_id}
                      subtitle={r.error ? String(r.error).slice(0, 80) : undefined}
                      meta={ts ? relativeTime(new Date(ts)) : undefined}
                      chips={[
                        { label: r.state, variant: isRunning ? 'blue' : 'red' },
                        ...(r.trigger ? [{ label: r.trigger, variant: 'muted' }] : []),
                      ]}
                      href={`/flows/${r.flow_id}`}
                      delay={i * 50}
                    />
                  )
                })}
              </div>
            ) : (
              <EmptyGroup label="No failed or running flow runs" />
            )}
          </section>

          {/* (c) Stale datasets */}
          <section aria-labelledby="stale-heading">
            <GroupHeader
              icon={Database}
              title="Stale data"
              count={stalenessLoading ? null : staleDatasets.length}
              description="Datasets that haven't refreshed within their expected interval"
            />
            {stalenessLoading ? (
              <GroupSkeleton rows={2} />
            ) : staleDatasets.length > 0 ? (
              <div className="space-y-3">
                {staleDatasets.map((d, i) => {
                  const ts = d.last_success_at
                  return (
                    <ItemRow
                      key={d.dataset_key}
                      icon={AlertTriangle}
                      iconVariant="red"
                      title={d.dataset_key}
                      subtitle={d.expected_interval_s
                        ? `Expected refresh every ${Math.round(d.expected_interval_s / 60)} min`
                        : undefined}
                      meta={ts ? `Last seen ${relativeTime(new Date(ts))}` : 'Never refreshed'}
                      chips={[{ label: 'stale', variant: 'red' }]}
                      delay={i * 50}
                    />
                  )
                })}
              </div>
            ) : (
              <EmptyGroup label="All datasets are fresh" />
            )}
          </section>

          {/* (d) Schema drift */}
          <section aria-labelledby="drift-heading">
            <GroupHeader
              icon={GitBranch}
              title="Schema drift"
              count={driftLoading ? null : driftItems.length}
              description="Datasets with detected schema changes since last run"
            />
            {driftLoading ? (
              <GroupSkeleton rows={2} />
            ) : driftItems.length > 0 ? (
              <div className="space-y-3">
                {driftItems.map((d, i) => {
                  const changeCount = Array.isArray(d.changes) ? d.changes.length : 0
                  const ts = d.detected_at
                  return (
                    <ItemRow
                      key={d.dataset_key}
                      icon={GitBranch}
                      iconVariant="amber"
                      title={d.dataset_key}
                      subtitle={changeCount > 0 ? `${changeCount} column change${changeCount !== 1 ? 's' : ''} detected` : 'Schema change detected'}
                      meta={ts ? `Detected ${relativeTime(new Date(ts))}` : undefined}
                      chips={[{ label: 'drift', variant: 'amber' }]}
                      delay={i * 50}
                    />
                  )
                })}
              </div>
            ) : (
              <EmptyGroup label="No schema drift detected" />
            )}
          </section>
        </div>
      )}

      {/* ── Footer refresh hint ─────────────────────────────────────────────── */}
      {!allClear && (
        <div className="flex items-center justify-center py-8 mt-2">
          <button
            onClick={() => window.location.reload()}
            className="inline-flex items-center gap-2 text-xs text-muted hover:text-primary transition-colors
              focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded px-2 min-h-[44px]"
          >
            <RefreshCw size={12} />
            Refresh
          </button>
        </div>
      )}
    </PageRoot>
  )
}
