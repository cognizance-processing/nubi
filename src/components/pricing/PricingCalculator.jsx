/**
 * PricingCalculator.jsx — core (OSS-safe) pricing calculator with two competitor
 * comparison sections (src/components/pricing/PricingCalculator.jsx)
 *
 * This component replaces the EE PricingCalculator for the OSS distribution and
 * is re-used by the EE PricingPage.  It has NO EE imports and NO checkout logic.
 *
 * Layout
 * ------
 * 1. Usage sliders (storage, compute, embedded sessions, agent runs, connectors)
 * 2. Team-size inputs (editors + viewers — only used for competitor comparison)
 * 3. Nubi cost recommendation box
 * 4. TWO CLEARLY-DISTINCT comparison sections behind tabs:
 *    (a) "vs BI / Embedded Analytics" — Metabase, Holistics, Hex, Count, Luzmo, Preset, …
 *    (b) "vs Data Orchestration"      — Prefect, Airflow, Dagster, Temporal, MWAA, Composer, Mage
 *
 *    Each section has its OWN usage parameters, because BI is driven by
 *    embedded_sessions + editors/viewers while orchestration is driven by
 *    flow_runs, workers, and serverless minutes.
 *
 * Props
 * -----
 * fxRate                number | null    Live USD→ZAR rate
 * competitorsBi         array | null     BI competitor models (pricing.js format); falls back to FALLBACK_COMPETITORS_BI
 * competitorsOrch       array | null     Orchestration competitor models; falls back to FALLBACK_COMPETITORS_ORCHESTRATION
 */

import { useState, useMemo, useCallback } from 'react'
import {
  ChevronDown, ChevronUp, Info, Users, Zap, Database, Globe, Cpu,
  GitBranch, Server, BarChart3, Wallet, Warehouse, Search,
} from 'lucide-react'
import {
  computeZar, formatZar, recommendNubi, estimateWarehouseCu,
  FALLBACK_COMPETITORS_BI, FALLBACK_COMPETITORS_ORCHESTRATION,
  FALLBACK_COMPETITORS_WAREHOUSE, WAREHOUSE_CU_MULTIPLIER,
  WALLET_OVERAGE_RATES,
} from '../../lib/pricing.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtUsd(usd) {
  if (usd == null) return 'Custom'
  return '$' + Math.round(usd).toLocaleString('en-US')
}

// ---------------------------------------------------------------------------
// UsageInput slider
// ---------------------------------------------------------------------------

function UsageInput({ label, icon: Icon, value, onChange, min = 0, max, step = 1, unit = '' }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="flex items-center gap-1.5 text-xs font-medium text-muted uppercase tracking-wide">
        {Icon && <Icon size={12} />}
        {label}
      </span>
      <div className="flex items-center gap-2">
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="flex-1 h-1.5 rounded-full accent-accent cursor-pointer"
        />
        <span className="w-20 text-right text-sm font-mono font-medium text-fg shrink-0">
          {value.toLocaleString()}{unit}
        </span>
      </div>
    </label>
  )
}

// ---------------------------------------------------------------------------
// Tab button
// ---------------------------------------------------------------------------

function Tab({ active, onClick, icon: Icon, children }) {
  return (
    <button
      onClick={onClick}
      className={[
        'flex items-center gap-2 px-4 py-2.5 text-sm font-semibold rounded-xl transition-colors',
        active
          ? 'bg-accent text-white shadow-sm'
          : 'bg-surface-2 text-muted hover:text-fg border border-border',
      ].join(' ')}
    >
      {Icon && <Icon size={14} />}
      {children}
    </button>
  )
}

// ---------------------------------------------------------------------------
// Nubi recommendation box
// ---------------------------------------------------------------------------

const TIER_COLORS = {
  free:       'bg-surface-2 text-muted',
  starter:    'bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300',
  team:       'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300',
  pro:        'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300',
  enterprise: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
}

