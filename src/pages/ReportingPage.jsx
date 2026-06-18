/**
 * ReportingPage — /reporting
 *
 * Marketing + architecture page for the "pay per refresh, not per viewer" wedge.
 *
 * Sections:
 *  1. Hero          — headline + subhead + CTAs
 *  2. ViewerFlat    — "Pay per refresh, not per viewer" — flat-cost visual
 *  3. EmbedModes    — live + connector override; frozen snapshot; public/CDN
 *  4. ExportPipeline — "One dashboard → live · PDF · deck" via Flows
 *  5. FrozenAlive   — public dashboards interactive in-browser (DuckDB-WASM)
 *  6. ClosingCta    — links to /pricing and /compare
 *
 * Design tokens: only bg-bg, bg-surface, bg-surface-2, text-fg, text-muted,
 * border-border, text-brand-{navy,blue,teal,cyan}, bg-brand-gradient.
 * Reuses MarketingStyles, useReveal, the lp-* CSS classes, and existing
 * illustration SVGs.
 */

import { Link } from 'react-router-dom'
import MarketingStyles from '../components/marketing/MarketingStyles.jsx'
import useReveal from '../components/marketing/useReveal.js'
import {
  ArrowRight,
  Users,
  Globe,
  FileText,
  Presentation,
  RefreshCw,
  Lock,
  Check,
  Database,
  Package,
  ChevronRight,
  Share2,
  Layers,
  MonitorPlay,
  Clock,
  Cpu,
} from 'lucide-react'
import EdgeCache from '../components/illustrations/EdgeCache.jsx'
import EmbedAuth from '../components/illustrations/EmbedAuth.jsx'
import FlowOrchestration from '../components/illustrations/FlowOrchestration.jsx'

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Scoped styles (all rp-* prefixed to not leak)                              */
/* ─────────────────────────────────────────────────────────────────────────── */

const ScopedStyles = () => (
  <style>{`
    /* ── Viewer-cost chart bars ── */
    .rp-bar-nubi {
      background: linear-gradient(90deg, #2456a6, #17b3a3);
    }
    .rp-bar-other {
      background: linear-gradient(90deg, #e11d48 0%, #be185d 100%);
    }

    /* ── Embed mode card hover ── */
    .rp-mode-card {
      transition: transform 0.22s cubic-bezier(0.34, 1.4, 0.64, 1),
                  box-shadow 0.22s ease,
                  border-color 0.22s ease;
    }
    .rp-mode-card:hover {
      transform: translateY(-4px);
      box-shadow: 0 24px 48px -18px rgba(27, 35, 99, 0.28);
      border-color: rgba(23, 179, 163, 0.45);
    }

    /* ── Pipeline step connector ── */
    .rp-connector {
      background: linear-gradient(180deg, #2456a6 0%, #17b3a3 100%);
    }

    /* ── Pipeline card ── */
    .rp-pipeline-card {
      transition: transform 0.22s cubic-bezier(0.34, 1.4, 0.64, 1), box-shadow 0.22s ease;
    }
    .rp-pipeline-card:hover {
      transform: translateY(-3px);
      box-shadow: 0 18px 40px -16px rgba(27, 35, 99, 0.32);
    }

    /* ── Frozen-alive browser mockup animation ── */
    @keyframes rp-cursor-blink {
      0%, 100% { opacity: 1; }
      50%       { opacity: 0; }
    }
    .rp-cursor { animation: rp-cursor-blink 1.2s step-end infinite; }

    @keyframes rp-bar-grow {
      from { transform: scaleX(0); }
      to   { transform: scaleX(1); }
    }
    .rp-bar-anim {
      transform-origin: left;
      animation: rp-bar-grow 1.2s cubic-bezier(0.22, 1, 0.36, 1) both;
    }
    .rp-bar-anim:nth-child(2) { animation-delay: 0.1s; }
    .rp-bar-anim:nth-child(3) { animation-delay: 0.2s; }
    .rp-bar-anim:nth-child(4) { animation-delay: 0.3s; }
  `}</style>
)

/* ─────────────────────────────────────────────────────────────────────────── */
/*  §1  Hero                                                                    */
/* ─────────────────────────────────────────────────────────────────────────── */

