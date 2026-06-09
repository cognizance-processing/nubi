/**
 * PricingPage — dedicated /pricing page.
 *
 * Public marketing page. Renders the customer-facing billing model and
 * grounded competitor comparisons (BI viewer-tax + orchestration) from
 * src/data/pricing.js. Billing enforcement lives in the EE tree, not here.
 *
 * Live data: fetches GET /api/v1/pricing on mount and updates the tier grid
 * with server-computed ZAR prices (using the daily FX rate).  Falls back to
 * the corrected static TIERS from src/data/pricing.js on any error so the
 * page never goes blank.
 */
import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import {
  Check, X, ArrowRight, ChevronRight, Headset, Star,
  Zap, Database, Bot, Server, Users, XCircle, CheckCircle2,
  SlidersHorizontal, TrendingDown, Shield, Wallet, GitFork, Gauge,
} from 'lucide-react'
import {
  TIERS, BILLING_MODEL, BI_COMPARISON, ORCH_COMPARISON, PRICING_FAQ, ENTERPRISE_NOTE,
  CALC_OPTIONS, ORCH_CALC_OPTIONS, OVERAGE_RATES, OVERAGE_NOTE,
} from '../data/pricing.js'
import { fetchPricingData } from '../lib/pricing.js'

const fmtUSD = (n) => {
  if (!n) return '$0'
  if (n >= 1e6) return `$${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`
  if (n >= 1e3) return `$${Math.round(n / 1e3)}k`
  return `$${Math.round(n)}`
}
const fmtNum = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : `${n}`)

const METER_ICONS = [Users, Zap, Database, Bot, Server]

function Eyebrow({ children }) {
  return (
    <p className="text-xs font-semibold tracking-widest uppercase mb-4 text-brand-teal">{children}</p>
  )
}

