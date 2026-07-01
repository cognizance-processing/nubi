/**
 * OverviewPage — /overview
 *
 * Workspace executive overview: at-a-glance stats, data health panel,
 * recent activity (dashboards + flows) and quick links.
 *
 * APIs called:
 *   GET /connectors          — connector count
 *   GET /query/registry      — query count
 *   GET /boards              — dashboard count + recent boards
 *   GET /flows               — flow count + recent flows
 *   GET /health/score        — overall score + grade + reasons
 *   GET /health/freshness    — dataset freshness list (RAG dots)
 *
 * Graceful degradation: every fetch is wrapped so a 404 / empty payload
 * degrades to 0 counts or an empty list — the page never crashes.
 */

import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  LayoutDashboard,
  SearchCode,
  Plug,
  Workflow,
  Clock,
  ChevronRight,
  ExternalLink,
  Activity,
  Database,
  ShieldCheck,
  ArrowRight,
  Plus,
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

async function fetchJson(path) {
  try {
    return await api.get(path)
  } catch {
    return null
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

function mostRecent(list, n) {
  return [...list]
    .sort((a, b) => {
      const ta = new Date(a.updated_at || a.created_at || 0).getTime()
      const tb = new Date(b.updated_at || b.created_at || 0).getTime()
      return tb - ta
    })
    .slice(0, n)
}

// ─────────────────────────────────────────────────────────────────────────────
// Scoped animation styles
// ─────────────────────────────────────────────────────────────────────────────

const ANIMATION_STYLE = `
  @keyframes ov-shimmer { 0% { background-position:-400px 0 } 100% { background-position:400px 0 } }
  .ov-skeleton {
    background: linear-gradient(90deg, var(--surface-2,#eef2f7) 25%, var(--border,#e2e8f0) 50%, var(--surface-2,#eef2f7) 75%);
    background-size: 800px 100%; animation: ov-shimmer 1.4s ease-in-out infinite; border-radius:.5rem;
  }
  @keyframes ov-reveal { from { opacity:0; transform: translateY(8px) } to { opacity:1; transform:none } }
  .ov-reveal { opacity:0; animation: ov-reveal .45s cubic-bezier(.16,1,.3,1) forwards; }
  @media (prefers-reduced-motion: reduce) { .ov-reveal { animation: none; opacity:1 } }
`

// ─────────────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────────────

/** Stat card — mirrors exactly the StatCard pattern from HomePage. */
function StatCard({ icon: Icon, label, value, to, accent, delay }) {
  return (
    <Link
      to={to}
      style={{ animationDelay: `${delay}ms` }}
      className="ov-reveal group relative overflow-hidden flex flex-col gap-3 p-5 rounded-2xl border border-border
        bg-surface hover:border-primary/40 hover:shadow-md hover:shadow-primary/5 transition-all duration-200
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className={`absolute -top-8 -right-8 w-24 h-24 rounded-full blur-2xl opacity-[0.07] bg-gradient-to-br ${accent}`} />
      <div className="flex items-center justify-between">
        <div className={`flex items-center justify-center w-10 h-10 rounded-xl bg-gradient-to-br ${accent}`}>
          <Icon size={18} className="text-white" />
        </div>
        <ArrowRight size={15} className="text-muted opacity-0 group-hover:opacity-100 group-hover:translate-x-0.5 transition-all" />
      </div>
      <div>
        <div className="font-display font-semibold text-3xl text-fg tabular-nums leading-none">{value}</div>
        <div className="text-sm text-muted mt-1.5">{label}</div>
      </div>
    </Link>
  )
}

function StatCardSkeleton() {
  return (
    <div className="rounded-2xl border border-border bg-surface p-5 space-y-3">
      <div className="ov-skeleton h-10 w-10 rounded-xl" />
      <div className="ov-skeleton h-8 w-12" />
      <div className="ov-skeleton h-3 w-20" />
    </div>
  )
}

/** RAG (Red/Amber/Green) status dot for dataset freshness. */
function RagDot({ status }) {
  const map = {
    fresh:   'bg-emerald-500',
    amber:   'bg-amber-400',
    stale:   'bg-red-500',
    unknown: 'bg-border',
  }
  return <span className={`inline-block w-2 h-2 rounded-full shrink-0 ${map[status] ?? 'bg-border'}`} aria-label={status} />
}

/** Grade badge for the health score (A/B/C/D/F). */
function GradeBadge({ grade }) {
  const map = {
    A: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
    B: 'bg-teal-500/10 text-teal-600 dark:text-teal-400',
    C: 'bg-amber-500/10 text-amber-600 dark:text-amber-400',
    D: 'bg-orange-500/10 text-orange-600 dark:text-orange-400',
    F: 'bg-red-500/10 text-red-600 dark:text-red-400',
  }
  return (
    <span className={`inline-flex items-center justify-center w-10 h-10 rounded-xl font-display font-bold text-lg ${map[grade] ?? 'bg-surface-2 text-muted'}`}>
      {grade ?? '?'}
    </span>
  )
}

/** Small item row for Recent sections. */
function RecentItem({ icon: Icon, iconBg, title, meta, onClick, href, delay }) {
  const inner = (
    <div
      style={{ animationDelay: `${delay}ms` }}
      className="ov-reveal group flex items-center gap-3 p-4 rounded-xl border border-border bg-surface
        hover:border-primary/40 hover:shadow-md hover:shadow-primary/5 transition-all duration-200
        min-h-[44px] w-full text-left"
    >
      <div className={`flex items-center justify-center w-9 h-9 rounded-lg shrink-0 ${iconBg ?? 'bg-surface-2 group-hover:bg-primary/10 transition-colors'}`}>
        <Icon size={16} className={iconBg ? 'text-white' : 'text-muted group-hover:text-primary transition-colors'} />
      </div>
      <div className="flex-1 min-w-0">
        <p className="font-display font-medium text-sm text-fg truncate">{title}</p>
        {meta && (
          <p className="text-xs text-muted mt-0.5 flex items-center gap-1">
            <Clock size={10} />{meta}
          </p>
        )}
      </div>
      <ExternalLink size={13} className="text-muted opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
    </div>
  )
  const focusCls = "rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
  if (onClick) return <button onClick={onClick} className={`block w-full ${focusCls}`}>{inner}</button>
  if (href) return <Link to={href} className={`block ${focusCls}`}>{inner}</Link>
  return inner
}

function EmptyRow({ icon: Icon, label, cta, onClick }) {
  return (
    <div className="flex flex-col items-center justify-center text-center gap-3 py-8 px-6 rounded-xl border border-dashed border-border bg-surface-2/40">
      <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-surface-2">
        <Icon size={18} className="text-muted" />
      </div>
      <p className="text-sm text-muted">{label}</p>
      {cta && onClick && (
        <button
          onClick={onClick}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium font-display
            bg-surface border border-border text-fg hover:border-primary/50 hover:text-primary transition-colors
            focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Plus size={14} />{cta}
        </button>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Main page
// ─────────────────────────────────────────────────────────────────────────────

export default function OverviewPage() {
  const { activeOrg } = useOrg()
  const navigate = useNavigate()

  const [loading, setLoading] = useState(true)
  const [counts, setCounts] = useState({ connectors: 0, queries: 0, dashboards: 0, flows: 0 })
  const [recentBoards, setRecentBoards] = useState([])
  const [recentFlows, setRecentFlows] = useState([])

  const [healthLoading, setHealthLoading] = useState(true)
  const [healthScore, setHealthScore] = useState(null)
  const [freshness, setFreshness] = useState([])

  // Fetch entity counts + recent items
  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      const [connectors, queries, boardsList, flowsList] = await Promise.all([
        fetchList('/connectors'),
        fetchList('/query/registry'),
        fetchList('/boards'),
        fetchList('/flows'),
      ])
      if (cancelled) return
      setCounts({
        connectors: connectors.length,
        queries: queries.length,
        dashboards: boardsList.length,
        flows: flowsList.length,
      })
      setRecentBoards(mostRecent(boardsList, 4))
      setRecentFlows(mostRecent(flowsList, 3))
      setLoading(false)
    }
    load()
    return () => { cancelled = true }
  }, [activeOrg?.id])

  // Fetch health data — independent so a health 404 never blocks the stat row
  useEffect(() => {
    let cancelled = false
    async function loadHealth() {
      setHealthLoading(true)
      const [scoreData, freshnessRaw] = await Promise.all([
        fetchJson('/health/score'),
        fetchList('/health/freshness'),
      ])
      if (cancelled) return
      setHealthScore(scoreData)
      setFreshness(freshnessRaw)
      setHealthLoading(false)
    }
    loadHealth()
    return () => { cancelled = true }
  }, [activeOrg?.id])

  // Derive a single top-level score.
  // /health/score returns either {score, grade, reasons} (single dataset)
  // or {datasets: [...]} (all datasets). Average across datasets when multi.
  const topScore = (() => {
    if (!healthScore) return null
    if (healthScore.score != null) return healthScore
    if (Array.isArray(healthScore.datasets) && healthScore.datasets.length > 0) {
      const ds = healthScore.datasets
      return {
        score: Math.round(ds.reduce((s, d) => s + (d.score ?? 0), 0) / ds.length),
        grade: ds[0]?.grade ?? null,
        reasons: ds.flatMap((d) => d.reasons ?? []).slice(0, 4),
      }
    }
    return null
  })()

  const staleCount = freshness.filter((d) => d.status === 'stale').length
  const amberCount = freshness.filter((d) => d.status === 'amber').length
  const freshnessRows = freshness.slice(0, 8)

  return (
    <PageRoot>
      <style>{ANIMATION_STYLE}</style>

      <PageHeader
        title="Overview"
        subtitle="Workspace stats, data health, and recent activity at a glance."
      />

      {/* ── Stat row ───────────────────────────────────────────────────────── */}
      <section aria-label="Workspace counts" className="mt-6">
        {loading ? (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {[0, 1, 2, 3].map((i) => <StatCardSkeleton key={i} />)}
          </div>
        ) : (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <StatCard icon={LayoutDashboard} label="Dashboards" value={counts.dashboards} to="/dashboards" accent="from-brand-teal to-brand-cyan"  delay={0} />
            <StatCard icon={SearchCode}      label="Queries"    value={counts.queries}    to="/queries"    accent="from-brand-blue to-brand-teal"  delay={60} />
            <StatCard icon={Plug}            label="Connectors" value={counts.connectors} to="/connectors" accent="from-brand-navy to-brand-blue"  delay={120} />
            <StatCard icon={Workflow}        label="Flows"      value={counts.flows}      to="/flows"      accent="from-brand-blue to-brand-cyan"  delay={180} />
          </div>
        )}
      </section>

      {/* ── Data health ─────────────────────────────────────────────────────── */}
      <section aria-labelledby="health-heading" className="mt-8">
        <div className="flex items-center justify-between mb-4">
          <h2 id="health-heading" className="font-display font-semibold text-lg text-fg">Data health</h2>
          <Link
            to="/workqueue"
            className="text-xs text-muted hover:text-primary transition-colors inline-flex items-center gap-1
              rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            View issues <ChevronRight size={12} />
          </Link>
        </div>

        {healthLoading ? (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="rounded-2xl border border-border bg-surface p-5 space-y-4">
              <div className="ov-skeleton h-10 w-10 rounded-xl" />
              <div className="ov-skeleton h-6 w-16" />
              <div className="space-y-2">
                <div className="ov-skeleton h-3 w-full" />
                <div className="ov-skeleton h-3 w-3/4" />
              </div>
            </div>
            <div className="rounded-2xl border border-border bg-surface p-5 space-y-3">
              {[0, 1, 2, 3].map((i) => <div key={i} className="ov-skeleton h-8 w-full rounded-lg" />)}
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Score card */}
            <div className="ov-reveal rounded-2xl border border-border bg-surface p-5 flex flex-col gap-4">
              <div className="flex items-center gap-3">
                <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-primary/10">
                  <ShieldCheck size={20} className="text-primary" />
                </div>
                <div>
                  <p className="font-display font-semibold text-sm text-fg">Health score</p>
                  <p className="text-xs text-muted">Freshness · completeness · availability</p>
                </div>
              </div>

              {topScore ? (
                <>
                  <div className="flex items-end gap-3">
                    <GradeBadge grade={topScore.grade} />
                    <div>
                      <span className="font-display font-bold text-3xl text-fg tabular-nums leading-none">
                        {topScore.score != null ? topScore.score : '—'}
                      </span>
                      <span className="text-sm text-muted ml-1">/ 100</span>
                    </div>
                  </div>
                  {topScore.score != null && (
                    <div className="h-2 w-full bg-surface-2 rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full bg-gradient-to-r from-brand-blue to-brand-teal transition-all duration-700"
                        style={{ width: `${topScore.score}%` }}
                      />
                    </div>
                  )}
                  {Array.isArray(topScore.reasons) && topScore.reasons.length > 0 && (
                    <ul className="space-y-1">
                      {topScore.reasons.slice(0, 3).map((r, i) => (
                        <li key={i} className="text-xs text-muted flex items-start gap-1.5">
                          <Activity size={11} className="mt-0.5 shrink-0" />
                          {r}
                        </li>
                      ))}
                    </ul>
                  )}
                </>
              ) : (
                <div className="flex flex-col items-center justify-center py-6 text-center gap-2">
                  <Database size={22} className="text-muted" />
                  <p className="text-sm text-muted">No health data yet — run a flow to generate scores.</p>
                </div>
              )}

              {(staleCount > 0 || amberCount > 0) && (
                <div className="flex items-center gap-4 pt-1 border-t border-border">
                  {staleCount > 0 && (
                    <span className="flex items-center gap-1.5 text-xs text-red-600 dark:text-red-400 font-medium">
                      <RagDot status="stale" /> {staleCount} stale
                    </span>
                  )}
                  {amberCount > 0 && (
                    <span className="flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-400 font-medium">
                      <RagDot status="amber" /> {amberCount} amber
                    </span>
                  )}
                </div>
              )}
            </div>

            {/* Freshness list */}
            <div className="ov-reveal rounded-2xl border border-border bg-surface p-5" style={{ animationDelay: '80ms' }}>
              <div className="flex items-center justify-between mb-3">
                <p className="text-xs font-medium text-muted uppercase tracking-wider">Dataset freshness</p>
                {freshness.length > 8 && (
                  <span className="text-xs text-muted">+{freshness.length - 8} more</span>
                )}
              </div>
              {freshnessRows.length > 0 ? (
                <ul>
                  {freshnessRows.map((d) => (
                    <li key={d.dataset_key} className="flex items-center gap-3 py-2.5 border-b border-border last:border-0">
                      <RagDot status={d.status} />
                      <span className="flex-1 font-mono text-xs text-fg truncate">{d.dataset_key}</span>
                      <span className={[
                        'text-xs font-medium px-2 py-0.5 rounded-full shrink-0',
                        d.status === 'fresh'
                          ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                          : d.status === 'stale'
                          ? 'bg-red-500/10 text-red-600 dark:text-red-400'
                          : d.status === 'amber'
                          ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
                          : 'bg-surface-2 text-muted',
                      ].join(' ')}>
                        {d.status}
                      </span>
                      {d.last_success_at && (
                        <span className="text-xs text-muted shrink-0 hidden sm:block">
                          {relativeTime(new Date(d.last_success_at))}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="flex flex-col items-center justify-center py-6 text-center gap-2">
                  <Database size={20} className="text-muted" />
                  <p className="text-sm text-muted">No datasets tracked yet.</p>
                </div>
              )}
            </div>
          </div>
        )}
      </section>

      {/* ── Recent activity ──────────────────────────────────────────────────── */}
      <section aria-labelledby="recent-heading" className="mt-8">
        <h2 id="recent-heading" className="font-display font-semibold text-lg text-fg mb-4">Recent</h2>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

          {/* Recent dashboards */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <p className="text-xs font-medium text-muted uppercase tracking-wider">Dashboards</p>
              <Link to="/dashboards" className="text-xs text-muted hover:text-primary transition-colors inline-flex items-center gap-1
                rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                View all <ChevronRight size={12} />
              </Link>
            </div>
            {loading ? (
              <div className="space-y-3">
                {[0, 1].map((i) => <div key={i} className="ov-skeleton h-[68px] w-full rounded-xl" />)}
              </div>
            ) : recentBoards.length > 0 ? (
              <div className="space-y-3">
                {recentBoards.map((b, i) => (
                  <RecentItem
                    key={b.id}
                    icon={LayoutDashboard}
                    iconBg="bg-brand-gradient"
                    title={b.name || 'Untitled board'}
                    meta={b.updated_at || b.created_at ? relativeTime(new Date(b.updated_at || b.created_at)) : null}
                    onClick={() => navigate(`/d/${b.id}`)}
                    delay={i * 60}
                  />
                ))}
              </div>
            ) : (
              <EmptyRow icon={LayoutDashboard} label="No dashboards yet" cta="Create one" onClick={() => navigate('/editor')} />
            )}
          </div>

          {/* Recent flows */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <p className="text-xs font-medium text-muted uppercase tracking-wider">Flows</p>
              <Link to="/flows" className="text-xs text-muted hover:text-primary transition-colors inline-flex items-center gap-1
                rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                View all <ChevronRight size={12} />
              </Link>
            </div>
            {loading ? (
              <div className="space-y-3">
                {[0, 1].map((i) => <div key={i} className="ov-skeleton h-[68px] w-full rounded-xl" />)}
              </div>
            ) : recentFlows.length > 0 ? (
              <div className="space-y-3">
                {recentFlows.map((f, i) => (
                  <RecentItem
                    key={f.id}
                    icon={Workflow}
                    title={f.name || f.title || 'Untitled flow'}
                    meta={f.updated_at || f.created_at ? relativeTime(new Date(f.updated_at || f.created_at)) : null}
                    href={`/flows/${f.id}`}
                    delay={i * 60}
                  />
                ))}
              </div>
            ) : (
              <EmptyRow icon={Workflow} label="No flows yet" cta="Build a flow" onClick={() => navigate('/flows')} />
            )}
          </div>
        </div>
      </section>

      {/* ── Quick links ──────────────────────────────────────────────────────── */}
      <section aria-labelledby="quicklinks-heading" className="mt-8 pb-8">
        <h2 id="quicklinks-heading" className="font-display font-semibold text-lg text-fg mb-4">Quick links</h2>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { icon: Plug,            label: 'Connectors', to: '/connectors', accent: 'from-brand-navy to-brand-blue' },
            { icon: SearchCode,      label: 'Queries',    to: '/queries',    accent: 'from-brand-blue to-brand-teal' },
            { icon: LayoutDashboard, label: 'Dashboards', to: '/dashboards', accent: 'from-brand-teal to-brand-cyan' },
            { icon: Workflow,        label: 'Flows',      to: '/flows',      accent: 'from-brand-blue to-brand-cyan' },
          ].map((item, i) => {
            const Icon = item.icon
            return (
              <Link
                key={item.label}
                to={item.to}
                style={{ animationDelay: `${i * 60}ms` }}
                className="ov-reveal group flex items-center gap-3 p-4 rounded-xl border border-border bg-surface
                  hover:border-primary/40 hover:shadow-md hover:shadow-primary/5 transition-all duration-200
                  focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring min-h-[44px]"
              >
                <div className={`flex items-center justify-center w-9 h-9 rounded-lg bg-gradient-to-br ${item.accent} shrink-0`}>
                  <Icon size={16} className="text-white" />
                </div>
                <span className="font-display font-medium text-sm text-fg">{item.label}</span>
                <ChevronRight size={13} className="ml-auto text-muted opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
              </Link>
            )
          })}
        </div>
      </section>
    </PageRoot>
  )
}
