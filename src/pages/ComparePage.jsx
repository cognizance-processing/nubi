/**
 * ComparePage — Nubi vs competitors. Full redesign.
 *
 * Design direction: Editorial/data-forward. Clean grid, strong typographic
 * hierarchy, brand-gradient Nubi column, honest competitor cards with source links.
 * Self-contained inline-SVG illustrations (no import from src/components/illustrations).
 * Uses only design tokens from tailwind.config.js + index.css — no hardcoded hex.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ExternalLink,
  ChevronDown,
  ChevronUp,
  Info,
  CheckCircle2,
  XCircle,
  Clock,
  ArrowRight,
  Zap,
  Shield,
  Layers,
} from 'lucide-react'
import { NUBI, COMPETITORS, COMPARE_DIMENSIONS, MATRIX } from '../data/compareData.js'

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Inline-SVG illustrations (self-contained, brand palette)                  */
/* ─────────────────────────────────────────────────────────────────────────── */

/** Scale / balance motif — represents comparison */
function ScaleIllustration() {
  return (
    <svg
      viewBox="0 0 320 200"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className="w-full max-w-xs mx-auto"
      aria-hidden="true"
    >
      {/* Glow backdrop */}
      <ellipse cx="160" cy="110" rx="120" ry="60" fill="#2456a6" opacity="0.07" />
      {/* Pivot arm */}
      <line x1="160" y1="50" x2="160" y2="90" stroke="#17b3a3" strokeWidth="2.5" strokeLinecap="round" />
      {/* Balance beam */}
      <line x1="60" y1="90" x2="260" y2="90" stroke="#2456a6" strokeWidth="3" strokeLinecap="round" />
      <circle cx="160" cy="90" r="5" fill="#17b3a3" />
      {/* Left pan cord */}
      <line x1="70" y1="90" x2="70" y2="118" stroke="#2456a6" strokeWidth="1.5" strokeDasharray="3 2" opacity="0.6" />
      {/* Right pan cord */}
      <line x1="250" y1="90" x2="250" y2="110" stroke="#2456a6" strokeWidth="1.5" strokeDasharray="3 2" opacity="0.6" />
      {/* Left pan — slightly lower (competitors weigh more in cost) */}
      <rect x="42" y="118" width="56" height="28" rx="14" fill="#1b2363" opacity="0.15" />
      <rect x="42" y="118" width="56" height="28" rx="14" stroke="#2456a6" strokeWidth="1.5" />
      <text x="70" y="137" textAnchor="middle" fontSize="9" fill="#566377" fontFamily="Inter,sans-serif">others</text>
      {/* Right pan — higher (Nubi is lighter / cheaper) */}
      <rect x="222" y="110" width="56" height="28" rx="14" fill="#17b3a3" opacity="0.18" />
      <rect x="222" y="110" width="56" height="28" rx="14" stroke="#17b3a3" strokeWidth="1.5" />
      <text x="250" y="129" textAnchor="middle" fontSize="9" fill="#17b3a3" fontWeight="600" fontFamily="Inter,sans-serif">Nubi</text>
      {/* Decorative data nodes */}
      {[[80, 55], [120, 35], [200, 40], [240, 60]].map(([cx, cy], i) => (
        <circle key={i} cx={cx} cy={cy} r={3 + (i % 2)} fill="#2dd4bf" opacity={0.35 + i * 0.1} />
      ))}
      {/* Arrow up on Nubi side */}
      <path d="M250 106 L246 114 L250 112 L254 114 Z" fill="#17b3a3" opacity="0.8" />
      {/* Tagline */}
      <text x="160" y="180" textAnchor="middle" fontSize="10" fill="#566377" fontFamily="Inter,sans-serif" letterSpacing="0.05em">
        browser-first · lower cost · no modeling tax
      </text>
    </svg>
  )
}

