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
 *
 * Design language: shares the marketing observatory system with the landing
 * page (MarketingStyles + useReveal) — hero/CTA panels, glass cards with
 * per-tier accents, mono data styling, terminal-framed calculators.
 */
import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import {
  Check, X, ArrowRight, ChevronRight, Headset, Star,
  Zap, Database, Bot, Server, Users, XCircle, CheckCircle2,
  SlidersHorizontal, TrendingDown, Shield, Wallet, GitFork, Gauge,
  HardDrive,
} from 'lucide-react'
import {
  TIERS, BILLING_MODEL, BI_COMPARISON, ORCH_COMPARISON, PRICING_FAQ, ENTERPRISE_NOTE,
  CALC_OPTIONS, ORCH_CALC_OPTIONS, OVERAGE_RATES, OVERAGE_NOTE,
} from '../data/pricing.js'
import {
  fetchPricingData,
  estimateLakehouseCost,
  LAKEHOUSE_STORAGE_USD_PER_GB, LAKEHOUSE_FREE_SCAN_TIB,
} from '../lib/pricing.js'
import MarketingStyles from '../components/marketing/MarketingStyles.jsx'
import useReveal from '../components/marketing/useReveal.js'

const fmtUSD = (n) => {
  if (!n) return '$0'
  if (n >= 1e6) return `$${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`
  if (n >= 1e3) return `$${Math.round(n / 1e3)}k`
  return `$${Math.round(n)}`
}
const fmtNum = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : `${n}`)

const METER_ICONS = [Users, Zap, Database, Bot, Server]

/* ── Scroll reveal wrapper (one-shot, shares lp-reveal from MarketingStyles) ── */
function Reveal({ children, className = '', delay = 0 }) {
  const [ref, seen] = useReveal()
  return (
    <div
      ref={ref}
      className={`lp-reveal ${seen ? 'lp-in' : ''} ${className}`}
      style={delay ? { transitionDelay: `${delay}ms` } : undefined}
    >
      {children}
    </div>
  )
}