function TierCard({ tier }) {
  const hi = tier.highlight
  const hasSla = !!tier.sla
  const zarLabel = tier.price_zar_label
  return (
    <div
      className={`relative flex flex-col rounded-2xl border p-5 transition-all duration-200
        ${hi
          ? 'border-brand-teal/70 bg-surface shadow-xl ring-1 ring-brand-teal/20 lg:-translate-y-2 z-10'
          : 'border-border bg-surface shadow-sm hover:-translate-y-1 hover:shadow-lg hover:border-brand-blue/40'}`}
    >
      {tier.badge && (
        <span className={`absolute -top-3 left-1/2 -translate-x-1/2 inline-flex items-center gap-1 px-3 py-1 rounded-full text-[10px] font-bold uppercase tracking-widest whitespace-nowrap shadow-sm
          ${hi ? 'bg-brand-gradient text-white' : 'bg-surface-2 border border-border text-brand-teal'}`}>
          {hasSla ? <Headset size={10} strokeWidth={2.5} /> : <Star size={10} strokeWidth={2.5} />}
          {tier.badge}
        </span>
      )}
      <h3 className="font-display text-base font-bold text-fg">{tier.name}</h3>
      <div className="mt-1.5 flex items-end gap-1.5">
        <span className="font-display text-3xl font-bold tracking-tight text-fg">{tier.price}</span>
        <span className="text-xs text-muted mb-1">{tier.cadence}</span>
      </div>
      {zarLabel && (
        <p className="mt-0.5 text-[11px] font-medium text-brand-blue tabular-nums">≈ {zarLabel}</p>
      )}
      <p className="mt-2 text-[13px] text-muted leading-relaxed min-h-[52px]">{tier.tagline}</p>

      {/* SLA badge for Enterprise */}
      {hasSla && tier.sla && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          <span className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700 text-amber-700 dark:text-amber-300">
            <Shield size={9} strokeWidth={2.5} />
            {tier.sla.uptime} SLA
          </span>
          <span className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold bg-surface-2 border border-border text-muted">
            P1 &lt;{tier.sla.p1_response_minutes}min
          </span>
        </div>
      )}

      <Link
        to={tier.href}
        className={`mt-4 inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold transition-all min-h-[44px]
          ${hi
            ? 'bg-brand-gradient text-white hover:opacity-90 shadow-sm'
            : 'bg-surface-2 border border-border text-fg hover:border-brand-blue hover:text-brand-blue'}`}
      >
        {tier.cta}
        <ArrowRight size={14} strokeWidth={2.5} />
      </Link>

      <ul className="mt-5 flex flex-col gap-2">
        {tier.features.map((f, i) => {
          const isHeader = f.endsWith('plus:')
          return (
            <li key={i} className={`flex items-start gap-2 text-[13px] ${isHeader ? 'text-muted font-semibold pt-1' : 'text-fg'}`}>
              {!isHeader && (
                <Check size={14} strokeWidth={2.75} className="mt-0.5 shrink-0 text-brand-teal" />
              )}
              <span className={isHeader ? '' : 'leading-snug'}>{f}</span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function ComparisonTable({ rows, columns }) {
  return (
    <div className="overflow-x-auto overscroll-x-contain rounded-2xl border border-border shadow-sm">
      <table className="border-collapse w-full" style={{ minWidth: 720 }}>
        <thead className="sticky top-0 z-10">
          <tr>
            {columns.map((c, i) => (
              <th key={i}
                className={`px-5 py-3.5 text-left bg-surface-2 border-b border-border ${i > 0 ? 'border-l' : ''}`}
                style={i === 0 ? { minWidth: 150 } : { minWidth: 180 }}>
                <span className="text-[11px] font-semibold uppercase tracking-widest text-muted">{c}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri}
              className={`border-b border-border last:border-0 transition-colors
                ${r.isNubi ? '' : 'hover:bg-brand-blue/[0.03]'}`}>
              {r.cells.map((cell, ci) => (
                <td key={ci}
                  className={`px-5 py-4 align-top text-[13px] leading-snug border-border ${ci > 0 ? 'border-l' : ''}
                    ${r.isNubi
                      ? 'bg-brand-teal/[0.08] ' + (ci === 0
                          ? 'font-bold text-brand-teal shadow-[inset_3px_0_0_#17b3a3]'
                          : 'text-fg font-medium')
                      : ((ri % 2 === 1 ? 'bg-surface-2/40 ' : 'bg-surface ') +
                          (ci === 0 ? 'font-semibold text-fg' : 'text-muted'))}`}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function CostCalculator() {
  const [sv, setSv] = useState(50)      // slider 0–100 → viewers (log scale)
  const [editors, setEditors] = useState(5)
  const viewers = Math.round(10 * Math.pow(2500, sv / 100)) // 10 → 25,000

  const results = CALC_OPTIONS
    .map(o => ({ ...o, cost: Math.round(o.annual(viewers, editors)) }))
    .sort((a, b) => a.cost - b.cost)
  const max = Math.max(...results.map(r => r.cost), 1)
  const nubi = results.find(r => r.isNubi)
  const cheapestComp = Math.min(...results.filter(r => !r.isNubi).map(r => r.cost))
  const savings = Math.max(0, cheapestComp - (nubi?.cost ?? 0))
  const multiple = nubi && nubi.cost > 0 ? cheapestComp / nubi.cost : null

  return (
    <div className="rounded-2xl border border-border bg-surface shadow-sm overflow-hidden">
      {/* Inputs */}
      <div className="grid md:grid-cols-2 gap-6 p-6 sm:p-8 border-b border-border bg-surface-2">
        <div>
          <div className="flex items-baseline justify-between mb-3">
            <label htmlFor="calc-viewers" className="text-sm font-semibold text-fg">Dashboard viewers</label>
            <span className="font-display text-xl font-bold text-brand-blue">{fmtNum(viewers)}</span>
          </div>
          <input
            id="calc-viewers" type="range" min="0" max="100" value={sv}
            onChange={e => setSv(Number(e.target.value))}
            className="nubi-range w-full"
            aria-label="Dashboard viewers"
          />
          <div className="flex justify-between text-[11px] text-muted mt-1.5">
            <span>10</span><span>25k</span>
          </div>
        </div>
        <div>
          <div className="flex items-baseline justify-between mb-3">
            <label htmlFor="calc-editors" className="text-sm font-semibold text-fg">Editors (creators)</label>
            <span className="font-display text-xl font-bold text-brand-blue">{editors}</span>
          </div>
          <input
            id="calc-editors" type="range" min="1" max="50" value={editors}
            onChange={e => setEditors(Number(e.target.value))}
            className="nubi-range w-full"
            aria-label="Editors"
          />
          <div className="flex justify-between text-[11px] text-muted mt-1.5">
            <span>1</span><span>50</span>
          </div>
        </div>
      </div>

      {/* Savings headline */}
      <div className="flex flex-wrap items-center justify-center gap-2 px-6 py-4 text-center bg-brand-teal/[0.06] border-b border-border">
        <TrendingDown size={18} className="text-brand-teal" />
        <span className="text-sm sm:text-base text-fg">
          Nubi costs <strong className="text-brand-teal font-bold">{fmtUSD(nubi?.cost ?? 0)}/yr</strong>
          {savings > 0 && (
            <> — that’s <strong className="text-brand-teal font-bold">{fmtUSD(savings)}/yr less</strong>
              {multiple && multiple >= 2 && <> ({Math.round(multiple)}× cheaper)</>} than the next option.</>
          )}
        </span>
      </div>

      {/* Bars */}
      <div className="p-6 sm:p-8 flex flex-col gap-3">
        {results.map(r => (
          <div key={r.name} className="grid grid-cols-[110px_1fr_auto] sm:grid-cols-[150px_1fr_auto] items-center gap-3">
            <div className="min-w-0">
              <div className={`text-sm font-semibold truncate ${r.isNubi ? 'text-brand-teal' : 'text-fg'}`}>
                {r.isNubi && <Star size={12} className="inline mr-1 -mt-0.5 text-brand-teal" strokeWidth={2.5} />}
                {r.name}{r.estimate ? <sup className="text-muted">†</sup> : null}
              </div>
              <div className="text-[11px] text-muted truncate hidden sm:block">{r.note}</div>
            </div>
            <div className="h-7 rounded-md bg-surface-2 overflow-hidden">
              <div
                className={`h-full rounded-md ${r.isNubi ? '' : 'bg-brand-blue/25'}`}
                style={{
                  width: `${Math.max(2, (r.cost / max) * 100)}%`,
                  background: r.isNubi ? 'linear-gradient(90deg, #2456a6, #17b3a3)' : undefined,
                }}
              />
            </div>
            <div className={`text-sm font-bold tabular-nums text-right w-16 ${r.isNubi ? 'text-brand-teal' : 'text-fg'}`}>
              {fmtUSD(r.cost)}
            </div>
          </div>
        ))}
      </div>
      <p className="px-6 sm:px-8 pb-6 text-xs text-muted opacity-70 leading-relaxed">
        Estimated annual cost from each vendor’s public model (before your own warehouse compute).
        † Looker is quote-only; figure is directional. Your actual cost depends on contract terms — verify before switching.
      </p>
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Overage / usage-wallet showcase — the "buy more when you need it" model    */
/* ─────────────────────────────────────────────────────────────────────────── */

const OVERAGE_ICONS = [Database, Zap, Bot, Server, Gauge]

function OverageShowcase() {
  return (
    <div className="rounded-3xl border border-brand-teal/30 bg-surface shadow-sm overflow-hidden">
      {/* Header band */}
      <div className="relative px-6 sm:px-9 py-7 border-b border-border bg-gradient-to-br from-brand-navy/[0.04] via-brand-blue/[0.04] to-brand-teal/[0.07]">
        <div className="flex items-start gap-4">
          <span className="shrink-0 w-12 h-12 rounded-2xl bg-brand-gradient text-white flex items-center justify-center shadow-sm">
            <Wallet size={22} strokeWidth={2} />
          </span>
          <div>
            <h3 className="font-display text-xl sm:text-2xl font-bold text-fg">
              A usage wallet — pay only for what you use
            </h3>
            <p className="mt-1.5 text-sm text-muted leading-relaxed max-w-2xl">
              {OVERAGE_NOTE}
            </p>
          </div>
        </div>
      </div>

      {/* Rate grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 divide-y divide-border sm:divide-y-0 sm:[&>*:nth-child(n+3)]:border-t lg:[&>*:nth-child(n+4)]:border-t lg:[&>*:nth-child(-n+3)]:border-t-0 sm:[&>*:nth-child(2n)]:border-l lg:[&>*]:border-l lg:[&>*:nth-child(3n+1)]:border-l-0 sm:divide-x-0 border-border">
        {OVERAGE_RATES.map((o, i) => {
          const Icon = OVERAGE_ICONS[i % OVERAGE_ICONS.length]
          return (
            <div key={o.label} className="flex items-start gap-3 px-6 py-5 border-border">
              <span className="shrink-0 mt-0.5 w-9 h-9 rounded-xl bg-surface-2 border border-border flex items-center justify-center text-brand-blue">
                <Icon size={16} strokeWidth={2} />
              </span>
              <div className="min-w-0">
                <div className="flex items-baseline gap-1.5">
                  <span className="font-display text-lg font-bold text-fg tabular-nums">{o.rate}</span>
                  <span className="text-xs text-muted">{o.unit}</span>
                </div>
                <p className="text-[13px] font-semibold text-fg mt-0.5">{o.label}</p>
                <p className="text-xs text-muted leading-snug mt-0.5">{o.desc}</p>
              </div>
            </div>
          )
        })}
        {/* trailing cell to balance the 3-col grid (5 rates + this = 6) */}
        <div className="flex flex-col justify-center gap-1 px-6 py-5 border-border bg-surface-2/40">
          <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-widest text-brand-teal">
            <Check size={12} strokeWidth={3} /> Same rate, every paid tier
          </span>
          <p className="text-xs text-muted leading-snug">
            No per-viewer or per-seat overage — ever. Top up once, draw from your wallet, and
            anything beyond simply lands on your next invoice.
          </p>
        </div>
      </div>
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Orchestration cost calculator — the SECOND calculator                      */
/* ─────────────────────────────────────────────────────────────────────────── */

function OrchCalculator() {
  const [envs, setEnvs] = useState(2)
  const [seats, setSeats] = useState(4)

  const results = ORCH_CALC_OPTIONS
    .map(o => ({ ...o, cost: Math.round(o.annual(envs, seats)) }))
    .sort((a, b) => a.cost - b.cost)
  const max = Math.max(...results.map(r => r.cost), 1)
  const nubi = results.find(r => r.isNubi)
  const cheapestComp = Math.min(...results.filter(r => !r.isNubi).map(r => r.cost))
  const savings = Math.max(0, cheapestComp - (nubi?.cost ?? 0))

  return (
    <div className="rounded-2xl border border-border bg-surface shadow-sm overflow-hidden">
      {/* Inputs */}
      <div className="grid md:grid-cols-2 gap-6 p-6 sm:p-8 border-b border-border bg-surface-2">
        <div>
          <div className="flex items-baseline justify-between mb-3">
            <label htmlFor="orch-envs" className="text-sm font-semibold text-fg">Environments (dev / staging / prod)</label>
            <span className="font-display text-xl font-bold text-brand-blue">{envs}</span>
          </div>
          <input
            id="orch-envs" type="range" min="1" max="6" value={envs}
            onChange={e => setEnvs(Number(e.target.value))}
            className="nubi-range w-full" aria-label="Environments"
          />
          <div className="flex justify-between text-[11px] text-muted mt-1.5"><span>1</span><span>6</span></div>
        </div>
        <div>
          <div className="flex items-baseline justify-between mb-3">
            <label htmlFor="orch-seats" className="text-sm font-semibold text-fg">Data engineers (seats)</label>
            <span className="font-display text-xl font-bold text-brand-blue">{seats}</span>
          </div>
          <input
            id="orch-seats" type="range" min="1" max="12" value={seats}
            onChange={e => setSeats(Number(e.target.value))}
            className="nubi-range w-full" aria-label="Seats"
          />
          <div className="flex justify-between text-[11px] text-muted mt-1.5"><span>1</span><span>12</span></div>
        </div>
      </div>

      {/* Headline */}
      <div className="flex flex-wrap items-center justify-center gap-2 px-6 py-4 text-center bg-brand-teal/[0.06] border-b border-border">
        <GitFork size={18} className="text-brand-teal" />
        <span className="text-sm sm:text-base text-fg">
          Flows is <strong className="text-brand-teal font-bold">included</strong> — that&rsquo;s{' '}
          <strong className="text-brand-teal font-bold">{fmtUSD(savings)}/yr</strong> you don&rsquo;t pay a
          standalone orchestrator.
        </span>
      </div>

      {/* Bars */}
      <div className="p-6 sm:p-8 flex flex-col gap-3">
        {results.map(r => (
          <div key={r.name} className="grid grid-cols-[120px_1fr_auto] sm:grid-cols-[190px_1fr_auto] items-center gap-3">
            <div className="min-w-0">
              <div className={`text-sm font-semibold truncate ${r.isNubi ? 'text-brand-teal' : 'text-fg'}`}>
                {r.isNubi && <Star size={12} className="inline mr-1 -mt-0.5 text-brand-teal" strokeWidth={2.5} />}
                {r.name}{r.estimate ? <sup className="text-muted">†</sup> : null}
              </div>
              <div className="text-[11px] text-muted truncate hidden sm:block">{r.note}</div>
            </div>
            <div className="h-7 rounded-md bg-surface-2 overflow-hidden">
              <div
                className={`h-full rounded-md ${r.isNubi ? '' : 'bg-brand-blue/25'}`}
                style={{
                  width: `${Math.max(r.isNubi ? 0 : 2, (r.cost / max) * 100)}%`,
                  background: r.isNubi ? 'linear-gradient(90deg, #2456a6, #17b3a3)' : undefined,
                }}
              />
            </div>
            <div className={`text-sm font-bold tabular-nums text-right w-16 ${r.isNubi ? 'text-brand-teal' : 'text-fg'}`}>
              {fmtUSD(r.cost)}
            </div>
          </div>
        ))}
      </div>
      <p className="px-6 sm:px-8 pb-6 text-xs text-muted opacity-70 leading-relaxed">
        Estimated annual cost of a standalone orchestrator, on top of your data platform. Most managed
        orchestrators bill per environment. † Self-host Airflow is OSS-free but carries real infra + on-call cost.
      </p>
    </div>
  )
}

/**
 * Merge live tier data from the /api/v1/pricing endpoint into the static TIERS
 * array.  The live data provides the up-to-date ZAR price (computed with the
 * daily FX rate).  All other presentation fields (tagline, CTA, SLA badge, etc.)
 * come from the corrected static TIERS so the page never goes blank.
 *
 * @param {object[]} liveTiers  - Array of tier objects from GET /api/v1/pricing
 * @param {object[]} staticTiers - The TIERS array from src/data/pricing.js
 * @returns {object[]}
 */
function mergeLiveTiers(liveTiers, staticTiers) {
  return staticTiers.map((st) => {
    const live = liveTiers.find((lt) => lt.tier === st.id)
    if (!live) return st
    // Build a ZAR price label from the live monthly_price_zar string
    const zarCents = Number(live.monthly_price_zar)
    const priceLabel = zarCents > 0
      ? `R ${Math.round(zarCents).toLocaleString('en-ZA')} / mo`
      : st.price
    return {
      ...st,
      // Override with live ZAR price; keep USD price from static for display
      price_zar_label: priceLabel,
      // SLA fields from live response (Enterprise)
      sla: live.features?.sla_uptime_pct != null ? {
        uptime: `${live.features.sla_uptime_pct}%`,
        p1_response_minutes: live.features.sla_response_time_p1_minutes ?? st.sla?.p1_response_minutes,
        p2_response_hours: live.features.sla_response_time_p2_hours ?? st.sla?.p2_response_hours,
        support: st.sla?.support,
      } : st.sla,
    }
  })
}

export default function PricingPage() {
  // Fetch live pricing data; fall back to static TIERS on error
  const [liveTiers, setLiveTiers] = useState(null)
  useEffect(() => {
    let cancelled = false
    fetchPricingData()
      .then((data) => {
        if (!cancelled && Array.isArray(data?.tiers) && data.tiers.length >= 4) {
          setLiveTiers(data.tiers)
        }
      })
      .catch(() => { /* silently fall back to static */ })
    return () => { cancelled = true }
  }, [])

  const displayTiers = liveTiers ? mergeLiveTiers(liveTiers, TIERS) : TIERS

  const biRows = BI_COMPARISON.map(p => ({
    isNubi: p.isNubi,
    cells: [
      <span className="inline-flex items-center gap-1.5">
        {p.isNubi && <Star size={13} className="text-brand-teal" strokeWidth={2.5} />}
        {p.name}{p.estimate ? <sup className="text-muted">†</sup> : null}
      </span>,
      p.model,
      p.cost500,
      p.computeExtra,
    ],
  }))
  const orchRows = ORCH_COMPARISON.map(p => ({
    isNubi: p.isNubi,
    cells: [
      <span className="inline-flex items-center gap-1.5">
        {p.isNubi && <Star size={13} className="text-brand-teal" strokeWidth={2.5} />}
        {p.name}
      </span>,
      p.floor,
      p.infra,
      p.meter,
    ],
  }))

  return (
    <div className="bg-bg text-fg font-sans">
      <style>{`
        .nubi-range {
          -webkit-appearance: none; appearance: none;
          height: 6px; border-radius: 999px; cursor: pointer;
          background: linear-gradient(90deg, #2456a6, #17b3a3);
        }
        .nubi-range::-webkit-slider-thumb {
          -webkit-appearance: none; appearance: none;
          width: 20px; height: 20px; border-radius: 50%;
          background: #fff; border: 3px solid #17b3a3;
          box-shadow: 0 1px 4px rgba(27,35,99,0.25);
        }
        .nubi-range::-moz-range-thumb {
          width: 20px; height: 20px; border-radius: 50%;
          background: #fff; border: 3px solid #17b3a3;
          box-shadow: 0 1px 4px rgba(27,35,99,0.25);
        }
      `}</style>
      {/* Hero */}
      <section className="relative overflow-hidden border-b border-border bg-surface-2">
        <div className="absolute top-0 left-0 right-0 h-1 bg-brand-gradient" />
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-16 sm:py-20 text-center">
          <Eyebrow>Pricing</Eyebrow>
          <h1 className="font-display text-4xl sm:text-5xl lg:text-6xl font-bold leading-[1.08] tracking-tight text-fg">
            Pricing that doesn’t<br className="hidden sm:block" />{' '}
            <span className="text-brand-gradient">tax your viewers.</span>
          </h1>
          <p className="mt-5 text-base sm:text-lg leading-relaxed text-muted max-w-2xl mx-auto">
            Dashboards compute in your users’ browsers, so an extra viewer costs us ≈ $0 — and we
            never charge you for one. Pay for editors, AI, and throughput. Not for people looking at charts.
          </p>
        </div>
      </section>

      {/* Tiers */}
      <section id="pricing" className="py-14 sm:py-20 scroll-mt-14">
        <div className="max-w-[88rem] mx-auto px-4 sm:px-6 lg:px-8">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-5 xl:gap-4 items-start pt-3">
            {displayTiers.map(t => <TierCard key={t.id} tier={t} />)}
          </div>
          <p className="mt-10 mx-auto max-w-3xl text-center text-sm text-muted leading-relaxed">
            {ENTERPRISE_NOTE}{' '}
            <Link to="/register" className="text-brand-teal font-medium hover:underline inline-flex items-center gap-1">
              Contact us <ChevronRight size={13} />
            </Link>
          </p>
        </div>
      </section>

      {/* Overage / usage-wallet showcase */}
      <section className="pb-14 sm:pb-20 scroll-mt-14">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-8">
            <Eyebrow><span className="inline-flex items-center gap-1.5"><Wallet size={12} /> Buy more when you need it</span></Eyebrow>
            <h2 className="font-display text-3xl sm:text-4xl font-bold text-fg mb-3">Metered overages, not surprise bills</h2>
            <p className="text-sm sm:text-base text-muted max-w-2xl mx-auto">
              Every paid tier includes a monthly quota. Need a burst of AI tokens or embed sessions for one
              busy month? Don’t jump a whole tier — just use more, metered to the same rate.
            </p>
          </div>
          <OverageShowcase />
        </div>
      </section>

      {/* BI cost calculator */}
      <section className="pb-14 sm:pb-20">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-8">
            <Eyebrow><span className="inline-flex items-center gap-1.5"><SlidersHorizontal size={12} /> Calculator 1 · BI &amp; embedded analytics</span></Eyebrow>
            <h2 className="font-display text-3xl sm:text-4xl font-bold text-fg mb-3">What would you pay?</h2>
            <p className="text-sm sm:text-base text-muted max-w-2xl mx-auto">
              Drag the sliders to your scale and watch the gap. Everyone else bills the viewer — we don’t.
            </p>
          </div>
          <CostCalculator />
        </div>
      </section>

      {/* Billing model */}
      <section className="py-14 sm:py-20 bg-surface-2 border-y border-border">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-10">
            <Eyebrow>How billing works</Eyebrow>
            <h2 className="font-display text-3xl sm:text-4xl font-bold text-fg">Billed for value, not for views</h2>
          </div>
          <div className="grid md:grid-cols-2 gap-5">
            <div className="rounded-2xl border border-border bg-surface p-6 sm:p-7">
              <h3 className="flex items-center gap-2 font-display font-bold text-lg text-fg mb-4">
                <CheckCircle2 size={18} className="text-brand-teal" /> What we charge for
              </h3>
              <ul className="flex flex-col gap-3">
                {BILLING_MODEL.metered.map((m, i) => {
                  const Icon = METER_ICONS[i % METER_ICONS.length]
                  return (
                    <li key={m.label} className="flex items-start gap-3">
                      <span className="shrink-0 mt-0.5 w-7 h-7 rounded-lg bg-surface-2 border border-border flex items-center justify-center text-brand-blue">
                        <Icon size={14} strokeWidth={2} />
                      </span>
                      <span className="text-sm text-muted leading-snug">
                        <strong className="text-fg font-semibold">{m.label}.</strong> {m.desc}
                      </span>
                    </li>
                  )
                })}
              </ul>
            </div>
            <div className="rounded-2xl border border-brand-teal/30 bg-surface p-6 sm:p-7">
              <h3 className="flex items-center gap-2 font-display font-bold text-lg text-fg mb-4">
                <XCircle size={18} className="text-muted" /> What we never charge for
              </h3>
              <ul className="flex flex-col gap-3">
                {BILLING_MODEL.neverBilled.map((m) => (
                  <li key={m} className="flex items-start gap-3">
                    <span className="shrink-0 mt-0.5 w-7 h-7 rounded-lg bg-brand-teal/10 flex items-center justify-center">
                      <X size={14} strokeWidth={2.5} className="text-brand-teal" />
                    </span>
                    <span className="text-sm text-fg leading-snug">{m}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-5 text-xs text-muted leading-relaxed border-t border-border pt-4">
                Competitors meter the viewer — per-seat or per-query. That’s the cost we designed away.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* BI comparison */}
      <section className="py-14 sm:py-20">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-10">
            <Eyebrow>The viewer tax</Eyebrow>
            <h2 className="font-display text-3xl sm:text-4xl font-bold text-fg mb-3">What 500 viewers cost</h2>
            <p className="text-sm sm:text-base text-muted max-w-2xl mx-auto">
              Illustrative annual cost to serve ~500 dashboard viewers, before warehouse compute,
              derived from each vendor’s public model. Everyone else scales with viewers or queries. We don’t.
            </p>
          </div>
          <ComparisonTable
            rows={biRows}
            columns={['Product', 'Viewer / embed model', '~500 viewers', 'Compute on top?']}
          />
          <p className="mt-4 text-xs text-muted opacity-70">
            † Looker and Sigma are quote-only; figures reconstructed from reseller/analyst data and shown as estimates.
            All others from public pricing pages (mid-2026). Verify current pricing before switching.
          </p>
        </div>
      </section>

      {/* Orchestration comparison + second calculator */}
      <section className="py-14 sm:py-20 bg-surface-2 border-y border-border">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-10">
            <Eyebrow><span className="inline-flex items-center gap-1.5"><GitFork size={12} /> Flows is included</span></Eyebrow>
            <h2 className="font-display text-3xl sm:text-4xl font-bold text-fg mb-3">No separate orchestrator bill</h2>
            <p className="text-sm sm:text-base text-muted max-w-2xl mx-auto">
              Flows runs on the Postgres you already have — no Redis, no Celery, no separate control plane.
              Retries, timeouts, result caching, and RLS-aware execution are built in.
            </p>
          </div>
          <ComparisonTable
            rows={orchRows}
            columns={['Orchestrator', 'Cost floor', 'Infra you operate', 'Metering']}
          />

          {/* Calculator 2 — orchestration */}
          <div className="mt-14 sm:mt-16">
            <div className="text-center mb-8">
              <Eyebrow><span className="inline-flex items-center gap-1.5"><SlidersHorizontal size={12} /> Calculator 2 · Orchestration</span></Eyebrow>
              <h2 className="font-display text-3xl sm:text-4xl font-bold text-fg mb-3">What a standalone orchestrator adds</h2>
              <p className="text-sm sm:text-base text-muted max-w-2xl mx-auto">
                Most orchestrators bill per environment or per seat. With Nubi, that line item is zero.
              </p>
            </div>
            <OrchCalculator />
          </div>
        </div>
      </section>

      {/* FAQ */}
      <section className="py-14 sm:py-20">
        <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-10">
            <Eyebrow>FAQ</Eyebrow>
            <h2 className="font-display text-3xl sm:text-4xl font-bold text-fg">Questions, answered</h2>
          </div>
          <div className="flex flex-col gap-3">
            {PRICING_FAQ.map(({ q, a }) => (
              <details key={q} className="group rounded-xl border border-border bg-surface p-5">
                <summary className="flex items-center justify-between cursor-pointer list-none font-display font-semibold text-fg">
                  {q}
                  <ChevronRight size={16} className="text-muted transition-transform group-open:rotate-90" />
                </summary>
                <p className="mt-3 text-sm leading-relaxed text-muted">{a}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="relative overflow-hidden py-16 sm:py-24 bg-surface-2 border-t border-border">
        <div className="absolute top-0 left-0 right-0 h-1 bg-brand-gradient" />
        <div className="max-w-3xl mx-auto px-4 sm:px-6 text-center">
          <h2 className="font-display text-3xl sm:text-5xl font-bold leading-tight mb-4 text-fg">
            Start free.<br /><span className="text-brand-gradient">Scale without the viewer tax.</span>
          </h2>
          <p className="text-sm sm:text-base text-muted mb-8 max-w-lg mx-auto">
            Unlimited dashboard views on every plan, including Free. Upgrade for seats, embed volume,
            governance, and dedicated support.
          </p>
          <div className="flex flex-col sm:flex-row gap-3 justify-center">
            <Link to="/register" className="inline-flex items-center justify-center gap-2 px-6 py-3.5 rounded-xl text-base font-semibold bg-brand-gradient text-white hover:opacity-90 transition-all min-h-[48px]">
              Get started free <ArrowRight size={16} strokeWidth={2.5} />
            </Link>
            <Link to="/compare" className="inline-flex items-center justify-center gap-2 px-6 py-3.5 rounded-xl text-base font-semibold bg-surface border border-border text-fg hover:border-brand-blue hover:text-brand-blue transition-all min-h-[48px]">
              See the full comparison
            </Link>
          </div>
        </div>
      </section>
    </div>
  )
}