/** Kernel / cost comparison bar chart motif */
function CostComparisonIllustration() {
  const bars = [
    { label: 'Looker', height: 88, color: '#e2e8f0' },
    { label: 'Tableau', height: 76, color: '#e2e8f0' },
    { label: 'Sigma', height: 68, color: '#e2e8f0' },
    { label: 'Hex', height: 58, color: '#e2e8f0' },
    { label: 'Cube', height: 50, color: '#e2e8f0' },
    { label: 'Metabase', height: 44, color: '#e2e8f0' },
    { label: 'Power BI', height: 38, color: '#e2e8f0' },
    { label: 'Superset', height: 30, color: '#e2e8f0' },
    { label: 'Nubi', height: 12, color: '#17b3a3', glow: true },
  ]
  const W = 300, H = 140, barW = 24, gap = 8, startX = 16
  return (
    <svg viewBox={`0 0 ${W} ${H + 30}`} fill="none" xmlns="http://www.w3.org/2000/svg"
      className="w-full max-w-xs mx-auto" aria-hidden="true">
      {/* Y-axis label */}
      <text x="4" y="12" fontSize="8" fill="#93a4bd" fontFamily="Inter,sans-serif">embed cost / view →</text>
      {bars.map((b, i) => {
        const x = startX + i * (barW + gap)
        const y = H - b.height
        return (
          <g key={b.label}>
            {b.glow && (
              <rect x={x - 2} y={y - 4} width={barW + 4} height={b.height + 4}
                rx="4" fill="#17b3a3" opacity="0.18" />
            )}
            <rect x={x} y={y} width={barW} height={b.height} rx="3"
              fill={b.glow ? 'url(#nubiBar)' : b.color} opacity={b.glow ? 1 : 0.55} />
            <text x={x + barW / 2} y={H + 14} textAnchor="middle"
              fontSize="7" fill={b.glow ? '#17b3a3' : '#93a4bd'}
              fontWeight={b.glow ? '700' : '400'}
              fontFamily="Inter,sans-serif"
              transform={`rotate(-35 ${x + barW / 2} ${H + 14})`}>
              {b.label}
            </text>
          </g>
        )
      })}
      <defs>
        <linearGradient id="nubiBar" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
      </defs>
    </svg>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Helpers                                                                    */
/* ─────────────────────────────────────────────────────────────────────────── */

/** Returns true if a value string contains "unverified" */
function isUnverified(val) {
  return typeof val === 'string' && val.toLowerCase().includes('unverified')
}

const ALL_TOOLS = [NUBI.name, ...COMPETITORS.map(c => c.name)]

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Sub-components                                                             */
/* ─────────────────────────────────────────────────────────────────────────── */

/** Tooltip wrapper — hover reveals description */
function Tooltip({ text, children }) {
  return (
    <span className="group relative inline-flex items-center gap-1">
      {children}
      <span className="absolute bottom-full left-0 mb-2 w-64 rounded-lg bg-surface border border-border
        shadow-xl p-3 text-xs text-muted leading-relaxed
        opacity-0 pointer-events-none group-hover:opacity-100 transition-opacity duration-200 z-50">
        {text}
      </span>
    </span>
  )
}

/** Badge indicating an unverified field */
function UnverifiedBadge() {
  return (
    <span className="inline-flex items-center gap-0.5 text-[10px] font-medium px-1.5 py-0.5 rounded-full
      bg-surface-2 text-muted border border-border ml-1 shrink-0">
      <Clock size={9} />
      est.
    </span>
  )
}

/** Single matrix table cell */
function MatrixCell({ toolName, dimKey }) {
  const value = MATRIX[dimKey]?.[toolName] ?? '—'
  const isNubi = toolName === 'Nubi'
  const unverified = isUnverified(value)

  return (
    <td
      className={[
        'px-4 py-3 text-xs align-top border-b border-border leading-relaxed',
        isNubi
          ? 'bg-surface-2 font-medium text-fg'
          : 'bg-surface text-muted',
      ].join(' ')}
      style={{ minWidth: 200, maxWidth: 260 }}
    >
      <span className={isNubi ? 'text-fg' : 'text-muted'}>
        {value}
      </span>
      {unverified && <UnverifiedBadge />}
    </td>
  )
}

/** Pricing quick-summary chips */
function PricingChip({ label }) {
  const lower = label.toLowerCase()
  const isGood = lower.includes('free') || lower.includes('usage-based') || lower.includes('open source')
  const isBad = lower.includes('contact sales') || lower.includes('no public') || lower.includes('$60k') || lower.includes('negotiated')
  return (
    <span className={[
      'inline-block text-[11px] font-medium px-2 py-0.5 rounded-full border',
      isGood
        ? 'text-accent border-accent bg-accent/10'
        : isBad
          ? 'text-muted border-border bg-surface-2'
          : 'text-fg border-border bg-surface-2',
    ].join(' ')}>
      {label}
    </span>
  )
}

/** Competitor card */
function CompetitorCard({ competitor }) {
  const [expanded, setExpanded] = useState(false)

  // Derive a short pricing label from pricing text
  function getPricingChips(text) {
    const chips = []
    if (/free tier|free forever|community free|open source|OSS|free \(/i.test(text)) chips.push('Free tier')
    if (/per.seat|per-seat|\$.*\/user/i.test(text)) chips.push('Per-seat')
    if (/usage.based|per.view|per.token|per.second|connector bytes/i.test(text)) chips.push('Usage-based')
    if (/contact sales|no public|negotiated|custom/i.test(text)) chips.push('Contact sales')
    if (/capacity|F-SKU|SKU/i.test(text)) chips.push('Capacity-based')
    return chips.slice(0, 3)
  }

  const chips = getPricingChips(competitor.pricing)
  const selfHostYes = /^Yes/i.test(competitor.selfHost)
  const selfHostNo = /^No/i.test(competitor.selfHost)

  return (
    <article className="bg-surface border border-border rounded-2xl overflow-hidden
      hover:border-accent/40 transition-colors duration-200 group">
      {/* Card header */}
      <div className="px-5 pt-5 pb-4 border-b border-border">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="font-display font-semibold text-lg text-fg leading-tight">
              {competitor.name}
            </h3>
            <p className="text-xs text-muted mt-0.5 leading-snug">{competitor.tagline}</p>
          </div>
          {/* Self-host indicator */}
          <div className="shrink-0 flex flex-col items-end gap-1">
            {selfHostYes && (
              <span className="inline-flex items-center gap-1 text-[10px] font-medium text-accent">
                <CheckCircle2 size={11} /> Self-host
              </span>
            )}
            {selfHostNo && (
              <span className="inline-flex items-center gap-1 text-[10px] font-medium text-muted">
                <XCircle size={11} /> Cloud-only
              </span>
            )}
          </div>
        </div>

        {/* Pricing chips */}
        <div className="flex flex-wrap gap-1.5 mt-3">
          {chips.map(c => <PricingChip key={c} label={c} />)}
        </div>
      </div>

      {/* Strength / limitation */}
      <div className="px-5 py-4 grid gap-3">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-widest text-accent mb-1">Strength</p>
          <p className="text-xs text-fg leading-relaxed">{competitor.strength}</p>
        </div>
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-widest text-muted mb-1">Limitation</p>
          <p className="text-xs text-muted leading-relaxed">{competitor.limitation}</p>
        </div>
      </div>

      {/* Expandable detail: pricing text */}
      {expanded && (
        <div className="px-5 pb-4 border-t border-border pt-4">
          <p className="text-[10px] font-semibold uppercase tracking-widest text-muted mb-1">Pricing detail</p>
          <p className="text-xs text-muted leading-relaxed">{competitor.pricing}</p>
          {isUnverified(competitor.pricing) && (
            <p className="text-[10px] text-muted/70 mt-1 flex items-center gap-1">
              <Clock size={9} /> Some pricing data is estimated — re-verify before publishing.
            </p>
          )}
        </div>
      )}

      {/* Footer: toggle + sources */}
      <div className="px-5 py-3 border-t border-border bg-surface-2 flex items-center justify-between gap-2">
        <button
          onClick={() => setExpanded(e => !e)}
          className="text-xs text-primary flex items-center gap-1 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
        >
          {expanded ? <><ChevronUp size={12} /> Less</> : <><ChevronDown size={12} /> Pricing detail</>}
        </button>

        {competitor.sourceUrls?.length > 0 && (
          <div className="flex items-center gap-2 flex-wrap justify-end">
            {competitor.sourceUrls.slice(0, 3).map((url, i) => {
              let label
              try { label = new URL(url).hostname.replace('www.', '') } catch { label = `Source ${i + 1}` }
              return (
                <a
                  key={url}
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-0.5 text-[10px] text-muted hover:text-primary transition-colors"
                >
                  <ExternalLink size={9} />
                  {label}
                </a>
              )
            })}
          </div>
        )}
      </div>
    </article>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Main page                                                                  */
/* ─────────────────────────────────────────────────────────────────────────── */

export default function ComparePage() {
  const [activeTooltipDim, setActiveTooltipDim] = useState(null)

  return (
    <div className="min-h-screen bg-bg text-fg">

      {/* ── HERO ─────────────────────────────────────────────────────────────── */}
      <section className="relative overflow-hidden">
        {/* Subtle gradient backdrop */}
        <div className="absolute inset-0 bg-brand-gradient opacity-[0.04] pointer-events-none" />
        <div className="absolute top-0 right-0 w-96 h-96 bg-brand-teal opacity-[0.04] rounded-full blur-3xl pointer-events-none" />

        <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-20 lg:py-28">
          <div className="grid lg:grid-cols-2 gap-12 items-center">
            {/* Left: copy */}
            <div>
              <p className="text-xs font-semibold uppercase tracking-widest text-accent mb-4">
                Competitive overview · 2026
              </p>
              <h1 className="font-display font-bold text-4xl sm:text-5xl lg:text-6xl text-fg leading-[1.08] mb-6">
                How Nubi
                <br />
                <span className="text-brand-gradient">compares</span>
              </h1>
              <p className="text-lg text-muted leading-relaxed max-w-lg mb-4">
                An honest, grounded comparison of Nubi against Hex, Cube, Metabase, Looker,
                Sigma, Tableau, Power BI, and Apache Superset.
              </p>
              <p className="text-sm text-muted leading-relaxed max-w-lg mb-8 pl-4 border-l-2 border-accent">
                <strong className="text-fg font-semibold">Nubi's edge:</strong> The analytics kernel runs
                in the user's browser by default — so the marginal cost of an embedded dashboard view is ≈&nbsp;$0.
                Arrow IPC + WebGL handles 1M+ point datasets. No hand-written semantic model required to start.
              </p>

              {/* Key stat pills */}
              <div className="flex flex-wrap gap-3">
                {[
                  { icon: <Zap size={13} />, text: '≈ $0 marginal cost / embed view' },
                  { icon: <Layers size={13} />, text: '1M+ pts at 60fps (WebGL)' },
                  { icon: <Shield size={13} />, text: 'JWKS-native auth — no SDK bolt-on' },
                ].map(({ icon, text }) => (
                  <span key={text}
                    className="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5
                      bg-surface border border-border rounded-full text-fg shadow-sm">
                    <span className="text-accent">{icon}</span>
                    {text}
                  </span>
                ))}
              </div>
            </div>

            {/* Right: illustration grid */}
            <div className="flex flex-col items-center gap-6 lg:gap-8">
              <div className="w-full max-w-sm">
                <ScaleIllustration />
              </div>
              <div className="w-full max-w-sm">
                <p className="text-[10px] text-center text-muted uppercase tracking-widest mb-3 font-semibold">
                  Typical embed cost per view (relative)
                </p>
                <CostComparisonIllustration />
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── HONESTY BANNER ───────────────────────────────────────────────────── */}
      <div className="bg-surface-2 border-y border-border py-3 px-4">
        <div className="max-w-7xl mx-auto flex items-start gap-2.5 text-xs text-muted">
          <Info size={14} className="shrink-0 mt-0.5 text-primary" />
          <p>
            <span className="font-semibold text-fg">Data transparency:</span>{' '}
            Competitor data web-researched June 2026 from public pricing pages and independent analysts.
            Fields marked <UnverifiedBadge /> contain estimates or unverified details — re-verify before publishing.
            Sources linked on each competitor card below. Nubi data sourced from{' '}
            <code className="font-mono bg-surface px-1 rounded text-[11px]">ROADMAP.md</code>.
          </p>
        </div>
      </div>

      {/* ── FEATURE MATRIX ───────────────────────────────────────────────────── */}
      <section className="py-16 px-4 sm:px-6 lg:px-8">
        <div className="max-w-7xl mx-auto">
          <div className="mb-8">
            <p className="text-xs font-semibold uppercase tracking-widest text-accent mb-2">Feature matrix</p>
            <h2 className="font-display font-bold text-3xl sm:text-4xl text-fg">
              Dimension-by-dimension
            </h2>
            <p className="text-muted mt-2 text-sm max-w-xl">
              Scroll horizontally on mobile. Nubi column is highlighted. Hover row labels for context.
            </p>
          </div>

          {/* Scroll wrapper */}
          <div className="overflow-x-auto rounded-2xl border border-border shadow-sm">
            <table className="border-collapse w-full" style={{ minWidth: 1200 }}>

              {/* ── Column headers ─────────────────────────────────────────── */}
              <thead>
                <tr>
                  {/* Dimension label column */}
                  <th
                    scope="col"
                    className="sticky left-0 z-20 px-5 py-4 text-left bg-surface border-b border-r border-border"
                    style={{ minWidth: 190, width: 190 }}
                  >
                    <span className="text-[10px] font-semibold uppercase tracking-widest text-muted">
                      Dimension
                    </span>
                  </th>

                  {/* Nubi — special gradient header */}
                  <th
                    scope="col"
                    className="px-5 py-4 text-left border-b border-border relative"
                    style={{ minWidth: 220 }}
                  >
                    <div className="absolute inset-0 bg-brand-gradient opacity-[0.08]" />
                    <div className="relative">
                      <span className="text-[10px] font-semibold uppercase tracking-widest text-accent">
                        ★ Nubi
                      </span>
                      <p className="text-[10px] text-muted mt-0.5 font-normal normal-case tracking-normal">
                        browser-first kernel
                      </p>
                    </div>
                  </th>

                  {/* Competitor headers */}
                  {COMPETITORS.map(c => (
                    <th
                      key={c.name}
                      scope="col"
                      className="px-4 py-4 text-left bg-surface border-b border-border"
                      style={{ minWidth: 200 }}
                    >
                      <span className="text-[10px] font-semibold uppercase tracking-widest text-muted">
                        {c.name}
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>

              {/* ── Rows ───────────────────────────────────────────────────── */}
              <tbody>
                {COMPARE_DIMENSIONS.map((dim, rowIdx) => (
                  <tr
                    key={dim.key}
                    className="group hover:bg-surface-2/50 transition-colors duration-100"
                  >
                    {/* Row label (sticky) */}
                    <td
                      className="sticky left-0 z-10 px-5 py-4 align-top bg-surface border-b border-r border-border
                        group-hover:bg-surface-2 transition-colors duration-100"
                      style={{ minWidth: 190, width: 190 }}
                    >
                      <Tooltip text={dim.description}>
                        <span className="text-sm font-semibold text-fg cursor-default select-none">
                          {dim.label}
                        </span>
                        <Info size={11} className="text-muted opacity-60 shrink-0" />
                      </Tooltip>
                      <p className="text-[10px] text-muted mt-1 leading-snug hidden xl:block">
                        {dim.description.slice(0, 80)}{dim.description.length > 80 ? '…' : ''}
                      </p>
                    </td>

                    {/* Nubi cell — gradient accent */}
                    <td
                      className="px-5 py-4 text-xs align-top border-b border-border leading-relaxed relative"
                      style={{ minWidth: 220 }}
                    >
                      <div className="absolute inset-0 bg-brand-gradient opacity-[0.05]" />
                      <span className="relative font-medium text-fg">
                        {MATRIX[dim.key]?.['Nubi'] ?? '—'}
                      </span>
                    </td>

                    {/* Competitor cells */}
                    {COMPETITORS.map(c => {
                      const value = MATRIX[dim.key]?.[c.name] ?? '—'
                      const unverified = isUnverified(value)
                      return (
                        <td
                          key={c.name}
                          className="px-4 py-4 text-xs align-top bg-surface border-b border-border leading-relaxed text-muted"
                          style={{ minWidth: 200 }}
                        >
                          {value}
                          {unverified && <UnverifiedBadge />}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Data freshness footnote */}
          <p className="mt-4 text-xs text-muted text-right">
            Researched June 2026 · Features and pricing change frequently.
          </p>
        </div>
      </section>

      {/* ── COMPETITOR CARDS ─────────────────────────────────────────────────── */}
      <section className="py-16 px-4 sm:px-6 lg:px-8 bg-surface-2/40 border-t border-border">
        <div className="max-w-7xl mx-auto">
          <div className="mb-10">
            <p className="text-xs font-semibold uppercase tracking-widest text-accent mb-2">Competitor profiles</p>
            <h2 className="font-display font-bold text-3xl sm:text-4xl text-fg">
              Know the field
            </h2>
            <p className="text-muted mt-2 text-sm max-w-xl">
              Strengths, limitations, pricing model, and source links for each tool. Expand a card for full pricing detail.
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-5">
            {COMPETITORS.map(c => (
              <CompetitorCard key={c.name} competitor={c} />
            ))}
          </div>
        </div>
      </section>

      {/* ── NUBI DEEP-DIVE ───────────────────────────────────────────────────── */}
      <section className="py-16 px-4 sm:px-6 lg:px-8 border-t border-border">
        <div className="max-w-5xl mx-auto">
          <div className="mb-10">
            <p className="text-xs font-semibold uppercase tracking-widest text-accent mb-2">Why Nubi</p>
            <h2 className="font-display font-bold text-3xl sm:text-4xl text-fg">
              The structural difference
            </h2>
          </div>

          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {[
              {
                icon: <Zap size={18} />,
                title: 'Near-zero marginal cost',
                body: `Compute runs in the viewer's browser (Pyodide + DuckDB-WASM). 500 concurrent embed viewers don't spin up 500 server kernels — they collapse to 1 cache hit.`
              },
              {
                icon: <Layers size={18} />,
                title: 'Arrow IPC end-to-end',
                body: 'Results move as columnar Arrow buffers over WebSocket. The viz layer reads them directly — no JSON serialisation round-trip. WebGL/WebGPU renders 1M+ points at 60 fps.'
              },
              {
                icon: <Shield size={18} />,
                title: 'Auth as code',
                body: 'Publish your JWKS, implement getToken(), mount <nubi-dashboard>. JWT claims drive row-level security. No separate embed SDK. Diffable in your repo, reviewable in PRs.'
              },
            ].map(({ icon, title, body }) => (
              <div key={title}
                className="bg-surface border border-border rounded-2xl p-5 hover:border-accent/40 transition-colors">
                <div className="w-9 h-9 rounded-xl bg-accent/10 text-accent flex items-center justify-center mb-4">
                  {icon}
                </div>
                <h3 className="font-display font-semibold text-base text-fg mb-2">{title}</h3>
                <p className="text-sm text-muted leading-relaxed">{body}</p>
              </div>
            ))}
          </div>

          {/* Nubi honest limitation */}
          <div className="mt-6 bg-surface border border-border rounded-2xl p-5">
            <p className="text-[10px] font-semibold uppercase tracking-widest text-muted mb-2">
              Honest limitations
            </p>
            <p className="text-sm text-muted leading-relaxed">{NUBI.limitation}</p>
          </div>
        </div>
      </section>

      {/* ── CTA BAND ─────────────────────────────────────────────────────────── */}
      <section className="relative overflow-hidden border-t border-border">
        <div className="absolute inset-0 bg-brand-gradient opacity-[0.06] pointer-events-none" />
        <div className="relative max-w-4xl mx-auto px-4 sm:px-6 py-20 text-center">
          <p className="text-xs font-semibold uppercase tracking-widest text-accent mb-3">
            Ready to try it?
          </p>
          <h2 className="font-display font-bold text-3xl sm:text-4xl lg:text-5xl text-fg mb-4 leading-tight">
            Embed live dashboards
            <br className="hidden sm:block" />{' '}
            at near-zero cost
          </h2>
          <p className="text-muted text-lg mb-10 max-w-xl mx-auto leading-relaxed">
            Connect your warehouse, embed a dashboard in minutes. Generous free tier. No credit card required to start.
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center items-center">
            <Link
              to="/register"
              className="inline-flex items-center gap-2 px-7 py-3.5 rounded-xl font-semibold text-sm
                bg-primary text-primary-fg shadow-lg hover:opacity-90 transition-opacity"
            >
              Get started free
              <ArrowRight size={15} />
            </Link>
            <Link
              to="/docs"
              className="inline-flex items-center gap-2 px-7 py-3.5 rounded-xl font-semibold text-sm
                bg-surface border border-border text-fg hover:border-accent/50 transition-colors"
            >
              Read the docs
            </Link>
          </div>
        </div>
      </section>

    </div>
  )
}
