/**
 * LandingPage — Nubi marketing site.
 *
 * Token reference (tailwind.config.js + src/index.css):
 * ──────────────────────────────────────────────────────
 *  bg-bg / bg-surface / bg-surface-2   — surfaces (auto light/dark)
 *  text-fg / text-muted                — text (auto light/dark)
 *  border-border                       — borders (auto light/dark)
 *  bg-primary / text-primary / text-primary-fg — interactive
 *  bg-accent / text-accent / ring-ring — teal accent
 *  text-brand-navy / text-brand-blue / text-brand-teal / text-brand-cyan
 *  bg-brand-gradient / text-brand-gradient
 *  font-display (Space Grotesk) / font-sans (Inter)
 *
 * Section IDs (scroll targets for footer / navbar links):
 * ────────────────────────────────────────────────────────
 *  #hero         — Hero
 *  #features     — Differentiators ("Why Nubi" / six decisions)
 *  #embedding    — Auth-as-code embedding diff row
 *  #connectors   — SQL-first connector SDK diff row
 *  #as-code      — Everything-as-code (in-app code view, local files, CLI, CI)
 *  #how-it-works — How it works
 *  #pricing      — Closing CTA / pricing callout
 *  #compare      — Comparison table section
 *  #about        — Footer brand tagline (re-used for about anchor)
 */

import { useState, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowRight,
  Zap,
  Shield,
  Database,
  Code2,
  Globe,
  Bot,
  Workflow,
  Warehouse,
  ChevronRight,
  Check,
  X,
  Minus,
  PlugZap,
  SearchCode,
  Layers,
  Lock,
  KeyRound,
  Filter,
  Sparkles,
  ArrowRightCircle,
  Star,
  Headset,
  Users,
  Server,
  SlidersHorizontal,
  TrendingDown,
  CheckCircle2,
  XCircle,
  Wallet,
  GitBranch,
  Terminal,
  FolderGit2,
} from 'lucide-react'
import {
  TIERS,
  BILLING_MODEL,
  CALC_OPTIONS,
  ORCH_CALC_OPTIONS,
  ENTERPRISE_NOTE,
  OVERAGE_RATES,
  OVERAGE_NOTE,
} from '../data/pricing.js'
import { CONNECTOR_TYPES } from '../data/connectors.js'
import {
  fetchPricingData, recommendNubi, estimateWarehouseCu,
  FALLBACK_COMPETITORS_WAREHOUSE, WAREHOUSE_CU_MULTIPLIER,
} from '../lib/pricing.js'
import QueryWorkspace from '../components/illustrations/QueryWorkspace.jsx'
import KernelInBrowser from '../components/illustrations/KernelInBrowser.jsx'
import EdgeCache from '../components/illustrations/EdgeCache.jsx'
import WebGLPerf from '../components/illustrations/WebGLPerf.jsx'
import ConnectorSdk from '../components/illustrations/ConnectorSdk.jsx'
import EmbedAuth from '../components/illustrations/EmbedAuth.jsx'
import LakehouseFlow from '../components/illustrations/LakehouseFlow.jsx'
// Dev-centric features read better as real code than abstract art.
import { ConnectorSdkCode, FlowCode, EmbedAuthCode, LlmDashboardCode, FilesAsCodeCli } from '../components/illustrations/CodeTile.jsx'

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Scoped animations — only on .nubi-lp so they don't bleed to other pages   */
/* ─────────────────────────────────────────────────────────────────────────── */
const ScopedStyles = () => (
  <style>{`
    /* ── Hero float (product frame + chips, staggered) ── */
    @keyframes lp-float {
      0%, 100% { transform: translateY(0px); }
      50%       { transform: translateY(-8px); }
    }
    .lp-float-1 { animation: lp-float 7s ease-in-out infinite; }
    .lp-float-2 { animation: lp-float 8.5s ease-in-out infinite; animation-delay: 0.9s; }
    .lp-float-3 { animation: lp-float 9.5s ease-in-out infinite; animation-delay: 1.7s; }

    /* ── Scroll reveal (decision rows) ── */
    .lp-reveal {
      opacity: 0;
      transform: translateY(26px);
      transition: opacity 0.7s ease, transform 0.7s cubic-bezier(0.22, 1, 0.36, 1);
    }
    .lp-reveal.lp-in { opacity: 1; transform: none; }
    @media (prefers-reduced-motion: reduce) {
      .lp-reveal { transition: none; opacity: 1; transform: none; }
      .lp-float-1, .lp-float-2, .lp-float-3, .lp-mesh-a, .lp-mesh-b { animation: none; }
    }

    /* ── Observatory hero panel — light by day, dark by night ── */
    .lp-hero-panel {
      background:
        radial-gradient(ellipse 60% 55% at 18% 8%,  rgba(36, 86, 166, 0.13) 0%, transparent 62%),
        radial-gradient(ellipse 55% 60% at 88% 36%, rgba(23, 179, 163, 0.11) 0%, transparent 60%),
        linear-gradient(180deg, #f6f9ff 0%, #e9effb 100%);
      transition: background 0.45s ease, border-color 0.45s ease;
    }
    .dark .lp-hero-panel {
      background:
        radial-gradient(ellipse 60% 55% at 18% 8%,  rgba(46, 96, 186, 0.34) 0%, transparent 62%),
        radial-gradient(ellipse 55% 60% at 88% 36%, rgba(20, 160, 146, 0.20) 0%, transparent 60%),
        radial-gradient(ellipse 70% 60% at 50% 115%, rgba(27, 35, 99, 0.55) 0%, transparent 70%),
        #070b21;
    }
    .lp-mesh-blob { opacity: 0.45; }
    .dark .lp-mesh-blob { opacity: 1; }
    .lp-hero-grid { opacity: 0.22; }
    .dark .lp-hero-grid { opacity: 0.14; }
    /* drifting mesh blobs — slow, barely-there life */
    @keyframes lp-mesh-a {
      0%, 100% { transform: translate(0, 0) scale(1); }
      50%       { transform: translate(3%, -4%) scale(1.07); }
    }
    @keyframes lp-mesh-b {
      0%, 100% { transform: translate(0, 0) scale(1); }
      50%       { transform: translate(-4%, 3%) scale(1.1); }
    }
    .lp-mesh-a { animation: lp-mesh-a 17s ease-in-out infinite; will-change: transform; }
    .lp-mesh-b { animation: lp-mesh-b 21s ease-in-out infinite; will-change: transform; }
    /* film grain on the dark panel */
    .lp-noise {
      background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>");
      opacity: 0.025;
      mix-blend-mode: overlay;
    }
    .dark .lp-noise { opacity: 0.05; }
    /* gradient display text — brand stops on light, lifted stops on dark */
    .lp-hero-gradient-text {
      background: linear-gradient(105deg, #1b2363 0%, #2456a6 45%, #17b3a3 100%);
      -webkit-background-clip: text;
      background-clip: text;
      -webkit-text-fill-color: transparent;
      color: transparent;
    }
    .dark .lp-hero-gradient-text {
      background: linear-gradient(105deg, #8db4f5 0%, #5fd6c8 60%, #2dd4bf 100%);
      -webkit-background-clip: text;
      background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    /* primary CTA glow */
    .lp-cta-glow {
      box-shadow: 0 12px 44px -10px rgba(23, 179, 163, 0.55), 0 4px 16px rgba(36, 86, 166, 0.45);
    }
    .lp-cta-glow:hover {
      box-shadow: 0 16px 56px -10px rgba(23, 179, 163, 0.7), 0 6px 20px rgba(36, 86, 166, 0.55);
    }
    /* glassy floating stat chips over the product frame */
    .lp-hero-chip {
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
      background: rgba(255, 255, 255, 0.78);
      border: 1px solid rgba(27, 35, 99, 0.12);
      box-shadow: 0 12px 32px -12px rgba(27, 35, 99, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.6);
    }
    .dark .lp-hero-chip {
      background: rgba(13, 20, 48, 0.72);
      border: 1px solid rgba(255, 255, 255, 0.14);
      box-shadow: 0 12px 32px -10px rgba(0, 0, 0, 0.6), inset 0 1px 0 rgba(255, 255, 255, 0.08);
    }

    /* ── CTA button pulse ── */
    @keyframes lp-pulse-primary {
      0%, 100% { box-shadow: 0 0 0 0 rgba(36, 86, 166, 0.4); }
      50%       { box-shadow: 0 0 0 10px rgba(36, 86, 166, 0); }
    }
    .lp-cta-pulse { animation: lp-pulse-primary 3s ease-in-out infinite; }
    .lp-cta-pulse:hover { animation: none; }

    /* ── Diff card hover lift ── */
    .lp-diff-card {
      transition: transform 0.24s cubic-bezier(0.34, 1.56, 0.64, 1),
                  box-shadow 0.24s ease;
    }
    .lp-diff-card:hover {
      transform: translateY(-3px);
    }

    /* ── Illustration canvas — dotted gradient panel so illustrations sit on an
          intentional surface (Stripe/Vercel-style) instead of empty whitespace ── */
    .lp-illo-card {
      position: relative;
      background:
        radial-gradient(circle at 1px 1px, rgba(36,86,166,0.07) 1px, transparent 1.6px) 0 0 / 22px 22px,
        linear-gradient(155deg, var(--surface-2) 0%, var(--surface) 60%);
      box-shadow:
        inset 0 1px 0 rgba(255,255,255,0.5),
        0 1px 2px rgba(27,35,99,0.04),
        0 18px 40px -18px rgba(27,35,99,0.22);
      transition: transform 0.3s cubic-bezier(0.34,1.4,0.64,1), box-shadow 0.3s ease;
    }
    .lp-illo-card:hover {
      transform: translateY(-4px);
      box-shadow:
        inset 0 1px 0 rgba(255,255,255,0.5),
        0 24px 50px -16px rgba(27,35,99,0.28);
    }
    /* brand hairline at the top edge of the canvas */
    .lp-illo-card::before {
      content: '';
      position: absolute; left: 16px; right: 16px; top: 0; height: 2px;
      background: linear-gradient(90deg, transparent, rgba(23,179,163,0.5), rgba(36,86,166,0.5), transparent);
      border-radius: 2px;
    }

    /* ── Step connector line ── */
    .lp-connector {
      background: linear-gradient(90deg, #1b2363 0%, #2456a6 50%, #17b3a3 100%);
    }

    /* ── Compare table row striping ── */
    .lp-compare-row:nth-child(even) { background: rgba(36, 86, 166, 0.04); }
    .lp-compare-row:hover           { background: rgba(36, 86, 166, 0.08); }

    /* ── Smooth scroll for the whole page ── */
    html { scroll-behavior: smooth; }

    /* ── Compare table — mobile horizontal scroll ── */
    .lp-compare-table-wrap {
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
    }
    .lp-compare-table-inner {
      min-width: 620px;
    }

    /* ── Compare table — Nubi column highlight ── */
    .lp-nubi-col {
      background: linear-gradient(180deg,
        rgba(23,179,163,0.07) 0%,
        rgba(36,86,166,0.05) 100%);
      border-left: 1.5px solid rgba(23,179,163,0.25);
      border-right: 1.5px solid rgba(23,179,163,0.25);
    }
    .lp-nubi-col-header {
      background: linear-gradient(180deg,
        rgba(23,179,163,0.15) 0%,
        rgba(36,86,166,0.10) 100%);
      border-left: 1.5px solid rgba(23,179,163,0.35);
      border-right: 1.5px solid rgba(23,179,163,0.35);
      border-top: 2px solid #17b3a3;
    }

    /* ── How-it-works step card ── */
    .lp-step-card {
      transition: box-shadow 0.2s ease, transform 0.2s ease;
    }
    .lp-step-card:hover {
      transform: translateY(-2px);
    }

    /* ── Step connector arrow ── */
    .lp-step-arrow {
      color: #17b3a3;
      opacity: 0.5;
    }

    /* ── Chip badges ── */
    .lp-chip {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: 0.7rem;
      font-weight: 600;
      padding: 3px 9px;
      border-radius: 999px;
      border: 1px solid;
      line-height: 1.4;
      white-space: nowrap;
    }

    /* ── Cost-calculator range input ── */
    .lp-range {
      -webkit-appearance: none; appearance: none;
      height: 6px; border-radius: 999px; cursor: pointer;
      background: linear-gradient(90deg, #2456a6, #17b3a3);
    }
    .lp-range::-webkit-slider-thumb {
      -webkit-appearance: none; appearance: none;
      width: 20px; height: 20px; border-radius: 50%;
      background: #fff; border: 3px solid #17b3a3;
      box-shadow: 0 1px 4px rgba(27,35,99,0.25);
    }
    .lp-range::-moz-range-thumb {
      width: 20px; height: 20px; border-radius: 50%;
      background: #fff; border: 3px solid #17b3a3;
      box-shadow: 0 1px 4px rgba(27,35,99,0.25);
    }

    /* ── Code-highlighter token colors — explicit light/dark pairs so every
          token keeps readable contrast on the code surface in BOTH themes ── */
    .nubi-lp {
      --lp-hl-kw:    #2456a6; /* keyword (blue) */
      --lp-hl-fn:    #0f766e; /* function / tag / command (teal) */
      --lp-hl-str:   #9a5b16; /* string (amber) */
      --lp-hl-num:   #0f766e;
      --lp-hl-param: #6d3fd4; /* {{param}} (violet) */
      --lp-hl-punc:  #64748b;
      --lp-hl-cm:    #6b7a90; /* comment */
    }
    .dark .nubi-lp {
      --lp-hl-kw:    #7eaaf0;
      --lp-hl-fn:    #2dd4bf;
      --lp-hl-str:   #e8a35c;
      --lp-hl-num:   #2dd4bf;
      --lp-hl-param: #b39df5;
      --lp-hl-punc:  #93a3b8;
      --lp-hl-cm:    #7a8aa0;
    }
  `}</style>
)

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Sub-components (all use tokens)                                            */
/* ─────────────────────────────────────────────────────────────────────────── */