function NubiRecommendation({ recommendation, fxRate, seats }) {
  const hasOverages = recommendation.overage_zar > 0
  const tierIsFree = recommendation.tier.id === 'free'

  return (
    <div className="rounded-xl border-2 border-accent/30 bg-accent/5 px-5 py-4 space-y-3">
      {/* Tier + total price */}
      <div className="flex flex-wrap items-center gap-3">
        <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold uppercase tracking-wide ${TIER_COLORS[recommendation.tier.id] ?? TIER_COLORS.free}`}>
          {recommendation.tier.name}
        </span>
        <span className="font-display font-semibold text-xl text-fg">
          {formatZar(recommendation.total_zar)} / month
        </span>
        {recommendation.tier.id !== 'free' && (
          <span className="text-xs text-muted">
            (${recommendation.tier.usd_monthly} USD anchor)
          </span>
        )}
      </div>

      {/* Cost breakdown */}
      <div className="flex flex-wrap gap-4 text-sm text-muted">
        <span>Flat plan: {formatZar(recommendation.base_zar)}</span>
        {hasOverages && (
          <span className="flex items-center gap-1 text-amber-600 dark:text-amber-400">
            <Wallet size={12} />
            + {formatZar(recommendation.overage_zar)} from usage wallet
          </span>
        )}
        <span className="font-medium text-teal-600 dark:text-teal-400">
          No per-seat charges
        </span>
      </div>

      {/* Overage line items */}
      {recommendation.overages.length > 0 && (
        <ul className="text-xs text-muted space-y-0.5 rounded-lg bg-surface border border-border px-3 py-2">
          <li className="font-semibold text-fg mb-1">Wallet overage breakdown:</li>
          {recommendation.overages.map((item) => (
            <li key={item.label} className="flex justify-between">
              <span>{item.label}</span>
              <span className="font-mono">{formatZar(item.zar)}</span>
            </li>
          ))}
        </ul>
      )}

      {/* Wallet explainer — shown when there are overages or on paid tiers */}
      {!tierIsFree && (
        <div className="rounded-lg bg-surface border border-border px-3 py-2.5 text-xs text-muted space-y-1">
          <p className="flex items-center gap-1.5 font-semibold text-fg">
            <Wallet size={12} className="text-accent" />
            How usage wallet works
          </p>
          <p>
            Your {recommendation.tier.name} plan includes a monthly quota. Usage beyond that is
            deducted from your <strong className="text-fg">prepaid credit balance</strong>{' '}
            at the rates below — never billed per seat.
            Set an <strong className="text-fg">auto-topup threshold</strong> so your card is
            charged automatically when balance runs low (like Anthropic's auto-reload).
            A <strong className="text-fg">monthly spend cap</strong> prevents runaway charges.
          </p>
          <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 pt-1 text-[11px]">
            <span>Storage: R {WALLET_OVERAGE_RATES.storage_zar_per_gb}/GB</span>
            <span>Compute: R {WALLET_OVERAGE_RATES.compute_zar_per_1000_cu}/1k CUs</span>
            <span>AI calls: R {WALLET_OVERAGE_RATES.ai_call_zar_per_call}/call</span>
            <span>Sessions: R {WALLET_OVERAGE_RATES.session_zar_per_10k}/10k</span>
          </div>
        </div>
      )}

      {seats && (
        <p className="text-xs text-muted/70 border-t border-accent/20 pt-2">
          Your cost stays the same whether you have {seats.editors} or {seats.editors * 10} editors,
          and {seats.viewers} or {seats.viewers * 10} viewers.
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// BI competitor comparison section
// ---------------------------------------------------------------------------

function BiComparisonSection({ competitors, usage, seats, fxRate, recommendation }) {
  const rate = fxRate ?? 16.26
  const nubiUsd = recommendation.tier.usd_monthly + Math.ceil(recommendation.overage_zar / rate)

  return (
    <div className="space-y-3">
      {/* Context note */}
      <p className="text-xs text-muted">
        Comparing for <strong className="text-fg">{usage.embedded_sessions.toLocaleString()} embedded sessions/mo</strong>,{' '}
        <strong className="text-fg">{seats.editors} editors</strong>, and{' '}
        <strong className="text-fg">{seats.viewers} viewers</strong>.
        Nubi's cost is identical at any team size.
      </p>

      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-surface-2">
              <th className="text-left px-4 py-2.5 font-semibold text-muted text-xs uppercase tracking-wide">Tool</th>
              <th className="text-right px-4 py-2.5 font-semibold text-muted text-xs uppercase tracking-wide">Est. USD/mo</th>
              <th className="text-right px-4 py-2.5 font-semibold text-muted text-xs uppercase tracking-wide">Est. ZAR/mo</th>
            </tr>
          </thead>
          <tbody>
            {/* Nubi row pinned first */}
            <tr className="border-b border-border bg-accent/5">
              <td className="px-4 py-2.5">
                <span className="font-semibold text-sm text-fg">Nubi {recommendation.tier.name}</span>
                <span className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded-full bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300 text-[9px] font-bold uppercase tracking-wide">
                  no seat charges
                </span>
              </td>
              <td className="px-4 py-2.5 text-right font-mono text-sm font-bold text-fg">
                {fmtUsd(nubiUsd)}
              </td>
              <td className="px-4 py-2.5 text-right font-mono text-sm font-bold text-fg">
                {formatZar(recommendation.total_zar)}
              </td>
            </tr>

            {competitors.map((comp) => {
              let usdCost = null
              try { usdCost = comp.model(usage, seats) } catch { /* ignore */ }
              const zarCost = usdCost != null ? Math.ceil(usdCost * rate) : null

              return (
                <tr key={comp.id} className="border-b border-border last:border-0">
                  <td className="px-4 py-2.5">
                    <span className="font-medium text-sm text-fg">{comp.name}</span>
                    {comp.highlight_seat_penalty && (
                      <span className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300 text-[9px] font-bold uppercase tracking-wide">
                        seat-based
                      </span>
                    )}
                    <p className="text-xs text-muted mt-0.5 leading-tight">{comp.note}</p>
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-sm text-fg">
                    {usdCost != null ? fmtUsd(usdCost) : 'Custom'}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-sm text-fg">
                    {zarCost != null ? formatZar(zarCost) : 'Custom'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Seat penalty callout */}
      {seats.editors > 5 && (
        <div className="rounded-xl bg-teal-50 dark:bg-teal-900/20 border border-teal-200 dark:border-teal-800 px-4 py-3 text-xs text-teal-800 dark:text-teal-200">
          With {seats.editors} editors and {seats.viewers} viewers, seat-priced tools
          like Metabase and Hex charge for every person on your team.
          Nubi's cost stays flat at 1 editor or 1,000.
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Orchestration comparison section
// ---------------------------------------------------------------------------

const DEFAULT_ORCH_USAGE = {
  flow_runs_per_month: 5000,
  serverless_minutes: 5000,
  workers: 2,
  deployments: 1,
  seats: 5,
  hours_per_month: 730,
  block_runs: 10000,
  compute_hours: 10,
  assets_per_run: 2,
  actions_per_month: 500000,
  dcu_per_hour: 12,
}

function OrchComparisonSection({ competitors, orchUsage, onOrchUsage, fxRate, recommendation }) {
  const rate = fxRate ?? 16.26
  const nubiUsd = recommendation.tier.usd_monthly

  return (
    <div className="space-y-4">
      {/* Context note */}
      <div className="rounded-xl bg-surface-2 border border-border px-4 py-3 text-xs text-muted space-y-1">
        <p className="font-semibold text-fg text-sm">
          Nubi Flows has no per-run, per-credit, or always-on environment fee.
        </p>
        <p>
          Unlike Prefect ($400/mo for 8 seats + compute), Dagster ($0.035/credit per materialization),
          or AWS MWAA ($360/mo before a single task runs), Nubi's flows orchestration is included in
          your platform subscription — on the same meter as dashboards, queries, and connectors.
        </p>
      </div>

      {/* Orchestration-specific usage inputs */}
      <div className="rounded-xl border border-border px-4 py-4 space-y-4 bg-surface">
        <p className="text-xs font-semibold text-muted uppercase tracking-wide">Orchestration usage inputs</p>
        <div className="grid gap-4 sm:grid-cols-2">
          <UsageInput
            label="Flow / pipeline runs / month"
            icon={GitBranch}
            value={orchUsage.flow_runs_per_month}
            onChange={(v) => onOrchUsage('flow_runs_per_month', v)}
            min={100}
            max={100000}
            step={500}
          />
          <UsageInput
            label="Workers / parallel executors"
            icon={Server}
            value={orchUsage.workers}
            onChange={(v) => onOrchUsage('workers', v)}
            min={1}
            max={20}
            step={1}
          />
          <UsageInput
            label="Seats / users (team size)"
            icon={Users}
            value={orchUsage.seats}
            onChange={(v) => onOrchUsage('seats', v)}
            min={1}
            max={50}
            step={1}
          />
          <UsageInput
            label="Serverless minutes / month"
            icon={Cpu}
            value={orchUsage.serverless_minutes}
            onChange={(v) => onOrchUsage('serverless_minutes', v)}
            min={500}
            max={50000}
            step={500}
          />
        </div>
      </div>

      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-surface-2">
              <th className="text-left px-4 py-2.5 font-semibold text-muted text-xs uppercase tracking-wide">Tool</th>
              <th className="text-left px-4 py-2.5 font-semibold text-muted text-xs uppercase tracking-wide hidden sm:table-cell">Pricing model</th>
              <th className="text-right px-4 py-2.5 font-semibold text-muted text-xs uppercase tracking-wide">Est. USD/mo</th>
              <th className="text-right px-4 py-2.5 font-semibold text-muted text-xs uppercase tracking-wide">Est. ZAR/mo</th>
            </tr>
          </thead>
          <tbody>
            {/* Nubi row */}
            <tr className="border-b border-border bg-accent/5">
              <td className="px-4 py-2.5">
                <span className="font-semibold text-sm text-fg">Nubi Flows ({recommendation.tier.name})</span>
                <div className="mt-0.5 flex flex-wrap gap-1">
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300 text-[9px] font-bold uppercase tracking-wide">
                    no per-run fee
                  </span>
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300 text-[9px] font-bold uppercase tracking-wide">
                    no idle env cost
                  </span>
                </div>
              </td>
              <td className="px-4 py-2.5 text-xs text-muted hidden sm:table-cell">
                Bundled with BI + query + connectors
              </td>
              <td className="px-4 py-2.5 text-right font-mono text-sm font-bold text-fg">
                {fmtUsd(nubiUsd)}
              </td>
              <td className="px-4 py-2.5 text-right font-mono text-sm font-bold text-fg">
                {formatZar(recommendation.total_zar)}
              </td>
            </tr>

            {competitors.map((comp) => {
              let usdCost = null
              try { usdCost = comp.model({ ...orchUsage }) } catch { /* ignore */ }
              const zarCost = usdCost != null ? Math.ceil(usdCost * rate) : null

              const modelTypeLabels = {
                'per-run':    'Per run / credit',
                'per-seat':   'Per seat',
                'flat':       'Flat subscription',
                'infra':      'Always-on infrastructure',
                'per-action': 'Per action / event',
              }

              return (
                <tr key={comp.id} className="border-b border-border last:border-0">
                  <td className="px-4 py-2.5">
                    <span className="font-medium text-sm text-fg">{comp.name}</span>
                    {comp.model_type === 'infra' && (
                      <span className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300 text-[9px] font-bold uppercase tracking-wide">
                        always-on cost
                      </span>
                    )}
                    {comp.model_type === 'per-run' && (
                      <span className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded-full bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300 text-[9px] font-bold uppercase tracking-wide">
                        per-run metered
                      </span>
                    )}
                    {comp.model_type === 'per-action' && (
                      <span className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded-full bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300 text-[9px] font-bold uppercase tracking-wide">
                        per-action metered
                      </span>
                    )}
                    <p className="text-xs text-muted mt-0.5 leading-tight">{comp.note}</p>
                  </td>
                  <td className="px-4 py-2.5 text-xs text-muted hidden sm:table-cell">
                    {modelTypeLabels[comp.model_type] ?? comp.model_type}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-sm text-fg">
                    {usdCost != null ? fmtUsd(usdCost) : 'Custom'}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-sm text-fg">
                    {zarCost != null ? formatZar(zarCost) : 'Custom'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-muted/80 italic">
        Orchestration-only tools above do not include BI, dashboards, connectors, or embedded analytics —
        Nubi bundles all of these at the same price point.
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Warehouse / OLAP comparison section
// ---------------------------------------------------------------------------

const DEFAULT_WAREHOUSE_USAGE = {
  data_gb: 100,            // total Parquet in the lakehouse
  queries_per_month: 5000, // warehouse-pool queries (big-table, cache misses)
  avg_gb_scanned: 2,       // per-query working set after partition/column pruning
}

function WarehouseComparisonSection({ competitors, whUsage, onWhUsage, fxRate }) {
  const rate = fxRate ?? 16.26

  // Nubi cost: Pro+ plan (warehouse is a Pro/Enterprise feature) carrying the
  // dataset in lakehouse storage and the workload as 4×-billed compute units.
  const warehouseCu = estimateWarehouseCu(whUsage)
  const recommendation = recommendNubi(
    {
      storage_gb: whUsage.data_gb,
      compute_units: warehouseCu,
      embedded_sessions: 0,
      agent_runs: 0,
      connectors: 1,
      flow_runs_per_month: 0,
    },
    fxRate,
    { minTierId: 'pro' },
  )
  const nubiUsd = recommendation.tier.usd_monthly + Math.ceil(recommendation.overage_zar / rate)

  // Honest envelope: Nubi's warehouse is a single machine per query.  Beyond
  // this, a dedicated warehouse is genuinely the better tool — say so.
  const outOfEnvelope = whUsage.data_gb > 1000 || whUsage.avg_gb_scanned > 20

  return (
    <div className="space-y-4">
      {/* Context note — fair framing in BOTH directions */}
      <div className="rounded-xl bg-surface-2 border border-border px-4 py-3 text-xs text-muted space-y-1">
        <p className="font-semibold text-fg text-sm">
          The hosted DuckDB warehouse runs big-table queries on dedicated 8 GB+ machines — no separate warehouse bill.
        </p>
        <p>
          Warehouse queries draw from the same compute-unit quota at a {WAREHOUSE_CU_MULTIPLIER}× rate;
          repeated dashboard reads come from rollups and cache for free.
          To keep the comparison fair: the engines below are <strong className="text-fg">warehouse-only</strong> —
          they don't include dashboards, embedded analytics, or orchestration, while the Nubi price does.
          They are also distributed engines that genuinely outperform Nubi's single-node pool on
          multi-terabyte scans and huge ad-hoc joins.
        </p>
      </div>

      {outOfEnvelope && (
        <div className="rounded-xl bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 px-4 py-3 text-xs text-amber-800 dark:text-amber-200">
          <strong>At this scale, a dedicated warehouse is the right tool.</strong>{' '}
          Nubi's hosted warehouse runs each query on one machine — its sweet spot is tables up to
          ~1B rows with selective scans. For workloads like this, connect your own BigQuery or
          ClickHouse as a Nubi datastore instead: queries push down to their engine, on their billing,
          and your dashboards, RLS, and caching stay in Nubi.
        </div>
      )}

      {/* Warehouse-specific usage inputs */}
      <div className="rounded-xl border border-border px-4 py-4 space-y-4 bg-surface">
        <p className="text-xs font-semibold text-muted uppercase tracking-wide">Warehouse usage inputs</p>
        <div className="grid gap-4 sm:grid-cols-2">
          <UsageInput
            label="Dataset size"
            icon={Database}
            value={whUsage.data_gb}
            onChange={(v) => onWhUsage('data_gb', v)}
            min={10} max={2000} step={10} unit=" GB"
          />
          <UsageInput
            label="Warehouse queries / month"
            icon={Search}
            value={whUsage.queries_per_month}
            onChange={(v) => onWhUsage('queries_per_month', v)}
            min={100} max={100000} step={100}
          />
          <UsageInput
            label="Avg data scanned / query"
            icon={Cpu}
            value={whUsage.avg_gb_scanned}
            onChange={(v) => onWhUsage('avg_gb_scanned', v)}
            min={0.5} max={50} step={0.5} unit=" GB"
          />
        </div>
        <p className="text-[11px] text-muted/80">
          ≈ {warehouseCu.toLocaleString()} compute units / month at the {WAREHOUSE_CU_MULTIPLIER}× warehouse rate.
          Partitioned Parquet + column pruning means a typical query scans a small fraction of the dataset.
        </p>
      </div>

      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-surface-2">
              <th className="text-left px-4 py-2.5 font-semibold text-muted text-xs uppercase tracking-wide">Engine</th>
              <th className="text-left px-4 py-2.5 font-semibold text-muted text-xs uppercase tracking-wide hidden sm:table-cell">Pricing model</th>
              <th className="text-right px-4 py-2.5 font-semibold text-muted text-xs uppercase tracking-wide">Est. USD/mo</th>
              <th className="text-right px-4 py-2.5 font-semibold text-muted text-xs uppercase tracking-wide">Est. ZAR/mo</th>
            </tr>
          </thead>
          <tbody>
            {/* Nubi row pinned first */}
            <tr className="border-b border-border bg-accent/5">
              <td className="px-4 py-2.5">
                <span className="font-semibold text-sm text-fg">
                  Nubi {recommendation.tier.name} + hosted warehouse
                </span>
                <div className="mt-0.5 flex flex-wrap gap-1">
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300 text-[9px] font-bold uppercase tracking-wide">
                    BI + flows included
                  </span>
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300 text-[9px] font-bold uppercase tracking-wide">
                    cached reads free
                  </span>
                </div>
              </td>
              <td className="px-4 py-2.5 text-xs text-muted hidden sm:table-cell">
                Plan + {WAREHOUSE_CU_MULTIPLIER}× CU on warehouse scans
              </td>
              <td className="px-4 py-2.5 text-right font-mono text-sm font-bold text-fg">
                {fmtUsd(nubiUsd)}
              </td>
              <td className="px-4 py-2.5 text-right font-mono text-sm font-bold text-fg">
                {formatZar(recommendation.total_zar)}
              </td>
            </tr>

            {competitors.map((comp) => {
              let usdCost = null
              try { usdCost = comp.model({ ...whUsage }) } catch { /* ignore */ }
              const zarCost = usdCost != null ? Math.ceil(usdCost * rate) : null

              return (
                <tr key={comp.id} className="border-b border-border last:border-0">
                  <td className="px-4 py-2.5">
                    <span className="font-medium text-sm text-fg">{comp.name}</span>
                    {comp.model_type === 'per-scan' && (
                      <span className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded-full bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300 text-[9px] font-bold uppercase tracking-wide">
                        per-scan metered
                      </span>
                    )}
                    {comp.model_type === 'infra' && (
                      <span className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300 text-[9px] font-bold uppercase tracking-wide">
                        always-on cost
                      </span>
                    )}
                    <p className="text-xs text-muted mt-0.5 leading-tight">{comp.note}</p>
                  </td>
                  <td className="px-4 py-2.5 text-xs text-muted hidden sm:table-cell">
                    {comp.model_type === 'per-scan' ? 'Pay per TB scanned' : 'Provisioned service'}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-sm text-fg">
                    {usdCost != null ? fmtUsd(usdCost) : 'Custom'}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-sm text-fg">
                    {zarCost != null ? formatZar(zarCost) : 'Custom'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-muted/80 italic">
        Fair-comparison notes: competitor estimates assume well-tuned auto-idle/auto-suspend (real bills
        are often higher with default settings); Nubi's estimate assumes your workload fits a single
        8 GB+ machine per query and that repeat queries hit cache/rollups. Standalone warehouses don't
        include dashboards, embedded analytics, flows, or connectors — but they outperform Nubi on
        multi-TB scans. If you already pay for one, connect it as a datastore instead of moving data.
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// PricingCalculator — main export
// ---------------------------------------------------------------------------

const DEFAULT_USAGE = {
  storage_gb: 25,
  compute_units: 10000,
  embedded_sessions: 10000,
  agent_runs: 20,
  connectors: 5,
  flow_runs_per_month: 2000,
}

const DEFAULT_SEATS = { editors: 8, viewers: 500 }

/**
 * @param {{
 *   fxRate?: number | null,
 *   competitorsBi?: object[] | null,
 *   competitorsOrch?: object[] | null,
 * }} props
 */
export default function PricingCalculator({
  fxRate = null,
  competitorsBi = null,
  competitorsOrch = null,
  competitorsWarehouse = null,
}) {
  const [usage, setUsage] = useState(DEFAULT_USAGE)
  const [seats, setSeats] = useState(DEFAULT_SEATS)
  const [activeTab, setActiveTab] = useState('bi') // 'bi' | 'orchestration' | 'warehouse'
  const [showComparison, setShowComparison] = useState(true)
  const [orchUsage, setOrchUsage] = useState(DEFAULT_ORCH_USAGE)
  const [whUsage, setWhUsage] = useState(DEFAULT_WAREHOUSE_USAGE)

  const setField = useCallback((key, val) => setUsage((u) => ({ ...u, [key]: val })), [])
  const setSeatField = useCallback((key, val) => setSeats((s) => ({ ...s, [key]: val })), [])
  const setOrchField = useCallback((key, val) => setOrchUsage((u) => ({ ...u, [key]: val })), [])
  const setWhField = useCallback((key, val) => setWhUsage((u) => ({ ...u, [key]: val })), [])

  const biCompetitors = competitorsBi ?? FALLBACK_COMPETITORS_BI
  const orchCompetitors = competitorsOrch ?? FALLBACK_COMPETITORS_ORCHESTRATION
  const warehouseCompetitors = competitorsWarehouse ?? FALLBACK_COMPETITORS_WAREHOUSE

  const recommendation = useMemo(() => recommendNubi(usage, fxRate), [usage, fxRate])

  return (
    <section className="rounded-2xl border border-border bg-surface overflow-hidden">
      {/* Section header */}
      <div className="px-6 py-5 border-b border-border bg-surface-2">
        <h2 className="font-display font-semibold text-base text-fg">
          Estimate your monthly cost
        </h2>
        <p className="text-xs text-muted mt-1">
          Adjust your expected usage. Nubi charges for what you use — never per seat.
        </p>
      </div>

      <div className="p-6 space-y-6">

        {/* ---------------------------------------------------------------- */}
        {/* Usage sliders                                                     */}
        {/* ---------------------------------------------------------------- */}
        <div className="grid gap-5 sm:grid-cols-2">
          <UsageInput
            label="Storage"
            icon={Database}
            value={usage.storage_gb}
            onChange={(v) => setField('storage_gb', v)}
            min={0} max={500} step={5} unit=" GB"
          />
          <UsageInput
            label="Compute units / month"
            icon={Cpu}
            value={usage.compute_units}
            onChange={(v) => setField('compute_units', v)}
            min={0} max={200000} step={1000}
          />
          <UsageInput
            label="Embedded sessions / month"
            icon={Globe}
            value={usage.embedded_sessions}
            onChange={(v) => setField('embedded_sessions', v)}
            min={0} max={200000} step={1000}
          />
          <UsageInput
            label="Agent / kernel runs / month"
            icon={Zap}
            value={usage.agent_runs}
            onChange={(v) => setField('agent_runs', v)}
            min={0} max={2000} step={10}
          />
          <UsageInput
            label="Connectors"
            value={usage.connectors}
            onChange={(v) => setField('connectors', v)}
            min={1} max={50} step={1}
          />
          <UsageInput
            label="Flows / pipeline runs / month"
            icon={GitBranch}
            value={usage.flow_runs_per_month}
            onChange={(v) => setField('flow_runs_per_month', v)}
            min={0} max={100000} step={500}
          />
        </div>

        {/* Team-size inputs — affects competitor comparison */}
        <div className="rounded-xl bg-surface-2 border border-border px-4 py-4 space-y-3">
          <div className="flex items-center gap-2">
            <Users size={13} className="text-muted" />
            <span className="text-xs font-semibold text-muted uppercase tracking-wide">
              Team size (for BI competitor comparison)
            </span>
          </div>
          <p className="text-xs text-muted">
            Nubi charges the same regardless of seats. See how seat-priced tools scale below.
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            <UsageInput
              label="Editors / creators"
              value={seats.editors}
              onChange={(v) => setSeatField('editors', v)}
              min={1} max={200} step={1}
            />
            <UsageInput
              label="Viewers / end-users"
              value={seats.viewers}
              onChange={(v) => setSeatField('viewers', v)}
              min={0} max={5000} step={50}
            />
          </div>
        </div>

        {/* ---------------------------------------------------------------- */}
        {/* Nubi recommendation                                               */}
        {/* ---------------------------------------------------------------- */}
        <NubiRecommendation
          recommendation={recommendation}
          fxRate={fxRate}
          seats={seats}
        />

        {/* ---------------------------------------------------------------- */}
        {/* Competitor comparison — tabbed                                    */}
        {/* ---------------------------------------------------------------- */}
        <div>
          <button
            onClick={() => setShowComparison((v) => !v)}
            className="flex items-center gap-2 text-sm font-medium text-muted hover:text-fg transition-colors"
            aria-expanded={showComparison}
          >
            {showComparison ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
            Compare with alternatives
            {!showComparison && (
              <span className="text-xs text-muted/60">(click to expand)</span>
            )}
          </button>

          {showComparison && (
            <div className="mt-4 space-y-4">
              {/* Tab row */}
              <div
                className="flex gap-2 p-1 rounded-xl bg-surface-2 border border-border w-fit"
                role="tablist"
                aria-label="Competitor comparison category"
              >
                <Tab
                  active={activeTab === 'bi'}
                  onClick={() => setActiveTab('bi')}
                  icon={BarChart3}
                >
                  vs BI / Embedded Analytics
                </Tab>
                <Tab
                  active={activeTab === 'orchestration'}
                  onClick={() => setActiveTab('orchestration')}
                  icon={GitBranch}
                >
                  vs Data Orchestration
                </Tab>
                <Tab
                  active={activeTab === 'warehouse'}
                  onClick={() => setActiveTab('warehouse')}
                  icon={Warehouse}
                >
                  vs Warehouse / OLAP
                </Tab>
              </div>

              {/* Tab panels */}
              {activeTab === 'bi' && (
                <BiComparisonSection
                  competitors={biCompetitors}
                  usage={usage}
                  seats={seats}
                  fxRate={fxRate}
                  recommendation={recommendation}
                />
              )}

              {activeTab === 'orchestration' && (
                <OrchComparisonSection
                  competitors={orchCompetitors}
                  orchUsage={orchUsage}
                  onOrchUsage={setOrchField}
                  fxRate={fxRate}
                  recommendation={recommendation}
                />
              )}

              {activeTab === 'warehouse' && (
                <WarehouseComparisonSection
                  competitors={warehouseCompetitors}
                  whUsage={whUsage}
                  onWhUsage={setWhField}
                  fxRate={fxRate}
                />
              )}

              {/* Shared disclaimer */}
              <div className="flex items-start gap-2 text-xs text-muted">
                <Info size={12} className="mt-0.5 shrink-0" />
                <p>
                  Competitor estimates are based on publicly available pricing pages as of June 2026.
                  Actual prices depend on your specific contract and usage.
                  Always verify with the vendor before making a purchasing decision.
                  ZAR amounts converted at{' '}
                  {fxRate != null
                    ? `1 USD = R ${fxRate.toFixed(2)}`
                    : '1 USD = R 16.26 (reference)'}.
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
