/**
 * PricingPage — /pricing.
 *
 * Public marketing page. Renders the customer-facing billing model and
 * grounded competitor comparisons (BI viewer tax + orchestration) from
 * src/data/pricing.ts. Billing enforcement lives in the EE tree, not here.
 *
 * Live data: GET /api/v1/pricing on mount refreshes the tier grid with
 * live SLA figures. On any error the static TIERS from
 * src/data/pricing.ts stay, so the page never goes blank.
 *
 * Swiss: one section recipe (2px rule, mono index, headline, hairline cells),
 * ink checks, the Pro plan on an accent top rule, bars in ink against accent.
 */
import { useState, useEffect, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, X, TrendingDown } from 'lucide-react'
import {
  TIERS, BILLING_MODEL, BI_COMPARISON, ORCH_COMPARISON, PRICING_FAQ, ENTERPRISE_NOTE,
  CALC_OPTIONS, ORCH_CALC_OPTIONS, OVERAGE_RATES, OVERAGE_NOTE,
} from '../data/pricing.js'
import { fetchPricingData } from '../lib/pricing.js'
import MarketingStyles from '../components/marketing/MarketingStyles.jsx'
import { SliderField } from '../components/marketing/SliderField.jsx'
import {
  Reveal, Section, PageHero, Facts, Tag, Checks, Faq, Seg, CostBars, useSeen, TableScroll,
} from '../components/marketing/kit.jsx'
import Figure from '../components/marketing/Figure.jsx'
import CurrencyPicker from '../components/pricing/CurrencyPicker.jsx'
import { useCurrency } from '../contexts/CurrencyContext.jsx'
import { CURRENCIES, convertFromUsd } from '../lib/currency.js'

/* ── Money helpers (display currency; USD returns byte-identical) ─────────── */

/** Compact money in the visitor's display currency, for calculator bars. */
const compactMoney = (usd: number, currency: string, rates: Record<string, number>) => {
  const amount = convertFromUsd(usd, currency, rates)
  const sym = (CURRENCIES as any)[currency]?.symbol ?? ''
  if (!amount) return `${sym}0`
  if (amount >= 1e6) return `${sym}${(amount / 1e6).toFixed(amount >= 1e7 ? 0 : 1)}M`
  if (amount >= 1e3) return `${sym}${Math.round(amount / 1e3)}k`
  return `${sym}${Math.round(amount)}`
}

const fmtMoneyCompact = (amount: number, sym: string) => {
  const a = Math.abs(amount)
  if (a === 0) return `${sym}0`
  if (a >= 1e6) return `${sym}${(amount / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`
  if (a >= 1e3) return `${sym}${Math.round(amount / 1e3)}k`
  if (a >= 1) return `${sym}${Math.round(amount)}`
  return `${sym}${Math.round(amount * 100) / 100}`
}

/**
 * Rewrite every USD amount inside a display string into the display currency,
 * keeping the text around it ("$90k–$210k", "≈ $0", "/viewer/yr"). Strings
 * without a "$" and USD itself pass through untouched.
 */
const convertMoneyString = (str: unknown, currency: string, rates: Record<string, number>) => {
  if (typeof str !== 'string' || currency === 'USD' || !str.includes('$')) return str as string
  const sym = (CURRENCIES as any)[currency]?.symbol ?? ''
  return str.replace(/\$(\d[\d,]*(?:\.\d+)?)([kKmM])?/g, (match, num, suffix) => {
    let usd = Number(String(num).replace(/,/g, ''))
    if (!Number.isFinite(usd)) return match
    const s = suffix?.toLowerCase()
    if (s === 'k') usd *= 1e3
    else if (s === 'm') usd *= 1e6
    return fmtMoneyCompact(convertFromUsd(usd, currency, rates), sym)
  })
}

const parseUsd = (price: unknown) => {
  const n = Number(String(price ?? '').replace(/[^0-9.]/g, ''))
  return Number.isFinite(n) && String(price).match(/\d/) ? n : NaN
}

const fmtNum = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : `${n}`)

/* ── Page styles ──────────────────────────────────────────────────────────── */

