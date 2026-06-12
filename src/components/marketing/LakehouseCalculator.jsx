/**
 * LakehouseCalculator — "calc/03 · lakehouse-data-cost", shared by /pricing and
 * the landing page so both always show the same (correct) lakehouse pricing
 * model: $5/TiB scanned (first 1 TiB/mo free) + $0.02/GB/mo storage, with the
 * cost breakdown and the BigQuery on-demand reference comparison.
 *
 * Also exports SliderField, the labeled range input the other calculators use.
 */
import { useState } from 'react'
import { HardDrive, Database, Star } from 'lucide-react'
import CalcShell from './CalcShell.jsx'
import {
  estimateLakehouseCost,
  LAKEHOUSE_STORAGE_USD_PER_GB, LAKEHOUSE_FREE_SCAN_TIB,
} from '../../lib/pricing.js'

const fmtUSD = (n) => {
  if (!n) return '$0'
  if (n >= 1e6) return `$${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`
  if (n >= 1e3) return `$${Math.round(n / 1e3)}k`
  return `$${Math.round(n)}`
}
const fmtNum = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : `${n}`)

export function SliderField({ id, label, display, min, max, step, value, onChange, lo, hi, ariaLabel }) {
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

export default function LakehouseCalculator() {
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
        <div className="rounded-xl border border-border overflow-x-auto overflow-y-clip">
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