function Eyebrow({ children }) {
  return (
    <p className="font-mono text-[11px] font-semibold tracking-[0.18em] uppercase mb-4 text-brand-teal">
      {children}
    </p>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Tier cards — glass cards with per-tier accents; Pro gets gradient border   */
/* ─────────────────────────────────────────────────────────────────────────── */

const TIER_ACCENTS = {
  free: '#94a3b8',
  starter: '#4d8de0',
  team: '#8b5cf6',
  pro: '#17b3a3',
  enterprise: '#f59e0b',
}

function TierCard({ tier, idx }) {
  const hi = tier.highlight
  const hasSla = !!tier.sla
  const zarLabel = tier.price_zar_label
  const accent = TIER_ACCENTS[tier.id] || '#17b3a3'
  const cadence = tier.cadence === 'per month' ? '/ mo' : tier.cadence

  const inner = (
    <div className={`relative flex flex-col h-full p-5 sm:p-6 ${hi ? 'rounded-[1.15rem] bg-surface' : ''}`}>
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-display text-lg font-bold text-fg">{tier.name}</h3>
        <span className="font-mono text-[11px] font-bold" style={{ color: accent }}>
          /{String(idx + 1).padStart(2, '0')}
        </span>
      </div>
      <div className="flex items-baseline gap-2">
        <span className="font-display text-[2.4rem] leading-none font-bold tracking-tight text-fg tabular-nums">
          {tier.price}
        </span>
        <span className="font-mono text-[11px] text-muted">{cadence}</span>
      </div>
      {zarLabel && (
        <p className="mt-1.5 font-mono text-[11px] text-primary tabular-nums">≈ {zarLabel}</p>
      )}
      <p className="mt-2.5 text-[13px] text-muted leading-relaxed min-h-[52px]">{tier.tagline}</p>

      {/* SLA badges for Enterprise */}
      {hasSla && tier.sla && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {tier.sla.uptime && (
            <span className="inline-flex items-center gap-1 px-2 py-1 rounded-lg font-mono text-[10px] font-semibold bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700 text-amber-700 dark:text-amber-300">
              <Shield size={9} strokeWidth={2.5} />
              {tier.sla.uptime} SLA
            </span>
          )}
          {tier.sla.p1_response_minutes != null && (
            <span className="inline-flex items-center gap-1 px-2 py-1 rounded-lg font-mono text-[10px] font-semibold bg-surface-2 border border-border text-muted">
              P1 &lt;{tier.sla.p1_response_minutes}min
            </span>
          )}
        </div>
      )}

      <Link
        to={tier.href}
        className={`mt-4 inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold transition-all min-h-[44px]
          ${hi
            ? 'lp-cta-glow bg-brand-gradient text-white hover:-translate-y-0.5'
            : 'bg-surface-2 border border-border text-fg hover:border-brand-blue hover:text-primary'}`}
      >
        {tier.cta}
        <ArrowRight size={14} strokeWidth={2.5} />
      </Link>

      <ul className="mt-5 flex flex-col gap-2">
        {tier.features.map((f, i) => {
          const isHeader = f.endsWith('plus:')
          return (
            <li
              key={i}
              className={`flex items-start gap-2 ${isHeader
                ? 'font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted pt-1.5'
                : 'text-[13px] text-fg'}`}
            >
              {!isHeader && (
                <Check size={14} strokeWidth={2.75} className="mt-0.5 shrink-0" style={{ color: accent }} />
              )}
              <span className={isHeader ? '' : 'leading-snug'}>{f}</span>
            </li>
          )
        })}
      </ul>
    </div>
  )

  // 6-col bento at lg (3 cards up top, 2 centered below); 5-up only at 2xl.
  const gridPlace = `lg:col-span-2 2xl:col-span-1 ${idx === 3 ? 'lg:col-start-2 2xl:col-start-auto' : ''}`

  if (hi) {
    return (
      <Reveal delay={(idx % 5) * 70} className={`h-full ${gridPlace}`}>
        <div className="relative h-full lg:-translate-y-3 z-10">
          <div className="pp-pop relative h-full rounded-[1.3rem] p-[1.5px] bg-brand-gradient">
            <span className="absolute -top-3 left-1/2 -translate-x-1/2 z-20 inline-flex items-center gap-1.5 px-3 py-1 rounded-full font-mono text-[10px] font-bold uppercase tracking-[0.14em] whitespace-nowrap bg-brand-gradient text-white shadow-lg">
              <Star size={10} strokeWidth={2.5} />
              {tier.badge || 'Most popular'}
            </span>
            {inner}
          </div>
        </div>
      </Reveal>
    )
  }

  return (
    <Reveal delay={(idx % 5) * 70} className={`h-full ${gridPlace}`}>
      <div className="pp-card h-full" style={{ '--pp-accent': accent }}>
        {tier.badge && (
          <span
            className="absolute -top-3 left-1/2 -translate-x-1/2 z-20 inline-flex items-center gap-1.5 px-3 py-1 rounded-full font-mono text-[10px] font-bold uppercase tracking-[0.14em] whitespace-nowrap bg-surface shadow-sm border"
            style={{ color: accent, borderColor: `${accent}55` }}
          >
            <Headset size={10} strokeWidth={2.5} />
            {tier.badge}
          </span>
        )}
        {inner}
      </div>
    </Reveal>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Comparison table                                                           */
/* ─────────────────────────────────────────────────────────────────────────── */

function ComparisonTable({ rows, columns }) {
  return (
    <div className="overflow-x-auto overscroll-x-contain rounded-2xl border border-border bg-surface shadow-[0_18px_44px_-26px_rgba(27,35,99,0.4)]">
      <table className="border-collapse w-full" style={{ minWidth: 720 }}>
        <thead className="sticky top-0 z-10">
          <tr>
            {columns.map((c, i) => (
              <th key={i}
                className={`px-5 py-3.5 text-left bg-surface-2 border-b border-border ${i > 0 ? 'border-l' : ''}`}
                style={i === 0 ? { minWidth: 150 } : { minWidth: 180 }}>
                <span className="font-mono text-[10.5px] font-semibold uppercase tracking-[0.14em] text-muted">{c}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri}
              className={`border-b border-border last:border-0 transition-colors
                ${r.isNubi ? '' : 'hover:bg-brand-blue/[0.04]'}`}>
              {r.cells.map((cell, ci) => (
                <td key={ci}
                  className={`px-5 py-4 align-top text-[13px] leading-snug border-border ${ci > 0 ? 'border-l' : ''}
                    ${r.isNubi
                      ? 'bg-brand-teal/[0.08] ' + (ci === 0
                          ? 'font-bold text-brand-teal shadow-[inset_3px_0_0_#17b3a3]'
                          : 'text-fg font-medium')
                      : ((ri % 2 === 1 ? 'bg-slate-500/[0.05] ' : 'bg-surface ') +
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

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Calculator shell — terminal-framed card shared by all three calculators    */
/* ─────────────────────────────────────────────────────────────────────────── */

function CalcShell({ index, slug, children }) {
  return (
    <div className="rounded-2xl sm:rounded-3xl border border-border bg-surface shadow-[0_30px_70px_-32px_rgba(27,35,99,0.45)] overflow-hidden">
      {/* always-dark terminal strip */}
      <div className="flex items-center justify-between gap-3 px-4 sm:px-7 py-2.5 bg-[#0d1430] border-b border-black/40">
        <span className="flex items-center gap-3 min-w-0">
          <span className="flex gap-1.5 shrink-0" aria-hidden="true">
            <span className="w-2.5 h-2.5 rounded-full bg-[#f4726f]/80" />
            <span className="w-2.5 h-2.5 rounded-full bg-[#f5bd4f]/80" />
            <span className="w-2.5 h-2.5 rounded-full bg-[#61c554]/80" />
          </span>
          <span className="font-mono text-[11px] text-slate-300 truncate">
            calc/{index} · {slug}
          </span>
        </span>
        <span className="hidden sm:inline font-mono text-[9.5px] text-teal-300/90 border border-teal-400/25 bg-teal-400/[0.08] rounded px-1.5 py-0.5 whitespace-nowrap">
          live estimate
        </span>
      </div>
      {children}
    </div>
  )
}

/* Slider input row — mono value chip + lp-range slider */
function SliderField({ id, label, display, min, max, step, value, onChange, lo, hi, ariaLabel }) {
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 mb-3">
        <label htmlFor={id} className="text-sm font-semibold text-fg">{label}</label>
        <span className="font-mono text-[13px] font-bold text-brand-teal tabular-nums bg-brand-teal/[0.08] border border-brand-teal/25 rounded-lg px-2.5 py-0.5">
          {display}
        </span>
      </div>
      <input
        id={id} type="range" min={min} max={max} step={step} value={value}
        onChange={onChange}
        className="lp-range w-full"
        aria-label={ariaLabel || label}
      />
      <div className="flex justify-between font-mono text-[10px] text-muted mt-1.5">
        <span>{lo}</span><span>{hi}</span>
      </div>
    </div>
  )
}

/* Competitor-vs-Nubi result bar row */
function ResultBar({ r, max, nameColsClass }) {
  return (
    <div className={`grid ${nameColsClass} items-center gap-3`}>
      <div className="min-w-0">
        <div className={`text-sm font-semibold truncate ${r.isNubi ? 'text-brand-teal' : 'text-fg'}`}>
          {r.isNubi && <Star size={12} className="inline mr-1 -mt-0.5 text-brand-teal" strokeWidth={2.5} />}
          {r.name}{r.estimate ? <sup className="text-muted">†</sup> : null}
        </div>
        <div className="font-mono text-[10px] text-muted truncate hidden sm:block">{r.note}</div>
      </div>
      <div className="h-7 rounded-md bg-surface-2 overflow-hidden">
        <div
          className={`h-full rounded-md transition-[width] duration-500 ease-out ${r.isNubi ? 'pp-bar-nubi' : 'bg-brand-blue/25 dark:bg-brand-blue/40'}`}
          style={{ width: `${Math.max(r.isNubi ? 0.6 : 2, (r.cost / max) * 100)}%` }}
        />
      </div>
      <div className={`font-mono text-[13px] font-bold tabular-nums text-right w-16 ${r.isNubi ? 'text-brand-teal' : 'text-fg'}`}>
        {fmtUSD(r.cost)}
      </div>
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Calculator 1 — BI & embedded analytics                                     */
/* ─────────────────────────────────────────────────────────────────────────── */

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
    <CalcShell index="01" slug="bi-embedded-analytics">
      {/* Inputs */}
      <div className="grid md:grid-cols-2 gap-6 p-5 sm:p-8 border-b border-border bg-surface-2">
        <SliderField
          id="calc-viewers" label="Dashboard viewers" display={fmtNum(viewers)}
          min="0" max="100" value={sv} onChange={e => setSv(Number(e.target.value))}
          lo="10" hi="25k" ariaLabel="Dashboard viewers"
        />
        <SliderField
          id="calc-editors" label="Editors (creators)" display={editors}
          min="1" max="50" value={editors} onChange={e => setEditors(Number(e.target.value))}
          lo="1" hi="50" ariaLabel="Editors"
        />
      </div>

      {/* Savings headline */}
      <div className="flex flex-wrap items-center justify-center gap-2 px-5 sm:px-6 py-4 text-center bg-brand-teal/[0.06] border-b border-border">
        <TrendingDown size={18} className="text-brand-teal shrink-0" />
        <span className="text-sm sm:text-base text-fg">
          Nubi costs <strong className="font-mono font-bold text-brand-teal">{fmtUSD(nubi?.cost ?? 0)}/yr</strong>
          {savings > 0 && (
            <> — that’s <strong className="font-mono font-bold text-brand-teal">{fmtUSD(savings)}/yr less</strong>
              {multiple && multiple >= 2 && <> ({Math.round(multiple)}× cheaper)</>} than the next option.</>
          )}
        </span>
      </div>

      {/* Bars */}
      <div className="p-5 sm:p-8 flex flex-col gap-3">
        {results.map(r => (
          <ResultBar key={r.name} r={r} max={max} nameColsClass="grid-cols-[100px_1fr_auto] sm:grid-cols-[150px_1fr_auto]" />
        ))}
      </div>
      <p className="px-5 sm:px-8 pb-6 font-mono text-[10.5px] text-muted opacity-70 leading-relaxed">
        Estimated annual cost from each vendor’s public model (BI platform only; lakehouse data cost is separate).
        † Looker is quote-only; figure is directional. Your actual cost depends on contract terms — verify before switching.
      </p>
    </CalcShell>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Overage / usage-wallet showcase — bento grid                               */
/* ─────────────────────────────────────────────────────────────────────────── */

const OVERAGE_ICONS = [Database, Zap, Bot, Server, Gauge]
const OVERAGE_ACCENTS = ['#4d8de0', '#f59e0b', '#8b5cf6', '#ec4899', '#17b3a3']

function OverageShowcase() {
  return (
    <div className="flex flex-col gap-4 sm:gap-5">
      {/* Wallet intro — gradient banner card */}
      <Reveal>
        <div className="relative rounded-[1.25rem] p-6 sm:p-7 text-white bg-brand-gradient shadow-[0_24px_50px_-20px_rgba(23,179,163,0.55)] overflow-hidden">
          <div className="lp-noise pointer-events-none absolute inset-0" aria-hidden="true" />
          <div className="relative flex flex-col sm:flex-row sm:items-center gap-4 sm:gap-6">
            <span className="shrink-0 w-12 h-12 rounded-2xl bg-white/15 border border-white/25 flex items-center justify-center">
              <Wallet size={22} strokeWidth={2} />
            </span>
            <div className="flex-1">
              <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-white/70 mb-1">
                the usage wallet
              </p>
              <h3 className="font-display text-xl sm:text-2xl font-bold leading-snug">
                Pay only for what you use.
              </h3>
              <p className="mt-1.5 text-sm leading-relaxed text-white/85 max-w-3xl">{OVERAGE_NOTE}</p>
            </div>
            <span className="self-start sm:self-center shrink-0 inline-flex items-center gap-1.5 font-mono text-[11px] font-semibold px-2.5 py-1.5 rounded-lg border border-white/25 bg-white/10 whitespace-nowrap">
              <Check size={11} strokeWidth={3} /> same rate, every paid tier
            </span>
          </div>
        </div>
      </Reveal>

      {/* Rate cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 sm:gap-5">
        {OVERAGE_RATES.map((o, i) => {
          const Icon = OVERAGE_ICONS[i % OVERAGE_ICONS.length]
          const accent = OVERAGE_ACCENTS[i % OVERAGE_ACCENTS.length]
          return (
            <Reveal key={o.label} delay={(i % 3) * 80} className="h-full">
              <div className="pp-card h-full p-5 sm:p-6" style={{ '--pp-accent': accent }}>
                <div className="flex items-center justify-between mb-3">
                  <span
                    className="inline-flex w-9 h-9 rounded-xl items-center justify-center text-white shadow-sm"
                    style={{ background: `linear-gradient(135deg, ${accent}, ${accent}cc)` }}
                  >
                    <Icon size={16} strokeWidth={2} />
                  </span>
                  <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
                    metered
                  </span>
                </div>
                <div className="flex items-baseline gap-1.5">
                  <span className="font-mono text-2xl font-bold text-fg tabular-nums">{o.rate}</span>
                  <span className="font-mono text-[11px] text-muted">{o.unit}</span>
                </div>
                <p className="text-[13px] font-semibold text-fg mt-1">{o.label}</p>
                <p className="text-xs text-muted leading-snug mt-1">{o.desc}</p>
              </div>
            </Reveal>
          )
        })}
        {/* the wedge cell — never per-seat */}
        <Reveal delay={160} className="h-full">
          <div className="pp-card h-full p-5 sm:p-6 bg-brand-teal/[0.05]" style={{ '--pp-accent': '#17b3a3' }}>
            <div className="flex items-center justify-between mb-3">
              <span className="inline-flex w-9 h-9 rounded-xl items-center justify-center bg-brand-teal/10 border border-brand-teal/30 text-brand-teal">
                <X size={16} strokeWidth={2.5} />
              </span>
              <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-brand-teal">
                never metered
              </span>
            </div>
            <p className="font-display text-base font-bold text-fg leading-snug">
              No per-viewer or per-seat overage — ever.
            </p>
            <p className="text-xs text-muted leading-snug mt-1.5">
              Top up once, draw from your wallet, and anything beyond simply lands on your
              next invoice. Viewers stay free at any usage level.
            </p>
          </div>
        </Reveal>
      </div>
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Calculator 2 — orchestration                                               */
/* ─────────────────────────────────────────────────────────────────────────── */

function OrchCalculator() {
  const [envs, setEnvs] = useState(2)
  const [gb, setGb] = useState(1000)

  const results = ORCH_CALC_OPTIONS
    .map(o => ({ ...o, cost: Math.round(o.annual(envs, gb)) }))
    .sort((a, b) => a.cost - b.cost)
  const max = Math.max(...results.map(r => r.cost), 1)
  const nubi = results.find(r => r.isNubi)
  const cheapestComp = Math.min(...results.filter(r => !r.isNubi).map(r => r.cost))
  const savings = Math.max(0, cheapestComp - (nubi?.cost ?? 0))

  return (
    <CalcShell index="02" slug="orchestration">
      {/* Inputs */}
      <div className="grid md:grid-cols-2 gap-6 p-5 sm:p-8 border-b border-border bg-surface-2">
        <SliderField
          id="orch-envs" label="Environments (dev / staging / prod)" display={envs}
          min="1" max="6" value={envs} onChange={e => setEnvs(Number(e.target.value))}
          lo="1" hi="6" ariaLabel="Environments"
        />
        <SliderField
          id="orch-gb" label="Data processed (GB / mo)" display={gb.toLocaleString()}
          min="0" max="10000" step="100" value={gb} onChange={e => setGb(Number(e.target.value))}
          lo="0" hi="10 TB" ariaLabel="Data processed in GB per month"
        />
      </div>

      {/* Headline — honest: Flows is metered on compute, not free. */}
      <div className="flex flex-wrap items-center justify-center gap-2 px-5 sm:px-6 py-4 text-center bg-brand-teal/[0.06] border-b border-border">
        <GitFork size={18} className="text-brand-teal shrink-0" />
        <span className="text-sm sm:text-base text-fg">
          Flows costs <strong className="font-mono font-bold text-brand-teal">{nubi?.cost ? `${fmtUSD(nubi.cost)}/yr` : '$0'}</strong>{' '}
          metered on data processed — no per-environment bill.
          {savings > 0 && (
            <> That&rsquo;s <strong className="font-mono font-bold text-brand-teal">{fmtUSD(savings)}/yr</strong> less than the cheapest standalone orchestrator.</>
          )}
        </span>
      </div>

      {/* Bars */}
      <div className="p-5 sm:p-8 flex flex-col gap-3">
        {results.map(r => (
          <ResultBar key={r.name} r={r} max={max} nameColsClass="grid-cols-[110px_1fr_auto] sm:grid-cols-[190px_1fr_auto]" />
        ))}
      </div>
      <p className="px-5 sm:px-8 pb-6 font-mono text-[10.5px] text-muted opacity-70 leading-relaxed">
        Apples-to-apples: data volume is converted to compute at ~50 GB / compute-hour, then each vendor is
        priced as its published always-on floor (per environment / capacity / seats) plus compute for the work.
        Directional estimates, not quotes. Managed orchestrators are floor-dominated; Nubi Flows has no floor.
        † Self-host Airflow is OSS-free but carries real infra + on-call cost.
      </p>
    </CalcShell>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Calculator 3 — lakehouse data cost                                         */
/* ─────────────────────────────────────────────────────────────────────────── */

function LakehouseCalculator() {
  const [storageGb, setStorageGb] = useState(100)
  const [queries, setQueries] = useState(5000)
  const [scanGb, setScanGb] = useState(2)

  const est = estimateLakehouseCost({
    queries_per_month: queries,
    avg_gb_scanned: scanGb,
    storage_gb: storageGb,
  })

  const withinFree = est.billable_tb === 0
  const bqScanCost = Math.max(0, est.tb_scanned - 1) * 6.25
  const bqStorageCost = Math.max(0, storageGb - 10) * 0.02
  const bqTotal = bqScanCost + bqStorageCost
  const savingsVsBq = Math.max(0, bqTotal - est.total_usd)

  return (
    <CalcShell index="03" slug="lakehouse-data-cost">
      {/* Inputs */}
      <div className="grid md:grid-cols-3 gap-6 p-5 sm:p-8 border-b border-border bg-surface-2">
        <SliderField
          id="lh-storage" label="Storage (GB)" display={storageGb.toLocaleString()}
          min="1" max="5000" step="10" value={storageGb} onChange={e => setStorageGb(Number(e.target.value))}
          lo="1 GB" hi="5 TB" ariaLabel="Storage in GB"
        />
        <SliderField
          id="lh-queries" label="Server-side queries / mo" display={fmtNum(queries)}
          min="0" max="50000" step="100" value={queries} onChange={e => setQueries(Number(e.target.value))}
          lo="0" hi="50k" ariaLabel="Server-side queries per month"
        />
        <SliderField
          id="lh-scan" label="Avg scanned / query (GB)" display={scanGb}
          min="0.1" max="50" step="0.1" value={scanGb} onChange={e => setScanGb(Number(e.target.value))}
          lo="0.1" hi="50" ariaLabel="Average GB scanned per query"
        />
      </div>

      {/* Headline */}
      <div className="flex flex-wrap items-center justify-center gap-2 px-5 sm:px-6 py-4 text-center bg-brand-teal/[0.06] border-b border-border">
        <HardDrive size={18} className="text-brand-teal shrink-0" />
        <span className="text-sm sm:text-base text-fg">
          {withinFree
            ? <><strong className="font-mono font-bold text-brand-teal">Free</strong> — within the 1 TiB/mo free scan tier</>
            : <>Lakehouse data cost ≈ <strong className="font-mono font-bold text-brand-teal">{fmtUSD(est.total_usd)}/mo</strong>
              {savingsVsBq > 1 && <> — <strong className="font-mono font-bold text-brand-teal">{fmtUSD(savingsVsBq)}</strong> less than BigQuery</>}</>
          }
          {' '}<span className="text-muted text-xs">(dashboard views are always free)</span>
        </span>
      </div>

      {/* Cost breakdown */}
      <div className="grid sm:grid-cols-3 divide-y sm:divide-y-0 sm:divide-x divide-border border-b border-border">
        {/* Scan cost */}
        <div className="px-5 sm:px-6 py-5">
          <p className="font-mono text-[10.5px] font-semibold uppercase tracking-[0.14em] text-muted mb-1">Scan cost</p>
          <p className="font-display text-2xl font-bold text-fg tabular-nums">
            {fmtUSD(est.scan_usd)}<span className="font-mono text-xs font-normal text-muted">/mo</span>
          </p>
          <p className="font-mono text-[10.5px] text-muted mt-1.5 leading-relaxed">
            {est.tb_scanned.toFixed(2)} TiB total — {est.billable_tb.toFixed(2)} TiB billable<br />
            ($5/TiB · first {LAKEHOUSE_FREE_SCAN_TIB} TiB free)
          </p>
        </div>
        {/* Storage cost */}
        <div className="px-5 sm:px-6 py-5">
          <p className="font-mono text-[10.5px] font-semibold uppercase tracking-[0.14em] text-muted mb-1">Storage cost</p>
          <p className="font-display text-2xl font-bold text-fg tabular-nums">
            {fmtUSD(est.storage_usd)}<span className="font-mono text-xs font-normal text-muted">/mo</span>
          </p>
          <p className="font-mono text-[10.5px] text-muted mt-1.5 leading-relaxed">
            {storageGb.toLocaleString()} GB × ${LAKEHOUSE_STORAGE_USD_PER_GB}/GB<br />
            (Cloudflare R2 — no egress fees)
          </p>
        </div>
        {/* Dashboard views */}
        <div className="px-5 sm:px-6 py-5 bg-brand-teal/[0.05]">
          <p className="font-mono text-[10.5px] font-semibold uppercase tracking-[0.14em] text-brand-teal mb-1">Dashboard views</p>
          <p className="font-display text-2xl font-bold text-brand-teal">
            Free
          </p>
          <p className="font-mono text-[10.5px] text-muted mt-1.5 leading-relaxed">
            Browser DuckDB kernel — compute<br />
            runs in your users' browser, not ours
          </p>
        </div>
      </div>

      {/* Pre-run estimate callout */}
      <div className="flex items-start gap-3 px-5 sm:px-6 py-4 border-b border-border bg-surface-2">
        <Database size={16} className="mt-0.5 shrink-0 text-primary" />
        <p className="text-xs text-muted leading-relaxed">
          <strong className="text-fg">Pre-run scan estimate</strong> — like BigQuery's dry-run, Nubi shows
          you how many bytes a query will scan <em>before</em> you run it, so there are no surprise costs.
          Queries that hit the rollup cache scan zero bytes.
        </p>
      </div>

      {/* BigQuery reference comparison */}
      <div className="px-5 sm:px-6 py-5">
        <p className="font-mono text-[10.5px] font-semibold uppercase tracking-[0.14em] text-muted mb-3">
          Comparable: Google BigQuery on-demand
        </p>
        <div className="rounded-xl border border-border overflow-x-auto">
          <table className="w-full text-sm" style={{ minWidth: 480 }}>
            <thead>
              <tr className="border-b border-border bg-surface-2">
                <th className="text-left px-4 py-2 font-mono text-[10px] font-semibold text-muted uppercase tracking-[0.12em]"> </th>
                <th className="text-right px-4 py-2 font-mono text-[10px] font-semibold text-muted uppercase tracking-[0.12em]">Scan rate</th>
                <th className="text-right px-4 py-2 font-mono text-[10px] font-semibold text-muted uppercase tracking-[0.12em]">Storage rate</th>
                <th className="text-right px-4 py-2 font-mono text-[10px] font-semibold text-muted uppercase tracking-[0.12em]">This workload</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-b border-border bg-brand-teal/[0.06]">
                <td className="px-4 py-2.5 font-semibold text-brand-teal">
                  <span className="inline-flex items-center gap-1.5">
                    <Star size={12} className="text-brand-teal" strokeWidth={2.5} /> Nubi Lakehouse
                  </span>
                </td>
                <td className="px-4 py-2.5 text-right font-mono text-brand-teal font-bold">$5/TiB</td>
                <td className="px-4 py-2.5 text-right font-mono text-brand-teal font-bold">$0.02/GB</td>
                <td className="px-4 py-2.5 text-right font-mono font-bold text-brand-teal">{fmtUSD(est.total_usd)}/mo</td>
              </tr>
              <tr>
                <td className="px-4 py-2.5 font-medium text-muted">BigQuery on-demand</td>
                <td className="px-4 py-2.5 text-right font-mono text-muted">$6.25/TiB</td>
                <td className="px-4 py-2.5 text-right font-mono text-muted">$0.02/GB</td>
                <td className="px-4 py-2.5 text-right font-mono text-muted">{fmtUSD(bqTotal)}/mo</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="mt-3 font-mono text-[10.5px] text-muted opacity-80 leading-relaxed">
          Same pay-per-scan model, ~20% cheaper scan rate. First 1 TiB/month free on both.
          BigQuery also charges for dashboard query refreshes — Nubi dashboard views run in the
          browser and scan zero bytes. If you outgrow the single-node lakehouse, connect your own
          BigQuery or Snowflake as a Nubi datastore and queries push down to their engine, on their
          billing, while dashboards, RLS, and caching stay in Nubi.
        </p>
      </div>
    </CalcShell>
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
    <>
      <MarketingStyles />
      {/* Page-scoped pricing styles — pp- prefixed so nothing leaks */}
      <style>{`
        /* glass card with per-card accent (set --pp-accent inline) */
        .pp-card {
          position: relative;
          border-radius: 1.25rem;
          border: 1px solid var(--border);
          background: var(--surface);
          box-shadow: 0 1px 2px rgba(27,35,99,0.05);
          transition: transform 0.28s cubic-bezier(0.34,1.4,0.64,1),
                      box-shadow 0.28s ease, border-color 0.28s ease;
        }
        .pp-card::before {
          content: '';
          position: absolute; left: 18px; right: 18px; top: -1px; height: 2px;
          border-radius: 2px;
          background: linear-gradient(90deg, transparent, var(--pp-accent, #17b3a3), transparent);
          opacity: 0.45;
          transition: opacity 0.28s ease;
        }
        .pp-card:hover {
          transform: translateY(-4px);
          border-color: color-mix(in srgb, var(--pp-accent, #17b3a3) 45%, var(--border));
          box-shadow: 0 22px 44px -18px color-mix(in srgb, var(--pp-accent, #17b3a3) 38%, transparent);
        }
        .pp-card:hover::before { opacity: 0.85; }
        /* highlighted (most popular) tier — gradient border + lift */
        .pp-pop {
          box-shadow: 0 30px 70px -28px rgba(23,179,163,0.55), 0 8px 30px -14px rgba(36,86,166,0.4);
          transition: transform 0.28s cubic-bezier(0.34,1.4,0.64,1), box-shadow 0.28s ease;
        }
        .pp-pop:hover {
          transform: translateY(-4px);
          box-shadow: 0 38px 80px -28px rgba(23,179,163,0.65), 0 10px 34px -14px rgba(36,86,166,0.5);
        }
        /* Nubi result bar — brand gradient with glow */
        .pp-bar-nubi {
          background: linear-gradient(90deg, #2456a6, #17b3a3);
          box-shadow: 0 0 16px rgba(23,179,163,0.5), 0 0 3px rgba(23,179,163,0.7);
        }
        @media (prefers-reduced-motion: reduce) {
          .pp-card, .pp-pop { transition: none; }
          .pp-card:hover, .pp-pop:hover { transform: none; }
        }
      `}</style>

      <div className="nubi-lp overflow-x-hidden bg-bg text-fg font-sans">

        {/* ════════════════════════════════════════════════════════════════════
            HERO — observatory panel: the pricing thesis + trust strip + stats
        ════════════════════════════════════════════════════════════════════ */}
        <section className="relative bg-bg px-3 sm:px-5 pt-3 sm:pt-5">
          <div className="lp-hero-panel relative max-w-[1440px] mx-auto rounded-[1.5rem] sm:rounded-[2rem] overflow-hidden border border-border dark:border-white/[0.06]">
            {/* drifting mesh blobs */}
            <div
              className="lp-mesh-a lp-mesh-blob pointer-events-none absolute -top-40 -left-40 w-[42rem] h-[42rem] rounded-full"
              style={{ background: 'radial-gradient(circle, rgba(72,124,214,0.28) 0%, transparent 65%)' }}
              aria-hidden="true"
            />
            <div
              className="lp-mesh-b lp-mesh-blob pointer-events-none absolute top-1/4 -right-48 w-[38rem] h-[38rem] rounded-full"
              style={{ background: 'radial-gradient(circle, rgba(45,212,191,0.16) 0%, transparent 65%)' }}
              aria-hidden="true"
            />
            {/* film grain */}
            <div className="lp-noise pointer-events-none absolute inset-0" aria-hidden="true" />

            <div className="relative px-5 sm:px-10 lg:px-14 pt-14 sm:pt-20 text-center">
              {/* terminal-flavoured eyebrow */}
              <p className="inline-flex items-center gap-2 font-mono text-[11px] sm:text-xs font-medium tracking-wide text-brand-teal dark:text-teal-300/90 border border-border dark:border-white/10 bg-white/60 dark:bg-white/[0.04] rounded-full px-3.5 py-1.5 mb-6 sm:mb-8">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-teal-400 opacity-60" />
                  <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-teal-300" />
                </span>
                pricing · unlimited seats on every plan
              </p>

              <h1 className="font-display text-4xl sm:text-5xl lg:text-[4.2rem] font-bold leading-[1.06] tracking-tight text-fg">
                Pricing that doesn’t
                <br />
                <span className="lp-hero-gradient-text">tax your viewers.</span>
              </h1>

              <p className="mt-5 sm:mt-7 text-base sm:text-lg leading-relaxed text-muted dark:text-slate-300/90 max-w-2xl mx-auto">
                Dashboards compute in <strong className="text-fg font-semibold">your users’ browsers</strong>,
                so an extra viewer costs us <strong className="text-fg font-semibold">≈ $0</strong> — and we{' '}
                <strong className="text-fg font-semibold">never charge you for one</strong>. Pay for editors,
                AI, and throughput. Not for people looking at charts.
              </p>

              {/* CTAs */}
              <div className="flex flex-col sm:flex-row flex-wrap gap-3 justify-center mt-8 sm:mt-9">
                <Link
                  to="/register"
                  className="lp-cta-glow inline-flex items-center justify-center gap-2 px-7 py-3.5 rounded-xl text-base font-semibold transition-all bg-brand-gradient text-white hover:-translate-y-0.5 min-h-[48px]"
                >
                  Start free
                  <ArrowRight size={16} strokeWidth={2.5} />
                </Link>
                <a
                  href="#calc-bi"
                  className="inline-flex items-center justify-center gap-1.5 px-5 py-3.5 rounded-xl text-sm font-medium transition-all text-muted hover:text-fg min-h-[48px]"
                >
                  See what you’d pay <ChevronRight size={13} className="rotate-90" />
                </a>
              </div>

              {/* trust strip — the "never charge for" wedge, mono */}
              <div className="flex flex-wrap justify-center gap-x-5 gap-y-2 font-mono text-[11px] font-medium text-muted mt-8 sm:mt-9">
                {[
                  'dashboard views — free',
                  'no per-viewer seats',
                  'cached reads — free',
                  'unlimited editors',
                ].map(f => (
                  <span key={f} className="flex items-center gap-1.5">
                    <Check size={11} strokeWidth={2.5} className="text-teal-400" />
                    {f}
                  </span>
                ))}
              </div>

              {/* proof stats — fused into the panel */}
              <div className="relative mt-10 sm:mt-14 border-t border-border dark:border-white/10 py-8 sm:py-10">
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-y-8 divide-x divide-border dark:divide-white/[0.07]">
                  {[
                    { v: '$0', l: 'marginal cost per dashboard view' },
                    { v: '∞', l: 'editors & viewers on every plan' },
                    { v: '$9', l: 'first paid tier — usd, billed in zar' },
                    { v: '1 TiB', l: 'free lakehouse scan, every month' },
                  ].map(s => (
                    <div key={s.l} className="px-4 sm:px-8 text-center">
                      <div className="lp-hero-gradient-text font-display text-3xl sm:text-4xl lg:text-[2.6rem] font-bold tracking-tight">
                        {s.v}
                      </div>
                      <div className="mt-1.5 font-mono text-[10.5px] sm:text-[11px] leading-snug text-muted">
                        {s.l}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            TIERS — five glass cards, Pro highlighted with gradient border
        ════════════════════════════════════════════════════════════════════ */}
        <section id="pricing" className="py-14 sm:py-20 scroll-mt-14">
          <div className="max-w-[88rem] 2xl:max-w-[110rem] mx-auto px-4 sm:px-6 lg:px-8">
            <Reveal className="text-center mb-10 sm:mb-12">
              <Eyebrow>tiers · usd anchored · billed in zar</Eyebrow>
              <h2 className="font-display text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight text-fg">
                Five tiers. <span className="lp-hero-gradient-text">Zero seat math.</span>
              </h2>
              <p className="mt-4 text-sm sm:text-base text-muted max-w-2xl mx-auto">
                Every tier — including Free — has <strong className="text-fg font-semibold">unlimited
                editors and viewers</strong>. You move up for throughput, embed volume, AI, and
                governance. Never for people.
              </p>
            </Reveal>
            {/* 3-col on laptops (lg–xl); only go 5-col at 2xl WITH a widened container,
                so cards never get narrower as the screen grows (D responsive fix). */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 2xl:grid-cols-5 gap-5 items-start pt-6 lg:pt-8">
              {displayTiers.map((t, i) => <TierCard key={t.id} tier={t} idx={i} />)}
            </div>
            <p className="mt-10 mx-auto max-w-3xl text-center text-sm text-muted leading-relaxed">
              {ENTERPRISE_NOTE}{' '}
              <Link to="/register" className="text-brand-teal font-medium hover:underline inline-flex items-center gap-1">
                Contact us <ChevronRight size={13} />
              </Link>
            </p>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            OVERAGE — usage-wallet bento
        ════════════════════════════════════════════════════════════════════ */}
        <section className="pb-14 sm:pb-20 scroll-mt-14">
          <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
            <Reveal className="text-center mb-8 sm:mb-10">
              <Eyebrow>buy more when you need it</Eyebrow>
              <h2 className="font-display text-3xl sm:text-4xl font-bold tracking-tight text-fg mb-3">
                Metered overages, <span className="lp-hero-gradient-text">not surprise bills</span>
              </h2>
              <p className="text-sm sm:text-base text-muted max-w-2xl mx-auto">
                Every paid tier includes a monthly quota. Need a burst of AI tokens or embed sessions for one
                busy month? <strong className="text-fg font-semibold">Don’t jump a whole tier</strong> — just
                use more, metered to the same rate.
              </p>
            </Reveal>
            <OverageShowcase />
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            CALCULATOR 1 — BI & embedded analytics
        ════════════════════════════════════════════════════════════════════ */}
        <section id="calc-bi" className="pb-14 sm:pb-20 scroll-mt-14">
          <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8">
            <Reveal className="text-center mb-8 sm:mb-10">
              <Eyebrow>
                <span className="inline-flex items-center gap-1.5">
                  <SlidersHorizontal size={12} /> calculator 01 · bi &amp; embedded analytics
                </span>
              </Eyebrow>
              <h2 className="font-display text-3xl sm:text-4xl font-bold tracking-tight text-fg mb-3">
                What would <span className="lp-hero-gradient-text">you</span> pay?
              </h2>
              <p className="text-sm sm:text-base text-muted max-w-2xl mx-auto">
                Drag the sliders to your scale and watch the gap.{' '}
                <strong className="text-fg font-semibold">Everyone else bills the viewer — we don’t.</strong>
              </p>
            </Reveal>
            <Reveal delay={80}>
              <CostCalculator />
            </Reveal>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            BILLING MODEL — what we charge for / what we never charge for
        ════════════════════════════════════════════════════════════════════ */}
        <section className="py-14 sm:py-20 bg-surface-2 border-y border-border">
          <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
            <Reveal className="text-center mb-10">
              <Eyebrow>how billing works</Eyebrow>
              <h2 className="font-display text-3xl sm:text-4xl font-bold tracking-tight text-fg">
                Billed for value, <span className="lp-hero-gradient-text">not for views</span>
              </h2>
            </Reveal>
            <div className="grid md:grid-cols-2 gap-5">
              {/* what we charge for */}
              <Reveal className="h-full">
                <div className="pp-card h-full p-6 sm:p-7" style={{ '--pp-accent': '#4d8de0' }}>
                  <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-primary mb-1.5">
                    metered
                  </p>
                  <h3 className="flex items-center gap-2 font-display font-bold text-lg text-fg mb-4">
                    <CheckCircle2 size={18} className="text-primary" /> What we charge for
                  </h3>
                  <ul className="flex flex-col gap-3">
                    {BILLING_MODEL.metered.map((m, i) => {
                      const Icon = METER_ICONS[i % METER_ICONS.length]
                      return (
                        <li key={m.label} className="flex items-start gap-3">
                          <span className="shrink-0 mt-0.5 w-7 h-7 rounded-lg bg-surface-2 border border-border flex items-center justify-center text-primary">
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
              </Reveal>
              {/* what we never charge for — the wedge, teal gradient border */}
              <Reveal delay={90} className="h-full">
                <div className="h-full rounded-[1.3rem] p-[1.5px] bg-gradient-to-br from-brand-teal via-brand-blue to-brand-navy shadow-[0_24px_50px_-22px_rgba(23,179,163,0.45)]">
                  <div className="h-full rounded-[1.2rem] bg-surface p-6 sm:p-7">
                    <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-brand-teal mb-1.5">
                      never billed · the wedge
                    </p>
                    <h3 className="flex items-center gap-2 font-display font-bold text-lg text-fg mb-4">
                      <XCircle size={18} className="text-brand-teal" /> What we never charge for
                    </h3>
                    <ul className="flex flex-col gap-3">
                      {BILLING_MODEL.neverBilled.map((m) => (
                        <li key={m} className="flex items-start gap-3">
                          <span className="shrink-0 mt-0.5 w-7 h-7 rounded-lg bg-brand-teal/10 border border-brand-teal/25 flex items-center justify-center">
                            <X size={14} strokeWidth={2.5} className="text-brand-teal" />
                          </span>
                          <span className="text-sm text-fg leading-snug">{m}</span>
                        </li>
                      ))}
                    </ul>
                    <p className="mt-5 font-mono text-[11px] text-muted leading-relaxed border-t border-border pt-4">
                      Competitors meter the viewer — per-seat or per-query. That’s the cost we designed away.
                    </p>
                  </div>
                </div>
              </Reveal>
            </div>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            BI COMPARISON — the viewer tax
        ════════════════════════════════════════════════════════════════════ */}
        <section className="py-14 sm:py-20">
          <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
            <Reveal className="text-center mb-10">
              <Eyebrow>the viewer tax</Eyebrow>
              <h2 className="font-display text-3xl sm:text-4xl font-bold tracking-tight text-fg mb-3">
                What <span className="lp-hero-gradient-text">500 viewers</span> cost
              </h2>
              <p className="text-sm sm:text-base text-muted max-w-2xl mx-auto">
                Illustrative annual cost to serve ~500 dashboard viewers (lakehouse data cost is separate),
                derived from each vendor’s public model.{' '}
                <strong className="text-fg font-semibold">Everyone else scales with viewers or queries. We don’t.</strong>
              </p>
            </Reveal>
            <Reveal delay={80}>
              <ComparisonTable
                rows={biRows}
                columns={['Product', 'Viewer / embed model', '~500 viewers', 'Compute on top?']}
              />
            </Reveal>
            <p className="mt-4 font-mono text-[10.5px] text-muted opacity-70">
              † Looker and Sigma are quote-only; figures reconstructed from reseller/analyst data and shown as estimates.
              All others from public pricing pages (mid-2026). Verify current pricing before switching.
            </p>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            ORCHESTRATION — comparison table + Calculator 2
        ════════════════════════════════════════════════════════════════════ */}
        <section className="py-14 sm:py-20 bg-surface-2 border-y border-border">
          <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
            <Reveal className="text-center mb-10">
              <Eyebrow>
                <span className="inline-flex items-center gap-1.5">
                  <GitFork size={12} /> flows is included
                </span>
              </Eyebrow>
              <h2 className="font-display text-3xl sm:text-4xl font-bold tracking-tight text-fg mb-3">
                No separate <span className="lp-hero-gradient-text">orchestrator bill</span>
              </h2>
              <p className="text-sm sm:text-base text-muted max-w-2xl mx-auto">
                Flows runs on <strong className="text-fg font-semibold">the Postgres you already have</strong> —
                no Redis, no Celery, no separate control plane.
                Retries, timeouts, result caching, and RLS-aware execution are built in.
              </p>
            </Reveal>
            <Reveal delay={80}>
              <ComparisonTable
                rows={orchRows}
                columns={['Orchestrator', 'Cost floor', 'Infra you operate', 'Metering']}
              />
            </Reveal>

            {/* Calculator 2 — orchestration */}
            <div className="mt-14 sm:mt-16">
              <Reveal className="text-center mb-8 sm:mb-10">
                <Eyebrow>
                  <span className="inline-flex items-center gap-1.5">
                    <SlidersHorizontal size={12} /> calculator 02 · orchestration
                  </span>
                </Eyebrow>
                <h2 className="font-display text-3xl sm:text-4xl font-bold tracking-tight text-fg mb-3">
                  What a standalone <span className="lp-hero-gradient-text">orchestrator adds</span>
                </h2>
                <p className="text-sm sm:text-base text-muted max-w-2xl mx-auto">
                  Most orchestrators bill per environment or per seat. With Nubi,{' '}
                  <strong className="text-fg font-semibold">that line item is zero</strong>.
                </p>
              </Reveal>
              <Reveal delay={80}>
                <OrchCalculator />
              </Reveal>
            </div>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            LAKEHOUSE — what it is, what it costs + Calculator 3
        ════════════════════════════════════════════════════════════════════ */}
        <section className="py-14 sm:py-20">
          <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
            <Reveal className="text-center mb-10">
              <Eyebrow>
                <span className="inline-flex items-center gap-1.5">
                  <HardDrive size={12} /> lakehouse data · all plans
                </span>
              </Eyebrow>
              <h2 className="font-display text-3xl sm:text-4xl font-bold tracking-tight text-fg mb-3">
                Pay per TiB scanned — <span className="lp-hero-gradient-text">dashboard views are free</span>
              </h2>
              <p className="text-sm sm:text-base text-muted max-w-2xl mx-auto">
                Your data lives as open Parquet on Cloudflare R2. DuckDB queries it on-demand —
                you pay <strong className="text-fg font-semibold">$5/TiB scanned</strong> (first 1 TiB/month always free),
                plus <strong className="text-fg font-semibold">$0.02/GB/month</strong> storage. That's cheaper than
                BigQuery. Dashboard views run in the user's browser — the DuckDB-WASM kernel costs us
                nothing, so we charge you nothing for them. When you outgrow the single-node lakehouse,
                connect your own BigQuery or Snowflake — Nubi pushes queries down to their engine while
                dashboards, RLS, and caching stay in Nubi.
              </p>
            </Reveal>

            {/* Calculator 3 — lakehouse */}
            <Reveal className="text-center mb-8">
              <Eyebrow>
                <span className="inline-flex items-center gap-1.5">
                  <SlidersHorizontal size={12} /> calculator 03 · lakehouse data cost
                </span>
              </Eyebrow>
            </Reveal>
            <Reveal delay={80}>
              <LakehouseCalculator />
            </Reveal>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            FAQ
        ════════════════════════════════════════════════════════════════════ */}
        <section className="py-14 sm:py-20">
          <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8">
            <Reveal className="text-center mb-10">
              <Eyebrow>faq</Eyebrow>
              <h2 className="font-display text-3xl sm:text-4xl font-bold tracking-tight text-fg">
                Questions, <span className="lp-hero-gradient-text">answered</span>
              </h2>
            </Reveal>
            <div className="flex flex-col gap-3">
              {PRICING_FAQ.map(({ q, a }, i) => (
                <Reveal key={q} delay={(i % 3) * 60}>
                  <details className="group rounded-xl border border-border bg-surface px-5 py-4 transition-colors hover:border-brand-teal/40 open:border-brand-teal/40 open:shadow-[0_12px_30px_-18px_rgba(23,179,163,0.35)]">
                    <summary className="flex items-center justify-between gap-4 cursor-pointer list-none">
                      <span className="flex items-baseline gap-3 min-w-0">
                        <span className="font-mono text-[11px] font-bold text-brand-teal shrink-0">
                          /{String(i + 1).padStart(2, '0')}
                        </span>
                        <span className="font-display font-semibold text-fg">{q}</span>
                      </span>
                      <ChevronRight size={16} className="shrink-0 text-muted transition-transform group-open:rotate-90" />
                    </summary>
                    <p className="mt-3 pl-8 text-sm leading-relaxed text-muted">{a}</p>
                  </details>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            CTA — observatory bookend
        ════════════════════════════════════════════════════════════════════ */}
        <section className="relative bg-bg px-3 sm:px-5 pb-8 sm:pb-12">
          <div className="lp-hero-panel relative max-w-[1440px] mx-auto rounded-[1.5rem] sm:rounded-[2rem] overflow-hidden border border-border dark:border-white/[0.06]">
            <div className="lp-noise pointer-events-none absolute inset-0" aria-hidden="true" />
            <div
              className="lp-mesh-blob pointer-events-none absolute -bottom-40 -left-32 w-[38rem] h-[38rem] rounded-full"
              style={{ background: 'radial-gradient(circle, rgba(72,124,214,0.22) 0%, transparent 65%)' }}
              aria-hidden="true"
            />
            <div
              className="lp-mesh-blob pointer-events-none absolute -top-32 -right-40 w-[34rem] h-[34rem] rounded-full"
              style={{ background: 'radial-gradient(circle, rgba(45,212,191,0.14) 0%, transparent 65%)' }}
              aria-hidden="true"
            />
            <div className="relative max-w-3xl mx-auto px-5 sm:px-10 py-16 sm:py-24 text-center">
              <p className="font-mono text-[11px] font-semibold tracking-[0.18em] uppercase mb-5 text-brand-teal dark:text-teal-300/90">
                no credit card · free forever tier
              </p>
              <h2 className="font-display text-3xl sm:text-5xl font-bold leading-tight tracking-tight mb-4 text-fg">
                Start free.<br />
                <span className="lp-hero-gradient-text">Scale without the viewer tax.</span>
              </h2>
              <p className="text-sm sm:text-base text-muted dark:text-slate-300/90 mb-8 max-w-lg mx-auto">
                Unlimited dashboard views on every plan, including Free. Upgrade for seats, embed volume,
                governance, and dedicated support.
              </p>
              <div className="flex flex-col sm:flex-row gap-3 justify-center">
                <Link
                  to="/register"
                  className="lp-cta-glow inline-flex items-center justify-center gap-2 px-7 py-3.5 rounded-xl text-base font-semibold bg-brand-gradient text-white hover:-translate-y-0.5 transition-all min-h-[48px]"
                >
                  Get started free <ArrowRight size={16} strokeWidth={2.5} />
                </Link>
                <Link
                  to="/compare"
                  className="inline-flex items-center justify-center gap-2 px-6 py-3.5 rounded-xl text-base font-semibold transition-all bg-surface border border-border text-fg hover:border-brand-blue dark:bg-white/[0.06] dark:border-white/15 dark:text-white dark:hover:bg-white/[0.12] dark:hover:border-white/25 min-h-[48px]"
                >
                  See the full comparison
                </Link>
              </div>
              <div className="flex flex-wrap justify-center gap-x-5 gap-y-2 font-mono text-[11px] font-medium text-muted mt-8">
                {[
                  'unlimited seats & viewers',
                  'usage wallet — no surprise bills',
                  'apache-2.0 open core',
                ].map(f => (
                  <span key={f} className="flex items-center gap-1.5">
                    <Check size={11} strokeWidth={2.5} className="text-teal-400" />
                    {f}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </section>
      </div>
    </>
  )
}