const CSS = `
.pp-tiers { grid-template-columns: minmax(0, 1fr); }
@media (min-width: 640px) { .pp-tiers { grid-template-columns: repeat(2, minmax(0, 1fr)); } .pp-tiers > :last-child:nth-child(odd) { grid-column: 1 / -1; } }
@media (min-width: 1180px) { .pp-tiers { grid-template-columns: repeat(5, minmax(0, 1fr)); } .pp-tiers > :last-child:nth-child(odd) { grid-column: auto; } }
.pp-tier { padding: 22px 20px 26px; display: flex; flex-direction: column; gap: 0; }
.pp-pop { box-shadow: inset 0 3px 0 var(--accent); }
.pp-tier-h { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-height: 22px; }
.pp-tier-h h3 { margin: 0; font-size: 17px; font-weight: 600; letter-spacing: -.015em; }
.pp-idx { font: 500 11px var(--font-mono); letter-spacing: .06em; color: var(--text-muted); }
.pp-price { display: flex; align-items: baseline; gap: 8px; margin-top: 18px; }
.pp-price b { font-weight: 600; font-size: 40px; line-height: 1; letter-spacing: -.04em; font-variant-numeric: tabular-nums; }
.pp-price span { font: 12px var(--font-mono); color: var(--text-muted); }
.pp-tag { margin-top: 10px; font-size: 14px; line-height: 1.45; color: var(--text-muted); text-wrap: pretty; }
@media (min-width: 1180px) { .pp-tag { min-height: 4.35em; } }
.pp-cta { margin-top: 18px; width: 100%; }
.pp-feats { margin-top: 22px; padding-top: 16px; border-top: 1px solid var(--border); }
.pp-ledger { border-top: 1px solid var(--ink); align-self: end; }
.pp-ledger-h, .pp-ledger-r { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--border); font-size: 15px; }
.pp-ledger-h { font: 500 11px/1.4 var(--font-mono); letter-spacing: .08em; text-transform: uppercase; color: var(--text-muted); padding-block: 10px; }
.pp-calc { border: 1px solid var(--ink); }
.pp-calc-in { display: grid; gap: 28px; padding: 24px 20px; background: var(--surface-2); border-bottom: 1px solid var(--border); }
@media (min-width: 720px) { .pp-calc-in { grid-template-columns: 1fr 1fr; gap: 48px; padding: 28px 28px; } }
.pp-calc-head { display: flex; align-items: flex-start; gap: 12px; padding: 16px 20px; border-bottom: 1px solid var(--border); font-size: 15px; line-height: 1.5; }
.pp-calc-head svg { flex: none; margin-top: 3px; }
.pp-calc-head b { font-family: var(--font-mono); font-weight: 600; font-variant-numeric: tabular-nums; color: var(--accent); }
.pp-calc-body { padding: 8px 20px 4px; }
@media (min-width: 720px) { .pp-calc-head { padding-inline: 28px; } .pp-calc-body { padding-inline: 28px; } }
.pp-foot { padding: 14px 20px 20px; font: 12px/1.6 var(--font-mono); color: var(--text-muted); }
@media (min-width: 720px) { .pp-foot { padding-inline: 28px; } }
.pp-rate { font: 600 28px/1.1 var(--font-mono); letter-spacing: -.02em; font-variant-numeric: tabular-nums; }
.pp-rate small { font: 12px var(--font-mono); color: var(--text-muted); letter-spacing: 0; margin-left: 6px; }
.pp-split { display: grid; gap: 40px; align-items: center; }
@media (min-width: 960px) { .pp-split { grid-template-columns: minmax(0, 6fr) minmax(0, 5fr); gap: 64px; } }
.pp-field { color: var(--text); }
.pp-field rect { fill: currentColor; }
.pp-field rect.pp-acc { fill: var(--accent); }
.pp-result { border-top: 2px solid var(--ink); padding-top: 14px; display: flex; flex-direction: column; gap: 6px; }
.pp-result b { font: 600 clamp(56px, 7vw, 96px)/.9 var(--font-ui); letter-spacing: -.05em; color: var(--accent); }
.pp-vt { display: grid; gap: 28px 56px; align-items: end; margin-bottom: 40px; }
@media (min-width: 900px) { .pp-vt { grid-template-columns: minmax(0, 7fr) minmax(0, 3fr); } }
.pp-two { display: grid; gap: 1px; background: var(--border); border: 1px solid var(--border); }
@media (min-width: 800px) { .pp-two { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.pp-two > div { background: var(--surface); padding: 24px 24px 28px; }
.pp-two .pp-never-cell { box-shadow: inset 0 3px 0 var(--ink); }
.pp-never li { display: flex; gap: 10px; align-items: flex-start; font-size: 15px; line-height: 1.45; }
.pp-never li svg { flex: none; margin-top: 3px; }
.pp-list { list-style: none; margin: 16px 0 0; padding: 0; display: flex; flex-direction: column; gap: 14px; }
.pp-list b { color: var(--text); font-weight: 600; }
.mk-pick { display: inline-flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.mk-select { height: 34px; padding: 0 10px; font: 500 13px var(--font-mono); color: var(--text); background: var(--surface); border: 1px solid var(--border-control, color-mix(in srgb, var(--ink) 48%, var(--surface))); border-radius: 2px; cursor: pointer; }
.mk-select:hover { border-color: var(--ink); }
`

