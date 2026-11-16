/**
 * HomePage — the workspace launchpad. Search first, then what to do, then what changed.
 *
 *   header      date, greeting, workspace
 *   search      one wide field that opens Spotlight (⌘K), under it the quick actions
 *               (one ink primary, the rest hairline)
 *
 *   NEW WORKSPACE (core setup incomplete and not skipped)
 *     01 Getting started — a four-step checklist (connect data, write a query, build a board,
 *        invite) with live completion, a progress rule and the onboarding figure.
 *     02 Then explore — the rest of the product, as ruled rows.
 *
 *   ESTABLISHED WORKSPACE (setup complete or skipped)
 *     counts       dashboards / queries / connectors / flows
 *     Needs attention — a single "All clear" line with a status dot, or ruled issue rows
 *     Recent dashboards (BoardMiniature cards), Recent queries, Recent flows
 *     Data health — collapses to a one-line setup strip until a score or freshness data exists
 *
 * Completion is derived from list endpoints (each "done" when >= 1 item):
 *     /connectors, /query/registry, /boards, /flows, and the org's members (invite step).
 * Needs-attention / data-health endpoints:
 *     /watches, /flows/{id}/runs, /health/score, /health/freshness, /health/drift
 * Errors / empty arrays degrade to a count of 0 / an empty list — no single 404 blocks the page.
 *
 * The "skip setup" choice persists in localStorage, keyed by the active org.
 *
 * Presentation only: every fetch, derivation and storage key below predates the Swiss pass.
 */

import { useEffect, useState, useMemo, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  Database, SearchCode, LayoutDashboard, ArrowRight, Sparkles, Workflow, CalendarClock, GitBranch, Plug,
  Plus, Bell, XCircle, Loader2, Search, Check, UserPlus, FileCode2, ChevronRight,
} from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext.jsx'
import { useOrg } from '../../contexts/OrgContext.jsx'
import { useUi } from '../../contexts/UiContext.jsx'
import * as api from '../../lib/api.js'
import { listMembers } from '../../lib/members.js'
import { openSpotlight } from '../../components/app/Spotlight.jsx'
import BoardMiniature from '../../components/app/BoardMiniature.jsx'
import { buildMiniature } from '../../dashboards/specMiniature.js'
import PageShell, { PageSection } from '../../components/app/PageShell.jsx'
import Button from '../../components/ui/Button.jsx'
import Tag from '../../components/ui/Tag.jsx'
import OnboardingSteps from '../../components/illustrations/OnboardingSteps.jsx'
import { SPOTLIGHT_KEY } from '../../shell/nav.js'

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function getGreeting() {
  const h = new Date().getHours()
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}

function firstName(name?: string | null) {
  if (!name) return null
  return name.split(' ')[0]
}

/**
 * Fetch a list from an endpoint, normalizing common envelope shapes to an array.
 * Returns [] on any error so the caller never has to guard.
 */
async function fetchList(path: string): Promise<any[]> {
  try {
    const data: any = await api.get(path)
    if (Array.isArray(data)) return data
    for (const key of ['items', 'data', 'boards', 'queries', 'connectors', 'flows', 'results', 'datasets', 'watches']) {
      if (Array.isArray(data?.[key])) return data[key]
    }
    return []
  } catch {
    return []
  }
}

/** Fetch a single JSON object, returning null on any error. */
async function fetchJson(path: string): Promise<any | null> {
  try {
    return await api.get(path)
  } catch {
    return null
  }
}

