/**
 * PricingCalculator.jsx — interactive usage-based pricing estimator.
 *
 * Sits on PricingPage below the tier grid.  The user enters expected monthly
 * usage (storage GB, compute units, embedded sessions, agent/kernel runs,
 * connectors) and team size (seats — does not affect Nubi cost but dramatically
 * affects seat-priced competitors).
 *
 * Outputs
 * -------
 * 1. Recommended Nubi tier + estimated ZAR cost (base + overages).
 * 2. Side-by-side competitor cost table for the same usage + seat count.
 *
 * Key point made visible: Nubi's cost is flat as seats grow; seat-priced
 * competitors climb linearly.
 *
 * Competitor models (as of June 2026 — estimates, not quotes)
 * -----------------------------------------------------------
 * See COMPETITOR_MODELS below.  Each model encodes the pricing formula as a
 * pure function (usage, seats) → USD/month.
 *
 * Caveat: competitor prices are encoded from June 2026 public pricing pages /
 * third-party sources and are estimates only.  Always verify with the vendor
 * before making a purchasing decision.
 */

import { useState, useMemo, useCallback } from 'react'
import { ChevronDown, ChevronUp, Info, Users, Zap, Database, Globe, Cpu } from 'lucide-react'
import { computeZar, formatZar } from '../../lib/ee/billing.js'

// ---------------------------------------------------------------------------
// Nubi tier definitions (mirrors FALLBACK_TIERS, used for recommendation logic)
// ---------------------------------------------------------------------------

const NUBI_TIERS = [
  {
    id: 'free',
    name: 'Free',
    usd_monthly: 0,
    quotas: { connectors: 3, storage_gb: 2, compute_units: 500, embedded_sessions: 0, agent_runs: 0, ai_calls: 0 },
    overages: null,
  },
  {
    id: 'starter',
    name: 'Starter',
    usd_monthly: 79,
    quotas: { connectors: 10, storage_gb: 10, compute_units: 5000, embedded_sessions: 5000, agent_runs: 0, ai_calls: 10 },
    overages: {
      storage_zar_per_gb: 1.50,
      compute_zar_per_1000_cu: 100,
      ai_call_zar_per_call: 5,
      session_zar_per_10k: 50,
      agent_run_zar_per_run: null,
      storage_hard_stop_gb: 20,
      compute_hard_stop_cu: 5000,
    },
  },
  {
    id: 'pro',
    name: 'Pro',
    usd_monthly: 199,
    quotas: { connectors: Infinity, storage_gb: 50, compute_units: 10000, embedded_sessions: 25000, agent_runs: 50, ai_calls: 50 },
    overages: {
      storage_zar_per_gb: 1.50,
      compute_zar_per_1000_cu: 100,
      ai_call_zar_per_call: 5,
      session_zar_per_10k: 50,
      agent_run_zar_per_run: 2,
      storage_hard_stop_gb: null,
      compute_hard_stop_cu: null,
    },
  },
  {
    id: 'business',
    name: 'Business',
    usd_monthly: 499,
    quotas: { connectors: Infinity, storage_gb: 200, compute_units: 40000, embedded_sessions: 100000, agent_runs: 200, ai_calls: 200 },
    overages: {
      storage_zar_per_gb: 1.50,
      compute_zar_per_1000_cu: 100,
      ai_call_zar_per_call: 5,
      session_zar_per_10k: 50,
      agent_run_zar_per_run: 2,
      storage_hard_stop_gb: null,
      compute_hard_stop_cu: null,
    },
  },
  {
    id: 'enterprise',
    name: 'Enterprise',
    usd_monthly: 1799,
    quotas: { connectors: Infinity, storage_gb: 500, compute_units: 200000, embedded_sessions: Infinity, agent_runs: 1000, ai_calls: 500 },
    overages: {
      storage_zar_per_gb: 1.50,
      compute_zar_per_1000_cu: 100,
      ai_call_zar_per_call: 5,
      session_zar_per_10k: 0,
      agent_run_zar_per_run: 2,
      storage_hard_stop_gb: null,
      compute_hard_stop_cu: null,
    },
  },
]