/* ── Tier cell ────────────────────────────────────────────────────────────── */

function TierCell({ tier, idx }: { tier: any; idx: number }) {
  const hi = !!tier.highlight
  const { format } = useCurrency()
  const cadence = tier.cadence === 'per month' ? '/ mo' : tier.cadence

  // Prices are USD-anchored; the display currency is an opt-in convenience.
  const usd = parseUsd(tier.price)
  const bigPrice = Number.isFinite(usd) ? format(usd) : tier.price

  const feats = (tier.features as string[]).map(f => {
    const head = f.endsWith('plus:')
    const soon = !head && /coming soon/i.test(f)
    const text = soon ? f.replace(/\s*[(·]?\s*coming soon[)\s]*/gi, '').replace(/\s*·\s*$/, '').trim() : f
    return { text, head, soon }
  })

  return (
    <article className={hi ? 'pp-tier pp-pop' : 'pp-tier'} aria-labelledby={`tier-${tier.id}`}>
      <div className="pp-tier-h">
        <h3 id={`tier-${tier.id}`}>{tier.name}</h3>
        {hi
          ? <Tag tone="ink">{tier.badge || 'Most popular'}</Tag>
          : <span className="pp-idx">{String(idx + 1).padStart(2, '0')}</span>}
      </div>
      <div className="pp-price">
        <b>{bigPrice}</b><span>{cadence}</span>
      </div>
      <p className="pp-tag">{tier.tagline}</p>

      <Link
        to={tier.href}
        className={`nubi-btn nubi-btn-lg pp-cta ${hi ? 'nubi-btn-primary' : 'nubi-btn-secondary'}`}
      >
        {tier.cta}
      </Link>

      <div className="pp-feats">
        {tier.sla && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 14 }}>
            {tier.sla.uptime && <Tag>{tier.sla.uptime} SLA</Tag>}
            {tier.sla.p1_response_minutes != null && <Tag>P1 &lt; {tier.sla.p1_response_minutes} min</Tag>}
          </div>
        )}
        <Checks items={feats} />
      </div>
    </article>
  )
}

 /* ── Hero ledger: what is billed and what is not ─────────────────────────── */

function Ledger() {
  const rows: [string, 'billed' | 'free'][] = [
    ['Embedded sessions', 'billed'],
    ['AI tokens', 'billed'],
    ['Agent and kernel runs', 'billed'],
    ['Dashboard viewers', 'free'],
    ['Seats and editors', 'free'],
    ['Dashboard views', 'free'],
  ]
  return (
    <div className="pp-ledger" role="table" aria-label="What is billed on every plan">
      <div className="pp-ledger-h" role="row"><span role="columnheader">Line item</span><span role="columnheader">Billing</span></div>
      {rows.map(([label, kind]) => (
        <div className="pp-ledger-r" role="row" key={label}>
          <span role="cell">{label}</span>
          <span role="cell">{kind === 'billed' ? <Tag>Metered</Tag> : <Tag tone="accent" dot>Free</Tag>}</span>
        </div>
      ))}
    </div>
  )
}