function relativeTime(date: Date) {
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

function mostRecent(list: any[], n: number) {
  return [...list]
    .sort((a, b) => {
      const ta = new Date(a.updated_at || a.created_at || 0).getTime()
      const tb = new Date(b.updated_at || b.created_at || 0).getTime()
      return tb - ta
    })
    .slice(0, n)
}

const skipKey = (orgId?: string) => `nubi:home:setupSkipped:${orgId || 'default'}`

// ─────────────────────────────────────────────────────────────────────────────
// Static config
// ─────────────────────────────────────────────────────────────────────────────

type Step = { id: string; icon: any; title: string; description: string; cta: string; href: string; optional?: boolean }

const STEPS: Step[] = [
  { id: 'connect', icon: Database, title: 'Connect your data', description: 'Link a database or warehouse. Queries run against it directly and credentials stay encrypted.', cta: 'Add connector', href: '/connectors' },
  { id: 'query', icon: SearchCode, title: 'Write a query', description: 'Write SQL, or ask the assistant to explore, then save it so boards can reuse it.', cta: 'Open queries', href: '/queries' },
  { id: 'dashboard', icon: LayoutDashboard, title: 'Build a board', description: 'Place charts on a grid, wire filters, and publish a live dashboard.', cta: 'New dashboard', href: '/editor' },
  { id: 'invite', icon: UserPlus, title: 'Invite your team', description: 'Viewers are free. Add the people who need to look at the numbers.', cta: 'Invite people', href: '/settings/members', optional: true },
]
const CORE_COUNT = 3

const EXPLORE = [
  { icon: Workflow, title: 'Automate with flows', description: 'Chain queries, transforms and exports into pipelines.', href: '/flows' },
  { icon: CalendarClock, title: 'Schedule and monitor', description: 'Put flows on a cron schedule and watch their runs.', href: '/flows' },
  { icon: GitBranch, title: 'Version control', description: 'Sync this project to Git to track and review changes.', href: '/settings/project' },
]

// ─────────────────────────────────────────────────────────────────────────────
// Small pieces
// ─────────────────────────────────────────────────────────────────────────────

function Stat({ icon: Icon, label, value, to }: { icon: any; label: string; value: number; to: string }) {
  return (
    <Link to={to} className="group flex flex-col gap-2 pt-3 pb-1 border-t border-[var(--ink)] rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
      <div className="t-label flex items-center justify-between">
        <span className="inline-flex items-center gap-1.5"><Icon size={14} strokeWidth={1.5} aria-hidden="true" />{label}</span>
        <ArrowRight size={14} strokeWidth={1.5} aria-hidden="true" className="opacity-0 group-hover:opacity-100 text-fg" />
      </div>
      <div className="font-mono text-[2.25rem] font-normal tracking-[-0.03em] text-fg tabular-nums leading-none">{value}</div>
    </Link>
  )
}

function StatusTag({ tone = 'neutral', children }: { tone?: 'neutral' | 'danger' | 'warning' | 'success'; children: ReactNode }) {
  return <Tag variant={tone} dot={tone !== 'neutral'}>{children}</Tag>
}

function IssueRow({ icon: Icon, tone, title, subtitle, chip, href }: {
  icon: any; tone: 'danger' | 'warning' | 'neutral'; title: string; subtitle?: string; chip: string; href: string
}) {
  return (
    <Link to={href} className="sh-row group min-h-[52px]">
      <Icon size={16} strokeWidth={1.5} aria-hidden="true" className="text-subtle shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-[13.5px] font-medium text-fg truncate m-0">{title}</p>
        {subtitle && <p className="text-xs text-muted truncate m-0 mt-0.5">{subtitle}</p>}
      </div>
      <StatusTag tone={tone}>{chip}</StatusTag>
      <ChevronRight size={14} strokeWidth={1.5} aria-hidden="true" className="text-subtle opacity-0 group-hover:opacity-100 shrink-0" />
    </Link>
  )
}

function BoardCard({ board }: { board: any }) {
  const updatedAt = board.updated_at || board.created_at
  const spec = board.config?.spec
  const hasLayout = !board.config?.html && buildMiniature(spec) !== null
  return (
    <Link to={`/d/${board.id}`} className="sh-card group flex flex-col">
      <div className="relative aspect-video bg-surface-2 border-b border-border overflow-hidden p-2 text-fg">
        {hasLayout ? (
          <BoardMiniature spec={spec} className="w-full h-full" />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-subtle">
            <LayoutDashboard size={22} strokeWidth={1.5} aria-hidden="true" />
          </div>
        )}
      </div>
      <div className="px-3 py-2.5 min-w-0">
        <p className="text-[13.5px] font-semibold text-fg truncate m-0">{board.name || 'Untitled dashboard'}</p>
        {updatedAt && <p className="font-mono text-[11px] text-subtle tabular-nums m-0 mt-1">{relativeTime(new Date(updatedAt))}</p>}
      </div>
    </Link>
  )
}

function RecentRow({ to, icon: Icon, name, meta }: { to: string; icon: any; name: string; meta?: string }) {
  return (
    <Link to={to} className="sh-row group">
      <Icon size={16} strokeWidth={1.5} aria-hidden="true" className="text-subtle shrink-0" />
      <span className="flex-1 min-w-0 truncate">{name}</span>
      {meta && <span className="font-mono text-[11px] text-subtle tabular-nums shrink-0">{meta}</span>}
    </Link>
  )
}

function EmptyLine({ children, to, cta }: { children: ReactNode; to: string; cta: string }) {
  return (
    <div className="flex items-center gap-3 min-h-[52px] px-2 border-y border-border text-[13px] text-muted">
      <span className="flex-1">{children}</span>
      <Link to={to} className="inline-flex items-center gap-1.5 text-fg font-medium rounded-sm hover:underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <Plus size={14} strokeWidth={1.5} aria-hidden="true" />{cta}
      </Link>
    </div>
  )
}

function SkeletonRows({ n = 3, h = 'h-10' }: { n?: number; h?: string }) {
  return (
    <div className="space-y-2" aria-hidden="true">
      {Array.from({ length: n }, (_, i) => <div key={i} className={`nubi-shimmer ${h} w-full`} />)}
    </div>
  )
}

const RAG_TONE: Record<string, 'success' | 'warning' | 'danger' | undefined> = { fresh: 'success', amber: 'warning', stale: 'danger' }

// ─────────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────────

export default function HomePage() {
  const { user } = useAuth()
  const { activeOrg, loading: orgLoading } = useOrg()
  const { openChat } = useUi()

  const [loading, setLoading] = useState(true)
  const [counts, setCounts] = useState({ connectors: 0, queries: 0, dashboards: 0, flows: 0, members: 0 })
  const [boards, setBoards] = useState<any[]>([])
  const [recentQueries, setRecentQueries] = useState<any[]>([])
  const [flows, setFlows] = useState<any[]>([])

  // Data health (score + freshness)
  const [healthLoading, setHealthLoading] = useState(true)
  const [healthScore, setHealthScore] = useState<any>(null)
  const [freshness, setFreshness] = useState<any[]>([])

  // Needs attention (alerts + failed/running runs + drift). "Stale data" derives from `freshness`.
  const [attentionLoading, setAttentionLoading] = useState(true)
  const [watches, setWatches] = useState<any[]>([])
  const [badRuns, setBadRuns] = useState<any[]>([])
  const [driftItems, setDriftItems] = useState<any[]>([])

  // The per-org "skip setup" flag lives in localStorage; read it via useMemo keyed by org + a bump counter.
  const [skipBump, setSkipBump] = useState(0)
  const skipped = useMemo(() => {
    try { return localStorage.getItem(skipKey(activeOrg?.id)) === '1' } catch { return false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeOrg?.id, skipBump])

  // Fetch all entity lists in parallel on mount.
  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      const orgId = activeOrg?.id
      const [connectors, queries, boardsList, flowsList, members] = await Promise.all([
        fetchList('/connectors'),
        fetchList('/query/registry'),
        fetchList('/boards'),
        fetchList('/flows'),
        orgId && orgId !== 'personal' ? listMembers(orgId).catch(() => []) : Promise.resolve([]),
      ])
      if (cancelled) return
      setCounts({
        connectors: connectors.length,
        queries: queries.length,
        dashboards: boardsList.length,
        flows: flowsList.length,
        members: Array.isArray(members) ? members.length : 0,
      })
      setBoards(mostRecent(boardsList, 4))
      setRecentQueries(mostRecent(queries, 5))
      setFlows(mostRecent(flowsList, 5))
      setLoading(false)
    }
    load()
    return () => { cancelled = true }
  }, [activeOrg?.id])

  // Data health — independent so a health 404 never blocks the counts.
  useEffect(() => {
    let cancelled = false
    async function loadHealth() {
      setHealthLoading(true)
      const [scoreData, freshnessRaw] = await Promise.all([fetchJson('/health/score'), fetchList('/health/freshness')])
      if (cancelled) return
      setHealthScore(scoreData)
      setFreshness(freshnessRaw)
      setHealthLoading(false)
    }
    loadHealth()
    return () => { cancelled = true }
  }, [activeOrg?.id])

  // Needs attention — active watches + recent failed/running flow runs + drift.
  useEffect(() => {
    let cancelled = false
    async function loadAttention() {
      setAttentionLoading(true)
      const [watchList, flowsList, drift] = await Promise.all([fetchList('/watches'), fetchList('/flows'), fetchList('/health/drift')])
      if (cancelled) return
      // The list endpoint exposes no "fired" flag, so every enabled watch is a potential alert.
      setWatches(watchList.filter((w) => w.config?.enabled !== false))
      setDriftItems(drift)

      // Recent runs for up to 10 flows in parallel (capped to avoid hammering).
      const runLists = await Promise.all(
        flowsList.slice(0, 10).map((f) =>
          fetchList(`/flows/${f.id}/runs?limit=5`).then((runs) =>
            runs.map((r) => ({ ...r, _flow_name: f.name || f.title || f.id })),
          ),
        ),
      )
      if (cancelled) return
      setBadRuns(
        runLists
          .flat()
          .filter((r) => r.state === 'failed' || r.state === 'running')
          .sort((a, b) => Number(new Date(b.created_at || 0)) - Number(new Date(a.created_at || 0)))
          .slice(0, 20),
      )
      setAttentionLoading(false)
    }
    loadAttention()
    return () => { cancelled = true }
  }, [activeOrg?.id])

  // Checklist completion. The first three are the core spine; inviting is optional.
  const stepDone = useMemo(
    () => [counts.connectors > 0, counts.queries > 0, counts.dashboards > 0, counts.members > 1],
    [counts],
  )
  const coreDone = stepDone.slice(0, CORE_COUNT).filter(Boolean).length
  const setupComplete = coreDone === CORE_COUNT
  const doneCount = stepDone.filter(Boolean).length
  const currentStep = stepDone.findIndex((d) => !d)
  const showGeneral = setupComplete || skipped

  // /health/score returns {score, grade, reasons} (one dataset) or {datasets: [...]} (all).
  const topScore = useMemo(() => {
    if (!healthScore) return null
    if (healthScore.score != null) return healthScore
    if (Array.isArray(healthScore.datasets) && healthScore.datasets.length > 0) {
      const ds = healthScore.datasets
      return {
        score: Math.round(ds.reduce((s: number, d: any) => s + (d.score ?? 0), 0) / ds.length),
        grade: ds[0]?.grade ?? null,
        reasons: ds.flatMap((d: any) => d.reasons ?? []).slice(0, 4),
      }
    }
    return null
  }, [healthScore])

  const staleDatasets = useMemo(() => freshness.filter((d) => d.status === 'stale'), [freshness])
  const amberDatasets = useMemo(() => freshness.filter((d) => d.status === 'amber'), [freshness])
  const totalIssues = watches.length + badRuns.length + staleDatasets.length + driftItems.length
  const attentionReady = !attentionLoading && !healthLoading
  const allClear = attentionReady && totalIssues === 0
  const hasHealthData = Boolean(topScore) || freshness.length > 0

  const skip = () => {
    try { localStorage.setItem(skipKey(activeOrg?.id), '1') } catch { /* ignore */ }
    setSkipBump((n) => n + 1)
  }
  const resume = () => {
    try { localStorage.removeItem(skipKey(activeOrg?.id)) } catch { /* ignore */ }
    setSkipBump((n) => n + 1)
  }

  const displayName = firstName(user?.name) || user?.email?.split('@')[0] || 'there'
  const orgName = activeOrg?.name
  const today = new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })

  let idx = 0
  const nextIndex = () => String(++idx).padStart(2, '0')

  return (
    <PageShell
      eyebrow={today}
      title={`${getGreeting()}, ${displayName}`}
      description={orgName && !orgLoading ? <>Workspace <strong className="font-semibold text-fg">{orgName}</strong></> : undefined}
    >
      {/* ── Search first, then the quick actions ─────────────────────────── */}
      <div className="space-y-4 mb-10">
        <button type="button" onClick={() => openSpotlight()} className="sh-search-bar" aria-label={`Search everything (${SPOTLIGHT_KEY})`}>
          <Search size={18} strokeWidth={1.5} aria-hidden="true" className="shrink-0 text-primary" />
          <span className="flex-1 min-w-0 truncate text-[16px]">Search dashboards, queries, connectors and settings</span>
          <kbd className="nubi-kbd hidden sm:inline-flex" aria-hidden="true">{SPOTLIGHT_KEY}</kbd>
        </button>

        <div className="flex flex-wrap gap-2" role="group" aria-label="Quick actions">
          <Button asChild size="sm">
            <Link to="/editor"><Plus size={14} strokeWidth={1.5} aria-hidden="true" /> New dashboard</Link>
          </Button>
          <Button asChild size="sm" variant="secondary">
            <Link to="/queries"><FileCode2 size={14} strokeWidth={1.5} aria-hidden="true" /> Write a query</Link>
          </Button>
          <Button asChild size="sm" variant="secondary">
            <Link to="/connectors"><Plug size={14} strokeWidth={1.5} aria-hidden="true" /> Connect data</Link>
          </Button>
          <Button size="sm" variant="secondary" onClick={openChat}>
            <Sparkles size={14} strokeWidth={1.5} aria-hidden="true" /> Ask AI
          </Button>
        </div>
      </div>

      {!showGeneral ? (
        /* ── NEW WORKSPACE ─────────────────────────────────────────────── */
        <>
          <PageSection
            id="getting-started"
            index={nextIndex()}
            title="Getting started"
            description={loading ? 'Checking your workspace' : `${doneCount} of ${STEPS.length} done. Three steps to a live dashboard.`}
            actions={
              <div className="flex items-center gap-4">
                {!loading && (
                  <div className="sh-progress" role="img" aria-label={`${doneCount} of ${STEPS.length} steps complete`}>
                    {STEPS.map((s, i) => <i key={s.id} data-done={stepDone[i] || undefined} />)}
                  </div>
                )}
                <button type="button" onClick={skip} className="text-xs font-medium text-muted hover:text-fg inline-flex items-center gap-1 rounded-sm px-1 py-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  Skip setup <ArrowRight size={12} strokeWidth={1.5} aria-hidden="true" />
                </button>
              </div>
            }
          >
            <div className="grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-x-12 gap-y-6 items-start">
              {loading ? (
                <SkeletonRows n={4} h="h-[64px]" />
              ) : (
                <ol className="m-0 p-0 list-none border-t border-border">
                  {STEPS.map((step, i) => {
                    const done = stepDone[i]
                    const current = i === currentStep
                    const Icon = step.icon
                    return (
                      <li key={step.id} className="flex items-start gap-4 py-4 border-b border-border">
                        <span
                          className={['mt-0.5 w-5 h-5 shrink-0 border flex items-center justify-center', done ? 'bg-[var(--ink)] border-[var(--ink)] text-[var(--ink-fg)]' : current ? 'border-[var(--ink)] text-fg' : 'border-[var(--frame-line)] text-subtle'].join(' ')}
                          aria-hidden="true"
                        >
                          {done ? <Check size={13} strokeWidth={2} /> : <span className="font-mono text-[11px] tabular-nums">{i + 1}</span>}
                        </span>
                        <div className="flex-1 min-w-0">
                          <h3 className="m-0 text-[14px] font-semibold text-fg flex items-center gap-2 flex-wrap">
                            <Icon size={15} strokeWidth={1.5} aria-hidden="true" className="text-subtle" />
                            {step.title}
                            {step.optional && <Tag>Optional</Tag>}
                            {done && <span className="sr-only">(done)</span>}
                          </h3>
                          <p className="m-0 mt-1 text-[13px] leading-[1.45] text-muted max-w-[56ch]">{step.description}</p>
                        </div>
                        <Button
                          asChild
                          size="sm"
                          variant={current && !done ? 'primary' : 'secondary'}
                          className="shrink-0"
                        >
                          <Link to={step.href} aria-label={done ? `${step.title}: revisit` : `${step.title}: ${step.cta}`}>
                            {done ? 'Revisit' : step.cta}
                            {!done && <ArrowRight size={14} strokeWidth={1.5} aria-hidden="true" />}
                          </Link>
                        </Button>
                      </li>
                    )
                  })}
                </ol>
              )}
              <OnboardingSteps compact step={(currentStep >= 0 && currentStep < 3 ? currentStep + 1 : 0) as 0 | 1 | 2 | 3} className="hidden lg:block w-full h-auto text-fg" />
            </div>
          </PageSection>

          <PageSection id="explore" index={nextIndex()} title="Then explore" description="Once data is in, automate and ship with the rest of Nubi.">
            <div className="border-t border-border">
              {EXPLORE.map((f) => (
                <Link key={f.title} to={f.href} className="sh-row group min-h-[52px]">
                  <f.icon size={16} strokeWidth={1.5} aria-hidden="true" className="text-subtle shrink-0" />
                  <span className="font-medium shrink-0">{f.title}</span>
                  <span className="flex-1 min-w-0 truncate text-[13px] text-muted hidden sm:block">{f.description}</span>
                  <ChevronRight size={14} strokeWidth={1.5} aria-hidden="true" className="text-subtle ml-auto shrink-0 opacity-0 group-hover:opacity-100" />
                </Link>
              ))}
            </div>
          </PageSection>
        </>
      ) : (
        /* ── ESTABLISHED WORKSPACE ─────────────────────────────────────── */
        <>
          {!setupComplete && (
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-8 pl-4 py-3 border-l-2 border-[var(--primary)] bg-surface-2">
              <div>
                <p className="m-0 text-[13.5px] font-semibold text-fg">Finish setting up your workspace</p>
                <p className="m-0 mt-0.5 text-xs text-muted">{coreDone} of {CORE_COUNT} core steps done: connect a source, write a query, build a board.</p>
              </div>
              <Button size="sm" variant="secondary" onClick={resume} className="mr-3 self-start sm:self-auto">
                Resume setup <ArrowRight size={14} strokeWidth={1.5} aria-hidden="true" />
              </Button>
            </div>
          )}

          {/* Counts */}
          <section aria-label="Workspace overview" className="mb-10">
            {loading ? (
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-x-6 gap-y-4" aria-hidden="true">
                {[0, 1, 2, 3].map((i) => <div key={i} className="nubi-shimmer h-[72px]" />)}
              </div>
            ) : (
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-x-6 gap-y-4">
                <Stat icon={LayoutDashboard} label="Dashboards" value={counts.dashboards} to="/dashboards" />
                <Stat icon={SearchCode} label="Queries" value={counts.queries} to="/queries" />
                <Stat icon={Plug} label="Connectors" value={counts.connectors} to="/connectors" />
                <Stat icon={Workflow} label="Flows" value={counts.flows} to="/flows" />
              </div>
            )}
          </section>

          {/* Needs attention */}
          <PageSection
            id="attention"
            index={nextIndex()}
            title="Needs attention"
            actions={attentionReady && totalIssues > 0 ? <span className="font-mono text-xs text-subtle tabular-nums">{totalIssues} item{totalIssues !== 1 ? 's' : ''}</span> : undefined}
          >
            {!attentionReady ? (
              <SkeletonRows n={1} h="h-[44px]" />
            ) : allClear ? (
              <p className="m-0 flex items-center gap-2.5 min-h-[44px] px-2 border-y border-border text-[13.5px] text-fg" role="status">
                <span className="nubi-dot nubi-dot-success" aria-hidden="true" />
                <span className="font-semibold">All clear.</span>
                <span className="text-muted">Nothing needs attention right now.</span>
              </p>
            ) : (
              <div className="sh-rows">
                {watches.slice(0, 3).map((w, i) => (
                  <IssueRow key={`watch-${w.id}-${i}`} icon={Bell} tone="warning" title={w.name || w.id} subtitle={w.metric_id ? `Metric: ${w.metric_id}` : 'Active alert'} chip="alert" href="/watches" />
                ))}
                {badRuns.slice(0, 3).map((r, i) => {
                  const running = r.state === 'running'
                  return (
                    <IssueRow
                      key={`run-${r.id}-${i}`}
                      icon={running ? Loader2 : XCircle}
                      tone={running ? 'neutral' : 'danger'}
                      title={r._flow_name || r.flow_id}
                      subtitle={r.error ? String(r.error).slice(0, 80) : running ? 'Run in progress' : 'Run failed'}
                      chip={r.state}
                      href={`/flows/${r.flow_id}`}
                    />
                  )
                })}
                {staleDatasets.slice(0, 3).map((d, i) => (
                  <IssueRow
                    key={`stale-${d.dataset_key}-${i}`} icon={Database} tone="danger" title={d.dataset_key}
                    subtitle={d.last_success_at ? `Last refreshed ${relativeTime(new Date(d.last_success_at))}` : 'Never refreshed'}
                    chip="stale" href="/connectors"
                  />
                ))}
                {driftItems.slice(0, 3).map((d, i) => (
                  <IssueRow key={`drift-${d.dataset_key}-${i}`} icon={GitBranch} tone="warning" title={d.dataset_key} subtitle="Schema change detected since last run" chip="drift" href="/connectors" />
                ))}
                {totalIssues > 12 && <p className="text-xs text-muted text-center pt-3 m-0">+{totalIssues - 12} more</p>}
              </div>
            )}
          </PageSection>

          {/* Recent dashboards */}
          <PageSection
            id="recent-dashboards"
            index={nextIndex()}
            title="Recent dashboards"
            actions={<Link to="/dashboards" className="text-xs text-muted hover:text-fg inline-flex items-center gap-1 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">View all <ChevronRight size={12} strokeWidth={1.5} aria-hidden="true" /></Link>}
          >
            {loading ? (
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-4" aria-hidden="true">{[0, 1, 2, 3].map((i) => <div key={i} className="nubi-shimmer aspect-video" />)}</div>
            ) : boards.length > 0 ? (
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
                {boards.map((b, i) => <BoardCard key={`${b.id}-${i}`} board={b} />)}
              </div>
            ) : (
              <EmptyLine to="/editor" cta="Create one">No dashboards yet.</EmptyLine>
            )}
          </PageSection>

          {/* Recent queries + flows */}
          <div className="sh-sections-grid grid grid-cols-1 lg:grid-cols-2 gap-x-12 gap-y-8">
            <PageSection
              id="recent-queries"
              index={nextIndex()}
              title="Recent queries"
              actions={<Link to="/queries" className="text-xs text-muted hover:text-fg inline-flex items-center gap-1 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">View all <ChevronRight size={12} strokeWidth={1.5} aria-hidden="true" /></Link>}
            >
              {loading ? <SkeletonRows /> : recentQueries.length > 0 ? (
                <div className="sh-rows">
                  {recentQueries.map((q, i) => {
                    const t = q.updated_at || q.created_at
                    return <RecentRow key={`${q.id}-${i}`} to={`/queries/${encodeURIComponent(q.id)}`} icon={FileCode2} name={q.name || q.id} meta={t ? relativeTime(new Date(t)) : undefined} />
                  })}
                </div>
              ) : (
                <EmptyLine to="/queries" cta="Write one">No queries yet.</EmptyLine>
              )}
            </PageSection>

            <PageSection
              id="recent-flows"
              index={nextIndex()}
              title="Recent flows"
              actions={<Link to="/flows" className="text-xs text-muted hover:text-fg inline-flex items-center gap-1 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">View all <ChevronRight size={12} strokeWidth={1.5} aria-hidden="true" /></Link>}
            >
              {loading ? <SkeletonRows /> : flows.length > 0 ? (
                <div className="sh-rows">
                  {flows.map((f, i) => {
                    const t = f.updated_at || f.created_at
                    return <RecentRow key={`${f.id}-${i}`} to={`/flows/${f.id}`} icon={Workflow} name={f.name || f.title || 'Untitled flow'} meta={t ? relativeTime(new Date(t)) : undefined} />
                  })}
                </div>
              ) : (
                <EmptyLine to="/flows" cta="Build one">No flows yet.</EmptyLine>
              )}
            </PageSection>
          </div>

          {/* Data health: a one-line setup strip until there is something to report */}
          <PageSection id="health" index={nextIndex()} title="Data health">
            {healthLoading ? (
              <SkeletonRows n={1} h="h-[44px]" />
            ) : !hasHealthData ? (
              <div className="flex items-center gap-3 min-h-[44px] px-2 border-y border-border text-[13px] text-muted">
                <span className="nubi-dot" aria-hidden="true" />
                <span className="flex-1">Freshness and health scores start reporting after your first flow run.</span>
                <Link to="/flows" className="inline-flex items-center gap-1.5 text-fg font-medium rounded-sm hover:underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  Open flows <ArrowRight size={14} strokeWidth={1.5} aria-hidden="true" />
                </Link>
              </div>
            ) : (
              <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-x-12 gap-y-8">
                <div>
                  <p className="t-label m-0">Health score</p>
                  {topScore ? (
                    <>
                      <p className="m-0 mt-2 flex items-baseline gap-2">
                        <span className="font-mono text-[2.25rem] leading-none tracking-[-0.03em] text-fg tabular-nums">{topScore.score ?? '—'}</span>
                        <span className="font-mono text-xs text-subtle">/ 100</span>
                        {topScore.grade && <Tag className="ml-2">Grade {topScore.grade}</Tag>}
                      </p>
                      {topScore.score != null && (
                        <div className="mt-3 h-1 bg-border" role="img" aria-label={`Health score ${topScore.score} out of 100`}>
                          <div className="h-full bg-[var(--ink)]" style={{ width: `${Math.max(0, Math.min(100, topScore.score))}%` }} />
                        </div>
                      )}
                      {Array.isArray(topScore.reasons) && topScore.reasons.length > 0 && (
                        <ul className="m-0 mt-3 p-0 list-none space-y-1">
                          {topScore.reasons.slice(0, 3).map((r: string, i: number) => (
                            <li key={i} className="text-xs text-muted">{r}</li>
                          ))}
                        </ul>
                      )}
                    </>
                  ) : (
                    <p className="m-0 mt-2 text-[13px] text-muted">No score yet. Run a flow to generate one.</p>
                  )}
                  {(staleDatasets.length > 0 || amberDatasets.length > 0) && (
                    <p className="m-0 mt-4 flex items-center gap-4 text-xs text-fg">
                      {staleDatasets.length > 0 && <span className="inline-flex items-center gap-1.5"><span className="nubi-dot nubi-dot-danger" aria-hidden="true" />{staleDatasets.length} stale</span>}
                      {amberDatasets.length > 0 && <span className="inline-flex items-center gap-1.5"><span className="nubi-dot nubi-dot-warning" aria-hidden="true" />{amberDatasets.length} ageing</span>}
                    </p>
                  )}
                </div>

                <div>
                  <p className="t-label m-0 mb-2 flex items-center justify-between">
                    <span>Dataset freshness</span>
                    {freshness.length > 6 && <span>+{freshness.length - 6} more</span>}
                  </p>
                  {freshness.length > 0 ? (
                    <ul className="m-0 p-0 list-none border-t border-border">
                      {freshness.slice(0, 6).map((d, i) => (
                        <li key={`${d.dataset_key}-${i}`} className="flex items-center gap-3 min-h-10 px-2 border-b border-border">
                          <span className="nubi-dot" data-tone={RAG_TONE[d.status]} role="img" aria-label={d.status} />
                          <span className="flex-1 font-mono text-xs text-fg truncate">{d.dataset_key}</span>
                          <StatusTag tone={RAG_TONE[d.status] ?? 'neutral'}>{d.status}</StatusTag>
                          {d.last_success_at && (
                            <span className="font-mono text-[11px] text-subtle tabular-nums shrink-0 hidden sm:block">{relativeTime(new Date(d.last_success_at))}</span>
                          )}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="m-0 text-[13px] text-muted">No datasets tracked yet.</p>
                  )}
                </div>
              </div>
            )}
          </PageSection>
        </>
      )}
    </PageShell>
  )
}