/* ── Product tour — tabbed REAL screenshots (regenerated by
      `npm run screenshots`, so they always match the shipping UI) ─────────── */
const TOUR_TABS = [
  {
    id: 'queries',
    label: 'Queries',
    icon: SearchCode,
    url: 'app.nubi.dev/queries',
    img: '/docs/screenshots/queries-editor.png',
    alt: 'The Nubi query workspace — SQL editor cells with a results grid streamed from the in-browser DuckDB kernel',
    title: 'Write SQL. Results stream back instantly.',
    body: 'The DuckDB-WASM kernel runs in the tab — no cold start, no per-session cloud cost. Named {{params}}, AI text-to-SQL grounded on your real schema, and results that arrive as Arrow IPC.',
    chips: ['DuckDB-WASM', 'Named params', 'AI text-to-SQL'],
  },
  {
    id: 'dashboards',
    label: 'Dashboards',
    icon: Layers,
    url: 'app.nubi.dev/editor',
    img: '/docs/screenshots/dashboard-editor.png',
    alt: 'The Nubi dashboard editor — KPIs, charts, and tables composed on a drag-and-drop grid',
    title: 'Compose it. Embed it anywhere.',
    body: 'Drag KPIs, charts, and tables onto a grid, then drop the <nubi-dashboard> web component into your app. Per-viewer row-level security travels in a signed JWT — and viewers are free on every plan.',
    chips: ['Drag & drop', 'Embed anywhere', 'Viewers free'],
  },
  {
    id: 'flows',
    label: 'Flows',
    icon: Workflow,
    url: 'app.nubi.dev/flows',
    img: '/docs/screenshots/flows-canvas.png',
    alt: 'The Nubi flows canvas — a SQL and Python pipeline drawn as a DAG with query, python, materialize, and export tasks',
    title: 'Orchestrate SQL + Python. Canvas, notebook, or code.',
    body: 'A built-in workflow orchestrator: wire query, Python, and export tasks into a DAG, schedule it, and watch runs live. The same flow is editable as a canvas, a notebook, or generated Python files.',
    chips: ['DAG canvas', 'Notebook cells', 'Files-as-code'],
  },
  {
    id: 'lakehouse',
    label: 'Lakehouse',
    icon: Warehouse,
    url: 'app.nubi.dev/data',
    img: '/docs/screenshots/data-explorer.png',
    alt: 'The Nubi data explorer browsing lakehouse datasets stored on object storage',
    title: 'A managed lakehouse, one click away.',
    body: 'Provision a per-org lakehouse on object storage and query it through DuckDB — Parquet in, dashboards out. Land flow outputs there, or bring your own bucket.',
    chips: ['Object storage', 'DuckDB over Parquet', 'Per-org isolation'],
  },
]

function ProductTour() {
  const [active, setActive] = useState(TOUR_TABS[0].id)
  const tab = TOUR_TABS.find(t => t.id === active)
  return (
    <div>
      {/* tab bar */}
      <div className="flex justify-center mb-7 sm:mb-9">
        <div className="inline-flex flex-wrap justify-center gap-1 p-1 rounded-2xl bg-surface-2 border border-border" role="tablist" aria-label="Product tour">
          {TOUR_TABS.map(t => {
            const ActiveIcon = t.icon
            const selected = t.id === active
            return (
              <button
                key={t.id}
                role="tab"
                aria-selected={selected}
                onClick={() => setActive(t.id)}
                className={[
                  'inline-flex items-center gap-2 px-4 sm:px-5 py-2.5 rounded-xl text-sm font-semibold transition-all',
                  selected
                    ? 'bg-brand-gradient text-white shadow-md'
                    : 'text-muted hover:text-fg',
                ].join(' ')}
              >
                <ActiveIcon size={15} strokeWidth={2.2} />
                {t.label}
              </button>
            )
          })}
        </div>
      </div>

      {/* framed screenshot */}
      <div className="relative max-w-5xl mx-auto">
        {/* glow bed */}
        <div
          className="pointer-events-none absolute -inset-6 sm:-inset-10 rounded-[2.5rem] blur-2xl opacity-50"
          style={{
            background:
              'radial-gradient(ellipse 65% 60% at 50% 45%, rgba(36,86,166,0.18) 0%, rgba(23,179,163,0.12) 55%, transparent 78%)',
          }}
          aria-hidden="true"
        />
        <div className="relative rounded-2xl overflow-hidden border border-border bg-surface shadow-[0_30px_70px_-28px_rgba(27,35,99,0.45)]">
          {/* browser chrome */}
          <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border bg-surface-2">
            <span className="flex gap-1.5" aria-hidden="true">
              <span className="w-2.5 h-2.5 rounded-full bg-[#f4726f]/70" />
              <span className="w-2.5 h-2.5 rounded-full bg-[#f5bd4f]/70" />
              <span className="w-2.5 h-2.5 rounded-full bg-[#61c554]/70" />
            </span>
            <span className="flex-1 max-w-xs mx-auto flex items-center justify-center gap-1.5 font-mono text-[10.5px] text-muted bg-bg border border-border rounded-md px-3 py-1">
              <Lock size={9} className="text-brand-teal" />
              {tab.url}
            </span>
            <span className="hidden sm:inline w-12" aria-hidden="true" />
          </div>
          {/* stacked images so switching never flashes a loading gap */}
          <div className="relative" style={{ aspectRatio: '1440 / 900' }}>
            {TOUR_TABS.map(t => (
              <div
                key={t.id}
                className={[
                  'absolute inset-0 transition-opacity duration-300',
                  t.id === active ? 'opacity-100' : 'opacity-0',
                ].join(' ')}
              >
                {/* light + dark captures from the screenshot pipeline; CSS picks
                    the one matching the site theme */}
                <img
                  src={t.img}
                  alt={t.alt}
                  width="2880"
                  height="1800"
                  loading="lazy"
                  className="absolute inset-0 w-full h-full object-cover object-top dark:hidden"
                />
                <img
                  src={t.img.replace('.png', '-dark.png')}
                  alt=""
                  aria-hidden="true"
                  width="2880"
                  height="1800"
                  loading="lazy"
                  className="hidden dark:block absolute inset-0 w-full h-full object-cover object-top"
                />
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* caption */}
      <div className="max-w-2xl mx-auto text-center mt-8 sm:mt-10">
        <h3 className="font-display text-xl sm:text-2xl font-bold text-fg">{tab.title}</h3>
        <p className="text-sm leading-relaxed text-muted mt-2.5">{tab.body}</p>
        <div className="flex flex-wrap justify-center gap-2 mt-4">
          {tab.chips.map(chip => (
            <span key={chip} className="font-mono text-[11px] font-medium px-2.5 py-1 rounded-full bg-brand-teal/10 text-brand-teal border border-brand-teal/20">
              {chip}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}

function CompareCell({ value, isNubi = false }) {
  if (value === true) {
    // Nubi's wins get a strong filled gradient check; competitors a quiet tint.
    return isNubi ? (
      <span
        className="inline-flex items-center justify-center w-7 h-7 rounded-full mx-auto shadow-sm"
        style={{ background: 'linear-gradient(135deg, #17b3a3, #2dd4bf)' }}
      >
        <Check size={15} strokeWidth={3.25} className="text-white" />
      </span>
    ) : (
      <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-accent/15 mx-auto">
        <Check size={13} strokeWidth={3} className="text-accent" />
      </span>
    )
  }
  if (value === false) {
    return (
      <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-border/50 mx-auto">
        <X size={12} strokeWidth={2.5} className="text-muted opacity-60" />
      </span>
    )
  }
  if (value === 'partial') {
    return (
      <span className="inline-flex items-center justify-center gap-1 px-2 h-6 rounded-full bg-amber-400/15 mx-auto">
        <Minus size={12} strokeWidth={3} className="text-amber-500" />
      </span>
    )
  }
  return (
    <span className={`text-[13px] leading-snug ${isNubi ? 'font-semibold text-fg' : 'text-muted'}`}>
      {value}
    </span>
  )
}

function Chip({ icon: Icon, children, accent = false }) {
  return (
    <span
      className={`lp-chip ${
        accent
          ? 'bg-accent/10 border-accent/30 text-brand-teal'
          : 'bg-surface-2 border-border text-muted'
      }`}
    >
      {Icon && <Icon size={10} strokeWidth={2.5} />}
      {children}
    </span>
  )
}

/** Mid-page CTA strip — repeats the primary "Start free" action after major sections. */
function SectionCta({ sub }) {
  return (
    <div className="flex flex-col items-center gap-4 text-center mt-12 sm:mt-16">
      {sub && <p className="text-sm sm:text-base text-muted max-w-md">{sub}</p>}
      <div className="flex flex-col sm:flex-row gap-3">
        <Link
          to="/register"
          className="inline-flex items-center justify-center gap-2 px-6 py-3 rounded-xl text-sm font-semibold transition-all bg-brand-gradient text-white hover:opacity-90 hover:-translate-y-0.5 min-h-[44px]"
        >
          Start free
          <ArrowRight size={14} strokeWidth={2.5} />
        </Link>
        <Link
          to="/pricing"
          className="inline-flex items-center justify-center gap-2 px-6 py-3 rounded-xl text-sm font-semibold transition-all bg-surface border border-border text-fg hover:border-brand-blue hover:text-primary min-h-[44px]"
        >
          See pricing
        </Link>
      </div>
    </div>
  )
}

/* ── Tiny dependency-free code highlighter (SQL / shell / html) ──────────────
   Single left-to-right scan with sticky regexes; the FIRST matching rule wins,
   so strings always beat keywords, comments beat punctuation, and tokens can
   never overlap or nest. Emits flat <span>s. Colors come from CSS variables
   with explicit light/dark values (see ScopedStyles) so tokens stay readable
   on the code surface in both themes. */
const HL = {
  kw:    'var(--lp-hl-kw)',    // keyword (blue)
  fn:    'var(--lp-hl-fn)',    // function / tag / leading command (teal)
  str:   'var(--lp-hl-str)',   // string (amber)
  num:   'var(--lp-hl-num)',
  param: 'var(--lp-hl-param)', // {{param}} / {expr} (violet)
  punc:  'var(--lp-hl-punc)',
  cm:    'var(--lp-hl-cm)',    // comment
  plain: 'currentColor',
}
const HL_RULES = {
  sql: [
    [/\s+/y, 'plain'],
    [/--[^\n]*/y, 'cm'],                 // -- line comment (before punc, so "--" never splits)
    [/'(?:[^']|'')*'/y, 'str'],          // SQL strings escape quotes by doubling ('')
    [/"(?:[^"\\]|\\.)*"/y, 'str'],
    [/\{\{[^}]*\}\}/y, 'param'],
    [/\b\d+(?:\.\d+)?\b/y, 'num'],
    [/\b(?:SELECT|FROM|WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|CROSS|ON|AS|AND|OR|NOT|IN|IS|NULL|LIKE|BETWEEN|DISTINCT|UNION|ALL|WITH|CASE|WHEN|THEN|ELSE|END|ASC|DESC|OVER|PARTITION)\b/iy, 'kw'],
    [/\b(?:SUM|COUNT|AVG|MIN|MAX|COALESCE|CAST|ROUND|DATE_TRUNC|NOW|EXTRACT|LOWER|UPPER|ABS|RANK|ROW_NUMBER)\b/iy, 'fn'],
    [/[a-zA-Z_][a-zA-Z0-9_]*/y, 'plain'], // identifiers AFTER keywords; \b in the kw rule
                                          // stops keyword prefixes inside identifiers
    [/[(),.*=<>+\-/|]/y, 'punc'],
  ],
  shell: [
    [/\s+/y, 'plain'],
    [/#[^\n]*/y, 'cm'],
    [/'(?:[^'\\]|\\.)*'/y, 'str'],
    [/"(?:[^"\\]|\\.)*"/y, 'str'],
    [/--?[a-zA-Z][\w-]*/y, 'kw'],        // flags before identifiers
    [/\.{1,2}\/[\w./-]*/y, 'plain'],     // ./relative and ../relative paths as one token
    [/[a-zA-Z_][\w.-]*/y, 'plain'],
    [/[=:/]/y, 'punc'],
  ],
  html: [
    [/\s+/y, 'plain'],
    [/<!--[\s\S]*?-->/y, 'cm'],
    [/"(?:[^"\\]|\\.)*"/y, 'str'],       // strings before {expr} so quoted braces stay strings
    [/\{[^}]*\}/y, 'param'],
    [/<\/?[a-zA-Z][\w-]*/y, 'kw'],
    [/\/?>/y, 'kw'],
    [/[a-zA-Z_][\w-]*(?==)/y, 'fn'],     // attribute names (incl. kebab-case like get-token)
    [/[a-zA-Z_][\w-]*/y, 'plain'],
    [/=/y, 'punc'],
  ],
}
function highlightCode(code, lang) {
  const rules = HL_RULES[lang]
  if (!rules) return code
  const out = []
  let i = 0, firstWord = true
  while (i < code.length) {
    let matched = false
    for (const [re, key] of rules) {
      re.lastIndex = i // sticky /y: anchors the match at i (shared regexes are reset every use)
      const m = re.exec(code)
      if (m) {
        let c = key
        // shell: color the leading command token
        if (lang === 'shell' && key === 'plain' && /\S/.test(m[0])) {
          if (firstWord) { c = 'fn'; firstWord = false }
        }
        out.push([m[0], c])
        i += m[0].length || 1
        matched = true
        break
      }
    }
    if (!matched) { out.push([code[i], 'plain']); i++ }
  }
  return out.map(([t, c], idx) => (
    <span key={idx} style={{ color: HL[c] || 'currentColor', fontWeight: c === 'kw' ? 600 : undefined }}>{t}</span>
  ))
}