/* ── Calculators ──────────────────────────────────────────────────────────── */

type CalcRow = { name: string; note?: string; estimate?: boolean; isNubi?: boolean; cost: number }

function CalcBars({ rows }: { rows: CalcRow[] }) {
  const { currency, rates } = useCurrency()
  const max = Math.max(...rows.map(r => r.cost), 1)
  return (
    <CostBars
      max={max}
      rows={rows.map(r => ({
        name: <>{r.name}{r.estimate ? <sup aria-label="estimate"> †</sup> : null}</>,
        note: r.note,
        value: r.isNubi ? Math.max(r.cost, max * 0.006) : Math.max(r.cost, max * 0.02),
        label: compactMoney(r.cost, currency, rates),
        nubi: r.isNubi,
      }))}
    />
  )
}

function CostCalculator() {
  const { currency, rates } = useCurrency()
  const [sv, setSv] = useState(50) // slider 0–100 → viewers (log scale)
  const [editors, setEditors] = useState(5)
  const viewers = Math.round(10 * Math.pow(2500, sv / 100)) // 10 → 25,000

  const rows: CalcRow[] = CALC_OPTIONS
    .map((o: any) => ({ ...o, cost: Math.round(o.annual(viewers, editors)) }))
    .sort((a: CalcRow, b: CalcRow) => a.cost - b.cost)
  const nubi = rows.find(r => r.isNubi)
  const cheapestComp = Math.min(...rows.filter(r => !r.isNubi).map(r => r.cost))
  const savings = Math.max(0, cheapestComp - (nubi?.cost ?? 0))
  const multiple = nubi && nubi.cost > 0 ? cheapestComp / nubi.cost : null

  return (
    <div className="pp-calc" id="calc-bi">
      <div className="pp-calc-in">
        <SliderField
          id="calc-viewers" label="Dashboard viewers" display={fmtNum(viewers)}
          min="0" max="100" value={sv} onChange={(e: any) => setSv(Number(e.target.value))}
          lo="10" hi="25k" ariaLabel="Dashboard viewers"
        />
        <SliderField
          id="calc-editors" label="Editors" display={editors}
          min="1" max="50" value={editors} onChange={(e: any) => setEditors(Number(e.target.value))}
          lo="1" hi="50" ariaLabel="Editors"
        />
      </div>
      <p className="pp-calc-head" aria-live="polite">
        <TrendingDown size={18} strokeWidth={1.75} aria-hidden="true" />
        <span>
          Nubi costs <b>{compactMoney(nubi?.cost ?? 0, currency, rates)}/yr</b>
          {savings > 0 && (<>, <b>{compactMoney(savings, currency, rates)}/yr</b> less than the next option{multiple && multiple >= 2 ? <> ({Math.round(multiple)}× cheaper)</> : null}.</>)}
        </span>
      </p>
      <div className="pp-calc-body"><CalcBars rows={rows} /></div>
      <p className="pp-foot">
        Estimated annual cost from each vendor's public model, before your own warehouse compute.
        † Looker is quote-only, so its figure is directional. Check contract terms before you switch.
      </p>
    </div>
  )
}

