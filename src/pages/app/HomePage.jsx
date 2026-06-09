/**
 * HomePage — Authenticated home page.
 *
 * Two states, chosen by setup progress + a per-org "skip" flag:
 *
 *   SETUP MODE  (new workspace, not skipped)
 *     - Warm greeting + org pill + "Ask AI" + a "Skip setup" escape hatch.
 *     - Core 3-step spine: Connect a source → Run a query → Build a dashboard,
 *       with live completion status fetched on mount.
 *     - A lighter "What's next" row teasing the rest of the product
 *       (Flows, Automations, Version control) so it never feels like Nubi is
 *       only queries + dashboards.
 *
 *   GENERAL HOME  (setup complete OR skipped)
 *     - Greeting + a slim "finish setup" banner if skipped-but-incomplete.
 *     - Stat row: live counts (dashboards / queries / connectors / flows) = usage.
 *     - All-features quick-access grid.
 *     - Recent dashboards + recent flows.
 *
 * Completion is derived from list endpoints (each "done" when ≥ 1 item):
 *     /connectors        connectors / data sources
 *     /query/registry    registered queries
 *     /boards            dashboards
 *     /flows             flows + automations (automations are scheduled flows)
 * Errors / empty arrays degrade gracefully to a count of 0.
 *
 * The "skip setup" choice persists in localStorage, keyed by the active org so a
 * dismissal in one workspace doesn't leak into another.
 */

import { useEffect, useState, useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  Database,
  SearchCode,
  LayoutDashboard,
  CheckCircle2,
  ArrowRight,
  Sparkles,
  ExternalLink,
  Clock,
  Bot,
  ChevronRight,
  Building2,
  Workflow,
  CalendarClock,
  Table2,
  GitBranch,
  Plug,
  Plus,
} from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext.jsx'
import { useOrg } from '../../contexts/OrgContext.jsx'
import { useUi } from '../../contexts/UiContext.jsx'
import * as api from '../../lib/api.js'

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function getGreeting() {
  const h = new Date().getHours()
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}

function firstName(name) {
  if (!name) return null
  return name.split(' ')[0]
}

/**
 * Fetch a list from an endpoint, normalizing common envelope shapes to an array.
 * Returns [] on any error so the caller never has to guard.
 */
