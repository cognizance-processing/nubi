/**
 * HomePage — Authenticated home / onboarding page.
 *
 * Layout:
 *   1. Warm header — greeting + org pill + "Ask AI" button
 *   2. Guided 3-step flow — big cards with live step-completion status
 *      Step 1: Connect a data source   → /connectors  (GET /api/v1/connectors)
 *      Step 2: Run your first query    → /queries     (GET /api/v1/query/registry)
 *      Step 3: Build a dashboard       → /editor      (GET /api/v1/boards)
 *   3. Recent section — latest dashboards grid + quick-access links
 *
 * Step completion:
 *   All three endpoints are fetched in parallel on mount.
 *   A step is "done" when its endpoint returns ≥ 1 item.
 *   Endpoint errors / empty arrays → "not done" (graceful).
 *   The current step is the first non-completed step.
 */

import { useEffect, useState, useCallback } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  Database,
  SearchCode,
  LayoutDashboard,
  CheckCircle2,
  Circle,
  ArrowRight,
  Sparkles,
  ExternalLink,
  Clock,
  Zap,
  Bot,
  ChevronRight,
  Building2,
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
 * Fetch count of items from an endpoint, returning 0 on any error.
 * Handles both Array responses and {items:[], data:[], boards:[], queries:[]} envelopes.
 */