// ---------------------------------------------------------------------------
// Competitor pricing models (June 2026 public data — estimates, not quotes)
// ---------------------------------------------------------------------------
// Each entry: { id, name, url, model(usage, seats) → USD/month | 'custom' }
// usage = { storage_gb, compute_units, embedded_sessions, agent_runs, connectors }
// seats = { editors, viewers }
//
// Sources are cited in the research doc; see the pricing blueprint artifact.

const COMPETITOR_MODELS = [
  {
    id: 'metabase_pro',
    name: 'Metabase Pro',
    note: '$575/mo base + $12/interactive viewer (10 included)',
    highlight_seat_penalty: true,
    model({ embedded_sessions }, { viewers }) {
      const base = 575
      // Metabase charges per interactive viewer seat (embedded users count as viewers)
      const effectiveViewers = embedded_sessions > 0
        ? Math.max(viewers, Math.ceil(embedded_sessions / 10))
        : viewers
      const viewerCost = Math.max(0, effectiveViewers - 10) * 12
      return base + viewerCost
    },
  },
  {
    id: 'holistics_standard',
    name: 'Holistics Standard',
    note: '$1,000/mo flat (annual) — unlimited viewers',
    highlight_seat_penalty: false,
    model() {
      return 1000 // flat, unlimited viewers
    },
  },
  {
    id: 'holistics_scs',
    name: 'Holistics SCS',
    note: '$2,000/mo flat (annual) — SAML/SCIM/RBAC',
    highlight_seat_penalty: false,
    model() {
      return 2000
    },
  },
  {
    id: 'lightdash_pro',
    name: 'Lightdash Cloud Pro',
    note: '$3,000/mo flat — unlimited seats & viewers',
    highlight_seat_penalty: false,
    model() {
      return 3000
    },
  },
  {
    id: 'hex_team',
    name: 'Hex Team',
    note: '$75/editor/mo + compute hours',
    highlight_seat_penalty: true,
    model({ compute_units }, { editors }) {
      const seatCost = editors * 75
      // Hex compute: rough estimate $0.05/CU (each CU = 1 min of compute)
      const computeCost = (compute_units / 1000) * 30 // roughly $30/1k CUs on Hex
      return seatCost + computeCost
    },
  },
  {
    id: 'count_pro',
    name: 'Count Pro',
    note: '$49/editor/mo — viewers free',
    highlight_seat_penalty: true,
    model(_, { editors }) {
      return editors * 49
    },
  },
  {
    id: 'embeddable_lite',
    name: 'Embeddable Lite',
    note: '$499/mo for 1,000 sessions; $200 per additional 500',
    highlight_seat_penalty: false,
    model({ embedded_sessions }) {
      const base = 499
      const includedSessions = 1000
      if (embedded_sessions <= includedSessions) return base
      const overage = Math.ceil((embedded_sessions - includedSessions) / 500) * 200
      return base + overage
    },
  },
  {
    id: 'luzmo_starter',
    name: 'Luzmo Starter',
    note: '~$540/mo (€495 annual) — MAU-based',
    highlight_seat_penalty: false,
    model({ embedded_sessions }) {
      // Luzmo is MAU-based; Starter ≈ $540/mo. Overage rough estimate.
      const base = 540
      const includedMau = 250 // Starter tier approximate MAU
      const estimatedMau = embedded_sessions / 4 // rough: 1 MAU = 4 sessions/mo
      if (estimatedMau <= includedMau) return base
      return 2175 // Premium tier at higher MAU
    },
  },
  {
    id: 'preset_professional',
    name: 'Preset Professional',
    note: '$20/user/mo + $500/mo embed add-on for 50 viewers',
    highlight_seat_penalty: true,
    model({ embedded_sessions }, { editors, viewers }) {
      const seats = editors + viewers
      const seatCost = seats * 20
      const embedAddon = embedded_sessions > 0 ? 500 : 0 // $500/mo add-on base
      const viewerOverage = embedded_sessions > 0 ? Math.max(0, Math.ceil(embedded_sessions / 10) - 50) * 10 : 0
      return seatCost + embedAddon + viewerOverage
    },
  },
]

// ---------------------------------------------------------------------------
// Nubi recommendation + cost engine
// ---------------------------------------------------------------------------