function HowItWorksStep({ num, icon: Icon, title, color, tagline, bullets, code, lang, chips, Illo }) {
  return (
    <div className="lp-step-card flex flex-col bg-surface rounded-2xl border border-border overflow-hidden flex-1 min-w-0">
      {/* Illustration header — guides the eye through each stage */}
      {Illo && (
        <div className="px-6 pt-6 pb-2 bg-surface-2 border-b border-border">
          <Illo className="w-full h-auto max-h-28 mx-auto" />
        </div>
      )}
      {/* Card header strip */}
      <div
        className="px-6 pt-6 pb-5"
        style={{ borderBottom: '1px solid var(--border)' }}
      >
        <div className="flex items-center gap-3 mb-3">
          <span
            className="shrink-0 w-10 h-10 rounded-xl flex items-center justify-center text-white shadow"
            style={{ background: color }}
          >
            <Icon size={19} strokeWidth={2} />
          </span>
          <span className="font-mono text-xs font-bold tracking-widest text-muted uppercase">
            Step {num}
          </span>
        </div>
        <h3 className="font-display font-bold text-xl sm:text-2xl text-fg mb-1">{title}</h3>
        <p className="text-sm leading-relaxed text-muted">{tagline}</p>
      </div>

      {/* Bullets */}
      <div className="px-6 py-5 flex flex-col gap-3 flex-1">
        {bullets.map(({ icon: BIcon, text }) => (
          <div key={text} className="flex items-start gap-2.5">
            <span className="shrink-0 mt-0.5 w-5 h-5 rounded-md bg-surface-2 border border-border flex items-center justify-center">
              <BIcon size={11} strokeWidth={2.5} className="text-accent" />
            </span>
            <span className="text-xs leading-relaxed text-muted">{text}</span>
          </div>
        ))}

        {/* Chips */}
        {chips && (
          <div className="flex flex-wrap gap-1.5 mt-1">
            {chips.map((c) => (
              <Chip key={c.label} icon={c.icon} accent={c.accent}>
                {c.label}
              </Chip>
            ))}
          </div>
        )}
      </div>

      {/* Code snippet */}
      {code && (
        <div className="px-6 pb-6">
          <code className="block text-xs font-mono px-3 py-2.5 rounded-lg bg-surface-2 border border-border text-fg break-words leading-relaxed">
            {lang ? highlightCode(code, lang) : code}
          </code>
        </div>
      )}
    </div>
  )
}

/**
 * DiffRow — alternating illustration left/right layout.
 * On mobile/tablet: always stacks (illustration on top, copy below).
 * On desktop (lg+): alternates left/right based on `reverse` prop.
 */
function DiffRow({ icon: Icon, index, title, hook, desc, outcome, Illustration, reverse = false, badge, id }) {
  // Scroll reveal: each row fades/slides in once, the first time it enters the
  // viewport. Falls back to always-visible when IntersectionObserver is absent.
  const revealRef = useRef(null)
  // Visible from the start when IntersectionObserver is unavailable (SSR/old
  // browsers) — the reveal is purely progressive enhancement.
  const [seen, setSeen] = useState(() => typeof IntersectionObserver === 'undefined')
  useEffect(() => {
    const el = revealRef.current
    if (!el || typeof IntersectionObserver === 'undefined') return
    const obs = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setSeen(true)
          obs.disconnect()
        }
      },
      { threshold: 0.18 }
    )
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  const IllustrationBlock = (
    <div className="relative w-full min-h-[220px] sm:min-h-[280px] lg:min-h-[320px] flex items-center justify-center px-4 py-6 sm:px-8 sm:py-8">
      {index && (
        <span
          aria-hidden="true"
          className="pointer-events-none select-none absolute -top-1 right-5 font-display font-bold text-[5rem] sm:text-[6.5rem] leading-none text-transparent"
          style={{ WebkitTextStroke: '1.5px rgba(36,86,166,0.16)' }}
        >
          {index}
        </span>
      )}
      <Illustration className="w-full h-auto max-w-[480px]" />
    </div>
  )
  const CopyBlock = (
    <div className={`flex flex-col gap-4 sm:gap-5 ${reverse ? 'lg:pr-8' : 'lg:pl-8'}`}>
      <div className="flex items-center gap-2.5">
        {index && <span className="font-mono text-xs font-bold text-brand-teal">/{index}</span>}
        {badge && (
          <span className="inline-flex items-center gap-1.5 font-mono text-[10px] font-semibold px-2.5 py-1 rounded-full bg-surface-2 border border-border text-muted tracking-[0.14em] uppercase">
            {badge}
          </span>
        )}
      </div>
      <div className="flex items-center gap-3.5">
        <span className="shrink-0 inline-flex items-center justify-center w-11 h-11 rounded-xl bg-brand-gradient text-white shadow-[0_8px_20px_-6px_rgba(36,86,166,0.5)]">
          <Icon size={20} strokeWidth={1.75} />
        </span>
        <h3 className="font-display font-bold text-2xl sm:text-3xl text-fg leading-tight tracking-tight">{title}</h3>
      </div>
      <p className="text-sm sm:text-base lg:text-lg leading-relaxed text-muted">
        {hook && <strong className="text-fg font-semibold">{hook}{' '}</strong>}
        {desc}
      </p>
      {outcome && (
        <p className="flex items-start gap-2 font-mono text-[12px] sm:text-[13px] font-medium text-brand-teal border-l-2 border-brand-teal/50 pl-3 leading-relaxed">
          <ArrowRight size={13} strokeWidth={2.5} className="mt-0.5 shrink-0" />
          {outcome}
        </p>
      )}
    </div>
  )

  // Render each block ONCE and reorder with CSS on desktop. Rendering the
  // illustration twice (mobile + desktop copies) duplicates its gradient ids in
  // the DOM; Chrome won't build gradient paint-servers from the display:none
  // copy, so the visible copy's gradient fills vanish. Single-render avoids it.
  return (
    <div
      ref={revealRef}
      id={id}
      className={`lp-reveal ${seen ? 'lp-in' : ''} grid grid-cols-1 lg:grid-cols-2 gap-8 sm:gap-10 lg:gap-16 items-center ${id ? 'scroll-mt-20' : ''}`}
    >
      {/* Mobile: illustration always first. Desktop: side depends on `reverse`. */}
      <div className={`lp-illo-card order-1 rounded-2xl border border-border overflow-hidden ${reverse ? 'lg:order-2' : 'lg:order-1'}`}>
        {IllustrationBlock}
      </div>
      <div className={`order-2 ${reverse ? 'lg:order-1' : 'lg:order-2'}`}>
        {CopyBlock}
      </div>
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  §6 helpers — Pricing section (TierCards + CostCalculator)                  */
/*  Pricing helpers — live data via src/lib/pricing.js; static fallback from src/data/pricing.js */
/* ─────────────────────────────────────────────────────────────────────────── */

const fmtUSD = (n) => {
  if (!n) return '$0'
  if (n >= 1e6) return `$${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`
  if (n >= 1e3) return `$${Math.round(n / 1e3)}k`
  return `$${Math.round(n)}`
}
const fmtNum = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : `${n}`)

const METER_ICONS = [Users, Zap, Database, Bot, Server]