function OrchCalculator() {
  const { currency, rates } = useCurrency()
  const [envs, setEnvs] = useState(2)
  const [gb, setGb] = useState(1000)

  const rows: CalcRow[] = ORCH_CALC_OPTIONS
    .map((o: any) => ({ ...o, cost: Math.round(o.annual(envs, gb)) }))
    .sort((a: CalcRow, b: CalcRow) => a.cost - b.cost)
  const nubi = rows.find(r => r.isNubi)
  const cheapestComp = Math.min(...rows.filter(r => !r.isNubi).map(r => r.cost))
  const savings = Math.max(0, cheapestComp - (nubi?.cost ?? 0))

  return (
    <div className="pp-calc" id="calc-orch">
      <div className="pp-calc-in">
        <SliderField
          id="orch-envs" label="Environments (dev, staging, prod)" display={envs}
          min="1" max="6" value={envs} onChange={(e: any) => setEnvs(Number(e.target.value))}
          lo="1" hi="6" ariaLabel="Environments"
        />
        <SliderField
          id="orch-gb" label="Data processed (GB / month)" display={gb.toLocaleString()}
          min="0" max="10000" step="100" value={gb} onChange={(e: any) => setGb(Number(e.target.value))}
          lo="0" hi="10 TB" ariaLabel="Data processed in GB per month"
        />
      </div>
      <p className="pp-calc-head" aria-live="polite">
        <TrendingDown size={18} strokeWidth={1.75} aria-hidden="true" />
        <span>
          Flows adds <b>{compactMoney(nubi?.cost ?? 0, currency, rates)}/yr</b> for orchestration: no per-environment bill.
          {savings > 0 && (<> The cheapest standalone orchestrator costs <b>{compactMoney(savings, currency, rates)}/yr</b> more.</>)}
        </span>
      </p>
      <div className="pp-calc-body"><CalcBars rows={rows} /></div>
      <p className="pp-foot">
        Same basis for every row: data volume converts to compute at about 50 GB per compute-hour, and each vendor
        is priced as its published always-on floor plus compute for the work. Directional estimates, not quotes.
        † Self-hosted Airflow is free software but carries real infrastructure and on-call cost.
      </p>
    </div>
  )
}

/* ── The viewer tax ───────────────────────────────────────────────────────── */

/** 500 squares, one per viewer, resolving into one accent square: one cached query. */
function ViewerField() {
  const cols = 50
  const rows = 10
  const cell = 10
  const s = 6
  const sq = []
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const i = r * cols + c
      sq.push(<rect key={i} x={c * cell} y={r * cell} width={s} height={s} opacity={i === 0 ? 1 : [1, 0.55, 0.2][(i * 7) % 3]} className={i === 0 ? 'pp-acc' : undefined} />)
    }
  }
  return (
    <svg viewBox={`0 0 ${cols * cell} ${rows * cell}`} className="pp-field" style={{ width: '100%', height: 'auto' }} aria-hidden="true">
      {sq}
    </svg>
  )
}

const VTAX_MAX_ANNUAL = 260000 // the chart's full scale (Looker's high end)

function ViewerTax() {
  const [ref, seen] = useSeen<HTMLDivElement>()
  const { currency, rates } = useCurrency()
  const money = (s: unknown) => convertMoneyString(s, currency, rates)

  const priced = (BI_COMPARISON as any[]).filter(p => !p.isNubi && p.annual_hi != null).sort((a, b) => b.annual_hi - a.annual_hi)
  const unpriced = (BI_COMPARISON as any[]).filter(p => !p.isNubi && p.annual_hi == null)
  const nubi = (BI_COMPARISON as any[]).find(p => p.isNubi)

  const rows = [
    ...priced.map(p => ({
      name: <a className="mk-link" href={p.sourceUrl} target="_blank" rel="noopener noreferrer">{p.name}{p.estimate ? ' †' : ''}</a>,
      note: `${money(p.model)} · ${money(p.computeExtra)}`,
      value: p.annual_lo,
      ext: p.annual_hi,
      label: p.annual_hi > p.annual_lo
        ? `${compactMoney(p.annual_lo, currency, rates)}–${compactMoney(p.annual_hi, currency, rates)}`
        : compactMoney(p.annual_lo, currency, rates),
    })),
    ...unpriced.map(p => ({
      name: <a className="mk-link" href={p.sourceUrl} target="_blank" rel="noopener noreferrer">{p.name}{p.estimate ? ' †' : ''}</a>,
      note: `${money(p.model)} · ${money(p.computeExtra)}`,
      value: VTAX_MAX_ANNUAL * 0.12,
      label: 'quote only',
    })),
    {
      name: nubi.name,
      note: `${money(nubi.model)} · ${money(nubi.computeExtra)}`,
      value: VTAX_MAX_ANNUAL * 0.004,
      label: money('$0'),
      nubi: true,
    },
  ]

  return (
    <div ref={ref}>
      <div className="pp-vt">
        <div style={{ display: 'grid', gap: 12 }}>
          <ViewerField />
          <p className="mk-label">500 viewers, one dashboard</p>
        </div>
        <div className="pp-result">
          <b>1</b>
          <p className="mk-label" style={{ color: 'var(--text)' }}>cached query</p>
          <p className="mk-small mk-mono">≈ {money('$0')} compute</p>
        </div>
      </div>
      <p className="mk-label" style={{ marginBottom: 8 }}>
        Annual cost, about 500 dashboard viewers · bars to scale · max ≈ {money('$260k')}/yr
      </p>
      <CostBars rows={rows} max={VTAX_MAX_ANNUAL} seen={seen} />
      <p className="pp-foot" style={{ paddingInline: 0 }}>
        † Looker and Sigma are quote-only; their figures are reconstructed from reseller and analyst data and shown as
        estimates. Everything else comes from public pricing pages (mid-2026). Check current pricing before you switch.
      </p>
    </div>
  )
}

