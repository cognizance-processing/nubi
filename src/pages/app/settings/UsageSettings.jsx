/**
 * UsageSettings — open-core USAGE metering, hosted inside the Settings area.
 *
 * Route:  /settings/usage  (Organization › Usage)
 *
 * Read-only visibility into what the active org has consumed this period:
 *  - one card per usage metric (queries, compute units, bytes scanned, flow
 *    runs, AI usage, embedded sessions, storage) showing used / limit / % with
 *    a progress bar (only when a soft limit is configured by the EE tier).
 *  - a time-series chart for the selected metric (reuses the app's EChart).
 *  - a period selector (Today / 7 days / Month).
 *  - loading / error / empty states.
 *
 * This page is intentionally BILLING-FREE. Billing (charging, wallet, plans)
 * lives behind the EE registry; usage counters are core. Limits come from the
 * EE tier when present and otherwise show as "unlimited" — the page never
 * implies a hard cap. Numbers are aggregated server-side from the core
 * usage_events table (populated off the hot path), surfaced via lib/usage.js.
 *
 * Anatomy mirrors the other settings sections (SettingsPageHeader + cards);
 * the period selector sits in the header. Hooks follow the repo's
 * react-hooks/set-state-in-effect rule (initial load is deferred).
 */

import { useEffect, useState, useCallback, useMemo } from 'react'
import {
  Gauge,
  RefreshCw,
  Loader2,
  AlertTriangle,
  Database,
  Cpu,
  HardDrive,
  Sparkles,
  Workflow,
  FileSearch,
  MonitorSmartphone,
  Infinity as InfinityIcon,
} from 'lucide-react'

import { useOrg } from '../../../contexts/OrgContext.jsx'
import { usageSummary, usageSeries, formatUsage } from '../../../lib/usage.js'
import EChart from '../../../viz/EChart.jsx'
import { SettingsPageHeader } from './SettingsUI.jsx'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PERIODS = [
  { id: 'day', label: 'Today' },
  { id: 'week', label: '7 days' },
  { id: 'month', label: 'Month' },
]

// Metric id → icon (purely cosmetic; unknown ids fall back to Gauge).
const METRIC_ICONS = {
  queries: FileSearch,
  compute_units: Cpu,
  bytes_scanned: Database,
  flow_runs: Workflow,
  ai_tokens: Sparkles,
  embedded_sessions: MonitorSmartphone,
  storage_gb: HardDrive,
}

// Progress-bar colour by utilisation band.
function barColor(pct) {
  if (pct == null) return 'bg-primary/40'
  if (pct >= 90) return 'bg-red-500'
  if (pct >= 70) return 'bg-amber-500'
  return 'bg-primary'
}

// ---------------------------------------------------------------------------
// Usage metric card
// ---------------------------------------------------------------------------

function UsageCard({ metric, selected, onSelect }) {
  const Icon = METRIC_ICONS[metric.id] ?? Gauge
  const hasLimit = metric.limit != null
  const pct = metric.pct
  const pctClamped = pct == null ? 0 : Math.min(pct, 100)

  return (
    <button
      type="button"
      onClick={() => onSelect(metric.id)}
      aria-pressed={selected}
      className={`
        text-left rounded-xl border p-4 bg-surface transition-colors
        focus:outline-none focus:ring-2 focus:ring-ring
        ${selected ? 'border-primary/50 ring-1 ring-primary/30' : 'border-border hover:border-primary/30'}
      `}
    >
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="flex items-center gap-2 min-w-0">
          <span className="flex items-center justify-center w-7 h-7 rounded-lg bg-surface-2 shrink-0">
            <Icon size={15} className="text-muted" strokeWidth={2} />
          </span>
          <span className="text-sm font-medium text-fg truncate">{metric.label}</span>
        </div>
        {hasLimit && pct != null && (
          <span
            className={`text-[11px] font-semibold tabular-nums shrink-0 ${
              pct >= 90 ? 'text-red-600 dark:text-red-400'
                : pct >= 70 ? 'text-amber-600 dark:text-amber-400'
                  : 'text-muted'
            }`}
          >
            {pct}%
          </span>
        )}
      </div>

      <div className="flex items-baseline gap-1.5 mb-2">
        <span className="text-2xl font-display font-semibold text-fg tabular-nums">
          {formatUsage(metric.used, metric.unit)}
        </span>
        {hasLimit ? (
          <span className="text-xs text-muted">/ {formatUsage(metric.limit, metric.unit)}</span>
        ) : (
          <span className="inline-flex items-center gap-0.5 text-xs text-muted" title="No limit on this plan">
            <InfinityIcon size={13} /> unlimited
          </span>
        )}
      </div>

      {/* Progress bar — only meaningful when a soft limit is configured. */}
      {hasLimit && (
        <div className="h-1.5 w-full rounded-full bg-surface-2 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${barColor(pct)}`}
            style={{ width: `${pctClamped}%` }}
          />
        </div>
      )}
    </button>
  )
}

// ---------------------------------------------------------------------------
// Series chart (ECharts line) for the selected metric
// ---------------------------------------------------------------------------

function buildSeriesOption(series) {
  const points = series?.points ?? []
  const isHourly = series?.bucket === 'hour'
  const x = points.map((p) => {
    const d = new Date(p.t)
    return isHourly
      ? d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
      : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  })
  const y = points.map((p) => Number(p.value) || 0)
  return {
    grid: { left: 48, right: 16, top: 16, bottom: 28 },
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: x,
      axisLabel: { color: '#94a3b8', fontSize: 10 },
      axisLine: { lineStyle: { color: '#e2e8f0' } },
      boundaryGap: false,
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#94a3b8', fontSize: 10 },
      splitLine: { lineStyle: { color: '#f1f5f9' } },
    },
    series: [
      {
        type: 'line',
        data: y,
        smooth: true,
        showSymbol: false,
        areaStyle: { opacity: 0.12 },
        lineStyle: { width: 2 },
        itemStyle: { color: '#6366f1' },
      },
    ],
  }
}