async function fetchList(path) {
  try {
    const data = await api.get(path)
    if (Array.isArray(data)) return data
    for (const key of ['items', 'data', 'boards', 'queries', 'connectors', 'flows', 'results']) {
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

function mostRecent(list, n) {
  return [...list]
    .sort((a, b) => {
      const ta = new Date(a.updated_at || a.created_at || 0).getTime()
      const tb = new Date(b.updated_at || b.created_at || 0).getTime()
      return tb - ta
    })
    .slice(0, n)
}

const skipKey = (orgId) => `nubi:home:setupSkipped:${orgId || 'default'}`

// ─────────────────────────────────────────────────────────────────────────────
// Static config
// ─────────────────────────────────────────────────────────────────────────────

// The core onboarding spine — the minimum to a first live board.
const CORE_STEPS = [
  {
    id: 'connect',
    icon: Database,
    title: 'Connect a data source',
    description: 'Link a database or warehouse so Nubi can query it directly.',
    cta: 'Add connector',
    href: '/connectors',
    iconBg: 'bg-blue-500/10 dark:bg-blue-400/10',
    iconColor: 'text-brand-blue dark:text-blue-400',
    accent: 'from-brand-navy to-brand-blue',
  },
  {
    id: 'query',
    icon: SearchCode,
    title: 'Run your first query',
    description: 'Write SQL or ask AI to explore, then register reusable queries.',
    cta: 'Open queries',
    href: '/queries',
    iconBg: 'bg-indigo-500/10 dark:bg-indigo-400/10',
    iconColor: 'text-indigo-600 dark:text-indigo-400',
    accent: 'from-brand-blue to-brand-teal',
  },
  {
    id: 'dashboard',
    icon: LayoutDashboard,
    title: 'Build a dashboard',
    description: 'Drag, drop and configure charts into your first live board.',
    cta: 'Open editor',
    href: '/editor',
    iconBg: 'bg-teal-500/10 dark:bg-teal-400/10',
    iconColor: 'text-brand-teal dark:text-teal-400',
    accent: 'from-brand-teal to-brand-cyan',
  },
]

// "What's next" — the rest of the product, surfaced after the core spine.
const NEXT_FEATURES = [
  {
    icon: Workflow,
    title: 'Automate with Flows',
    description: 'Chain queries, transforms and exports into pipelines.',
    href: '/flows',
  },
  {
    icon: CalendarClock,
    title: 'Schedule automations',
    description: 'Run flows on a cron schedule, hands-free.',
    href: '/automations',
  },
  {
    icon: GitBranch,
    title: 'Version control',
    description: 'Sync this project to Git to track and review changes.',
    href: '/settings',
  },
]

// Quick-access tiles in the general home — the full feature surface.
const QUICK_ACCESS = [
  { icon: Plug,            label: 'Connectors',  description: 'Data sources',        to: '/connectors' },
  { icon: Table2,          label: 'Data',        description: 'Browse & explore',    to: '/data' },
  { icon: SearchCode,      label: 'Queries',     description: 'Author & run SQL',    to: '/queries' },
  { icon: LayoutDashboard, label: 'Dashboards',  description: 'Live boards',         to: '/dashboards' },
  { icon: Workflow,        label: 'Flows',       description: 'Pipelines',           to: '/flows' },
  { icon: CalendarClock,   label: 'Automations', description: 'Scheduled runs',      to: '/automations' },
  { icon: GitBranch,       label: 'Version control', description: 'Git sync',        to: '/settings' },
  { icon: Bot,             label: 'AI assistant', description: 'Ask about your data', chat: true },
]

// ─────────────────────────────────────────────────────────────────────────────
// SETUP MODE — step card
// ─────────────────────────────────────────────────────────────────────────────

function StepCard({ step, done, current, delay }) {
  const Icon = step.icon
  const isFuture = !done && !current

  return (
    <Link
      to={step.href}
      style={{ animationDelay: `${delay}ms` }}
      className={[
        'hp-reveal group relative flex flex-col gap-4 rounded-2xl p-6 border transition-all duration-200',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        done
          ? 'bg-surface border-emerald-400/30 dark:border-emerald-500/30 hover:border-emerald-400/60 hover:shadow-md'
          : current
          ? 'bg-surface border-primary/50 shadow-lg shadow-primary/5 hover:shadow-xl hover:shadow-primary/10 hover:border-primary/70'
          : 'bg-surface border-border hover:border-border/80 hover:shadow-md opacity-70 hover:opacity-100',
      ].join(' ')}
      aria-label={`${step.title}${done ? ' — completed' : ''}`}
    >
      {current && (
        <div className={`absolute inset-x-0 top-0 h-0.5 rounded-t-2xl bg-gradient-to-r ${step.accent}`} />
      )}

      <div className="flex items-center justify-between">
        <span
          className={[
            'text-xs font-medium uppercase tracking-widest font-display',
            done ? 'text-emerald-500 dark:text-emerald-400' : current ? 'text-primary' : 'text-muted',
          ].join(' ')}
        >
          {done ? 'Completed' : current ? 'Up next' : 'Pending'}
        </span>
        {done && <CheckCircle2 size={18} className="text-emerald-500 dark:text-emerald-400 shrink-0" />}
      </div>

      <div className={['flex items-center justify-center w-12 h-12 rounded-xl transition-transform group-hover:scale-105', step.iconBg].join(' ')}>
        <Icon size={22} className={step.iconColor} />
      </div>

      <div className="flex-1">
        <h3 className={['font-display font-semibold text-base mb-1', isFuture ? 'text-muted' : 'text-fg'].join(' ')}>
          {step.title}
        </h3>
        <p className="text-sm text-muted leading-relaxed">{step.description}</p>
      </div>

      <div
        className={[
          'inline-flex items-center gap-2 text-sm font-medium font-display min-h-[44px]',
          done ? 'text-emerald-600 dark:text-emerald-400' : current ? 'text-primary' : 'text-muted group-hover:text-fg',
        ].join(' ')}
      >
        {done ? 'Revisit' : step.cta}
        <ArrowRight size={15} className="transition-transform group-hover:translate-x-1" />
      </div>
    </Link>
  )
}

function NextFeatureCard({ feature, delay }) {
  const Icon = feature.icon
  return (
    <Link
      to={feature.href}
      style={{ animationDelay: `${delay}ms` }}
      className="hp-reveal group flex items-center gap-3 p-4 rounded-xl border border-border bg-surface/60
        hover:bg-surface hover:border-primary/40 hover:shadow-md hover:shadow-primary/5 transition-all duration-200
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring min-h-[44px]"
    >
      <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-surface-2 shrink-0 group-hover:bg-primary/10 transition-colors">
        <Icon size={17} className="text-muted group-hover:text-primary transition-colors" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="font-display font-medium text-sm text-fg">{feature.title}</p>
        <p className="text-xs text-muted truncate">{feature.description}</p>
      </div>
      <ChevronRight size={14} className="text-muted shrink-0 group-hover:text-primary group-hover:translate-x-0.5 transition-all" />
    </Link>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// GENERAL HOME — stat + quick access + recent
// ─────────────────────────────────────────────────────────────────────────────

function StatCard({ icon, label, value, to, accent, delay }) {
  const Icon = icon
  return (
    <Link
      to={to}
      style={{ animationDelay: `${delay}ms` }}
      className="hp-reveal group relative overflow-hidden flex flex-col gap-3 p-5 rounded-2xl border border-border
        bg-surface hover:border-primary/40 hover:shadow-md hover:shadow-primary/5 transition-all duration-200
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {/* faint corner glow */}
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

function QuickTile({ item, onChat, delay }) {
  const Icon = item.icon
  const inner = (
    <div
      style={{ animationDelay: `${delay}ms` }}
      className="hp-reveal group flex items-center gap-3 p-3.5 rounded-xl border border-border bg-surface
        hover:border-primary/40 hover:shadow-md hover:shadow-primary/5 transition-all duration-200
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring min-h-[44px] w-full text-left"
    >
      <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-surface-2 shrink-0 group-hover:bg-primary/10 transition-colors">
        <Icon size={16} className="text-muted group-hover:text-primary transition-colors" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="font-display font-medium text-sm text-fg truncate">{item.label}</p>
        <p className="text-xs text-muted truncate">{item.description}</p>
      </div>
    </div>
  )
  if (item.chat) return <button onClick={onChat} className="block w-full">{inner}</button>
  return <Link to={item.to} className="block">{inner}</Link>
}

function BoardCard({ board, delay }) {
  const navigate = useNavigate()
  const updatedAt = board.updated_at || board.created_at
  const timeAgo = updatedAt ? relativeTime(new Date(updatedAt)) : null
  return (
    <button
      onClick={() => navigate(`/d/${board.id}`)}
      style={{ animationDelay: `${delay}ms` }}
      className="hp-reveal group text-left flex items-center gap-3 p-4 rounded-xl border border-border bg-surface
        hover:border-primary/40 hover:shadow-md hover:shadow-primary/5 transition-all duration-200
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring min-h-[44px]"
    >
      <div className="flex items-center justify-center w-9 h-9 rounded-lg shrink-0 bg-brand-gradient">
        <LayoutDashboard size={16} className="text-white" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="font-display font-medium text-sm text-fg truncate">{board.name || 'Untitled board'}</p>
        {timeAgo && (
          <p className="text-xs text-muted mt-0.5 flex items-center gap-1">
            <Clock size={10} />{timeAgo}
          </p>
        )}
      </div>
      <ExternalLink size={13} className="text-muted opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
    </button>
  )
}

function FlowCard({ flow, delay }) {
  const navigate = useNavigate()
  const updatedAt = flow.updated_at || flow.created_at
  const timeAgo = updatedAt ? relativeTime(new Date(updatedAt)) : null
  return (
    <button
      onClick={() => navigate(`/flows/${flow.id}`)}
      style={{ animationDelay: `${delay}ms` }}
      className="hp-reveal group text-left flex items-center gap-3 p-4 rounded-xl border border-border bg-surface
        hover:border-primary/40 hover:shadow-md hover:shadow-primary/5 transition-all duration-200
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring min-h-[44px]"
    >
      <div className="flex items-center justify-center w-9 h-9 rounded-lg shrink-0 bg-surface-2 group-hover:bg-primary/10 transition-colors">
        <Workflow size={16} className="text-muted group-hover:text-primary transition-colors" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="font-display font-medium text-sm text-fg truncate">{flow.name || flow.title || 'Untitled flow'}</p>
        {timeAgo && (
          <p className="text-xs text-muted mt-0.5 flex items-center gap-1">
            <Clock size={10} />{timeAgo}
          </p>
        )}
      </div>
      <ChevronRight size={14} className="text-muted opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
    </button>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────────

export default function HomePage() {
  const { user } = useAuth()
  const { activeOrg, loading: orgLoading } = useOrg()
  const { openChat } = useUi()
  const navigate = useNavigate()

  const [loading, setLoading] = useState(true)
  const [counts, setCounts] = useState({ connectors: 0, queries: 0, dashboards: 0, flows: 0 })
  const [boards, setBoards] = useState([])
  const [flows, setFlows] = useState([])
  // The per-org "skip setup" flag lives in localStorage. We read it via useMemo
  // (a side-effect-free read) keyed by the active org + a bump counter that
  // skip()/resume() increment — avoiding a synchronous setState-in-effect.
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
      setBoards(mostRecent(boardsList, 4))
      setFlows(mostRecent(flowsList, 3))
      setLoading(false)
    }
    load()
    return () => { cancelled = true }
  }, [activeOrg?.id])

  // Core onboarding completion.
  const stepDone = useMemo(
    () => [counts.connectors > 0, counts.queries > 0, counts.dashboards > 0],
    [counts],
  )
  const doneCount = stepDone.filter(Boolean).length
  const setupComplete = doneCount === CORE_STEPS.length
  const currentStepIndex = stepDone.findIndex((d) => !d)

  // Show the general home when setup is complete OR the user skipped it.
  const showGeneral = setupComplete || skipped

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

  return (
    <div className="min-h-full bg-bg relative">
      {/* Scoped animations + skeleton shimmer */}
      <style>{`
        @keyframes hp-shimmer { 0% { background-position:-400px 0 } 100% { background-position:400px 0 } }
        .hp-skeleton {
          background: linear-gradient(90deg, var(--surface-2,#eef2f7) 25%, var(--border,#e2e8f0) 50%, var(--surface-2,#eef2f7) 75%);
          background-size: 800px 100%; animation: hp-shimmer 1.4s ease-in-out infinite; border-radius:.5rem;
        }
        @keyframes hp-reveal { from { opacity:0; transform: translateY(10px) } to { opacity:1; transform:none } }
        .hp-reveal { opacity:0; animation: hp-reveal .5s cubic-bezier(.16,1,.3,1) forwards; }
        @media (prefers-reduced-motion: reduce) { .hp-reveal { animation: none; opacity:1 } }
      `}</style>

      {/* Atmospheric brand glow behind the header */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-64 opacity-[0.06]"
        style={{ background: 'radial-gradient(60% 100% at 20% 0%, var(--brand-teal), transparent 70%), radial-gradient(50% 100% at 80% 0%, var(--brand-blue), transparent 70%)' }}
      />

      <div className="relative max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8 sm:py-10 space-y-10">

        {/* ── Header ──────────────────────────────────────────────────────── */}
        <header className="hp-reveal flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div className="space-y-1">
            <h1 className="font-display font-semibold text-2xl sm:text-3xl text-fg leading-tight">
              {getGreeting()},{' '}
              <span className="text-brand-gradient bg-clip-text">{displayName}</span>&nbsp;👋
            </h1>
            {orgName && !orgLoading && (
              <div className="flex items-center gap-1.5 text-sm text-muted">
                <Building2 size={13} />
                <span>{orgName}</span>
              </div>
            )}
          </div>
          <button
            onClick={openChat}
            className="self-start sm:self-auto inline-flex items-center gap-2 px-4 py-2.5 rounded-xl
              bg-surface border border-border hover:border-primary/50 text-sm font-medium font-display text-fg hover:text-primary
              transition-all duration-150 shadow-sm hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring min-h-[44px]"
          >
            <Sparkles size={15} className="text-brand-teal" />
            Ask AI to build it for you
          </button>
        </header>

        {/* ════════════════════════════════════════════════════════════════ */}
        {!showGeneral ? (
          // ── SETUP MODE ──────────────────────────────────────────────────
          <>
            <section aria-labelledby="setup-heading">
              <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-3 mb-5">
                <div>
                  <h2 id="setup-heading" className="font-display font-semibold text-lg text-fg">Get started</h2>
                  <p className="text-sm text-muted mt-0.5">Three steps to your first live dashboard — then explore the rest.</p>
                </div>
                <div className="flex items-center gap-4">
                  {!loading && (
                    <div className="flex items-center gap-3">
                      <div className="h-1.5 w-[120px] bg-surface-2 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-gradient-to-r from-brand-blue to-brand-teal rounded-full transition-all duration-500"
                          style={{ width: `${Math.round((doneCount / CORE_STEPS.length) * 100)}%` }}
                        />
                      </div>
                      <span className="text-xs text-muted font-medium tabular-nums">{doneCount}/{CORE_STEPS.length}</span>
                    </div>
                  )}
                  <button
                    onClick={skip}
                    className="text-xs font-medium text-muted hover:text-fg transition-colors inline-flex items-center gap-1
                      focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded px-1.5 py-1"
                  >
                    Skip setup <ArrowRight size={12} />
                  </button>
                </div>
              </div>

              {loading ? (
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                  {[0, 1, 2].map((i) => (
                    <div key={i} className="rounded-2xl border border-border bg-surface p-6 space-y-4">
                      <div className="hp-skeleton h-4 w-24" />
                      <div className="hp-skeleton h-10 w-10 rounded-xl" />
                      <div className="space-y-2">
                        <div className="hp-skeleton h-4 w-3/4" />
                        <div className="hp-skeleton h-3 w-full" />
                      </div>
                      <div className="hp-skeleton h-4 w-20" />
                    </div>
                  ))}
                </div>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                  {CORE_STEPS.map((step, i) => (
                    <StepCard key={step.id} step={step} done={stepDone[i]} current={i === currentStepIndex} delay={i * 70} />
                  ))}
                </div>
              )}
            </section>

            {/* What's next — the broader product */}
            <section aria-labelledby="next-heading">
              <h2 id="next-heading" className="font-display font-semibold text-lg text-fg mb-1">There's more to explore</h2>
              <p className="text-sm text-muted mb-4">Once your data's in, automate and ship with the rest of Nubi.</p>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {NEXT_FEATURES.map((f, i) => (
                  <NextFeatureCard key={f.href} feature={f} delay={250 + i * 70} />
                ))}
              </div>
            </section>
          </>
        ) : (
          // ── GENERAL HOME ────────────────────────────────────────────────
          <>
            {/* Finish-setup banner (skipped but incomplete) */}
            {!setupComplete && (
              <div className="hp-reveal flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-2xl border border-primary/30 bg-primary/[0.04] px-5 py-4">
                <div className="flex items-center gap-3">
                  <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-primary/10 shrink-0">
                    <Sparkles size={16} className="text-primary" />
                  </div>
                  <div>
                    <p className="font-display font-medium text-sm text-fg">Finish setting up your workspace</p>
                    <p className="text-xs text-muted">{doneCount} of {CORE_STEPS.length} steps done — connect a source, query, and build a board.</p>
                  </div>
                </div>
                <button
                  onClick={resume}
                  className="shrink-0 inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium font-display
                    bg-primary text-primary-fg hover:opacity-90 transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring min-h-[44px]"
                >
                  Resume setup <ArrowRight size={14} />
                </button>
              </div>
            )}

            {/* Stat row — live usage */}
            <section aria-label="Workspace overview">
              {loading ? (
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                  {[0, 1, 2, 3].map((i) => (
                    <div key={i} className="rounded-2xl border border-border bg-surface p-5 space-y-3">
                      <div className="hp-skeleton h-10 w-10 rounded-xl" />
                      <div className="hp-skeleton h-8 w-12" />
                      <div className="hp-skeleton h-3 w-20" />
                    </div>
                  ))}
                </div>
              ) : (
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                  <StatCard icon={LayoutDashboard} label="Dashboards" value={counts.dashboards} to="/dashboards" accent="from-brand-teal to-brand-cyan" delay={0} />
                  <StatCard icon={SearchCode} label="Queries" value={counts.queries} to="/queries" accent="from-brand-blue to-brand-teal" delay={60} />
                  <StatCard icon={Plug} label="Connectors" value={counts.connectors} to="/connectors" accent="from-brand-navy to-brand-blue" delay={120} />
                  <StatCard icon={Workflow} label="Flows" value={counts.flows} to="/flows" accent="from-brand-blue to-brand-cyan" delay={180} />
                </div>
              )}
            </section>

            {/* Quick access — full feature surface */}
            <section aria-labelledby="quick-heading">
              <h2 id="quick-heading" className="font-display font-semibold text-lg text-fg mb-4">Quick access</h2>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                {QUICK_ACCESS.map((item, i) => (
                  <QuickTile key={item.label} item={item} onChat={openChat} delay={i * 50} />
                ))}
              </div>
            </section>

            {/* Recent */}
            <section aria-labelledby="recent-heading">
              <div className="flex items-center justify-between mb-4">
                <h2 id="recent-heading" className="font-display font-semibold text-lg text-fg">Recent</h2>
              </div>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Recent dashboards */}
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <p className="text-xs font-medium text-muted uppercase tracking-wider">Dashboards</p>
                    <Link to="/dashboards" className="text-xs text-muted hover:text-primary transition-colors inline-flex items-center gap-1">
                      View all <ChevronRight size={12} />
                    </Link>
                  </div>
                  {loading ? (
                    <div className="space-y-3">
                      {[0, 1].map((i) => <div key={i} className="hp-skeleton h-[68px] w-full rounded-xl" />)}
                    </div>
                  ) : boards.length > 0 ? (
                    <div className="space-y-3">
                      {boards.map((b, i) => <BoardCard key={b.id} board={b} delay={i * 60} />)}
                    </div>
                  ) : (
                    <EmptyRow icon={LayoutDashboard} label="No dashboards yet" cta="Create one" onClick={() => navigate('/editor')} />
                  )}
                </div>

                {/* Recent flows */}
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <p className="text-xs font-medium text-muted uppercase tracking-wider">Flows</p>
                    <Link to="/flows" className="text-xs text-muted hover:text-primary transition-colors inline-flex items-center gap-1">
                      View all <ChevronRight size={12} />
                    </Link>
                  </div>
                  {loading ? (
                    <div className="space-y-3">
                      {[0, 1].map((i) => <div key={i} className="hp-skeleton h-[68px] w-full rounded-xl" />)}
                    </div>
                  ) : flows.length > 0 ? (
                    <div className="space-y-3">
                      {flows.map((f, i) => <FlowCard key={f.id} flow={f} delay={i * 60} />)}
                    </div>
                  ) : (
                    <EmptyRow icon={Workflow} label="No flows yet" cta="Build a flow" onClick={() => navigate('/flows')} />
                  )}
                </div>
              </div>
            </section>
          </>
        )}

        {/* Bottom AI nudge */}
        <div className="flex items-center justify-center pb-4">
          <button
            onClick={openChat}
            className="inline-flex items-center gap-2 text-xs text-muted hover:text-primary transition-colors
              focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded min-h-[44px] px-2"
          >
            <Sparkles size={13} className="text-brand-teal" />
            Not sure where to start? Ask AI to guide you
            <ArrowRight size={12} />
          </button>
        </div>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Small empty-row used in the Recent section
// ─────────────────────────────────────────────────────────────────────────────

function EmptyRow({ icon, label, cta, onClick }) {
  const Icon = icon
  return (
    <div className="flex flex-col items-center justify-center text-center gap-3 py-8 px-6 rounded-xl border border-dashed border-border bg-surface-2/40">
      <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-surface-2">
        <Icon size={18} className="text-muted" />
      </div>
      <p className="text-sm text-muted">{label}</p>
      <button
        onClick={onClick}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium font-display
          bg-surface border border-border text-fg hover:border-primary/50 hover:text-primary transition-colors
          focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Plus size={14} />{cta}
      </button>
    </div>
  )
}