/** Landing-page tier card — matches PricingPage TierCard quality */
function LpTierCard({ tier }) {
  const hi = tier.highlight
  return (
    <div
      className={`relative flex flex-col rounded-2xl border p-5 transition-all duration-200
        ${hi
          ? 'border-brand-teal/70 bg-surface shadow-xl ring-1 ring-brand-teal/20 lg:-translate-y-2 z-10'
          : 'border-border bg-surface shadow-sm hover:-translate-y-1 hover:shadow-lg hover:border-brand-blue/40'}`}
    >
      {tier.badge && (
        <span
          className={`absolute -top-3 left-1/2 -translate-x-1/2 inline-flex items-center gap-1 px-3 py-1 rounded-full text-[10px] font-bold uppercase tracking-widest whitespace-nowrap shadow-sm
            ${hi
              ? 'bg-brand-gradient text-white'
              : 'bg-surface-2 border border-border text-brand-teal'}`}
        >
          {tier.id === 'enterprise'
            ? <Headset size={10} strokeWidth={2.5} />
            : <Star size={10} strokeWidth={2.5} />}
          {tier.badge}
        </span>
      )}
      <h3 className="font-display text-base font-bold text-fg">{tier.name}</h3>
      <div className="mt-1.5 flex items-end gap-1.5">
        <span className="font-display text-3xl font-bold tracking-tight text-fg">{tier.price}</span>
        <span className="text-xs text-muted mb-1">{tier.cadence}</span>
      </div>
      <p className="mt-2 text-[13px] text-muted leading-relaxed min-h-[52px]">{tier.tagline}</p>

      <Link
        to={tier.href}
        className={`mt-4 inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold transition-all min-h-[44px]
          ${hi
            ? 'bg-brand-gradient text-white hover:opacity-90 shadow-sm'
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
              className={`flex items-start gap-2 text-[13px]
                ${isHeader ? 'text-muted font-semibold pt-1' : 'text-fg'}`}
            >
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

/** Landing-page cost calculator — mirrors PricingPage CostCalculator */
function LpCostCalculator() {
  const [sv, setSv] = useState(50)
  const [editors, setEditors] = useState(5)
  const viewers = Math.round(10 * Math.pow(2500, sv / 100))

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
            <label htmlFor="lp-calc-viewers" className="text-sm font-semibold text-fg">
              Dashboard viewers
            </label>
            <span className="font-display text-xl font-bold text-primary">{fmtNum(viewers)}</span>
          </div>
          <input
            id="lp-calc-viewers" type="range" min="0" max="100" value={sv}
            onChange={e => setSv(Number(e.target.value))}
            className="lp-range w-full"
            aria-label="Dashboard viewers"
          />
          <div className="flex justify-between text-[11px] text-muted mt-1.5">
            <span>10</span><span>25k</span>
          </div>
        </div>
        <div>
          <div className="flex items-baseline justify-between mb-3">
            <label htmlFor="lp-calc-editors" className="text-sm font-semibold text-fg">
              Editors (creators)
            </label>
            <span className="font-display text-xl font-bold text-primary">{editors}</span>
          </div>
          <input
            id="lp-calc-editors" type="range" min="1" max="50" value={editors}
            onChange={e => setEditors(Number(e.target.value))}
            className="lp-range w-full"
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
          Nubi costs{' '}
          <strong className="text-brand-teal font-bold">{fmtUSD(nubi?.cost ?? 0)}/yr</strong>
          {savings > 0 && (
            <>
              {' '}— that&rsquo;s{' '}
              <strong className="text-brand-teal font-bold">{fmtUSD(savings)}/yr less</strong>
              {multiple && multiple >= 2 && <> ({Math.round(multiple)}&times; cheaper)</>}{' '}
              than the next option.
            </>
          )}
        </span>
      </div>

      {/* Bars */}
      <div className="p-6 sm:p-8 flex flex-col gap-3">
        {results.map(r => (
          <div
            key={r.name}
            className="grid grid-cols-[110px_1fr_auto] sm:grid-cols-[150px_1fr_auto] items-center gap-3"
          >
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
        Estimated annual cost from each vendor&rsquo;s public model (before your own warehouse compute).
        † Looker is quote-only; figure is directional. Verify before switching.
      </p>
    </div>
  )
}

/** Landing-page orchestration cost calculator — Flows vs standalone orchestrators */
function LpOrchCalculator() {
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
    <div className="rounded-2xl border border-border bg-surface shadow-sm overflow-hidden">
      {/* Inputs */}
      <div className="grid md:grid-cols-2 gap-6 p-6 sm:p-8 border-b border-border bg-surface-2">
        <div>
          <div className="flex items-baseline justify-between mb-3">
            <label htmlFor="lp-orch-envs" className="text-sm font-semibold text-fg">Environments</label>
            <span className="font-display text-xl font-bold text-primary">{envs}</span>
          </div>
          <input
            id="lp-orch-envs" type="range" min="1" max="5" value={envs}
            onChange={e => setEnvs(Number(e.target.value))}
            className="lp-range w-full" aria-label="Environments"
          />
          <div className="flex justify-between text-[11px] text-muted mt-1.5"><span>1</span><span>5</span></div>
        </div>
        <div>
          <div className="flex items-baseline justify-between mb-3">
            <label htmlFor="lp-orch-gb" className="text-sm font-semibold text-fg">Data processed (GB/mo)</label>
            <span className="font-display text-xl font-bold text-primary">{fmtNum(gb)}</span>
          </div>
          <input
            id="lp-orch-gb" type="range" min="0" max="10000" step="100" value={gb}
            onChange={e => setGb(Number(e.target.value))}
            className="lp-range w-full" aria-label="Data processed in GB per month"
          />
          <div className="flex justify-between text-[11px] text-muted mt-1.5"><span>0</span><span>10 TB</span></div>
        </div>
      </div>

      {/* Savings headline — honest: Flows has a real metered-compute cost. */}
      <div className="flex flex-wrap items-center justify-center gap-2 px-6 py-4 text-center bg-brand-teal/[0.06] border-b border-border">
        <TrendingDown size={18} className="text-brand-teal" />
        <span className="text-sm sm:text-base text-fg">
          Flows costs{' '}
          <strong className="text-brand-teal font-bold">{nubi?.cost ? `${fmtUSD(nubi.cost)}/yr` : '$0'}</strong>
          {' '}— metered on data processed, no per-environment bill.
          {savings > 0 && (
            <> Saving <strong className="text-brand-teal font-bold">{fmtUSD(savings)}/yr</strong> vs the cheapest standalone orchestrator.</>
          )}
        </span>
      </div>

      {/* Bars */}
      <div className="p-6 sm:p-8 flex flex-col gap-3">
        {results.map(r => (
          <div key={r.name} className="grid grid-cols-[120px_1fr_auto] sm:grid-cols-[180px_1fr_auto] items-center gap-3">
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
            <div className={`text-sm font-bold tabular-nums text-right w-20 ${r.isNubi ? 'text-brand-teal' : 'text-fg'}`}>
              {r.cost === 0 ? 'Included' : fmtUSD(r.cost)}
            </div>
          </div>
        ))}
      </div>
      <p className="px-6 sm:px-8 pb-6 text-xs text-muted opacity-70 leading-relaxed">
        Apples-to-apples: data volume → compute at ~50 GB / compute-hour, then each vendor priced as its
        published always-on floor (per environment / capacity / seats) + compute for the work. Directional
        estimates, not quotes. Managed orchestrators are floor-dominated; Nubi Flows has no floor.
        † Self-host Airflow is infra + on-call estimate.
      </p>
    </div>
  )
}

/** Landing-page warehouse cost calculator — hosted lakehouse vs standalone warehouses */
function LpWarehouseCalculator() {
  const [dataGb, setDataGb] = useState(100)
  const [queries, setQueries] = useState(5000)
  const [scanGb, setScanGb] = useState(2)

  const whUsage = { data_gb: dataGb, queries_per_month: queries, avg_gb_scanned: scanGb }
  const warehouseCu = estimateWarehouseCu(whUsage)
  const rec = recommendNubi(
    {
      storage_gb: dataGb, compute_units: warehouseCu, embedded_sessions: 0,
      agent_runs: 0, connectors: 1, flow_runs_per_month: 0,
    },
    null,
    { minTierId: 'pro' },
  )
  const nubiCost = Math.round(rec.tier.usd_monthly + rec.overage_zar / 16.26)

  const results = [
    {
      name: `Nubi ${rec.tier.name}`,
      note: `Full BI platform included · warehouse scans at ${WAREHOUSE_CU_MULTIPLIER}× CU`,
      isNubi: true,
      cost: nubiCost,
    },
    ...FALLBACK_COMPETITORS_WAREHOUSE.map(c => ({
      name: c.name,
      note: c.note,
      isNubi: false,
      estimate: true,
      cost: Math.round(c.model(whUsage)),
    })),
  ].sort((a, b) => a.cost - b.cost)
  const max = Math.max(...results.map(r => r.cost), 1)
  const outOfEnvelope = dataGb > 1000 || scanGb > 20

  return (
    <div className="rounded-2xl border border-border bg-surface shadow-sm overflow-hidden">
      {/* Inputs */}
      <div className="grid md:grid-cols-3 gap-6 p-6 sm:p-8 border-b border-border bg-surface-2">
        <div>
          <div className="flex items-baseline justify-between mb-3">
            <label htmlFor="lp-wh-data" className="text-sm font-semibold text-fg">Dataset (GB)</label>
            <span className="font-display text-xl font-bold text-primary">{fmtNum(dataGb)}</span>
          </div>
          <input
            id="lp-wh-data" type="range" min="10" max="2000" step="10" value={dataGb}
            onChange={e => setDataGb(Number(e.target.value))}
            className="lp-range w-full" aria-label="Dataset size in GB"
          />
          <div className="flex justify-between text-[11px] text-muted mt-1.5"><span>10 GB</span><span>2 TB</span></div>
        </div>
        <div>
          <div className="flex items-baseline justify-between mb-3">
            <label htmlFor="lp-wh-queries" className="text-sm font-semibold text-fg">Big queries / mo</label>
            <span className="font-display text-xl font-bold text-primary">{fmtNum(queries)}</span>
          </div>
          <input
            id="lp-wh-queries" type="range" min="100" max="50000" step="100" value={queries}
            onChange={e => setQueries(Number(e.target.value))}
            className="lp-range w-full" aria-label="Warehouse queries per month"
          />
          <div className="flex justify-between text-[11px] text-muted mt-1.5"><span>100</span><span>50k</span></div>
        </div>
        <div>
          <div className="flex items-baseline justify-between mb-3">
            <label htmlFor="lp-wh-scan" className="text-sm font-semibold text-fg">Scanned / query (GB)</label>
            <span className="font-display text-xl font-bold text-primary">{scanGb}</span>
          </div>
          <input
            id="lp-wh-scan" type="range" min="0.5" max="50" step="0.5" value={scanGb}
            onChange={e => setScanGb(Number(e.target.value))}
            className="lp-range w-full" aria-label="Average GB scanned per query"
          />
          <div className="flex justify-between text-[11px] text-muted mt-1.5"><span>0.5</span><span>50</span></div>
        </div>
      </div>

      {/* Headline — monthly (warehouse vendors quote monthly) */}
      <div className="flex flex-wrap items-center justify-center gap-2 px-6 py-4 text-center bg-brand-teal/[0.06] border-b border-border">
        <TrendingDown size={18} className="text-brand-teal" />
        <span className="text-sm sm:text-base text-fg">
          Hosted warehouse ≈ <strong className="text-brand-teal font-bold">{fmtUSD(nubiCost)}/mo</strong>
          {' '}— and that price includes the whole BI platform, not just the engine.
        </span>
      </div>

      {/* Honest out-of-envelope note */}
      {outOfEnvelope && (
        <div className="px-6 py-3 text-xs sm:text-sm bg-amber-50 dark:bg-amber-900/20 border-b border-amber-200 dark:border-amber-800 text-amber-800 dark:text-amber-200">
          <strong>Honest note:</strong> at this scale a dedicated warehouse is the better tool — Nubi
          runs each query on one machine. Connect your own BigQuery or ClickHouse as a datastore and
          Nubi pushes queries down to it.
        </div>
      )}

      {/* Bars */}
      <div className="p-6 sm:p-8 flex flex-col gap-3">
        {results.map(r => (
          <div key={r.name} className="grid grid-cols-[120px_1fr_auto] sm:grid-cols-[180px_1fr_auto] items-center gap-3">
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
        Monthly estimates. † Vendor figures assume well-tuned auto-idle / auto-suspend (defaults cost
        more) and include free tiers; they are warehouse-only — no dashboards, embedding, or flows —
        but genuinely outperform Nubi&rsquo;s single-machine pool on multi-TB scans. Directional, not
        quotes — verify with the vendor.
      </p>
    </div>
  )
}

/**
 * LpPricingSection — full pricing section for the landing page.
 *
 * Renders:
 *  1. Hero header + "pricing that doesn't tax viewers" copy
 *  2. Full tier cards grid — live data from GET /api/v1/pricing, falls back
 *     to TIERS from src/data/pricing.js if the endpoint is unavailable.
 *  3. What we charge / never charge cards (BILLING_MODEL)
 *  4. Interactive cost calculator (CALC_OPTIONS)
 *  5. CTA strip + micro-features
 *
 * Live data: fetchPricingData() from src/lib/pricing.js calls the public
 * GET /api/v1/pricing endpoint (no auth required) and returns FALLBACK_TIERS
 * on any error, so the section always renders. The live endpoint tiers are
 * mapped to the LpTierCard display shape (price label, cadence, tagline, cta,
 * href, features) using the TIERS array from src/data/pricing.js as the
 * display-metadata source — the live endpoint provides pricing signals
 * (usd_monthly_price, monthly_price_zar) that override the static prices.
 */
function LpPricingSection() {
  const [liveTiers, setLiveTiers] = useState(TIERS)

  useEffect(() => {
    fetchPricingData().then(data => {
      if (!Array.isArray(data?.tiers) || data.tiers.length === 0) return
      // Merge live USD prices into the static display tiers.
      // The API returns backend-shaped objects; we overlay price/cadence only
      // and keep all other display fields (tagline, features, cta, href) from
      // the static TIERS so the landing page copy stays under editorial control.
      const merged = TIERS.map(staticTier => {
        const live = data.tiers.find(t => t.tier === staticTier.id)
        if (!live) return staticTier
        const usd = parseFloat(live.usd_monthly_price ?? 0)
        const price = usd === 0 ? '$0' : '$' + usd.toLocaleString('en-US')
        return { ...staticTier, price }
      })
      setLiveTiers(merged)
    })
  }, [])

  return (
    <section id="pricing" className="scroll-mt-14 bg-bg">
      {/* ── 6a: Header ── */}
      <div className="relative overflow-hidden border-y border-border bg-surface-2 py-16 sm:py-20">
        <div className="absolute top-0 left-0 right-0 h-1 bg-brand-gradient" />
        <svg className="absolute inset-0 w-full h-full opacity-[0.03] pointer-events-none" aria-hidden="true">
          <defs>
            <pattern id="lp-pricing-dots" x="0" y="0" width="28" height="28" patternUnits="userSpaceOnUse">
              <circle cx="1" cy="1" r="1" fill="currentColor" />
            </pattern>
          </defs>
          <rect width="100%" height="100%" fill="url(#lp-pricing-dots)" className="text-primary" />
        </svg>
        <div className="relative max-w-3xl mx-auto px-4 sm:px-6 text-center">
          <p className="font-mono text-[11px] font-semibold tracking-[0.18em] uppercase mb-4 text-brand-teal">Pricing</p>
          <h2 className="font-display text-3xl sm:text-5xl lg:text-6xl font-bold leading-tight mb-5 text-fg">
            Pricing that doesn&rsquo;t{' '}
            <span className="text-brand-gradient">tax your viewers.</span>
          </h2>
          <p className="text-sm sm:text-base lg:text-lg leading-relaxed text-muted">
            Dashboards compute in your users&rsquo; browsers — an extra viewer costs us ≈ $0, and
            we never charge for one. Pay for{' '}
            <strong className="text-fg font-medium">storage</strong>,{' '}
            <strong className="text-fg font-medium">compute</strong>, and{' '}
            <strong className="text-fg font-medium">AI</strong>. Not for people looking at charts.
          </p>
        </div>
      </div>

      {/* ── 6b: Tier cards ── */}
      <div className="py-14 sm:py-20 bg-bg">
        <div className="max-w-[88rem] mx-auto px-4 sm:px-6 lg:px-8">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-5 xl:gap-4 items-start pt-3">
            {liveTiers.map(t => <LpTierCard key={t.id} tier={t} />)}
          </div>
          <p className="mt-8 text-center text-sm text-muted">
            {ENTERPRISE_NOTE}{' '}
            <Link
              to="/register"
              className="text-brand-teal font-medium hover:underline inline-flex items-center gap-1"
            >
              Contact us <ChevronRight size={13} />
            </Link>
          </p>
        </div>
      </div>

      {/* ── 6b-ii: Usage wallet / overage strip ── */}
      <div className="pb-14 sm:pb-20 bg-bg">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="rounded-3xl border border-brand-teal/30 bg-surface shadow-sm overflow-hidden">
            <div className="grid lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
              {/* Pitch */}
              <div className="p-6 sm:p-8 bg-gradient-to-br from-brand-navy/[0.04] via-brand-blue/[0.04] to-brand-teal/[0.07] border-b lg:border-b-0 lg:border-r border-border flex flex-col justify-center">
                <span className="inline-flex items-center gap-2 self-start text-[11px] font-semibold uppercase tracking-widest text-brand-teal mb-3">
                  <span className="w-8 h-8 rounded-xl bg-brand-gradient text-white flex items-center justify-center">
                    <Wallet size={15} strokeWidth={2} />
                  </span>
                  Buy more when you need it
                </span>
                <h3 className="font-display text-xl sm:text-2xl font-bold text-fg mb-2">
                  A usage wallet — pay only for what you use
                </h3>
                <p className="text-sm text-muted leading-relaxed">{OVERAGE_NOTE}</p>
              </div>
              {/* Rates */}
              <div className="grid grid-cols-1 sm:grid-cols-2 divide-y divide-border sm:divide-y-0 sm:[&>*:nth-child(n+3)]:border-t sm:[&>*:nth-child(2n)]:border-l border-border">
                {OVERAGE_RATES.map((o, i) => {
                  const Icon = METER_ICONS[(i + 2) % METER_ICONS.length]
                  return (
                    <div key={o.label} className="flex items-start gap-3 px-5 py-4 border-border">
                      <span className="shrink-0 mt-0.5 w-8 h-8 rounded-lg bg-surface-2 border border-border flex items-center justify-center text-primary">
                        <Icon size={14} strokeWidth={2} />
                      </span>
                      <div className="min-w-0">
                        <div className="flex items-baseline gap-1">
                          <span className="font-display text-base font-bold text-fg tabular-nums">{o.rate}</span>
                          <span className="text-[11px] text-muted">{o.unit}</span>
                        </div>
                        <p className="text-[12px] font-semibold text-fg leading-tight">{o.label}</p>
                      </div>
                    </div>
                  )
                })}
                <div className="flex items-center px-5 py-4 border-t border-border bg-surface-2/40">
                  <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-brand-teal">
                    <Check size={12} strokeWidth={3} /> Same rate, every paid tier — never per-seat
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ── 6c: What we charge / never charge ── */}
      <div className="pb-14 sm:pb-20 bg-bg">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
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
            <div className="rounded-2xl border border-brand-teal/30 bg-surface p-6 sm:p-7">
              <h3 className="flex items-center gap-2 font-display font-bold text-lg text-fg mb-4">
                <XCircle size={18} className="text-muted" /> What we never charge for
              </h3>
              <ul className="flex flex-col gap-3">
                {BILLING_MODEL.neverBilled.map(m => (
                  <li key={m} className="flex items-start gap-3">
                    <span className="shrink-0 mt-0.5 w-7 h-7 rounded-lg bg-brand-teal/10 flex items-center justify-center">
                      <X size={14} strokeWidth={2.5} className="text-brand-teal" />
                    </span>
                    <span className="text-sm text-fg leading-snug">{m}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-5 text-xs text-muted leading-relaxed border-t border-border pt-4">
                Competitors meter the viewer — per-seat or per-query. That&rsquo;s the cost we designed away.
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* ── 6d: Cost calculator ── */}
      <div className="pb-14 sm:pb-20 bg-bg">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-8">
            <p className="font-mono text-[11px] font-semibold tracking-[0.18em] uppercase mb-4 text-brand-teal inline-flex items-center gap-1.5">
              <SlidersHorizontal size={12} /> Estimate your cost
            </p>
            <h3 className="font-display text-3xl sm:text-4xl font-bold text-fg mb-3">
              What would you pay?
            </h3>
            <p className="text-sm sm:text-base text-muted max-w-2xl mx-auto">
              Drag the sliders to your scale and watch the gap.
              Everyone else bills the viewer — we don&rsquo;t.
            </p>
          </div>

          {/* Calculator 1 — BI viewer tax */}
          <p className="text-[11px] font-semibold tracking-widest uppercase text-muted mb-2">
            Calculator 1 · BI viewer cost
          </p>
          <LpCostCalculator />

          {/* Calculator 2 — orchestration */}
          <div className="mt-10">
            <p className="text-[11px] font-semibold tracking-widest uppercase text-muted mb-2">
              Calculator 2 · Orchestration cost
            </p>
            <p className="text-sm text-muted max-w-2xl mb-4">
              Flows is built in. A standalone orchestrator (Prefect, Microsoft Fabric, MWAA,
              self-host Airflow) is pure added cost — and most bill per environment.
            </p>
            <LpOrchCalculator />
          </div>

          {/* Calculator 3 — warehouse */}
          <div className="mt-10">
            <p className="text-[11px] font-semibold tracking-widest uppercase text-muted mb-2">
              Calculator 3 · Warehouse cost
            </p>
            <p className="text-sm text-muted max-w-2xl mb-4">
              The lakehouse (Pro+) runs big-table queries on dedicated machines, billed as ordinary
              compute units at {WAREHOUSE_CU_MULTIPLIER}× — no per-TB scan fees, no always-on cluster.
              Compare against running a standalone warehouse for the same workload.
            </p>
            <LpWarehouseCalculator />
          </div>
        </div>
      </div>

      {/* ── 6e: CTA + micro-features ── */}
      <div className="relative overflow-hidden py-16 sm:py-24 bg-surface-2 border-t border-border">
        <div className="absolute top-0 left-0 right-0 h-1 bg-brand-gradient" />
        <div className="max-w-3xl mx-auto px-4 sm:px-6 text-center">
          <h3 className="font-display text-3xl sm:text-5xl font-bold leading-tight mb-4 text-fg">
            Start free.<br />
            <span className="text-brand-gradient">Scale without the viewer tax.</span>
          </h3>
          <p className="text-sm sm:text-base text-muted mb-8 max-w-lg mx-auto">
            Unlimited dashboard views on every plan, including Free. Upgrade for seats, embed
            volume, governance, and dedicated support.
          </p>
          <div className="flex flex-col sm:flex-row gap-3 sm:gap-4 justify-center mb-8">
            <Link
              to="/register"
              className="lp-cta-pulse inline-flex items-center justify-center gap-2 px-6 sm:px-8 py-4 rounded-xl text-base font-semibold transition-all bg-brand-gradient text-white hover:opacity-90 hover:-translate-y-0.5 min-h-[52px]"
            >
              Start free
              <ArrowRight size={16} strokeWidth={2.5} />
            </Link>
            <Link
              to="/pricing"
              className="inline-flex items-center justify-center gap-2 px-6 sm:px-8 py-4 rounded-xl text-base font-semibold transition-all bg-surface border border-border text-fg hover:border-brand-blue hover:text-primary min-h-[52px]"
            >
              See full pricing →
            </Link>
          </div>
          <div className="flex flex-wrap justify-center gap-x-6 sm:gap-x-8 gap-y-2 text-xs font-medium text-muted">
            {[
              'No credit card required',
              'Free tier — no gotchas',
              'Self-host connector option',
              'Connector SDK included',
            ].map(f => (
              <span key={f} className="flex items-center gap-1.5">
                <Check size={10} strokeWidth={3} className="text-accent" />
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
/*  Page                                                                       */
/* ─────────────────────────────────────────────────────────────────────────── */

export default function LandingPage() {
  return (
    <>
      <ScopedStyles />

      <div className="nubi-lp overflow-x-hidden bg-bg text-fg font-sans">

        {/* ════════════════════════════════════════════════════════════════════
            §1  HERO — dark observatory panel: copy | real product frame,
            with the proof stats fused into the panel's lower band
        ════════════════════════════════════════════════════════════════════ */}
        <section id="hero" className="relative scroll-mt-14 bg-bg px-3 sm:px-5 pt-3 sm:pt-5">
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

            {/* perspective data-grid floor */}
            <svg
              className="lp-hero-grid pointer-events-none absolute inset-x-0 bottom-0 h-[55%] w-full"
              preserveAspectRatio="none"
              viewBox="0 0 1200 400"
              aria-hidden="true"
            >
              <defs>
                <linearGradient id="lp-gridfade" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0" stopColor="#8db4f5" stopOpacity="0" />
                  <stop offset="1" stopColor="#8db4f5" stopOpacity="0.8" />
                </linearGradient>
              </defs>
              {Array.from({ length: 13 }, (_, i) => (
                <line key={`v${i}`} x1={600 + (i - 6) * 100} y1="0" x2={600 + (i - 6) * 260} y2="400" stroke="url(#lp-gridfade)" strokeWidth="1" />
              ))}
              {Array.from({ length: 7 }, (_, i) => (
                <line key={`h${i}`} x1="0" y1={60 + i * 56 + i * i * 2} x2="1200" y2={60 + i * 56 + i * i * 2} stroke="url(#lp-gridfade)" strokeWidth="1" />
              ))}
            </svg>

            {/* film grain */}
            <div className="lp-noise pointer-events-none absolute inset-0" aria-hidden="true" />

            <div className="relative px-5 sm:px-10 lg:px-14 pt-12 sm:pt-16 lg:pt-20">
              <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.18fr] gap-12 lg:gap-14 items-center">

                {/* ── Left: copy ── */}
                <div>
                  {/* terminal-flavoured eyebrow */}
                  <p className="inline-flex items-center gap-2 font-mono text-[11px] sm:text-xs font-medium tracking-wide text-brand-teal dark:text-teal-300/90 border border-border dark:border-white/10 bg-white/60 dark:bg-white/[0.04] rounded-full px-3.5 py-1.5 mb-6 sm:mb-8">
                    <span className="relative flex h-1.5 w-1.5">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-teal-400 opacity-60" />
                      <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-teal-300" />
                    </span>
                    open source · apache-2.0 · real free tier
                  </p>

                  <h1 className="font-display text-4xl sm:text-5xl lg:text-[3.9rem] xl:text-[4.4rem] font-bold leading-[1.04] tracking-tight mb-5 sm:mb-7 text-fg">
                    BI that runs in
                    <br />
                    <span className="lp-hero-gradient-text">your browser.</span>
                    <br />
                    Viewers are free.
                  </h1>

                  <p className="text-base sm:text-lg leading-relaxed mb-8 sm:mb-9 max-w-lg text-muted dark:text-slate-300/90">
                    A DuckDB-WASM kernel runs{' '}
                    <strong className="text-fg font-semibold">inside the tab</strong> — zero
                    cold starts, and an extra viewer costs{' '}
                    <strong className="text-fg font-semibold">≈ $0</strong>. So every plan has{' '}
                    <strong className="text-fg font-semibold">unlimited seats</strong>. Flows
                    orchestration built in. Embed it in your SaaS for a fraction of per-seat BI.
                  </p>

                  {/* CTAs */}
                  <div className="flex flex-col sm:flex-row flex-wrap gap-3 mb-8 sm:mb-10">
                    <Link
                      to="/register"
                      className="lp-cta-glow inline-flex items-center justify-center gap-2 px-7 py-3.5 rounded-xl text-base font-semibold transition-all bg-brand-gradient text-white hover:-translate-y-0.5 min-h-[48px]"
                    >
                      Start free
                      <ArrowRight size={16} strokeWidth={2.5} />
                    </Link>
                    <Link
                      to="/docs"
                      className="inline-flex items-center justify-center gap-2 px-6 py-3.5 rounded-xl text-base font-semibold transition-all bg-surface border border-border text-fg hover:border-brand-blue dark:bg-white/[0.06] dark:border-white/15 dark:text-white dark:hover:bg-white/[0.12] dark:hover:border-white/25 min-h-[48px]"
                    >
                      View docs
                    </Link>
                    <Link
                      to="/compare"
                      className="inline-flex items-center justify-center gap-1.5 px-4 py-3.5 rounded-xl text-sm font-medium transition-all text-muted hover:text-fg min-h-[48px]"
                    >
                      Compare vs Hex &amp; Cube <ChevronRight size={13} />
                    </Link>
                  </div>

                  {/* trust strip — mono, data-tool flavour */}
                  <div className="flex flex-wrap gap-x-5 gap-y-2 font-mono text-[11px] font-medium text-muted">
                    {[
                      'unlimited seats & viewers',
                      'no credit card',
                      'apache-2.0 open core',
                      'arrow ipc → echarts',
                    ].map(f => (
                      <span key={f} className="flex items-center gap-1.5">
                        <Check size={11} strokeWidth={2.5} className="text-teal-400" />
                        {f}
                      </span>
                    ))}
                  </div>
                </div>

                {/* ── Right: the real product, in a glass browser frame ── */}
                <div className="relative mt-4 lg:mt-0 lg:-mr-2">
                  {/* glow bed under the frame */}
                  <div
                    className="pointer-events-none absolute -inset-8 rounded-[2.5rem] blur-2xl opacity-60"
                    style={{
                      background:
                        'radial-gradient(ellipse 70% 60% at 50% 55%, rgba(45,140,220,0.35) 0%, rgba(45,212,191,0.18) 50%, transparent 75%)',
                    }}
                    aria-hidden="true"
                  />

                  <div className="lp-float-1 relative rounded-2xl overflow-hidden border border-border dark:border-white/[0.13] bg-surface dark:bg-[#0c1230]/80 shadow-[0_30px_70px_-26px_rgba(27,35,99,0.4)] dark:shadow-[0_40px_80px_-24px_rgba(0,0,0,0.7)]">
                    {/* browser chrome */}
                    <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border dark:border-white/[0.08] bg-surface-2 dark:bg-white/[0.03]">
                      <span className="flex gap-1.5" aria-hidden="true">
                        <span className="w-2.5 h-2.5 rounded-full bg-[#f4726f]/80" />
                        <span className="w-2.5 h-2.5 rounded-full bg-[#f5bd4f]/80" />
                        <span className="w-2.5 h-2.5 rounded-full bg-[#61c554]/80" />
                      </span>
                      <span className="flex-1 max-w-xs mx-auto flex items-center justify-center gap-1.5 font-mono text-[10.5px] text-muted bg-bg dark:bg-white/[0.05] border border-border dark:border-white/[0.07] rounded-md px-3 py-1">
                        <Lock size={9} className="text-teal-400/80" />
                        app.nubi.dev/d/retail-sales
                      </span>
                      <span className="hidden sm:inline-flex font-mono text-[9.5px] text-brand-teal dark:text-teal-300/80 border border-brand-teal/25 dark:border-teal-400/20 bg-brand-teal/[0.07] dark:bg-teal-400/[0.07] rounded px-1.5 py-0.5">
                        arrow ipc
                      </span>
                    </div>
                    <img
                      src="/landing/hero-light.png"
                      alt="A live Nubi dashboard — retail sales KPIs, trend line, and category breakdowns rendered in the browser"
                      width="2400"
                      height="1500"
                      fetchPriority="high"
                      className="block w-full h-auto dark:hidden"
                    />
                    <img
                      src="/landing/hero-dark.png"
                      alt=""
                      aria-hidden="true"
                      width="2400"
                      height="1500"
                      loading="lazy"
                      className="hidden w-full h-auto dark:block"
                    />
                  </div>

                  {/* floating stat chips */}
                  <div className="lp-float-2 lp-hero-chip absolute -left-3 sm:-left-6 top-20 hidden md:flex items-center gap-2.5 rounded-xl px-3.5 py-2.5">
                    <Zap size={15} className="text-teal-300" />
                    <span className="font-mono text-[11px] leading-tight text-fg dark:text-white">
                      0 s cold start
                      <span className="block text-[9.5px] text-muted">kernel lives in the tab</span>
                    </span>
                  </div>
                  <div className="lp-float-3 lp-hero-chip absolute -right-2 sm:-right-5 -bottom-5 hidden md:flex items-center gap-2.5 rounded-xl px-3.5 py-2.5">
                    <Users size={15} className="text-sky-300" />
                    <span className="font-mono text-[11px] leading-tight text-fg dark:text-white">
                      ≈ $0 / dashboard view
                      <span className="block text-[9.5px] text-muted">so viewers are never billed</span>
                    </span>
                  </div>
                </div>
              </div>

              {/* ── Proof stats — fused into the panel ── */}
              <div className="relative mt-12 sm:mt-16 lg:mt-20 border-t border-border dark:border-white/10 py-8 sm:py-10">
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-y-8 divide-x divide-border dark:divide-white/[0.07]">
                  {[
                    { v: '≈ $0', l: 'marginal cost per dashboard view' },
                    { v: '∞', l: 'users & viewers — no per-seat pricing' },
                    { v: '10–50×', l: 'cost reduction vs naive warehouse use¹' },
                    { v: '0 s', l: 'cold start — kernel runs in the tab' },
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
                <p className="text-center font-mono text-[10px] mt-7 text-muted opacity-70">
                  ¹ real at high cache-hit / pre-aggregation rates — 500 viewers of one dashboard collapse to 1 warehouse hit
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            §2.5  PRODUCT SHOWCASE — what you actually work in
            id="product" — query workspace + dashboard builder, side by side
        ════════════════════════════════════════════════════════════════════ */}
        <section id="product" className="py-14 sm:py-20 lg:py-24 bg-bg scroll-mt-14">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="text-center mb-12 sm:mb-16">
              <p className="font-mono text-[11px] font-semibold tracking-[0.18em] uppercase mb-4 text-brand-teal">
                See it in action
              </p>
              <h2 className="font-display text-3xl sm:text-4xl lg:text-5xl font-bold text-fg">
                One workspace, from{' '}
                <span className="text-primary">SQL</span> to{' '}
                <span className="text-brand-teal">embedded dashboard</span>.
              </h2>
              <p className="text-sm sm:text-base leading-relaxed mt-4 text-muted max-w-2xl mx-auto">
                Write a query, see results{' '}
                <strong className="text-fg font-semibold">the instant you hit Run</strong>, then
                drag the charts into a dashboard your customers can open —{' '}
                <strong className="text-fg font-semibold">no separate tools, no cold-start
                kernel, no per-viewer bill.</strong>
              </p>
            </div>

            <ProductTour />
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            §3  DIFFERENTIATORS — alternating left/right, LARGE illustrations
            id="features" — scroll target for footer "Dashboards" link
        ════════════════════════════════════════════════════════════════════ */}
        <section id="features" className="py-14 sm:py-20 lg:py-24 bg-bg scroll-mt-14">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">

            {/* Section header */}
            <div className="text-center mb-12 sm:mb-16 lg:mb-20 max-w-2xl mx-auto">
              <p className="font-mono text-[11px] font-semibold tracking-[0.18em] uppercase mb-4 text-brand-teal">
                why nubi · one bet, compounded
              </p>
              <h2 className="font-display text-3xl sm:text-4xl lg:text-[3.4rem] font-bold leading-[1.08] tracking-tight mb-4 sm:mb-6 text-fg">
                Eight decisions that make{' '}
                <span className="text-brand-gradient">everything cheaper.</span>
              </h2>
              <p className="text-sm sm:text-base lg:text-lg leading-relaxed text-muted">
                Most BI rents you compute — by the seat, by the session, by the query.
                Nubi makes one structural bet instead:{' '}
                <strong className="text-fg font-semibold">ship the kernel to the browser</strong>{' '}
                and fall through to a server only when you must. Every decision below
                compounds that bet into{' '}
                <strong className="text-fg font-semibold">≈ $0 per dashboard view.</strong>
              </p>
            </div>

            {/* Alternating rows */}
            <div className="flex flex-col gap-12 sm:gap-16 lg:gap-20">
              <DiffRow
                id="kernel"
                index="01"
                icon={Zap}
                title="Kernel in the browser"
                badge="Core architecture"
                hook="The kernel ships to the user's tab."
                desc="A DuckDB-WASM engine does the analytics where your viewers already are — zero cold starts, zero per-session cloud cost. Python falls through to a metered, scale-to-zero server kernel: the escape hatch, never the default."
                outcome="marginal cost per dashboard view ≈ $0"
                Illustration={KernelInBrowser}
                reverse={false}
              />

              <DiffRow
                index="02"
                icon={Globe}
                title="Fast charts on Arrow buffers"
                badge="Rendering"
                hook="Charts read columns, not JSON."
                desc="Results stream as columnar Arrow IPC straight into Apache ECharts on canvas — no serialisation round-trip, no DOM-bound SVG. Cross-filter a six-figure result set and it just moves."
                outcome="fluid charts at 100k+ rows"
                Illustration={WebGLPerf}
                reverse={true}
              />

              <DiffRow
                id="cache"
                index="03"
                icon={Database}
                title="Edge cache + auto pre-agg"
                badge="Cost architecture"
                hook="500 viewers. One warehouse hit."
                desc="A content-hashed edge cache keyed on (plan + JWT claims) collapses identical dashboard traffic, while a rollup suggester mines hot GROUP BY shapes from your query log — materialize the winners in one click, no hand-written cubes."
                outcome="10–50× fewer warehouse scans"
                Illustration={EdgeCache}
                reverse={false}
              />

              <DiffRow
                id="embedding"
                index="04"
                icon={Shield}
                title="Auth-as-code embedding"
                badge="Security"
                hook="Row-level security lives in your repo."
                desc="One JWT primitive powers users, groups, and embeds. RLS policies are claims in a token your backend signs — and predicates are injected into the SQL AST, never string-concatenated. Auth logic stays in code review, not a vendor UI."
                outcome="<nubi-dashboard get-token> — that's the whole integration"
                Illustration={EmbedAuthCode}
                reverse={true}
              />

              <DiffRow
                index="05"
                icon={Bot}
                title="LLM-authorable dashboards"
                badge="AI-native"
                hook="Agents speak fluent dashboard."
                desc="A dashboard is sanitized HTML with declarative <nubi-*> elements — a format LLMs author natively, grounded on your real schema. Six MCP tools let agents query, build, and iterate end-to-end."
                outcome="author_dashboard · run_query · 4 more MCP tools"
                Illustration={LlmDashboardCode}
                reverse={false}
              />

              <DiffRow
                id="connectors"
                index="06"
                icon={Code2}
                title="SQL-first connector SDK"
                badge="Extensibility"
                hook="Point at a warehouse and go."
                desc="No semantic model to build first. 25+ connectors out of the box, plus a Python SDK that wraps any Arrow-returning function as a first-class source — behind a capability gate that enforces the security floor."
                outcome="predicate_rls=False → 501 — sources fail closed"
                Illustration={ConnectorSdkCode}
                reverse={true}
              />

              <DiffRow
                id="warehouse"
                index="07"
                icon={Warehouse}
                title="A lakehouse you don't operate"
                badge="Pro & Enterprise"
                hook="Big-table analytics without the warehouse tax."
                desc="Datasets live as open Parquet in object storage, queried by DuckDB on dedicated machines billed as ordinary compute at 4× — no per-TB scan fees, no cluster to babysit. Outgrow it and nothing migrates: connect BigQuery or ClickHouse and your dashboards, RLS, caching, and rollups stay exactly where they are."
                outcome="open formats — leaving is a connection string"
                Illustration={LakehouseFlow}
                reverse={false}
              />

              <DiffRow
                id="flows"
                index="08"
                icon={Workflow}
                title="Flows: orchestration included"
                badge="Workflows"
                hook="The orchestrator bill, deleted."
                desc="SQL and Python cells wired into a DAG you edit as a canvas, a notebook, or files. Schedules, retries, timeouts, caching, fan-out, and conditional gates — running on Postgres alone. No Redis, no Celery, no separate Airflow to feed."
                outcome="one platform, zero extra orchestrator"
                Illustration={FlowCode}
                reverse={true}
              />
            </div>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            §3.25  EVERYTHING-AS-CODE — power-user / files / CLI / CI story
            id="as-code" — in-app code view + local files + CLI + CI deploy
        ════════════════════════════════════════════════════════════════════ */}
        <section id="as-code" className="py-14 sm:py-20 lg:py-24 bg-surface-2 border-y border-border scroll-mt-14">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">

            {/* Section header */}
            <div className="text-center mb-12 sm:mb-16 lg:mb-20 max-w-2xl mx-auto">
              <p className="font-mono text-[11px] font-semibold tracking-[0.18em] uppercase mb-4 text-brand-teal">
                Everything-as-code
              </p>
              <h2 className="font-display text-3xl sm:text-4xl lg:text-5xl font-bold leading-tight mb-4 sm:mb-5 text-fg">
                Click to build. Then{' '}
                <span className="text-brand-gradient">own it as code.</span>
              </h2>
              <p className="text-sm sm:text-base leading-relaxed text-muted">
                Every dashboard, query, flow, and connector is{' '}
                <strong className="text-fg font-semibold">a file you can edit, review, and version</strong> —
                in a VS Code-style editor right in the app, or pulled to your own git repo and
                shipped with the <code className="font-mono text-xs px-1 py-0.5 rounded bg-surface border border-border text-fg">nubi</code> CLI.
                No lock-in, no copy-paste between a vendor UI and your stack.
              </p>
            </div>

            {/* Alternating rows */}
            <div className="flex flex-col gap-12 sm:gap-16 lg:gap-20">
              <DiffRow
                id="code-view"
                icon={Code2}
                title="A code view for everything"
                badge="In-app editor"
                desc="Flip any dashboard, query, or flow into a VS Code-style file view — the same resource, edited as files instead of forms. A persistent git/versions rail lets you switch refs, diff, and roll back without leaving the page. Power users get a text editor; everyone else keeps the visual builder. Same source, two views."
                Illustration={LlmDashboardCode}
                reverse={false}
              />

              <DiffRow
                id="local-files"
                icon={FolderGit2}
                title="Pull your project to git"
                badge="Local files-as-code"
                desc="nubi pull writes your whole project as a normal git repo: dashboards, queries (raw .sql + metadata), flows (one folder per flow, one file per cell), and non-secret connector manifests. Commit it, branch it, PR it. Secrets stay in gitignored local .env files — non-secret config is committed, credentials never are."
                Illustration={FilesAsCodeCli}
                reverse={true}
              />

              <DiffRow
                id="ci-deploy"
                icon={GitBranch}
                title="Ship local → cloud on push"
                badge="CLI & CI/CD"
                desc="One CLI to pull, push, sync, and deploy. nubi secrets push seals your secrets into GitHub Actions or GitLab CI; the scaffolded pipeline materializes them and runs nubi deploy on every push to main — manifests and secrets land in the right environment. Edit locally, open a PR, merge, deployed."
                Illustration={FlowCode}
                reverse={false}
              />
            </div>

            <SectionCta sub="Browse the on-disk project format, the full CLI command tree, and the CI templates in the docs." />
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            §3.5  CONNECTORS — brand logo wall
            id="sources" — "connect to your whole stack"
        ════════════════════════════════════════════════════════════════════ */}
        <section id="sources" className="py-14 sm:py-20 lg:py-24 bg-bg scroll-mt-14">
          <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="text-center mb-10 sm:mb-14 max-w-2xl mx-auto">
              <p className="font-mono text-[11px] font-semibold tracking-[0.18em] uppercase mb-4 text-brand-teal">
                Connectors
              </p>
              <h2 className="font-display text-3xl sm:text-4xl lg:text-5xl font-bold leading-tight mb-4 sm:mb-5 text-fg">
                Connect to your{' '}
                <span className="text-brand-gradient">whole stack.</span>
              </h2>
              <p className="text-sm sm:text-base leading-relaxed text-muted">
                Point Nubi at the warehouses, databases, and lakes you already run —{' '}
                <strong className="text-fg font-semibold">no proprietary semantic model to
                start.</strong> Relational, cloud-managed, warehouse, query-engine, and
                object-storage sources are first-class, all enforcing{' '}
                <strong className="text-fg font-semibold">the same security floor.</strong>
              </p>
            </div>

            <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-7 gap-3 sm:gap-4">
              {CONNECTOR_TYPES.map((info) => (
                <div
                  key={info.id}
                  title={info.description}
                  className="group flex flex-col items-center gap-2.5 rounded-2xl border border-border bg-surface p-3 sm:p-4 transition-all duration-200 hover:-translate-y-1 hover:shadow-md hover:border-border/80"
                >
                  <span
                    className="inline-flex items-center justify-center w-11 h-11 rounded-xl shrink-0"
                    style={{ background: `${info.color}14` }}
                  >
                    <img
                      src={info.logo}
                      alt={info.label}
                      className="w-6 h-6 object-contain transition-transform duration-200 group-hover:scale-110"
                      loading="lazy"
                    />
                  </span>
                  <span className="text-[11px] font-medium text-muted text-center leading-tight">
                    {info.label}
                  </span>
                </div>
              ))}
            </div>

            <p className="text-center text-xs sm:text-sm text-muted mt-8 sm:mt-10 max-w-xl mx-auto">
              Don&rsquo;t see yours? The{' '}
              <span className="text-fg font-medium">Python connector SDK</span> wraps any
              Arrow-returning function as a first-class source — and JDBC covers the long tail.
            </p>

            <SectionCta sub="Connect a database and ship your first dashboard on the free tier — no credit card required." />
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            §4  HOW IT WORKS — 3-step
            id="how-it-works" — scroll target for footer link
        ════════════════════════════════════════════════════════════════════ */}
        <section id="how-it-works" className="py-14 sm:py-20 lg:py-24 bg-surface-2 scroll-mt-14">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="text-center mb-12 sm:mb-16">
              <p className="font-mono text-[11px] font-semibold tracking-[0.18em] uppercase mb-4 text-brand-teal">
                How it works
              </p>
              <h2 className="font-display text-3xl sm:text-4xl lg:text-5xl font-bold text-fg">
                Connect <span className="text-muted opacity-40 font-normal">→</span> Query <span className="text-muted opacity-40 font-normal">→</span> Embed
              </h2>
              <p className="text-sm sm:text-base leading-relaxed mt-4 text-muted max-w-2xl mx-auto">
                Three stages from zero to a live, cross-filtering, multi-tenant dashboard.
                No proprietary semantic model. No cold-start cloud kernel. No per-view compute cost.
              </p>
            </div>

            {/* Three step cards with connector arrows */}
            <div className="flex flex-col lg:flex-row items-stretch gap-4 lg:gap-3 xl:gap-5">

              <HowItWorksStep
                num={1}
                icon={PlugZap}
                Illo={ConnectorSdk}
                color="linear-gradient(135deg, #1b2363, #2456a6)"
                title="Connect"
                tagline="Bring your warehouse. Secrets stay in your network."
                bullets={[
                  { icon: Database, text: 'BigQuery, Snowflake, Redshift, Postgres, ClickHouse — point and go, no semantic model required.' },
                  { icon: Lock, text: 'Warehouse credentials never leave the connector. Self-host in your VPC or use Nubi-managed regional connectors near your data.' },
                  { icon: Code2, text: 'Python connector SDK: any Arrow-returning function is a first-class source. Wrap a proprietary store, a dataframe job, or a REST feed.' },
                  { icon: Shield, text: 'Capability contract enforces a security floor — connectors without predicate-RLS support are refused (501), not silently trusted.' },
                ]}
                chips={[
                  { label: 'BigQuery', accent: false },
                  { label: 'Snowflake', accent: false },
                  { label: 'Postgres', accent: false },
                  { label: 'Python SDK', accent: true },
                  { label: 'Private VPC bridge', accent: true },
                ]}
                code="nubi deploy ./resources --dry-run"
                lang="shell"
              />

              {/* Arrow connector */}
              <div className="flex lg:flex-col items-center justify-center shrink-0 py-1 lg:py-0 px-0 lg:px-1">
                {/* desktop: cards in a row → arrow points right */}
                <div className="hidden lg:flex items-center gap-2">
                  <div className="h-px w-8 bg-gradient-to-r from-transparent via-border to-transparent" />
                  <ArrowRightCircle size={22} strokeWidth={1.5} className="lp-step-arrow" />
                  <div className="h-px w-8 bg-gradient-to-r from-transparent via-border to-transparent" />
                </div>
                {/* mobile: cards stacked → arrow points down */}
                <div className="lg:hidden flex flex-col items-center gap-2">
                  <div className="w-px h-8 bg-gradient-to-b from-transparent via-border to-transparent" />
                  <ArrowRightCircle size={22} strokeWidth={1.5} className="lp-step-arrow rotate-90" />
                  <div className="w-px h-8 bg-gradient-to-b from-transparent via-border to-transparent" />
                </div>
              </div>

              <HowItWorksStep
                num={2}
                icon={SearchCode}
                Illo={QueryWorkspace}
                color="linear-gradient(135deg, #2456a6, #17b3a3)"
                title="Query"
                tagline="SQL, named params, and AI text-to-SQL — all in the browser."
                bullets={[
                  { icon: Database, text: 'DuckDB-WASM kernel runs in the user\'s tab — zero cold starts, zero per-session cloud cost. Results stream as Arrow IPC.' },
                  { icon: Sparkles, text: 'AI text-to-SQL grounded on your actual catalog and lineage graph — not hallucinated schemas. Six MCP tools for agent authoring.' },
                  { icon: SearchCode, text: 'Named-parameter registered queries keep your SQL versioned and reusable. The query planner pushes predicates and projections to the warehouse.' },
                  { icon: Globe, text: 'Content-hashed edge cache: 500 viewers of the same dashboard collapse to 1 warehouse hit. Auto pre-aggregation mines query logs to build rollups automatically.' },
                ]}
                chips={[
                  { label: 'DuckDB-WASM', accent: true },
                  { label: 'Arrow IPC', accent: true },
                  { label: 'AI text-to-SQL', accent: false },
                  { label: 'Named params', accent: false },
                  { label: 'Edge cache', accent: false },
                ]}
                code="SELECT month, SUM(revenue) FROM events WHERE tenant_id = {{tenant_id}} GROUP BY 1"
                lang="sql"
              />

              {/* Arrow connector */}
              <div className="flex lg:flex-col items-center justify-center shrink-0 py-1 lg:py-0 px-0 lg:px-1">
                {/* desktop: cards in a row → arrow points right */}
                <div className="hidden lg:flex items-center gap-2">
                  <div className="h-px w-8 bg-gradient-to-r from-transparent via-border to-transparent" />
                  <ArrowRightCircle size={22} strokeWidth={1.5} className="lp-step-arrow" />
                  <div className="h-px w-8 bg-gradient-to-r from-transparent via-border to-transparent" />
                </div>
                {/* mobile: cards stacked → arrow points down */}
                <div className="lg:hidden flex flex-col items-center gap-2">
                  <div className="w-px h-8 bg-gradient-to-b from-transparent via-border to-transparent" />
                  <ArrowRightCircle size={22} strokeWidth={1.5} className="lp-step-arrow rotate-90" />
                  <div className="w-px h-8 bg-gradient-to-b from-transparent via-border to-transparent" />
                </div>
              </div>

              <HowItWorksStep
                num={3}
                icon={Layers}
                Illo={EmbedAuth}
                color="linear-gradient(135deg, #17b3a3, #2dd4bf)"
                title="Embed"
                tagline="One JWT primitive. Per-viewer RLS. Cross-filtering dashboards."
                bullets={[
                  { icon: KeyRound, text: 'Signed JWT carries per-viewer claims. Predicate injection is AST-based — never string concat. Policies live as code in your repo, PR-reviewable.' },
                  { icon: Filter, text: 'Token-locked params prevent viewers from escaping their data scope. Column masking and row-level security enforced server-side before any data leaves the connector.' },
                  { icon: Globe, text: 'Cross-filtering dashboards rendered with ECharts on Arrow buffers — canvas rendering that stays smooth on large result sets.' },
                  { icon: Code2, text: 'Drop the <nubi-dashboard> web component into your host app — UMD or ES module. Theme attribute, short-lived JWTs that refresh before expiry.' },
                ]}
                chips={[
                  { label: 'JWT RLS', accent: true },
                  { label: 'AST predicate inject', accent: true },
                  { label: 'Token-locked params', accent: false },
                  { label: 'Cross-filter dashboards', accent: false },
                  { label: 'Web component', accent: false },
                ]}
                code={'<nubi-dashboard query="SELECT * FROM sales" get-token="getEmbedToken">'}
                lang="html"
              />
            </div>

            {/* Architecture note */}
            <div className="mt-10 sm:mt-12 mx-auto max-w-3xl rounded-2xl p-5 sm:p-6 text-sm leading-relaxed text-center bg-surface border border-border">
              <strong className="text-primary font-semibold">One language, one engine, one wire format.</strong>
              {' '}Python everywhere (FastAPI + connector planner + flows executor). DuckDB everywhere (WASM in the browser, embedded in the connector).
              Arrow IPC at every boundary — results stream from connector to browser with no serialization tax.
              <span className="text-muted"> sqlglot rewrites SQL between dialects on the server.</span>
            </div>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            §5  POSITIONING vs Hex / Cube
            id="compare" — scroll target, also has a full /compare page
        ════════════════════════════════════════════════════════════════════ */}
        <section id="compare" className="py-14 sm:py-20 lg:py-24 bg-bg scroll-mt-14">
          <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="text-center mb-10 sm:mb-14">
              <p className="font-mono text-[11px] font-semibold tracking-[0.18em] uppercase mb-4 text-brand-teal">
                Honest comparison
              </p>
              <h2 className="font-display text-3xl sm:text-4xl lg:text-5xl font-bold mb-4 text-fg">
                Nubi vs the field
              </h2>
              <p className="text-sm sm:text-base text-muted max-w-xl mx-auto mb-4">
                Hex is great. Cube is great. But both run compute in their cloud —
                and that single decision shapes everything about their pricing and architecture.
              </p>
              <Link
                to="/compare"
                className="inline-flex items-center gap-1 text-sm font-medium text-brand-teal hover:underline"
              >
                Full comparison page <ChevronRight size={13} />
              </Link>
            </div>

            {/* Horizontally scrollable on mobile */}
            <div className="lp-compare-table-wrap rounded-2xl border border-border overflow-hidden shadow-sm">
              <div className="lp-compare-table-inner">

                {/* Column headers */}
                <div className="grid grid-cols-[1.6fr_1fr_1fr_1.15fr] text-xs font-semibold">
                  {/* Dimension label */}
                  <div className="py-4 px-5 bg-surface-2 border-b border-border">
                    <span className="text-muted uppercase tracking-widest">Dimension</span>
                  </div>
                  {/* Hex */}
                  <div className="py-4 px-4 bg-surface-2 border-b border-l border-border text-center">
                    <span className="text-muted tracking-wide">Hex</span>
                    <p className="text-muted opacity-50 font-normal normal-case tracking-normal mt-0.5 text-[10px]">Notebook + apps</p>
                  </div>
                  {/* Cube */}
                  <div className="py-4 px-4 bg-surface-2 border-b border-l border-border text-center">
                    <span className="text-muted tracking-wide">Cube</span>
                    <p className="text-muted opacity-50 font-normal normal-case tracking-normal mt-0.5 text-[10px]">Semantic layer</p>
                  </div>
                  {/* Nubi — highlighted */}
                  <div className="lp-nubi-col-header py-4 px-4 border-b text-center">
                    <span className="text-brand-teal tracking-wide">Nubi</span>
                    <p className="text-brand-teal opacity-60 font-normal normal-case tracking-normal mt-0.5 text-[10px]">BI + embed</p>
                  </div>
                </div>

                {/* Rows */}
                {[
                  {
                    category: 'Architecture',
                    dim: 'Compute kernel',
                    hex: 'Python/session, their cloud (10–30s cold)',
                    cube: 'n/a — warehouse + Cube Store',
                    nubi: 'DuckDB-WASM in the browser; on-demand server kernel only when needed',
                  },
                  {
                    dim: 'Wire format',
                    hex: 'JSON via pandas',
                    cube: 'JSON / SQL API',
                    nubi: 'Arrow IPC streamed to the browser',
                  },
                  {
                    dim: 'Cold-start',
                    hex: '10–30 s per session',
                    cube: 'n/a',
                    nubi: '0 s — kernel is in the tab',
                  },
                  {
                    category: 'Data & Caching',
                    dim: 'Edge caching',
                    hex: 'Per-session, weak cross-user',
                    cube: 'Pre-aggs in Cube Store',
                    nubi: 'Content-hashed edge cache + auto pre-aggs',
                  },
                  {
                    dim: 'Modeling tax',
                    hex: 'Medium',
                    cube: 'High — define cubes first',
                    nubi: 'Low — point at a warehouse and go',
                  },
                  {
                    dim: 'Pre-aggregation rollups',
                    hex: false,
                    cube: 'partial',
                    nubi: 'Auto-suggested from query log · one-click build',
                  },
                  {
                    category: 'Visualization',
                    dim: 'Rendering engine',
                    hex: 'Plotly / SVG, chokes past ~50k rows',
                    cube: 'Bring your own',
                    nubi: 'ECharts on Arrow buffers · canvas, fast on large result sets',
                  },
                  {
                    dim: 'Cross-filter at scale',
                    hex: false,
                    cube: false,
                    nubi: true,
                    isBool: true,
                  },
                  {
                    category: 'Auth & Embedding',
                    dim: 'Embedding',
                    hex: 'Separate product, bolt-on auth',
                    cube: 'Core strength — headless only',
                    nubi: 'Core surface; dashboards + cell widgets embeddable',
                  },
                  {
                    dim: 'Auth-as-code RLS',
                    hex: false,
                    cube: 'partial',
                    nubi: true,
                    isBool: true,
                  },
                  {
                    category: 'AI & Pricing',
                    dim: 'LLM / MCP authoring',
                    hex: false,
                    cube: false,
                    nubi: 'MCP server · 6 tools · LLM-authorable HTML dashboards',
                  },
                  {
                    dim: 'Real free tier',
                    hex: false,
                    cube: false,
                    nubi: true,
                    isBool: true,
                  },
                ].map(({ category, dim, hex, cube, nubi }, i, arr) => {
                  const isLastInBlock = i === arr.length - 1 || arr[i + 1]?.category
                  return (
                    <div key={dim}>
                      {/* Category separator row */}
                      {category && (
                        <div className="grid grid-cols-[1.6fr_1fr_1fr_1.15fr] bg-surface-2 border-t border-border">
                          <div className="py-2 px-5 col-span-1 flex items-center gap-2">
                            <span className="w-1.5 h-1.5 rounded-full bg-brand-teal" />
                            <span className="text-[11px] font-bold uppercase tracking-widest text-primary">{category}</span>
                          </div>
                          <div className="border-l border-border" />
                          <div className="border-l border-border" />
                          <div className="lp-nubi-col" />
                        </div>
                      )}
                      {/* Data row */}
                      <div className={`lp-compare-row grid grid-cols-[1.6fr_1fr_1fr_1.15fr] ${!isLastInBlock ? 'border-b border-border' : ''}`}>
                        <div className="py-4 px-5 flex items-center">
                          <span className="text-[13px] font-semibold text-fg">{dim}</span>
                        </div>
                        <div className="py-3.5 px-4 border-l border-border flex items-center justify-center">
                          <CompareCell value={hex} />
                        </div>
                        <div className="py-3.5 px-4 border-l border-border flex items-center justify-center">
                          <CompareCell value={cube} />
                        </div>
                        <div className="lp-nubi-col py-3.5 px-4 flex items-center justify-center">
                          <CompareCell value={nubi} isNubi />
                        </div>
                      </div>
                    </div>
                  )
                })}

              </div>
            </div>

            <p className="text-center text-xs mt-5 text-muted opacity-50">
              Data sourced from public documentation and the Nubi roadmap. We&rsquo;re honest: check primary sources before switching.
            </p>

            <SectionCta sub="Try the architecture instead of reading about it — the free tier is the real product." />
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            §6  FULL PRICING SECTION
            id="pricing" — scroll target for footer "Pricing" link
            Renders live tier cards (GET /api/v1/pricing, falls back to
            src/data/pricing.js) + cost calculator. No EE imports.
        ════════════════════════════════════════════════════════════════════ */}
        <LpPricingSection />

        {/* ════════════════════════════════════════════════════════════════════
            §7  ABOUT — minimal "about" anchor for footer link
        ════════════════════════════════════════════════════════════════════ */}
        <section id="about" className="py-14 sm:py-16 bg-bg scroll-mt-14">
          <div className="max-w-3xl mx-auto px-4 sm:px-6 text-center">
            <p className="font-mono text-[11px] font-semibold tracking-[0.18em] uppercase mb-4 text-brand-teal">About Nubi</p>
            <h2 className="font-display text-2xl sm:text-3xl lg:text-4xl font-bold mb-4 sm:mb-5 text-fg">
              Built on one structural bet.
            </h2>
            <p className="text-sm sm:text-base leading-relaxed text-muted mb-6">
              The analytics kernel runs in the user&rsquo;s browser by default. That single decision
              makes the marginal cost of a dashboard view{' '}
              <strong className="text-fg font-semibold">≈ $0 — not a marketing claim, a
              consequence of where compute runs.</strong> Hex runs a Python kernel per session in their
              cloud. Cube runs the data plane in their cloud. Nubi pushes compute to the browser
              and only falls through to a metered server kernel for the workloads that need it.
            </p>
            <p className="text-sm text-muted opacity-70 mb-8">
              Apache-2.0 open core &middot; Real free tier &middot; Self-hostable connectors
            </p>
            <Link
              to="/register"
              className="inline-flex items-center justify-center gap-2 px-6 py-3.5 rounded-xl text-base font-semibold transition-all bg-brand-gradient text-white hover:opacity-90 hover:-translate-y-0.5 min-h-[48px]"
            >
              Start free
              <ArrowRight size={16} strokeWidth={2.5} />
            </Link>
          </div>
        </section>

      </div>
    </>
  )
}