async function fetchCount(path) {
  try {
    const data = await api.get(path)
    if (Array.isArray(data)) return data.length
    // common envelope shapes
    for (const key of ['items', 'data', 'boards', 'queries', 'connectors', 'results']) {
      if (Array.isArray(data?.[key])) return data[key].length
    }
    return 0
  } catch {
    return 0
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Step configuration
// ─────────────────────────────────────────────────────────────────────────────

const STEPS = [
  {
    id: 'connect',
    number: 1,
    icon: Database,
    title: 'Connect a data source',
    description: 'Link your database or warehouse so Nubi can query it directly.',
    cta: 'Add connector',
    href: '/connectors',
    endpoint: '/connectors',
    accentClass: 'from-brand-navy to-brand-blue',
    iconBg: 'bg-blue-500/10 dark:bg-blue-400/10',
    iconColor: 'text-brand-blue dark:text-blue-400',
    ringColor: 'ring-brand-blue/40',
    doneRing: 'ring-emerald-400/40',
  },
  {
    id: 'query',
    number: 2,
    icon: SearchCode,
    title: 'Run your first query',
    description: 'Write SQL or use AI to explore and register reusable queries.',
    cta: 'Open queries',
    href: '/queries',
    endpoint: '/query/registry',
    accentClass: 'from-brand-blue to-brand-teal',
    iconBg: 'bg-indigo-500/10 dark:bg-indigo-400/10',
    iconColor: 'text-indigo-600 dark:text-indigo-400',
    ringColor: 'ring-indigo-400/40',
    doneRing: 'ring-emerald-400/40',
  },
  {
    id: 'dashboard',
    number: 3,
    icon: LayoutDashboard,
    title: 'Build a dashboard',
    description: 'Drag, drop and configure charts to create your first live board.',
    cta: 'Open editor',
    href: '/editor',
    endpoint: '/boards',
    accentClass: 'from-brand-teal to-brand-cyan',
    iconBg: 'bg-teal-500/10 dark:bg-teal-400/10',
    iconColor: 'text-brand-teal dark:text-teal-400',
    ringColor: 'ring-teal-400/40',
    doneRing: 'ring-emerald-400/40',
  },
]

// ─────────────────────────────────────────────────────────────────────────────
// StepCard
// ─────────────────────────────────────────────────────────────────────────────

function StepCard({ step, done, current, index, totalDone }) {
  const Icon = step.icon
  const isPast = done
  const isCurrent = current
  const isFuture = !done && !current

  return (
    <Link
      to={step.href}
      className={[
        'group relative flex flex-col gap-4 rounded-2xl p-6 border transition-all duration-200',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        // state-driven styles
        isPast
          ? 'bg-surface border-emerald-400/30 dark:border-emerald-500/30 hover:border-emerald-400/60 hover:shadow-md'
          : isCurrent
          ? 'bg-surface border-primary/50 shadow-lg shadow-primary/5 hover:shadow-xl hover:shadow-primary/10 hover:border-primary/70'
          : 'bg-surface border-border hover:border-border/80 hover:shadow-md opacity-70 hover:opacity-90',
      ].join(' ')}
      aria-label={`${step.title}${done ? ' — completed' : ''}`}
    >
      {/* Current step highlight bar */}
      {isCurrent && (
        <div
          className={`absolute inset-x-0 top-0 h-0.5 rounded-t-2xl bg-gradient-to-r ${step.accentClass}`}
        />
      )}

      {/* Step number + status */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <span
            className={[
              'flex items-center justify-center w-6 h-6 rounded-full text-xs font-semibold font-display border transition-colors',
              isPast
                ? 'bg-emerald-500 border-emerald-500 text-white'
                : isCurrent
                ? 'bg-primary border-primary text-primary-fg'
                : 'bg-surface-2 border-border text-muted',
            ].join(' ')}
          >
            {isPast ? '✓' : step.number}
          </span>
          <span
            className={[
              'text-xs font-medium uppercase tracking-widest font-display',
              isPast ? 'text-emerald-500 dark:text-emerald-400' : isCurrent ? 'text-primary' : 'text-muted',
            ].join(' ')}
          >
            {isPast ? 'Completed' : isCurrent ? 'Up next' : 'Pending'}
          </span>
        </div>

        {isPast && (
          <CheckCircle2 size={18} className="text-emerald-500 dark:text-emerald-400 shrink-0" />
        )}
      </div>

      {/* Icon */}
      <div
        className={[
          'flex items-center justify-center w-12 h-12 rounded-xl transition-transform group-hover:scale-105',
          step.iconBg,
        ].join(' ')}
      >
        <Icon size={22} className={step.iconColor} />
      </div>

      {/* Text */}
      <div className="flex-1">
        <h3
          className={[
            'font-display font-semibold text-base mb-1 transition-colors',
            isFuture ? 'text-muted' : 'text-fg',
          ].join(' ')}
        >
          {step.title}
        </h3>
        <p className="text-sm text-muted leading-relaxed">{step.description}</p>
      </div>

      {/* CTA */}
      <div
        className={[
          'inline-flex items-center gap-2 text-sm font-medium font-display min-h-[44px] transition-colors',
          isPast
            ? 'text-emerald-600 dark:text-emerald-400 group-hover:text-emerald-700 dark:group-hover:text-emerald-300'
            : isCurrent
            ? 'text-primary group-hover:text-primary'
            : 'text-muted group-hover:text-fg',
        ].join(' ')}
      >
        {step.cta}
        <ArrowRight
          size={15}
          className="transition-transform group-hover:translate-x-1"
        />
      </div>
    </Link>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Progress bar
// ─────────────────────────────────────────────────────────────────────────────

function OnboardingProgress({ done }) {
  const count = done.filter(Boolean).length
  const pct = Math.round((count / STEPS.length) * 100)

  if (count === STEPS.length) {
    return (
      <div className="flex items-center gap-2 text-sm text-emerald-600 dark:text-emerald-400 font-medium">
        <CheckCircle2 size={16} />
        <span>Setup complete — you&apos;re all set!</span>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 h-1.5 bg-surface-2 rounded-full overflow-hidden max-w-[160px]">
        <div
          className="h-full bg-gradient-to-r from-brand-blue to-brand-teal rounded-full transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-muted font-medium tabular-nums">
        {count}/{STEPS.length} done
      </span>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Recent boards grid
// ─────────────────────────────────────────────────────────────────────────────

function BoardCard({ board }) {
  const navigate = useNavigate()
  const updatedAt = board.updated_at || board.created_at
  const timeAgo = updatedAt ? relativeTime(new Date(updatedAt)) : null

  return (
    <button
      onClick={() => navigate(`/d/${board.id}`)}
      className="
        group text-left flex flex-col gap-3 p-4 rounded-xl border border-border
        bg-surface hover:border-primary/40 hover:shadow-md hover:shadow-primary/5
        transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring
        min-h-[44px]
      "
    >
      <div className="flex items-start justify-between gap-2">
        <div
          className="flex items-center justify-center w-9 h-9 rounded-lg shrink-0"
          style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
        >
          <LayoutDashboard size={16} className="text-white" />
        </div>
        <ExternalLink
          size={13}
          className="text-muted opacity-0 group-hover:opacity-100 transition-opacity mt-0.5 shrink-0"
        />
      </div>
      <div className="flex-1 min-w-0">
        <p className="font-display font-medium text-sm text-fg truncate">{board.name || 'Untitled board'}</p>
        {timeAgo && (
          <p className="text-xs text-muted mt-0.5 flex items-center gap-1">
            <Clock size={10} />
            {timeAgo}
          </p>
        )}
      </div>
    </button>
  )
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
// Quick links
// ─────────────────────────────────────────────────────────────────────────────

function QuickLink({ icon: Icon, label, description, onClick, to }) {
  const inner = (
    <div className="
      group flex items-center gap-3 p-4 rounded-xl border border-border
      bg-surface hover:border-primary/40 hover:shadow-md hover:shadow-primary/5
      transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring
      min-h-[44px] w-full text-left
    ">
      <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-surface-2 shrink-0 group-hover:bg-primary/10 transition-colors">
        <Icon size={17} className="text-muted group-hover:text-primary transition-colors" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="font-display font-medium text-sm text-fg">{label}</p>
        {description && <p className="text-xs text-muted truncate">{description}</p>}
      </div>
      <ChevronRight size={14} className="text-muted shrink-0 group-hover:text-primary transition-colors" />
    </div>
  )

  if (to) return <Link to={to} className="block">{inner}</Link>
  return <button onClick={onClick} className="block w-full">{inner}</button>
}

// ─────────────────────────────────────────────────────────────────────────────
// Empty state for recent section
// ─────────────────────────────────────────────────────────────────────────────

function RecentEmptyState({ onAskAi }) {
  return (
    <div className="flex flex-col items-center justify-center py-10 px-6 text-center rounded-2xl border border-dashed border-border bg-surface-2/50">
      <div
        className="flex items-center justify-center w-12 h-12 rounded-2xl mb-4"
        style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
      >
        <Sparkles size={20} className="text-white" />
      </div>
      <p className="font-display font-semibold text-sm text-fg mb-1">No dashboards yet</p>
      <p className="text-xs text-muted max-w-[220px] leading-relaxed mb-4">
        Complete the setup above to create your first dashboard — or let AI kick things off.
      </p>
      <button
        onClick={onAskAi}
        className="
          inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium font-display
          bg-primary text-primary-fg hover:opacity-90 transition-opacity
          focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring
          min-h-[44px]
        "
      >
        <Bot size={15} />
        Ask AI to build one
      </button>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────────

export default function HomePage() {
  const { user } = useAuth()
  const { activeOrg, loading: orgLoading } = useOrg()
  const { openChat } = useUi()

  // Step completion state: [connectorsDone, queriesDone, boardsDone]
  const [stepDone, setStepDone] = useState([false, false, false])
  const [boards, setBoards] = useState([])
  const [loadingSteps, setLoadingSteps] = useState(true)

  // Fetch all three endpoints in parallel on mount
  useEffect(() => {
    let cancelled = false

    async function fetchStatus() {
      setLoadingSteps(true)

      const [connectorsCount, queriesCount, boardsCount] = await Promise.all([
        fetchCount('/connectors'),
        fetchCount('/query/registry'),
        fetchCount('/boards'),
      ])

      // Also fetch the boards list for the "Recent" section
      let boardsList = []
      try {
        const data = await api.get('/boards')
        if (Array.isArray(data)) boardsList = data
        else if (Array.isArray(data?.boards)) boardsList = data.boards
        else if (Array.isArray(data?.items)) boardsList = data.items
        else if (Array.isArray(data?.data)) boardsList = data.data
      } catch {
        boardsList = []
      }

      if (!cancelled) {
        setStepDone([connectorsCount > 0, queriesCount > 0, boardsCount > 0])
        // Sort by most recently updated and take top 6
        setBoards(
          [...boardsList]
            .sort((a, b) => {
              const ta = new Date(a.updated_at || a.created_at || 0).getTime()
              const tb = new Date(b.updated_at || b.created_at || 0).getTime()
              return tb - ta
            })
            .slice(0, 6),
        )
        setLoadingSteps(false)
      }
    }

    fetchStatus()
    return () => { cancelled = true }
  }, [])

  // Current step = first non-done step index
  const currentStepIndex = stepDone.findIndex((d) => !d)

  const displayName = firstName(user?.name) || user?.email?.split('@')[0] || 'there'
  const orgName = activeOrg?.name

  return (
    <div className="min-h-full bg-bg">
      {/* ── Scoped keyframe for the shimmer skeleton ─────────────────────── */}
      <style>{`
        @keyframes hp-shimmer {
          0%   { background-position: -400px 0; }
          100% { background-position: 400px 0; }
        }
        .hp-skeleton {
          background: linear-gradient(
            90deg,
            var(--surface-2, #eef2f7) 25%,
            var(--border, #e2e8f0) 50%,
            var(--surface-2, #eef2f7) 75%
          );
          background-size: 800px 100%;
          animation: hp-shimmer 1.4s ease-in-out infinite;
          border-radius: 0.5rem;
        }
      `}</style>

      <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8 sm:py-10 space-y-10">

        {/* ── 1. Header ─────────────────────────────────────────────────────── */}
        <header className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="font-display font-semibold text-2xl sm:text-3xl text-fg leading-tight">
                {getGreeting()},{' '}
                <span className="text-brand-gradient bg-clip-text">{displayName}</span>
                &nbsp;👋
              </h1>
            </div>
            {orgName && !orgLoading && (
              <div className="flex items-center gap-1.5 text-sm text-muted">
                <Building2 size={13} />
                <span>{orgName}</span>
              </div>
            )}
          </div>

          {/* Ask AI affordance */}
          <button
            onClick={openChat}
            className="
              self-start sm:self-auto
              inline-flex items-center gap-2 px-4 py-2.5 rounded-xl
              bg-surface border border-border hover:border-primary/50
              text-sm font-medium font-display text-fg hover:text-primary
              transition-all duration-150 shadow-sm hover:shadow-md
              focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring
              min-h-[44px]
            "
          >
            <Sparkles size={15} className="text-brand-teal" />
            Ask AI to build it for you
          </button>
        </header>

        {/* ── 2. Guided 3-step flow ─────────────────────────────────────────── */}
        <section aria-labelledby="setup-heading">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-5">
            <div>
              <h2
                id="setup-heading"
                className="font-display font-semibold text-lg text-fg"
              >
                Get started
              </h2>
              <p className="text-sm text-muted mt-0.5">
                Three steps to your first live dashboard.
              </p>
            </div>
            {!loadingSteps && <OnboardingProgress done={stepDone} />}
          </div>

          {/* Step cards */}
          {loadingSteps ? (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              {[0, 1, 2].map((i) => (
                <div key={i} className="rounded-2xl border border-border bg-surface p-6 space-y-4">
                  <div className="hp-skeleton h-4 w-24" />
                  <div className="hp-skeleton h-10 w-10 rounded-xl" />
                  <div className="space-y-2">
                    <div className="hp-skeleton h-4 w-3/4" />
                    <div className="hp-skeleton h-3 w-full" />
                    <div className="hp-skeleton h-3 w-5/6" />
                  </div>
                  <div className="hp-skeleton h-4 w-20" />
                </div>
              ))}
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              {STEPS.map((step, i) => (
                <StepCard
                  key={step.id}
                  step={step}
                  done={stepDone[i]}
                  current={i === currentStepIndex}
                  index={i}
                  totalDone={stepDone.filter(Boolean).length}
                />
              ))}
            </div>
          )}
        </section>

        {/* ── 3. Recent & quick access ──────────────────────────────────────── */}
        <section aria-labelledby="recent-heading">
          <h2
            id="recent-heading"
            className="font-display font-semibold text-lg text-fg mb-4"
          >
            Recent &amp; quick access
          </h2>

          <div className="grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-6">
            {/* Recent boards */}
            <div>
              <p className="text-xs font-medium text-muted uppercase tracking-wider mb-3">
                Dashboards
              </p>
              {loadingSteps ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {[0, 1, 2, 3].map((i) => (
                    <div key={i} className="rounded-xl border border-border bg-surface p-4 space-y-3">
                      <div className="hp-skeleton h-9 w-9 rounded-lg" />
                      <div className="hp-skeleton h-3.5 w-2/3" />
                      <div className="hp-skeleton h-3 w-1/3" />
                    </div>
                  ))}
                </div>
              ) : boards.length > 0 ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {boards.map((board) => (
                    <BoardCard key={board.id} board={board} />
                  ))}
                </div>
              ) : (
                <RecentEmptyState onAskAi={openChat} />
              )}
            </div>

            {/* Quick links */}
            <div>
              <p className="text-xs font-medium text-muted uppercase tracking-wider mb-3">
                Quick links
              </p>
              <div className="flex flex-col gap-2">
                <QuickLink
                  icon={Zap}
                  label="Queries"
                  description="Author & run SQL instantly"
                  to="/queries"
                />
                <QuickLink
                  icon={Bot}
                  label="AI assistant"
                  description="Ask anything about your data"
                  onClick={openChat}
                />
                <QuickLink
                  icon={LayoutDashboard}
                  label="All dashboards"
                  description="Browse and manage boards"
                  to="/dashboards"
                />
                <QuickLink
                  icon={Database}
                  label="Connectors"
                  description="Manage data sources"
                  to="/connectors"
                />
              </div>
            </div>
          </div>
        </section>

        {/* ── Bottom AI nudge ───────────────────────────────────────────────── */}
        <div className="flex items-center justify-center pb-4">
          <button
            onClick={openChat}
            className="
              inline-flex items-center gap-2 text-xs text-muted hover:text-primary
              transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded
              min-h-[44px] px-2
            "
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
