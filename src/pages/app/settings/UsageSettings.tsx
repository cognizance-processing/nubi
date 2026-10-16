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
  Cpu,
  Sparkles,
  Workflow,
  FileSearch,
  MonitorSmartphone,
  Infinity as InfinityIcon,
} from 'lucide-react'

import { useOrg } from '../../../contexts/OrgContext.jsx'
import { useTheme } from '../../../contexts/ThemeContext.jsx'
import Button from '../../../components/ui/Button.jsx'
import SegmentedControl from '../../../components/ui/SegmentedControl.jsx'
import EmptyState from '../../../components/ui/EmptyState.jsx'
import Skeleton from '../../../components/ui/Skeleton.jsx'
import { ErrorFigure, MeteringWallet } from '../../../components/illustrations'
import '../../../components/app/appPatterns.css'
import { resolveSpaTheme } from '../../../lib/spaTheme.js'
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
  flow_runs: Workflow,
  ai_tokens: Sparkles,
  ai_calls: Sparkles,
  agent_runs: Cpu,
  embedded_sessions: MonitorSmartphone,
}

// Progress-bar colour by utilisation band.
function barColor(pct) {
  if (pct == null) return 'var(--text-subtle)'
  if (pct >= 90) return 'var(--danger)'
  if (pct >= 70) return 'var(--warning)'
  return 'var(--accent)'
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
      className="border bg-surface p-4 text-left transition-colors hover:border-ink"
      style={{ borderRadius: 2, borderColor: selected ? 'var(--accent)' : 'var(--border)', boxShadow: selected ? 'inset 0 0 0 1px var(--accent)' : 'none' }}
    >
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="flex items-center gap-2 min-w-0">
          <Icon size={15} className="shrink-0 text-muted" strokeWidth={1.75} aria-hidden="true" />
          <span className="t-label truncate">{metric.label}</span>
        </div>
        {hasLimit && pct != null && (
          <span
            className={`ap-mono shrink-0 text-[12px] font-medium ${
              pct >= 90 ? 'text-danger-fg'
                : pct >= 70 ? 'text-warning-fg'
                  : 'text-muted'
            }`}
          >
            {pct}%
          </span>
        )}
      </div>

      <div className="flex items-baseline gap-1.5 mb-2">
        <span className="ap-mono whitespace-nowrap text-[22px] font-semibold leading-none text-fg">
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
        <div className="h-1.5 w-full bg-surface-3" role="presentation">
          <div className="h-full" style={{ width: `${pctClamped}%`, background: barColor(pct) }} />
        </div>
      )}
    </button>
  )
}

// ---------------------------------------------------------------------------
// Series chart (ECharts line) for the selected metric
// ---------------------------------------------------------------------------

// Colours are resolved from the app's design tokens (via resolveSpaTheme) so
// the chart stays legible in both light and dark mode instead of hardcoding
// hex values that only look right on a light background.
function buildSeriesOption(series, spaTheme) {
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
    tooltip: {
      trigger: 'axis',
      backgroundColor: spaTheme.tooltipBg,
      borderColor: spaTheme.border,
      textStyle: { color: spaTheme.tooltipFg },
    },
    xAxis: {
      type: 'category',
      data: x,
      axisLabel: { color: spaTheme.fgMuted, fontSize: 11 },
      axisLine: { lineStyle: { color: spaTheme.border } },
      boundaryGap: false,
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: spaTheme.fgMuted, fontSize: 11 },
      splitLine: { lineStyle: { color: spaTheme.grid } },
    },
    series: [
      {
        type: 'line',
        data: y,
        smooth: true,
        showSymbol: false,
        areaStyle: { opacity: 0.12 },
        lineStyle: { width: 2 },
        itemStyle: { color: spaTheme.primary },
      },
    ],
  }
}

function SeriesChart({ series, loading }) {
  const { theme } = useTheme()
  const spaTheme = useMemo(() => resolveSpaTheme(theme === 'dark'), [theme])
  const option = useMemo(() => buildSeriesOption(series, spaTheme), [series, spaTheme])
  const hasData = (series?.points ?? []).some((p) => Number(p.value) > 0)

  return (
    <div className="ap-panel overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <span className="ap-panel-title">
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
      <SegmentedControl
        size="md"
        value={period}
        onChange={onChange}
        options={PERIODS.map((p) => ({ value: p.id, label: p.label }))}
      />
      <Button variant="ghost" size="icon" onClick={onRefresh} disabled={loading} title="Refresh" aria-label="Refresh usage">
        <RefreshCw size={14} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
      </Button>
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
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2" aria-hidden="true">
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-28 w-full" />)}
        </div>
      )}

      {!loading && error && (
        <EmptyState
          kind="error"
          art={<ErrorFigure />}
          title="Usage didn’t load"
          description={`${error} Nothing was changed.`}
          action={<Button variant="secondary" onClick={loadSummary}><RefreshCw size={14} aria-hidden="true" /> Retry</Button>}
        />
      )}

      {!loading && !error && metrics.length === 0 && (
        <EmptyState
          art={<MeteringWallet />}
          title="No usage yet"
          description="Queries, flows and AI generations are counted here as they run. Viewers never count toward usage."
        />
      )}

      {!loading && !error && metrics.length > 0 && (
        <div className="space-y-5">
          {/* Metric cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
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