function SeriesChart({ series, loading }) {
  const option = useMemo(() => buildSeriesOption(series), [series])
  const hasData = (series?.points ?? []).some((p) => Number(p.value) > 0)

  return (
    <div className="rounded-xl border border-border bg-surface overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <span className="text-sm font-medium text-fg">
          {series?.label ?? 'Usage'} over time
        </span>
        {loading && <Loader2 size={14} className="animate-spin text-muted" />}
      </div>
      <div className="p-2 relative" style={{ minHeight: 260 }}>
        {!loading && !hasData && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <p className="text-sm text-muted">No usage recorded in this period.</p>
          </div>
        )}
        <EChart option={option} height={260} />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Period selector (lives in the section header)
// ---------------------------------------------------------------------------

function PeriodSelector({ period, onChange, loading, onRefresh }) {
  return (
    <div className="flex items-center gap-2">
      <div className="inline-flex items-center rounded-lg border border-border bg-surface p-0.5 shrink-0">
        {PERIODS.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => onChange(p.id)}
            className={`px-2.5 h-7 rounded-md text-xs font-medium transition-colors ${
              period === p.id
                ? 'bg-primary text-primary-fg'
                : 'text-muted hover:text-fg'
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>
      <button
        type="button"
        onClick={onRefresh}
        disabled={loading}
        title="Refresh"
        aria-label="Refresh usage"
        className="flex items-center justify-center w-8 h-8 rounded-lg shrink-0 border border-border text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
      >
        <RefreshCw size={14} className={loading ? 'animate-spin' : ''} strokeWidth={2} />
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// UsageSettings
// ---------------------------------------------------------------------------

export default function UsageSettings() {
  const { activeOrg } = useOrg()

  const [period, setPeriod] = useState('month')
  const [summary, setSummary] = useState(null)
  const [selected, setSelected] = useState('queries')
  const [series, setSeries] = useState(null)
  const [loading, setLoading] = useState(true)
  const [seriesLoading, setSeriesLoading] = useState(false)
  const [error, setError] = useState(null)

  const activeOrgId = activeOrg?.id

  const loadSummary = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await usageSummary(period)
      setSummary(data)
      // Keep the selected metric valid against the returned set.
      if (data.metrics.length && !data.metrics.some((m) => m.id === selected)) {
        setSelected(data.metrics[0].id)
      }
    } catch (err) {
      setError(err?.message ?? 'Failed to load usage.')
    } finally {
      setLoading(false)
    }
  }, [period, selected])

  const loadSeries = useCallback(async () => {
    if (!selected) return
    setSeriesLoading(true)
    try {
      setSeries(await usageSeries(selected, period))
    } finally {
      setSeriesLoading(false)
    }
  }, [selected, period])

  // Defer initial/refresh loads so they aren't synchronous setState in the
  // effect body (react-hooks/set-state-in-effect). Re-runs on period/org change.
  useEffect(() => {
    const t = setTimeout(loadSummary, 0)
    return () => clearTimeout(t)
    // activeOrgId in deps so switching org refetches.
  }, [loadSummary, activeOrgId])

  useEffect(() => {
    const t = setTimeout(loadSeries, 0)
    return () => clearTimeout(t)
  }, [loadSeries, activeOrgId])

  const metrics = summary?.metrics ?? []
  const periodLabel = useMemo(
    () => PERIODS.find((p) => p.id === period)?.label ?? 'Month',
    [period],
  )

  return (
    <div className="space-y-6">
      <SettingsPageHeader
        title="Usage"
        description={
          `What this organisation has consumed for ${periodLabel}. ` +
          'Metrics with a plan limit show usage as a percentage; everything else ' +
          'is unlimited. Select a metric to chart it over time.'
        }
      >
        <PeriodSelector
          period={period}
          onChange={setPeriod}
          loading={loading}
          onRefresh={loadSummary}
        />
      </SettingsPageHeader>

      {loading && (
        <div className="flex items-center gap-2 text-sm text-muted py-12 justify-center">
          <Loader2 size={16} className="animate-spin" /> Loading usage…
        </div>
      )}

      {!loading && error && (
        <div className="flex flex-col items-center justify-center py-12 gap-3 rounded-xl border border-dashed border-red-200 dark:border-red-900/40">
          <AlertTriangle size={20} className="text-red-500" />
          <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
          <button onClick={loadSummary} className="text-xs text-muted hover:text-fg underline">Retry</button>
        </div>
      )}

      {!loading && !error && metrics.length === 0 && (
        <div className="flex flex-col items-center justify-center py-14 px-6 text-center rounded-xl border border-dashed border-border">
          <div className="flex items-center justify-center w-16 h-16 rounded-2xl bg-brand-gradient shadow-lg mb-5">
            <Gauge size={28} className="text-white" />
          </div>
          <h3 className="font-display font-semibold text-xl text-fg mb-2">No usage yet</h3>
          <p className="text-sm text-muted max-w-sm leading-relaxed">
            Once you run queries, flows, or AI generations, your consumption for
            the period will appear here.
          </p>
        </div>
      )}

      {!loading && !error && metrics.length > 0 && (
        <div className="space-y-5">
          {/* Metric cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {metrics.map((m) => (
              <UsageCard
                key={m.id}
                metric={m}
                selected={m.id === selected}
                onSelect={setSelected}
              />
            ))}
          </div>

          {/* Series chart for the selected metric */}
          <SeriesChart series={series} loading={seriesLoading} />
        </div>
      )}
    </div>
  )
}