/* ── Orchestrator table ───────────────────────────────────────────────────── */

function OrchTable() {
  const { currency, rates } = useCurrency()
  const money = (s: unknown) => convertMoneyString(s, currency, rates)
  return (
    <TableScroll min={760} cue="Scroll to compare">
      <table className="mk-table">
        <thead>
          <tr>
            <th scope="col">Orchestrator</th>
            <th scope="col">Cost floor</th>
            <th scope="col">Infra you operate</th>
            <th scope="col">Metering</th>
          </tr>
        </thead>
        <tbody>
          {(ORCH_COMPARISON as any[]).map(p => (
            <tr key={p.name}>
              <td className={p.isNubi ? 'mk-col-nubi' : undefined}>{p.name}{p.estimate ? ' †' : ''}</td>
              <td className={p.isNubi ? 'mk-col-nubi' : undefined}>{money(p.floor)}</td>
              <td className={p.isNubi ? 'mk-col-nubi' : undefined}>{p.infra}</td>
              <td className={p.isNubi ? 'mk-col-nubi' : undefined}>{money(p.meter)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  )
}

/* ── Live tier merge ──────────────────────────────────────────────────────── */

/**
 * Merge live tier data from /api/v1/pricing into the static TIERS: the live
 * response carries the up-to-date SLA figures; every
 * presentation field stays from the static data.
 */
function mergeLiveTiers(liveTiers: any[], staticTiers: any[]) {
  return staticTiers.map(st => {
    const live = liveTiers.find(lt => lt.tier === st.id)
    if (!live) return st
    return {
      ...st,
      sla: live.features?.sla_uptime_pct != null ? {
        uptime: `${live.features.sla_uptime_pct}%`,
        p1_response_minutes: live.features.sla_response_time_p1_minutes ?? st.sla?.p1_response_minutes,
        p2_response_hours: live.features.sla_response_time_p2_hours ?? st.sla?.p2_response_hours,
        support: st.sla?.support,
      } : st.sla,
    }
  })
}

/* ── Page ─────────────────────────────────────────────────────────────────── */

export default function PricingPage() {
  const { currency, rates } = useCurrency()
  const money = (s: unknown) => convertMoneyString(s, currency, rates)
  const [calc, setCalc] = useState<'bi' | 'orch'>('bi')

  const [liveTiers, setLiveTiers] = useState<any[] | null>(null)
  useEffect(() => {
    let cancelled = false
    fetchPricingData()
      .then((data: any) => { if (!cancelled && Array.isArray(data?.tiers) && data.tiers.length >= 4) setLiveTiers(data.tiers) })
      .catch(() => { /* keep the static tiers */ })
    return () => { cancelled = true }
  }, [])
  const displayTiers = liveTiers ? mergeLiveTiers(liveTiers, TIERS as any[]) : (TIERS as any[])

  return (
    <div className="mk">
      <MarketingStyles />
      <style href="nubi-pricing-page" precedence="default">{CSS}</style>

      <PageHero
        eyebrow="PRICING"
        meta="UNLIMITED SEATS ON EVERY PLAN"
        aside={<Ledger />}
        title={<>Pricing that doesn&rsquo;t tax your <em>viewers.</em></>}
        lead={<>Dashboards compute in <b>your users&rsquo; browsers</b>, so an extra viewer costs us about {money('$0')} and we never charge you for one. Pay for embeds, AI and throughput. Not for people looking at charts.</>}
        actions={
          <>
            <Link to="/register" className="nubi-btn nubi-btn-primary nubi-btn-xl">Start free</Link>
            <a href="#calculators" className="nubi-btn nubi-btn-ghost nubi-btn-xl">See what you&rsquo;d pay</a>
          </>
        }
      >
        <div style={{ marginTop: 56 }}>
          <Facts items={[
            { v: money('$0'), l: 'marginal cost per dashboard view' },
            { v: '∞', l: 'editors and viewers on every plan' },
            { v: money('$9'), l: 'first paid plan, per month' },
            { v: '10–50×', l: 'fewer warehouse scans than naive use' },
          ]} />
        </div>
      </PageHero>

      {/* 01 · Plans */}
      <Section
        id="pricing" index="01" label="PLANS"
        title={<>Five plans. <em>Zero seat math.</em></>}
        lead={<>Every plan, Free included, has <b>unlimited editors and viewers</b>. You move up for embed volume, AI, connectors and governance. Never for people.</>}
      >
        <Reveal>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px 24px', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
            <p className="mk-label">{currency === 'USD' ? 'Prices in USD' : `Prices set in USD · ${currency} shown at indicative rates`}</p>
            <CurrencyPicker />
          </div>
          <div className="mk-cells pp-tiers">
            {displayTiers.map((t, i) => <TierCell key={t.id} tier={t} idx={i} />)}
          </div>
          <div className="mk-note mk-ink" style={{ marginTop: 28, maxWidth: '78ch' }}>
            <p>{ENTERPRISE_NOTE}{' '}<Link to="/register" className="mk-link">Contact us</Link>.</p>
          </div>
        </Reveal>
      </Section>

      {/* 02 · Metered */}
      <Section
        id="usage" index="02" label="USAGE WALLET" sunk
        title={<>Metered overages, <em>not surprise bills.</em></>}
        lead={<>Every paid plan includes a monthly quota. Need a burst for one busy month? Don&rsquo;t jump a whole plan. Use more, at the same rate.</>}
      >
        <div className="pp-split">
          <Reveal>
            <p className="mk-p" style={{ maxWidth: '58ch' }}>{OVERAGE_NOTE}</p>
          </Reveal>
          <Reveal delay={60}>
            <Figure name="MeteringWallet" title="A prepaid wallet drawing down against metered usage" className="w-full h-auto" />
          </Reveal>
        </div>
        <Reveal>
          <div className="mk-cells mk-cells-4" style={{ marginTop: 40 }}>
            {(OVERAGE_RATES as any[]).map(o => (
              <div key={o.label} className="mk-cell">
                <p className="mk-label">Metered</p>
                <p className="pp-rate">{money(o.rate)}<small>{money(o.unit)}</small></p>
                <h3>{o.label}</h3>
                <p>{o.desc}</p>
              </div>
            ))}
            <div className="mk-cell" style={{ boxShadow: 'inset 0 3px 0 var(--ink)' }}>
              <p className="mk-label">Never metered</p>
              <h3>No per-viewer or per-seat overage. Ever.</h3>
              <p>Top up once and draw from your wallet. Anything beyond lands on your next invoice. Viewers stay free at any usage level.</p>
            </div>
          </div>
        </Reveal>
      </Section>

      {/* 03 · Calculators */}
      <Section
        id="calculators" index="03" label="ESTIMATE"
        title={<>What would <em>you</em> pay?</>}
        lead={<>Set your scale and watch the gap. <b>Everyone else bills the viewer. We don&rsquo;t.</b></>}
      >
        <Reveal>
          <div style={{ marginBottom: 20 }}>
            <Seg<'bi' | 'orch'>
              tabs label="Calculator" value={calc} onChange={setCalc}
              options={[{ value: 'bi', label: 'BI and embedded analytics' }, { value: 'orch', label: 'Orchestration' }]}
            />
          </div>
          {calc === 'bi' ? <CostCalculator /> : (
            <>
              <OrchCalculator />
              <div className="mk-sec-h" style={{ marginTop: 56, marginBottom: 20 }}>
                <div className="mk-idx" style={{ border: 0, padding: 0 }}><b>FLOWS</b><span>INCLUDED</span></div>
                <div>
                  <h3 className="mk-h3">No separate orchestrator bill</h3>
                  <p className="mk-p" style={{ marginTop: 10, maxWidth: '62ch' }}>
                    Flows runs on <b>the Postgres you already have</b>: no Redis, no Celery, no separate control plane.
                    Retries, timeouts, result caching and RLS-aware execution are built in.
                  </p>
                </div>
              </div>
              <OrchTable />
              <p className="pp-foot" style={{ paddingInline: 0 }}>† Estimate. Sources are linked in the comparison on the compare page.</p>
            </>
          )}
        </Reveal>
      </Section>

      {/* 04 · Viewer tax */}
      <Section
        id="viewer-tax" index="04" label="THE VIEWER TAX" sunk
        title={<>What <em>500 viewers</em> cost.</>}
        lead={<>Illustrative annual cost to serve about 500 dashboard viewers, before your own warehouse compute, derived from each vendor&rsquo;s public model. <b>Everyone else scales with viewers or queries. We don&rsquo;t.</b></>}
      >
        <ViewerTax />
      </Section>

      {/* 05 · Billing model */}
      <Section
        id="billing" index="05" label="HOW BILLING WORKS"
        title={<>Billed for value, <em>not for views.</em></>}
      >
        <Reveal>
          <div className="pp-two">
            <div>
              <p className="mk-label">Metered</p>
              <h3 className="mk-h3" style={{ marginTop: 8 }}>What we charge for</h3>
              <ul className="pp-list mk-p">
                {(BILLING_MODEL.metered as any[]).map(m => (
                  <li key={m.label}><b>{m.label}.</b> {m.desc}</li>
                ))}
              </ul>
            </div>
            <div className="pp-never-cell">
              <p className="mk-label">Never billed</p>
              <h3 className="mk-h3" style={{ marginTop: 8 }}>What we never charge for</h3>
              <ul className="pp-list pp-never">
                {(BILLING_MODEL.neverBilled as string[]).map(m => (
                  <li key={m}><X size={16} strokeWidth={1.75} aria-hidden="true" /><span>{m}</span></li>
                ))}
              </ul>
              <p className="mk-small" style={{ marginTop: 20, paddingTop: 16, borderTop: '1px solid var(--border)' }}>
                Competitors meter the viewer, per seat or per query. That is the cost we designed away.
              </p>
            </div>
          </div>
          <p className="mk-p" style={{ marginTop: 28, maxWidth: '70ch' }}>
            Already run BigQuery or Snowflake? <b>Point Nubi at it and go.</b> Nubi pushes queries down to your engine
            while dashboards, row-level security and caching stay in Nubi.
          </p>
        </Reveal>
      </Section>

      {/* 06 · FAQ */}
      <Section id="faq" index="06" label="FAQ" sunk title={<>Questions, <em>answered.</em></>}>
        <Reveal>
          <div style={{ maxWidth: 920 }}>
            <Faq items={(PRICING_FAQ as any[]).map(f => ({ q: f.q, a: f.a }))} />
          </div>
        </Reveal>
      </Section>

      {/* CTA */}
      <section className="mk-sec mk-ink-band">
        <div className="mk-wrap">
          <div className="mk-idx" style={{ borderBottomColor: 'var(--ink-fg)', color: 'inherit' }}><b style={{ color: 'inherit' }}>START</b><span>NO CREDIT CARD</span></div>
          <div style={{ display: 'grid', gap: 28, marginTop: 40, maxWidth: 820 }}>
            <h2 className="mk-h2" style={{ fontSize: 'clamp(36px, 5vw, 72px)', lineHeight: 1 }}>
              Start free. Scale without <em>the viewer tax.</em>
            </h2>
            <p className="mk-lead">Unlimited dashboard views on every plan, Free included. Upgrade for embed volume, governance and dedicated support.</p>
            <div className="mk-ctas">
              <Link to="/register" className="nubi-btn nubi-btn-primary nubi-btn-xl">Start free <ArrowRight size={16} strokeWidth={1.75} aria-hidden="true" /></Link>
              <Link to="/compare" className="nubi-btn nubi-btn-secondary nubi-btn-xl">See the full comparison</Link>
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}