/**
 * Find the cheapest Nubi tier covering the given usage, then compute total ZAR
 * cost including overages.
 *
 * @param {{ storage_gb: number, compute_units: number, embedded_sessions: number,
 *            agent_runs: number, connectors: number }} usage
 * @param {number | null} fxRate  USD→ZAR rate; uses 16.26 as fallback
 * @returns {{ tier: object, base_zar: number, overage_zar: number, total_zar: number, overages: object[] }}
 */
function recommendNubi(usage, fxRate) {
  const rate = fxRate ?? 16.26

  for (const tier of NUBI_TIERS) {
    const q = tier.quotas
    const fits =
      (q.connectors >= usage.connectors || q.connectors === Infinity) &&
      (q.storage_gb >= usage.storage_gb || q.storage_gb === Infinity) &&
      (q.compute_units >= usage.compute_units || q.compute_units === Infinity) &&
      (q.embedded_sessions >= usage.embedded_sessions || q.embedded_sessions === Infinity) &&
      (q.agent_runs >= usage.agent_runs || q.agent_runs === Infinity)

    if (fits) {
      const base_zar = computeZar(tier.usd_monthly, rate)
      return {
        tier,
        base_zar,
        overage_zar: 0,
        total_zar: base_zar,
        overages: [],
        is_exact_fit: true,
      }
    }
  }

  // Usage exceeds all tiers without overages — find best tier + compute overages
  // Start from largest tier downwards to find first tier that can handle it with overages
  for (let i = NUBI_TIERS.length - 1; i >= 1; i--) {
    const tier = NUBI_TIERS[i]
    if (!tier.overages) continue
    const q = tier.quotas
    const ov = tier.overages
    const overageItems = []
    let overage_zar = 0

    if (usage.storage_gb > q.storage_gb) {
      const gb = usage.storage_gb - q.storage_gb
      const cost = gb * ov.storage_zar_per_gb
      overage_zar += cost
      overageItems.push({ label: `${gb} GB extra storage`, zar: cost })
    }
    if (usage.compute_units > q.compute_units) {
      const cu = usage.compute_units - q.compute_units
      const cost = (cu / 1000) * ov.compute_zar_per_1000_cu
      overage_zar += cost
      overageItems.push({ label: `${cu.toLocaleString()} extra CUs`, zar: cost })
    }
    if (usage.embedded_sessions > q.embedded_sessions && q.embedded_sessions !== Infinity) {
      const sessions = usage.embedded_sessions - q.embedded_sessions
      const cost = (sessions / 10000) * ov.session_zar_per_10k
      overage_zar += cost
      overageItems.push({ label: `${sessions.toLocaleString()} extra embed sessions`, zar: cost })
    }
    if (usage.agent_runs > q.agent_runs && ov.agent_run_zar_per_run) {
      const runs = usage.agent_runs - q.agent_runs
      const cost = runs * ov.agent_run_zar_per_run
      overage_zar += cost
      overageItems.push({ label: `${runs} extra agent runs`, zar: cost })
    }

    const base_zar = computeZar(tier.usd_monthly, rate)
    return {
      tier,
      base_zar,
      overage_zar: Math.ceil(overage_zar),
      total_zar: base_zar + Math.ceil(overage_zar),
      overages: overageItems,
      is_exact_fit: false,
    }
  }

  // Fallback — Enterprise
  const tier = NUBI_TIERS[NUBI_TIERS.length - 1]
  const base_zar = computeZar(tier.usd_monthly, rate)
  return { tier, base_zar, overage_zar: 0, total_zar: base_zar, overages: [], is_exact_fit: true }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtUsd(usd) {
  if (usd === null || usd === undefined) return 'Custom'
  return '$' + usd.toLocaleString('en-US')
}

// ---------------------------------------------------------------------------
// Input component
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
// Competitor row
// ---------------------------------------------------------------------------

function CompetitorRow({ model, usage, seats, fxRate }) {
  const usdCost = useMemo(() => {
    try { return model.model(usage, seats) }
    catch { return null }
  }, [model, usage, seats])

  const rate = fxRate ?? 16.26
  const zarCost = usdCost != null ? Math.ceil(usdCost * rate) : null

  return (
    <tr className="border-b border-border last:border-0">
      <td className="px-4 py-2.5">
        <span className="font-medium text-sm text-fg">{model.name}</span>
        {model.highlight_seat_penalty && (
          <span className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300 text-[9px] font-bold uppercase tracking-wide">
            seat-based
          </span>
        )}
        <p className="text-xs text-muted mt-0.5 leading-tight">{model.note}</p>
      </td>
      <td className="px-4 py-2.5 text-right font-mono text-sm text-fg">
        {usdCost != null ? fmtUsd(usdCost) : 'Custom'}
      </td>
      <td className="px-4 py-2.5 text-right font-mono text-sm text-fg">
        {zarCost != null ? formatZar(zarCost) : 'Custom'}
      </td>
    </tr>
  )
}

// ---------------------------------------------------------------------------
// PricingCalculator
// ---------------------------------------------------------------------------

const DEFAULT_USAGE = {
  storage_gb: 30,
  compute_units: 8000,
  embedded_sessions: 15000,
  agent_runs: 20,
  connectors: 5,
}

const DEFAULT_SEATS = { editors: 8, viewers: 500 }

/**
 * @param {{ fxRate?: number | null }} props
 */
export default function PricingCalculator({ fxRate = null }) {
  const [usage, setUsage] = useState(DEFAULT_USAGE)
  const [seats, setSeats] = useState(DEFAULT_SEATS)
  const [showCompetitors, setShowCompetitors] = useState(true)

  const setField = useCallback((key, val) =>
    setUsage((u) => ({ ...u, [key]: val })), [])

  const setSeatField = useCallback((key, val) =>
    setSeats((s) => ({ ...s, [key]: val })), [])

  const recommendation = useMemo(() =>
    recommendNubi(usage, fxRate), [usage, fxRate])

  const tierColors = {
    free: 'bg-surface-2 text-muted',
    starter: 'bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300',
    pro: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300',
    business: 'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300',
    enterprise: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  }

  return (
    <section className="rounded-2xl border border-border bg-surface overflow-hidden">
      {/* Header */}
      <div className="px-6 py-5 border-b border-border bg-surface-2">
        <h2 className="font-display font-semibold text-base text-fg">
          Estimate your monthly cost
        </h2>
        <p className="text-xs text-muted mt-1">
          Adjust your expected usage below. Nubi charges for what you use — never per seat.
        </p>
      </div>

      <div className="p-6 space-y-6">
        {/* Usage sliders */}
        <div className="grid gap-5 sm:grid-cols-2">
          <UsageInput
            label="Storage"
            icon={Database}
            value={usage.storage_gb}
            onChange={(v) => setField('storage_gb', v)}
            min={0}
            max={500}
            step={5}
            unit=" GB"
          />
          <UsageInput
            label="Compute units / month"
            icon={Cpu}
            value={usage.compute_units}
            onChange={(v) => setField('compute_units', v)}
            min={0}
            max={200000}
            step={1000}
          />
          <UsageInput
            label="Embedded sessions / month"
            icon={Globe}
            value={usage.embedded_sessions}
            onChange={(v) => setField('embedded_sessions', v)}
            min={0}
            max={200000}
            step={1000}
          />
          <UsageInput
            label="Agent / kernel runs / month"
            icon={Zap}
            value={usage.agent_runs}
            onChange={(v) => setField('agent_runs', v)}
            min={0}
            max={2000}
            step={10}
          />
          <UsageInput
            label="Connectors"
            value={usage.connectors}
            onChange={(v) => setField('connectors', v)}
            min={1}
            max={50}
            step={1}
          />
        </div>

        {/* Seat inputs — only relevant for competitor comparison */}
        <div className="rounded-xl bg-surface-2 border border-border px-4 py-4 space-y-3">
          <div className="flex items-center gap-2">
            <Users size={13} className="text-muted" />
            <span className="text-xs font-semibold text-muted uppercase tracking-wide">
              Team size (for competitor comparison)
            </span>
          </div>
          <p className="text-xs text-muted">
            Nubi charges the same regardless of seats. See how seat-priced competitors
            scale below.
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            <UsageInput
              label="Editors / creators"
              value={seats.editors}
              onChange={(v) => setSeatField('editors', v)}
              min={1}
              max={200}
              step={1}
            />
            <UsageInput
              label="Viewers / end-users"
              value={seats.viewers}
              onChange={(v) => setSeatField('viewers', v)}
              min={0}
              max={5000}
              step={50}
            />
          </div>
        </div>

        {/* Nubi recommendation box */}
        <div className="rounded-xl border-2 border-accent/30 bg-accent/5 px-5 py-4 space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <span
              className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold uppercase tracking-wide ${tierColors[recommendation.tier.id] ?? tierColors.free}`}
            >
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

          <div className="flex flex-wrap gap-4 text-sm text-muted">
            <span>Base: {formatZar(recommendation.base_zar)}</span>
            {recommendation.overage_zar > 0 && (
              <span className="text-amber-600 dark:text-amber-400">
                + {formatZar(recommendation.overage_zar)} overages
              </span>
            )}
            <span className="font-medium text-teal-600 dark:text-teal-400">
              No per-seat charges
            </span>
          </div>

          {/* Overage breakdown */}
          {recommendation.overages.length > 0 && (
            <ul className="text-xs text-muted space-y-0.5">
              {recommendation.overages.map((item) => (
                <li key={item.label} className="flex justify-between">
                  <span>{item.label}</span>
                  <span className="font-mono">{formatZar(item.zar)}</span>
                </li>
              ))}
            </ul>
          )}

          {/* Seat impact note */}
          <p className="text-xs text-muted/70 border-t border-accent/20 pt-2">
            Your cost stays the same whether you have {seats.editors} or {seats.editors * 10} editors,
            and {seats.viewers} or {seats.viewers * 10} viewers.
          </p>
        </div>

        {/* Competitor comparison toggle */}
        <div>
          <button
            onClick={() => setShowCompetitors((v) => !v)}
            className="flex items-center gap-2 text-sm font-medium text-muted hover:text-fg transition-colors"
            aria-expanded={showCompetitors}
          >
            {showCompetitors ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
            Compare with alternatives
            {!showCompetitors && (
              <span className="text-xs text-muted/60">(click to expand)</span>
            )}
          </button>

          {showCompetitors && (
            <div className="mt-4 space-y-3">
              <div className="overflow-x-auto rounded-xl border border-border">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-surface-2">
                      <th className="text-left px-4 py-2.5 font-semibold text-muted text-xs uppercase tracking-wide">
                        Tool
                      </th>
                      <th className="text-right px-4 py-2.5 font-semibold text-muted text-xs uppercase tracking-wide">
                        Est. USD/mo
                      </th>
                      <th className="text-right px-4 py-2.5 font-semibold text-muted text-xs uppercase tracking-wide">
                        Est. ZAR/mo
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {/* Nubi row */}
                    <tr className="border-b border-border bg-accent/5">
                      <td className="px-4 py-2.5">
                        <span className="font-semibold text-sm text-fg">Nubi {recommendation.tier.name}</span>
                        <span className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded-full bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300 text-[9px] font-bold uppercase tracking-wide">
                          no seat charges
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-right font-mono text-sm font-bold text-fg">
                        {fmtUsd(recommendation.tier.usd_monthly + Math.ceil(recommendation.overage_zar / (fxRate ?? 16.26)))}
                      </td>
                      <td className="px-4 py-2.5 text-right font-mono text-sm font-bold text-fg">
                        {formatZar(recommendation.total_zar)}
                      </td>
                    </tr>
                    {COMPETITOR_MODELS.map((m) => (
                      <CompetitorRow
                        key={m.id}
                        model={m}
                        usage={usage}
                        seats={seats}
                        fxRate={fxRate}
                      />
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Disclaimer */}
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

              {/* Seat-penalty callout */}
              {seats.editors > 5 && (
                <div className="rounded-xl bg-teal-50 dark:bg-teal-900/20 border border-teal-200 dark:border-teal-800 px-4 py-3 text-xs text-teal-800 dark:text-teal-200">
                  With {seats.editors} editors and {seats.viewers} viewers, seat-priced tools
                  like Metabase and Hex are charging for every person on your team.
                  Nubi's cost is the same at 1 editor or 1,000.
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