function HeroSection() {
  return (
    <section id="rp-hero" className="relative scroll-mt-14 bg-bg px-3 sm:px-5 pt-3 sm:pt-5">
      <div
        className="lp-hero-panel relative max-w-[1440px] mx-auto rounded-[1.5rem] sm:rounded-[2rem] overflow-hidden border border-border dark:border-white/[0.06]"
      >
        {/* mesh blobs */}
        <div
          className="lp-mesh-a lp-mesh-blob pointer-events-none absolute -top-40 -left-40 w-[42rem] h-[42rem] rounded-full"
          style={{ background: 'radial-gradient(circle, rgba(72,124,214,0.26) 0%, transparent 65%)' }}
          aria-hidden="true"
        />
        <div
          className="lp-mesh-b lp-mesh-blob pointer-events-none absolute top-1/3 -right-48 w-[36rem] h-[36rem] rounded-full"
          style={{ background: 'radial-gradient(circle, rgba(45,212,191,0.14) 0%, transparent 65%)' }}
          aria-hidden="true"
        />
        {/* perspective grid */}
        <svg
          className="lp-hero-grid pointer-events-none absolute inset-x-0 bottom-0 h-[55%] w-full"
          preserveAspectRatio="none"
          viewBox="0 0 1200 400"
          aria-hidden="true"
        >
          <defs>
            <linearGradient id="rp-gridfade" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#8db4f5" stopOpacity="0" />
              <stop offset="1" stopColor="#8db4f5" stopOpacity="0.8" />
            </linearGradient>
          </defs>
          {Array.from({ length: 13 }, (_, i) => (
            <line key={`v${i}`} x1={600 + (i - 6) * 100} y1="0" x2={600 + (i - 6) * 260} y2="400"
              stroke="url(#rp-gridfade)" strokeWidth="1" />
          ))}
          {Array.from({ length: 7 }, (_, i) => (
            <line key={`h${i}`} x1="0" y1={60 + i * 56 + i * i * 2} x2="1200" y2={60 + i * 56 + i * i * 2}
              stroke="url(#rp-gridfade)" strokeWidth="1" />
          ))}
        </svg>
        <div className="lp-noise pointer-events-none absolute inset-0" aria-hidden="true" />

        <div className="relative px-5 sm:px-10 lg:px-14 pt-12 sm:pt-16 lg:pt-20 pb-14 sm:pb-20">
          {/* Two-column layout: copy left, illustration right */}
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_1fr] gap-12 lg:gap-16 items-center">

            {/* Copy */}
            <div>
              <p className="inline-flex items-center gap-2 font-mono text-[11px] sm:text-xs font-medium tracking-wide text-brand-teal dark:text-teal-300/90 border border-border dark:border-white/10 bg-white/60 dark:bg-white/[0.04] rounded-full px-3.5 py-1.5 mb-6 sm:mb-8">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-teal-400 opacity-60" />
                  <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-teal-300" />
                </span>
                browser-native · duckdb-wasm · flows-powered
              </p>

              <h1 className="font-display text-4xl sm:text-5xl lg:text-[3.6rem] xl:text-[4rem] font-bold leading-[1.05] tracking-tight mb-5 sm:mb-7 text-fg">
                Unlimited viewers.
                <br />
                <span className="lp-hero-gradient-text">Pay per refresh —</span>
                <br />
                not per viewer.
              </h1>

              <p className="text-base sm:text-lg leading-relaxed mb-8 sm:mb-9 max-w-lg text-muted dark:text-slate-300/90">
                A dashboard with 10 or 10,000,000 viewers{' '}
                <strong className="text-fg font-semibold">costs the same</strong>. The DuckDB-WASM
                kernel runs inside the browser — viewers are free on every plan. Embed it live,
                export to PDF or deck, auto-refresh via{' '}
                <strong className="text-fg font-semibold">Flows</strong>. Same board, every output.
              </p>

              <div className="flex flex-col sm:flex-row flex-wrap gap-3 mb-8 sm:mb-10">
                <Link
                  to="/register"
                  className="lp-cta-glow inline-flex items-center justify-center gap-2 px-7 py-3.5 rounded-xl text-base font-semibold transition-all bg-brand-gradient text-white hover:-translate-y-0.5 min-h-[48px]"
                >
                  Start free
                  <ArrowRight size={16} strokeWidth={2.5} />
                </Link>
                <Link
                  to="/pricing"
                  className="inline-flex items-center justify-center gap-2 px-6 py-3.5 rounded-xl text-base font-semibold transition-all bg-surface border border-border text-fg hover:border-brand-blue dark:bg-white/[0.06] dark:border-white/15 dark:text-white dark:hover:bg-white/[0.12] dark:hover:border-white/25 min-h-[48px]"
                >
                  See pricing
                </Link>
              </div>

              <div className="flex flex-wrap gap-x-5 gap-y-2 font-mono text-[11px] font-medium text-muted">
                {[
                  '≈ $0 / dashboard view',
                  'unlimited viewers every plan',
                  'frozen snapshots stay interactive',
                  'one board → live · pdf · deck',
                ].map(f => (
                  <span key={f} className="flex items-center gap-1.5">
                    <Check size={11} strokeWidth={2.5} className="text-teal-400" />
                    {f}
                  </span>
                ))}
              </div>
            </div>

            {/* Right: architecture diagram — viewer fan-in */}
            <div className="relative mt-4 lg:mt-0">
              {/* glow */}
              <div
                className="pointer-events-none absolute -inset-8 rounded-[2.5rem] blur-2xl opacity-50"
                style={{
                  background: 'radial-gradient(ellipse 70% 60% at 50% 55%, rgba(36,86,166,0.28) 0%, rgba(23,179,163,0.15) 55%, transparent 78%)',
                }}
                aria-hidden="true"
              />
              <div className="lp-float-1 relative rounded-2xl overflow-hidden border border-border bg-surface shadow-[0_30px_70px_-26px_rgba(27,35,99,0.4)]">
                <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border bg-surface-2">
                  <span className="flex gap-1.5" aria-hidden="true">
                    <span className="w-2.5 h-2.5 rounded-full bg-[#f4726f]/80" />
                    <span className="w-2.5 h-2.5 rounded-full bg-[#f5bd4f]/80" />
                    <span className="w-2.5 h-2.5 rounded-full bg-[#61c554]/80" />
                  </span>
                  <span className="flex-1 max-w-xs mx-auto flex items-center justify-center gap-1.5 font-mono text-[10.5px] text-muted bg-bg border border-border rounded-md px-3 py-1">
                    <Lock size={9} className="text-teal-400/80" />
                    your-app.com/embed
                  </span>
                </div>
                {/* The "edge cache / viewer collapse" illustration */}
                <div className="p-4 sm:p-6 bg-surface">
                  <EdgeCache className="w-full max-w-[480px] mx-auto" />
                </div>
                {/* annotation strip */}
                <div className="grid grid-cols-3 divide-x divide-border border-t border-border bg-surface-2">
                  {[
                    { v: '≈ $0', l: 'per view' },
                    { v: '∞', l: 'viewers' },
                    { v: '1', l: 'warehouse hit' },
                  ].map(s => (
                    <div key={s.l} className="px-4 py-3 text-center">
                      <div
                        className="font-display text-xl font-bold"
                        style={{ background: 'linear-gradient(105deg, #2456a6, #17b3a3)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text' }}
                      >
                        {s.v}
                      </div>
                      <div className="font-mono text-[10px] text-muted mt-0.5">{s.l}</div>
                    </div>
                  ))}
                </div>
              </div>

              {/* floating chips */}
              <div className="lp-float-2 lp-hero-chip absolute -left-4 sm:-left-7 top-24 hidden md:flex items-center gap-2.5 rounded-xl px-3.5 py-2.5">
                <Users size={15} className="text-teal-300" />
                <span className="font-mono text-[11px] leading-tight text-fg dark:text-white">
                  500 viewers
                  <span className="block text-[9.5px] text-muted">→ 1 warehouse query</span>
                </span>
              </div>
              <div className="lp-float-3 lp-hero-chip absolute -right-3 sm:-right-6 bottom-16 hidden md:flex items-center gap-2.5 rounded-xl px-3.5 py-2.5">
                <Cpu size={15} className="text-sky-300" />
                <span className="font-mono text-[11px] leading-tight text-fg dark:text-white">
                  browser kernel
                  <span className="block text-[9.5px] text-muted">duckdb-wasm · 0 ms cold start</span>
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  §2  Pay per refresh, not per viewer — flat-cost visual                     */
/* ─────────────────────────────────────────────────────────────────────────── */

// Log-scale competitor widths so even the 100-viewer row shows clear contrast.
// Nubi bar is always the same tiny fixed width (flat cost).
// Competitor bar uses log10(viewers) / log10(1M) * 85 → grows visibly from row 1.
const VIEWER_TIERS = [
  { label: '100 viewers',  viewers: 100,     nubiLabel: 'flat',   compLabel: '$90/mo' },
  { label: '1k viewers',   viewers: 1_000,   nubiLabel: 'flat',   compLabel: '$900/mo' },
  { label: '10k viewers',  viewers: 10_000,  nubiLabel: 'flat',   compLabel: '$9k/mo' },
  { label: '100k viewers', viewers: 100_000, nubiLabel: 'flat',   compLabel: '$90k/mo' },
  { label: '1M viewers',   viewers: 1_000_000, nubiLabel: 'flat', compLabel: '$900k/mo' },
]

// Nubi: always 3% (visually flat hairline)
// Competitor: log10(viewers)/log10(1_000_000) * 85 → 28%, 43%, 57%, 71%, 85%
function getBarWidths(viewers) {
  const nubiPct = 3
  const compPct = Math.round((Math.log10(viewers) / Math.log10(1_000_000)) * 85)
  return { nubiPct, compPct }
}

function ViewerFlatSection() {
  const [ref, seen] = useReveal()

  return (
    <section id="rp-viewer-cost" className="py-14 sm:py-20 lg:py-24 bg-bg scroll-mt-14">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.1fr] gap-12 lg:gap-16 items-center">

          {/* Copy */}
          <div>
            <p className="font-mono text-[11px] font-semibold tracking-[0.18em] uppercase mb-4 text-brand-teal">
              The economics
            </p>
            <h2 className="font-display text-3xl sm:text-4xl lg:text-5xl font-bold leading-[1.08] tracking-tight mb-5 text-fg">
              Cost stays flat as
              <br />
              <span className="text-brand-gradient">your audience grows.</span>
            </h2>
            <p className="text-sm sm:text-base lg:text-lg leading-relaxed mb-6 text-muted">
              Most BI vendors charge per viewer seat. Add ten thousand users and you
              owe ten thousand licenses. Nubi charges for{' '}
              <strong className="text-fg font-semibold">refreshes</strong> — the
              data-fetching events — not for eyeballs. The DuckDB-WASM kernel renders
              the chart inside each visitor's tab, so the marginal cost of an
              additional viewer is{' '}
              <strong className="text-fg font-semibold">≈ $0</strong>.
            </p>
            <p className="text-sm text-muted leading-relaxed">
              500 viewers of the same dashboard collapse to a{' '}
              <strong className="text-fg font-semibold">single warehouse hit</strong> via
              content-hashed edge cache — so Nubi's cost curve is genuinely flat, not
              just marketed that way.
            </p>

            <div className="mt-8 flex flex-wrap gap-3">
              <Link
                to="/pricing"
                className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all bg-surface border border-border text-fg hover:border-brand-blue hover:text-primary min-h-[44px]"
              >
                See full pricing <ChevronRight size={14} />
              </Link>
              <Link
                to="/compare"
                className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all text-muted hover:text-fg min-h-[44px]"
              >
                Compare vs Hex &amp; Cube <ChevronRight size={14} />
              </Link>
            </div>
          </div>

          {/* Chart */}
          <div ref={ref} className={`lp-reveal ${seen ? 'lp-in' : ''}`}>
            <div className="rounded-2xl border border-border bg-surface overflow-hidden shadow-sm">
              {/* Header */}
              <div className="px-6 py-4 border-b border-border bg-surface-2 flex items-center justify-between">
                <span className="font-mono text-[11px] font-semibold uppercase tracking-wider text-muted">
                  Cost vs viewer count (log scale)
                </span>
                <div className="flex items-center gap-4">
                  <span className="flex items-center gap-1.5 font-mono text-[10px] text-brand-teal">
                    <span className="w-3 h-2 rounded-sm rp-bar-nubi inline-block" />
                    Nubi
                  </span>
                  <span className="flex items-center gap-1.5 font-mono text-[10px] text-rose-500">
                    <span className="w-3 h-2 rounded-sm rp-bar-other inline-block" />
                    Per-seat BI
                  </span>
                </div>
              </div>

              {/* Bars */}
              <div className="p-6 flex flex-col gap-5">
                {VIEWER_TIERS.map((t) => {
                  const { nubiPct, compPct } = getBarWidths(t.viewers)
                  return (
                    <div key={t.label} className="flex flex-col gap-2">
                      <span className="font-mono text-[11px] font-semibold text-muted">{t.label}</span>
                      <div className="flex flex-col gap-1.5">
                        {/* Nubi bar — always the same tiny flat width */}
                        <div className="flex items-center gap-2">
                          <div className="flex-1 h-5 bg-surface-2 rounded-md overflow-hidden">
                            <div
                              className="h-full rounded-md rp-bar-nubi rp-bar-anim"
                              style={{ width: `${nubiPct}%` }}
                            />
                          </div>
                          <span className="w-24 text-right font-mono text-[10.5px] font-semibold text-brand-teal whitespace-nowrap">{t.nubiLabel}</span>
                        </div>
                        {/* Competitor bar — grows on log scale */}
                        <div className="flex items-center gap-2">
                          <div className="flex-1 h-5 bg-surface-2 rounded-md overflow-hidden">
                            <div
                              className="h-full rounded-md rp-bar-other rp-bar-anim"
                              style={{ width: `${compPct}%` }}
                            />
                          </div>
                          <span className="w-24 text-right font-mono text-[10.5px] font-semibold text-rose-500 whitespace-nowrap">
                            {t.compLabel}
                          </span>
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>

              <div className="px-6 pb-5">
                <p className="font-mono text-[10px] text-muted opacity-70 leading-relaxed">
                  Illustrative — competitor bar width is log-scaled to viewer count (100 → 1M).
                  Nubi charges for refresh events (data fetches), not viewer seats.
                  Per-seat BI scales linearly; exact numbers depend on vendor and tier.
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  §3  Embedding modes                                                         */
/* ─────────────────────────────────────────────────────────────────────────── */

const EMBED_MODES = [
  {
    id: 'live',
    icon: MonitorPlay,
    accent: '#2456a6',
    badge: 'live embed',
    title: 'Live embed + connector override',
    body: 'Drop a <nubi-dashboard> web component into your host app. Your backend signs a short-lived JWT carrying per-viewer RLS claims — Nubi injects them into the SQL AST before any query runs. Use the connector override to point individual embed tokens at a different data source per tenant.',
    chips: ['<nubi-dashboard>', 'JWT RLS', 'id-based connector override', 'cross-filter'],
    code: '<nubi-dashboard\n  dashboard-id="revenue"\n  get-token="getEmbedToken"\n  connector-id="tenant-42" />',
  },
  {
    id: 'frozen',
    icon: Package,
    accent: '#17b3a3',
    badge: 'frozen snapshot',
    title: 'Frozen snapshot — still interactive',
    body: 'Schedule a Flows pipeline to snapshot the dashboard state into a frozen DuckDB bundle. Viewers open the snapshot and the full DuckDB-WASM kernel loads their copy — cross-filtering, drill-downs, and query re-runs work offline. The data is frozen at snapshot time; the UX is not.',
    chips: ['duckdb snapshot', 'offline-capable', 'scheduled via Flows', 'interactive cross-filter'],
    code: '# flows/snapshot.yaml\nschedule: "0 */6 * * *"\nsteps:\n  - type: snapshot\n    dashboard: revenue\n    output: public/revenue.nubi',
  },
  {
    id: 'public',
    icon: Globe,
    accent: '#0ea5e9',
    badge: 'public / CDN',
    title: 'Public CDN static export',
    body: 'Mark a snapshot as public and Nubi publishes it to your CDN as a static bundle. Zero auth, zero backend. The WASM kernel ships with the snapshot — anyone with the URL gets a fully interactive dashboard, not a dead screenshot. Opt-in and labeled unsafe for sensitive data.',
    chips: ['static CDN bundle', 'zero backend', 'opt-in · unsafe flag', 'public link'],
    code: '# opt-in: unsafe_public: true\nnubi export --dashboard revenue \\\n  --output dist/revenue.nubi \\\n  --public',
  },
]

function EmbedModeCard({ mode, idx }) {
  const [ref, seen] = useReveal()
  const Icon = mode.icon
  return (
    <div
      ref={ref}
      style={{ transitionDelay: `${idx * 90}ms` }}
      className={`lp-reveal ${seen ? 'lp-in' : ''} rp-mode-card flex flex-col rounded-2xl border border-border bg-surface p-6 sm:p-7`}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <span
          className="inline-flex items-center justify-center w-11 h-11 rounded-xl text-white shadow-md"
          style={{ background: `linear-gradient(135deg, ${mode.accent}, ${mode.accent}cc)` }}
        >
          <Icon size={20} strokeWidth={1.75} />
        </span>
        <span
          className="font-mono text-[10px] font-semibold uppercase tracking-[0.15em] px-2.5 py-1 rounded-full border"
          style={{ color: mode.accent, borderColor: `${mode.accent}40`, background: `${mode.accent}0e` }}
        >
          {mode.badge}
        </span>
      </div>

      <h3 className="font-display text-lg sm:text-xl font-bold text-fg leading-snug mb-2">
        {mode.title}
      </h3>
      <p className="text-[13.5px] sm:text-sm leading-relaxed text-muted flex-1 mb-5">
        {mode.body}
      </p>

      {/* Code block */}
      <div className="rounded-xl overflow-hidden border border-white/10 bg-[#0c1230] mb-4">
        <div className="flex items-center gap-2 px-3 py-2 border-b border-white/[0.07] bg-white/[0.03]">
          <span className="flex gap-1.5" aria-hidden="true">
            <span className="w-2 h-2 rounded-full bg-[#f4726f]/70" />
            <span className="w-2 h-2 rounded-full bg-[#f5bd4f]/70" />
            <span className="w-2 h-2 rounded-full bg-[#61c554]/70" />
          </span>
          <span className="ml-1 font-mono text-[10px] text-slate-400">{mode.badge}</span>
        </div>
        <pre className="px-4 py-3 font-mono text-[11px] leading-relaxed text-slate-300 whitespace-pre overflow-x-auto">
          {mode.code}
        </pre>
      </div>

      {/* Chips */}
      <div className="flex flex-wrap gap-1.5">
        {mode.chips.map(c => (
          <span
            key={c}
            className="font-mono text-[10px] font-semibold px-2 py-1 rounded-full border"
            style={{ color: mode.accent, borderColor: `${mode.accent}40`, background: `${mode.accent}0e` }}
          >
            {c}
          </span>
        ))}
      </div>
    </div>
  )
}

function EmbedModesSection() {
  return (
    <section id="rp-embed" className="relative scroll-mt-14 bg-bg px-3 sm:px-5 py-6 sm:py-8">
      <div
        className="lp-hero-panel relative max-w-[1440px] mx-auto rounded-[1.5rem] sm:rounded-[2rem] overflow-hidden border border-border dark:border-white/[0.06]"
      >
        <div className="lp-noise pointer-events-none absolute inset-0" aria-hidden="true" />
        <div
          className="lp-mesh-blob pointer-events-none absolute -top-32 -right-40 w-[36rem] h-[36rem] rounded-full"
          style={{ background: 'radial-gradient(circle, rgba(45,212,191,0.12) 0%, transparent 65%)' }}
          aria-hidden="true"
        />

        <div className="relative px-5 sm:px-10 lg:px-14 py-10 sm:py-14 lg:py-16">
          {/* Section header */}
          <div className="text-center mb-10 sm:mb-12 max-w-3xl mx-auto">
            <p className="font-mono text-[11px] font-semibold tracking-[0.18em] uppercase mb-4 text-brand-teal">
              embedding modes
            </p>
            <h2 className="font-display text-3xl sm:text-4xl lg:text-[3.2rem] font-bold leading-[1.08] tracking-tight mb-4 text-fg">
              Three modes,
              <br />
              <span className="lp-hero-gradient-text">one dashboard.</span>
            </h2>
            <p className="text-sm sm:text-base lg:text-lg leading-relaxed text-muted">
              Embed live with per-viewer JWT auth, ship a frozen-but-interactive snapshot,
              or publish to a CDN as a zero-backend static bundle. Pick the mode that
              fits your security posture — not your vendor's product limits.
            </p>
          </div>

          {/* Three cards */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
            {EMBED_MODES.map((m, i) => (
              <EmbedModeCard key={m.id} mode={m} idx={i} />
            ))}
          </div>

          {/* Illustration */}
          <div className="mt-10 sm:mt-14 max-w-4xl mx-auto">
            <div className="lp-illo-card rounded-2xl border border-border overflow-hidden">
              <div className="p-6 sm:p-8 flex items-center justify-center">
                <EmbedAuth className="w-full max-w-[480px]" />
              </div>
            </div>
            <p className="text-center font-mono text-[11px] text-muted mt-3 leading-relaxed">
              JWT token → AST predicate injection → rendered in-browser. Auth lives in your repo, not a vendor UI.
            </p>
          </div>
        </div>
      </div>
    </section>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  §4  Export pipeline — one dashboard → live · PDF · deck                   */
/* ─────────────────────────────────────────────────────────────────────────── */

const PIPELINE_STEPS = [
  {
    icon: Database,
    color: 'linear-gradient(135deg, #1b2363, #2456a6)',
    tag: 'source',
    title: 'Your warehouse',
    body: 'BigQuery, Snowflake, Postgres — or the built-in lakehouse over Parquet. A single Flows pipeline queries once and fans the result out to every output.',
    chip: '25+ connectors',
  },
  {
    icon: Layers,
    color: 'linear-gradient(135deg, #2456a6, #17b3a3)',
    tag: 'canvas',
    title: 'Dashboard',
    body: 'One live dashboard with charts, KPIs, cross-filters. Everything-as-code: edit visually or as files, version in git, deploy via CLI.',
    chip: 'echarts · arrow ipc',
  },
  {
    icon: Share2,
    color: 'linear-gradient(135deg, #17b3a3, #2dd4bf)',
    tag: 'flows',
    title: 'Flows auto-refresh',
    body: 'Schedule a Flows DAG to re-query, snapshot, and fan-out on a cron. Same data pipeline drives every output — edit once, propagates everywhere.',
    chip: 'scheduled · incremental',
  },
]

const OUTPUTS = [
  {
    icon: MonitorPlay,
    accent: '#2456a6',
    label: 'Live embed',
    desc: 'Web component. JWT RLS. Real-time.',
  },
  {
    icon: FileText,
    accent: '#0ea5e9',
    label: 'PDF report',
    desc: 'Vector — ECharts SSR → cairosvg.',
  },
  {
    icon: Presentation,
    accent: '#8b5cf6',
    label: 'PPTX deck',
    desc: 'Native SVG in PowerPoint slides.',
  },
  {
    icon: RefreshCw,
    accent: '#17b3a3',
    label: 'Scheduled delivery',
    desc: 'Email or webhook on a cron.',
  },
]

function PipelineStepCard({ s, i }) {
  const [ref, seen] = useReveal()
  const Icon = s.icon
  return (
    <div ref={ref} style={{ transitionDelay: `${i * 100}ms` }}
      className={`lp-reveal ${seen ? 'lp-in' : ''} flex-1 relative flex flex-col`}>
      {/* connector line between cards (desktop) */}
      {i < PIPELINE_STEPS.length - 1 && (
        <div
          className="hidden sm:block absolute top-1/2 -right-3 w-6 h-0.5 rp-connector z-10"
          aria-hidden="true"
        />
      )}
      {/* connector (mobile) */}
      {i < PIPELINE_STEPS.length - 1 && (
        <div
          className="sm:hidden absolute -bottom-3 left-1/2 w-0.5 h-6 rp-connector z-10"
          aria-hidden="true"
        />
      )}
      <div className="rp-pipeline-card flex flex-col flex-1 rounded-2xl border border-border bg-surface p-5 mx-1 sm:mx-2">
        <div className="flex items-center gap-3 mb-3">
          <span
            className="inline-flex items-center justify-center w-9 h-9 rounded-xl text-white shadow-sm"
            style={{ background: s.color }}
          >
            <Icon size={16} strokeWidth={1.9} />
          </span>
          <span className="font-mono text-[10px] font-semibold uppercase tracking-wider text-brand-teal">{s.tag}</span>
        </div>
        <h3 className="font-display text-base font-bold text-fg mb-1.5">{s.title}</h3>
        <p className="text-xs leading-relaxed text-muted flex-1 mb-3">{s.body}</p>
        <span className="self-start font-mono text-[10px] font-semibold px-2 py-1 rounded-full border border-brand-teal/35 bg-brand-teal/[0.07] text-brand-teal">
          {s.chip}
        </span>
      </div>
    </div>
  )
}

function OutputCard({ o, i }) {
  const [ref, seen] = useReveal()
  const Icon = o.icon
  return (
    <div
      ref={ref}
      style={{ transitionDelay: `${i * 70}ms` }}
      className={`lp-reveal ${seen ? 'lp-in' : ''} rp-pipeline-card rounded-2xl border border-border bg-surface p-5 flex flex-col items-center text-center`}
    >
      <span
        className="inline-flex items-center justify-center w-11 h-11 rounded-xl text-white shadow-md mb-3"
        style={{ background: `linear-gradient(135deg, ${o.accent}, ${o.accent}cc)` }}
      >
        <Icon size={20} strokeWidth={1.75} />
      </span>
      <span className="font-display text-sm font-bold text-fg mb-1">{o.label}</span>
      <span className="font-mono text-[10.5px] text-muted leading-snug">{o.desc}</span>
    </div>
  )
}

function ExportPipelineSection() {
  return (
    <section id="rp-pipeline" className="py-14 sm:py-20 lg:py-24 bg-surface-2 border-y border-border scroll-mt-14">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">

        {/* Section header */}
        <div className="text-center mb-12 sm:mb-16 max-w-3xl mx-auto">
          <p className="font-mono text-[11px] font-semibold tracking-[0.18em] uppercase mb-4 text-brand-teal">
            one dashboard · all outputs
          </p>
          <h2 className="font-display text-3xl sm:text-4xl lg:text-5xl font-bold leading-tight mb-4 text-fg">
            Build once.{' '}
            <span className="text-brand-gradient">Deliver everywhere.</span>
          </h2>
          <p className="text-sm sm:text-base lg:text-lg leading-relaxed text-muted">
            The same dashboard canvas is the source of truth for your live embed,
            your PDF board pack, your PPTX deck, and your scheduled email report.
            Flows auto-refreshes and delivers each output — no duplicate pipelines.
          </p>
        </div>

        {/* Pipeline diagram: steps → outputs */}
        <div className="max-w-5xl mx-auto">

          {/* Top: pipeline steps */}
          <div className="flex flex-col sm:flex-row items-center sm:items-stretch gap-0">
            {PIPELINE_STEPS.map((s, i) => (
              <PipelineStepCard key={s.tag} s={s} i={i} />
            ))}
          </div>

          {/* Vertical fan-out arrow */}
          <div className="flex justify-center my-6 sm:my-8">
            <div className="flex flex-col items-center gap-1">
              <div
                className="w-0.5 h-8 rp-connector"
                aria-hidden="true"
              />
              <ArrowRight
                size={18}
                className="text-brand-teal rotate-90"
                strokeWidth={2.5}
                aria-hidden="true"
              />
            </div>
          </div>

          {/* Bottom: output cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {OUTPUTS.map((o, i) => (
              <OutputCard key={o.label} o={o} i={i} />
            ))}
          </div>
        </div>

        {/* Illustration */}
        <div className="mt-14 sm:mt-20 max-w-3xl mx-auto">
          <div className="lp-illo-card rounded-2xl border border-border overflow-hidden">
            <div className="p-8 sm:p-10 flex items-center justify-center">
              <FlowOrchestration className="w-full max-w-[420px]" />
            </div>
          </div>
          <p className="text-center font-mono text-[11px] text-muted mt-4">
            Flows DAG — one pipeline refresh drives every output format.
          </p>
        </div>
      </div>
    </section>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  §5  "Frozen, still alive"                                                   */
/* ─────────────────────────────────────────────────────────────────────────── */

function FrozenAliveSection() {
  const [ref, seen] = useReveal()

  const bars = [
    { label: 'Revenue', pct: 72, color: '#2456a6' },
    { label: 'Churn', pct: 38, color: '#17b3a3' },
    { label: 'LTV', pct: 88, color: '#2dd4bf' },
    { label: 'DAU', pct: 55, color: '#0ea5e9' },
  ]

  return (
    <section id="rp-frozen" className="py-14 sm:py-20 lg:py-24 bg-bg scroll-mt-14">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 lg:gap-16 items-center">

          {/* Illustration: mockup browser with interactive snapshot */}
          <div ref={ref} className={`lp-reveal ${seen ? 'lp-in' : ''} order-2 lg:order-1`}>
            <div className="relative max-w-xl mx-auto">
              {/* glow */}
              <div
                className="pointer-events-none absolute -inset-6 rounded-[2rem] blur-xl opacity-40"
                style={{
                  background: 'radial-gradient(ellipse 70% 55% at 50% 50%, rgba(23,179,163,0.35) 0%, rgba(36,86,166,0.2) 60%, transparent 80%)',
                }}
                aria-hidden="true"
              />
              <div className="relative rounded-2xl overflow-hidden border border-border bg-surface shadow-[0_24px_56px_-22px_rgba(27,35,99,0.4)]">
                {/* browser chrome */}
                <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border bg-surface-2">
                  <span className="flex gap-1.5" aria-hidden="true">
                    <span className="w-2.5 h-2.5 rounded-full bg-[#f4726f]/70" />
                    <span className="w-2.5 h-2.5 rounded-full bg-[#f5bd4f]/70" />
                    <span className="w-2.5 h-2.5 rounded-full bg-[#61c554]/70" />
                  </span>
                  <span className="flex-1 max-w-xs mx-auto flex items-center justify-center gap-1.5 font-mono text-[10.5px] text-muted bg-bg border border-border rounded-md px-3 py-1">
                    <Globe size={9} className="text-teal-400/80" />
                    cdn.yourco.com/dashboards/q3-review
                  </span>
                  {/* frozen badge */}
                  <span className="flex items-center gap-1 font-mono text-[9px] font-semibold px-2 py-0.5 rounded-md bg-amber-400/15 border border-amber-400/30 text-amber-500">
                    <Clock size={9} strokeWidth={2.5} />
                    frozen
                  </span>
                </div>

                {/* Dashboard canvas mock */}
                <div className="p-5 bg-surface">
                  {/* KPI row */}
                  <div className="grid grid-cols-3 gap-3 mb-5">
                    {[
                      { label: 'MRR', value: '$142k', delta: '+8.2%', up: true },
                      { label: 'Customers', value: '2,841', delta: '+124', up: true },
                      { label: 'Churn', value: '1.9%', delta: '-0.3%', up: false },
                    ].map(kpi => (
                      <div key={kpi.label} className="rounded-xl border border-border bg-surface-2 p-3">
                        <p className="font-mono text-[9.5px] font-semibold uppercase tracking-wider text-muted mb-1">{kpi.label}</p>
                        <p className="font-display text-base font-bold text-fg">{kpi.value}</p>
                        <p className={`font-mono text-[10px] font-semibold ${kpi.up ? 'text-brand-teal' : 'text-rose-500'}`}>{kpi.delta}</p>
                      </div>
                    ))}
                  </div>

                  {/* Bar chart mock (interactive) */}
                  <div className="rounded-xl border border-border bg-surface-2 p-4 mb-3">
                    <div className="flex items-center justify-between mb-3">
                      <span className="font-mono text-[10px] font-semibold text-muted">Segment breakdown</span>
                      <span className="font-mono text-[9px] text-brand-teal">click to filter</span>
                    </div>
                    <div className="flex flex-col gap-2">
                      {bars.map(b => (
                        <div key={b.label} className="flex items-center gap-2">
                          <span className="w-12 font-mono text-[9.5px] text-muted text-right">{b.label}</span>
                          <div className="flex-1 h-4 bg-surface rounded overflow-hidden">
                            <div
                              className="h-full rounded transition-all duration-300 hover:opacity-80 cursor-pointer rp-bar-anim"
                              style={{ width: `${b.pct}%`, background: b.color }}
                            />
                          </div>
                          <span className="font-mono text-[9.5px] text-muted w-6 text-right">{b.pct}%</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Status row */}
                  <div className="flex items-center gap-2">
                    <span className="flex items-center gap-1.5 font-mono text-[10px] text-brand-teal">
                      <span className="relative flex h-1.5 w-1.5">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-teal-400 opacity-50" />
                        <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-teal-300" />
                      </span>
                      duckdb-wasm active
                    </span>
                    <span className="font-mono text-[10px] text-muted">·</span>
                    <span className="font-mono text-[10px] text-muted">snapshot: 2025-06-15 06:00 UTC</span>
                    <span className="ml-auto font-mono text-[9px] text-amber-500 flex items-center gap-1">
                      <Clock size={9} strokeWidth={2.5} />
                      frozen
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Copy */}
          <div className="order-1 lg:order-2">
            <p className="font-mono text-[11px] font-semibold tracking-[0.18em] uppercase mb-4 text-brand-teal">
              frozen · still alive
            </p>
            <h2 className="font-display text-3xl sm:text-4xl lg:text-[2.85rem] xl:text-5xl font-bold leading-[1.08] tracking-tight mb-5 text-fg">
              Public dashboards:{' '}
              <span className="text-brand-gradient">interactive,</span>
              <br />
              not screenshots.
            </h2>
            <p className="text-sm sm:text-base lg:text-lg leading-relaxed mb-5 text-muted">
              A frozen Nubi snapshot is not an image. It ships the full DuckDB-WASM kernel
              alongside a compressed snapshot of the query results — so viewers get the
              same cross-filter, drill-down, and hover experience as a live dashboard.
            </p>
            <p className="text-sm leading-relaxed mb-8 text-muted">
              Scheduled Flows pipelines refresh the snapshot on a cron — every 6 hours,
              every morning at 06:00, or on any webhook trigger. The URL never changes.
              CDN edge nodes serve the bundle; your warehouse is called{' '}
              <strong className="text-fg font-semibold">zero times per view</strong>.
            </p>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-8">
              {[
                { icon: Package, label: 'Frozen DuckDB snapshot', desc: 'Compressed query state, not pixels' },
                { icon: MonitorPlay, label: 'Full cross-filter UX', desc: 'WASM kernel ships with the bundle' },
                { icon: RefreshCw, label: 'Scheduled refresh via Flows', desc: 'Cron, webhook, or manual trigger' },
                { icon: Globe, label: 'CDN-served, zero backend', desc: 'Static bundle, no server on load' },
              ].map(f => {
                const Icon = f.icon
                return (
                  <div key={f.label} className="flex items-start gap-3 rounded-xl border border-border bg-surface p-3.5">
                    <span className="shrink-0 mt-0.5 w-8 h-8 rounded-lg bg-surface-2 border border-border flex items-center justify-center">
                      <Icon size={14} strokeWidth={2} className="text-brand-teal" />
                    </span>
                    <div>
                      <p className="text-sm font-semibold text-fg leading-snug">{f.label}</p>
                      <p className="text-[12px] text-muted leading-snug mt-0.5">{f.desc}</p>
                    </div>
                  </div>
                )
              })}
            </div>

            <div className="flex flex-wrap gap-3">
              <Link
                to="/docs"
                className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all bg-surface border border-border text-fg hover:border-brand-blue hover:text-primary min-h-[44px]"
              >
                Read the docs <ArrowRight size={14} strokeWidth={2.5} />
              </Link>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  §6  Closing CTA                                                             */
/* ─────────────────────────────────────────────────────────────────────────── */

function B({ children }) {
  return <strong className="font-semibold text-fg dark:text-white">{children}</strong>
}

function ClosingCtaSection() {
  return (
    <section className="relative bg-bg px-3 sm:px-5 py-6 sm:py-8 pb-8 sm:pb-10">
      <div
        className="lp-hero-panel relative max-w-[1440px] mx-auto rounded-[1.5rem] sm:rounded-[2rem] overflow-hidden border border-border dark:border-white/[0.06]"
      >
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

        <div className="relative max-w-3xl mx-auto px-5 sm:px-10 py-14 sm:py-20 text-center">
          <p className="font-mono text-[11px] font-semibold tracking-[0.18em] uppercase mb-4 text-brand-teal">
            Start today
          </p>
          <h2 className="font-display text-3xl sm:text-4xl lg:text-[3.4rem] font-bold leading-[1.08] tracking-tight mb-4 sm:mb-6 text-fg">
            Unlimited viewers.
            <br />
            <span className="lp-hero-gradient-text">One flat refresh cost.</span>
          </h2>
          <p className="text-sm sm:text-base lg:text-lg leading-relaxed mb-8 sm:mb-10 text-muted dark:text-slate-300/90 max-w-xl mx-auto">
            Connect your warehouse, <B>embed a live dashboard in minutes</B>. Freeze it,
            export it to PDF, turn it into a deck. <B>No credit card required.</B>
          </p>

          <div className="flex flex-col sm:flex-row gap-3 justify-center mb-9">
            <Link
              to="/register"
              className="lp-cta-glow lp-cta-pulse inline-flex items-center justify-center gap-2 px-7 py-3.5 rounded-xl text-base font-semibold transition-all bg-brand-gradient text-white hover:-translate-y-0.5 min-h-[48px]"
            >
              Start free
              <ArrowRight size={16} strokeWidth={2.5} />
            </Link>
            <Link
              to="/pricing"
              className="inline-flex items-center justify-center gap-2 px-6 py-3.5 rounded-xl text-base font-semibold transition-all bg-surface border border-border text-fg hover:border-brand-blue dark:bg-white/[0.06] dark:border-white/15 dark:text-white dark:hover:bg-white/[0.12] dark:hover:border-white/25 min-h-[48px]"
            >
              See pricing →
            </Link>
            <Link
              to="/compare"
              className="inline-flex items-center justify-center gap-2 px-6 py-3.5 rounded-xl text-base font-semibold transition-all bg-surface border border-border text-fg hover:border-brand-blue dark:bg-white/[0.06] dark:border-white/15 dark:text-white dark:hover:bg-white/[0.12] dark:hover:border-white/25 min-h-[48px]"
            >
              Compare vs Hex &amp; Cube →
            </Link>
          </div>

          <div className="flex flex-wrap justify-center gap-x-6 gap-y-2 font-mono text-[11px] font-medium text-muted">
            {[
              'no credit card required',
              'unlimited viewers every plan',
              'apache-2.0 open core',
              'frozen snapshots ship free',
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
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Page                                                                        */
/* ─────────────────────────────────────────────────────────────────────────── */

export default function ReportingPage() {
  return (
    <>
      <MarketingStyles />
      <ScopedStyles />

      <div className="nubi-lp overflow-x-clip bg-bg text-fg font-sans">
        <HeroSection />
        <ViewerFlatSection />
        <EmbedModesSection />
        <ExportPipelineSection />
        <FrozenAliveSection />
        <ClosingCtaSection />
      </div>
    </>
  )
}
