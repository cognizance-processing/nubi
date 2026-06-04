/**
 * LandingPage — Nubi redesign using real design tokens.
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
 * Sections
 * ─────────
 * 1. Hero — two-column: copy left, large HeroIllustration right
 * 2. Stats / proof band — bg-brand-gradient
 * 3. Differentiators — alternating left/right, BIG illustrations
 * 4. How it works — 3-step
 * 5. vs Hex / Cube comparison table
 * 6. Closing CTA
 */

import { Link } from 'react-router-dom'
import {
  ArrowRight,
  Zap,
  Shield,
  Database,
  Code2,
  Globe,
  Bot,
  ChevronRight,
  Check,
  X,
} from 'lucide-react'
import HeroIllustration from '../components/illustrations/HeroIllustration.jsx'
import KernelInBrowser from '../components/illustrations/KernelInBrowser.jsx'
import EdgeCache from '../components/illustrations/EdgeCache.jsx'
import EmbedAuth from '../components/illustrations/EmbedAuth.jsx'
import LlmDashboards from '../components/illustrations/LlmDashboards.jsx'
import ConnectorSdk from '../components/illustrations/ConnectorSdk.jsx'
import WebGLPerf from '../components/illustrations/WebGLPerf.jsx'

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Scoped animations — only on .nubi-lp so they don't bleed to other pages   */
/* ─────────────────────────────────────────────────────────────────────────── */
const ScopedStyles = () => (
  <style>{`
    /* ── Hero illustration gentle float ── */
    @keyframes lp-float {
      0%, 100% { transform: translateY(0px); }
      50%       { transform: translateY(-8px); }
    }
    .lp-hero-illo { animation: lp-float 7s ease-in-out infinite; }

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

    /* ── Step connector line ── */
    .lp-connector {
      background: linear-gradient(90deg, #1b2363 0%, #2456a6 50%, #17b3a3 100%);
    }

    /* ── Compare table row striping ── */
    .lp-compare-row:nth-child(even) { background: rgba(36, 86, 166, 0.04); }
    .lp-compare-row:hover           { background: rgba(36, 86, 166, 0.08); }
  `}</style>
)

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Sub-components (all use tokens)                                            */
/* ─────────────────────────────────────────────────────────────────────────── */

function EyebrowBadge({ children }) {
  return (
    <div className="inline-flex items-center gap-2 text-xs font-semibold tracking-widest uppercase px-3 py-1.5 rounded-full mb-6 bg-surface-2 border border-border text-muted">
      <span className="w-1.5 h-1.5 rounded-full bg-accent inline-block" />
      {children}
    </div>
  )
}

function StatBadge({ value, label, accent = 'text-brand-teal' }) {
  return (
    <div className="flex flex-col items-center px-6 py-5 text-white">
      <span className={`font-display text-4xl sm:text-5xl font-bold leading-none mb-1.5 ${accent}`}>
        {value}
      </span>
      <span className="text-xs sm:text-sm font-medium tracking-wide uppercase text-white/60 text-center max-w-[10rem]">
        {label}
      </span>
    </div>
  )
}

function CompareCheck({ yes }) {
  return yes ? (
    <Check size={14} strokeWidth={2.5} className="mx-auto text-accent" />
  ) : (
    <X size={14} strokeWidth={2.5} className="mx-auto text-muted opacity-40" />
  )
}

function Step({ num, title, desc, code }) {
  return (
    <div className="flex flex-col items-center text-center max-w-sm">
      <div className="w-14 h-14 rounded-2xl flex items-center justify-center text-2xl font-bold font-display mb-5 bg-brand-gradient text-white shadow-lg">
        {num}
      </div>
      <h3 className="font-display font-semibold text-xl mb-3 text-fg">{title}</h3>
      <p className="text-sm leading-relaxed mb-4 text-muted">{desc}</p>
      {code && (
        <code className="text-xs px-3 py-2 rounded-lg font-mono bg-surface-2 border border-border text-brand-teal">
          {code}
        </code>
      )}
    </div>
  )
}

/**
 * DiffRow — alternating illustration left/right layout for large visibility.
 * odd = illustration on left, copy on right
 * even = copy on left, illustration on right
 */
function DiffRow({ icon: Icon, title, desc, Illustration, reverse = false, badge }) {
  const IllustrationBlock = (
    <div className="w-full min-h-[320px] lg:min-h-[380px] flex items-center">
      <Illustration className="w-full h-auto" />
    </div>
  )
  const CopyBlock = (
    <div className={`flex flex-col gap-5 ${reverse ? 'lg:pr-8' : 'lg:pl-8'}`}>
      {badge && (
        <span className="inline-flex items-center self-start gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-full bg-surface-2 border border-border text-muted tracking-widest uppercase">
          {badge}
        </span>
      )}
      <div className="flex items-center gap-3">
        <span className="shrink-0 inline-flex items-center justify-center w-10 h-10 rounded-xl bg-surface-2 border border-border text-accent">
          <Icon size={20} strokeWidth={1.75} />
        </span>
        <h3 className="font-display font-bold text-2xl lg:text-3xl text-fg leading-tight">{title}</h3>
      </div>
      <p className="text-base lg:text-lg leading-relaxed text-muted">{desc}</p>
    </div>
  )

  return (
    <div className={`grid lg:grid-cols-2 gap-10 lg:gap-16 items-center ${reverse ? 'direction-rtl' : ''}`}>
      {reverse ? (
        <>
          <div>{CopyBlock}</div>
          <div className="bg-surface rounded-2xl border border-border overflow-hidden p-2">
            {IllustrationBlock}
          </div>
        </>
      ) : (
        <>
          <div className="bg-surface rounded-2xl border border-border overflow-hidden p-2">
            {IllustrationBlock}
          </div>
          <div>{CopyBlock}</div>
        </>
      )}
    </div>
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
            §1  HERO — two-column: copy | large illustration
        ════════════════════════════════════════════════════════════════════ */}
        <section className="relative min-h-[92vh] flex items-center bg-bg">
          {/* Subtle brand gradient wash behind illustration */}
          <div
            className="pointer-events-none absolute inset-0"
            style={{
              background:
                'radial-gradient(ellipse 55% 60% at 75% 50%, rgba(36,86,166,0.07) 0%, transparent 70%), ' +
                'radial-gradient(ellipse 35% 40% at 20% 70%, rgba(23,179,163,0.05) 0%, transparent 60%)',
            }}
          />

          <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-20 sm:py-28 w-full">
            <div className="grid lg:grid-cols-[1fr_1.25fr] gap-12 lg:gap-20 items-center">

              {/* ── Left: copy ── */}
              <div>
                <EyebrowBadge>Open beta · real free tier</EyebrowBadge>

                <h1 className="font-display text-5xl sm:text-6xl lg:text-[4.25rem] xl:text-7xl font-bold leading-[1.04] tracking-tight mb-6 text-fg">
                  BI that runs{' '}
                  <span className="text-brand-gradient">
                    in your browser.
                  </span>
                  <br />
                  <span className="text-brand-teal">Near-zero</span>{' '}
                  cost per view.
                </h1>

                <p className="text-lg sm:text-xl leading-relaxed mb-10 max-w-lg text-muted">
                  Pyodide + DuckDB run inside the user&rsquo;s tab —
                  no per-session cloud kernel, no cold starts.
                  Embed a cross-filtering, million-point dashboard inside
                  your SaaS for a{' '}
                  <strong className="text-fg font-semibold">fraction of what Hex or Cube charge.</strong>
                </p>

                {/* CTAs */}
                <div className="flex flex-col sm:flex-row gap-3 mb-10">
                  <Link
                    to="/register"
                    className="lp-cta-pulse inline-flex items-center justify-center gap-2 px-7 py-3.5 rounded-xl text-base font-semibold transition-all bg-brand-gradient text-white hover:opacity-90 hover:-translate-y-0.5"
                  >
                    Get started free
                    <ArrowRight size={16} strokeWidth={2.5} />
                  </Link>
                  <Link
                    to="/docs"
                    className="inline-flex items-center justify-center gap-2 px-7 py-3.5 rounded-xl text-base font-semibold transition-all bg-surface-2 border border-border text-fg hover:border-brand-blue hover:text-brand-blue"
                  >
                    View docs
                  </Link>
                  <Link
                    to="/compare"
                    className="inline-flex items-center justify-center gap-1.5 px-5 py-3.5 rounded-xl text-sm font-medium transition-all text-muted hover:text-fg"
                  >
                    Compare vs Hex &amp; Cube <ChevronRight size={13} />
                  </Link>
                </div>

                {/* Trust strip */}
                <div className="flex flex-wrap gap-5 text-xs font-medium text-muted">
                  {[
                    'Arrow IPC wire format',
                    'WebGL · 1M+ points',
                    'SQL-first connector SDK',
                    'Auth-as-code embed',
                  ].map(f => (
                    <span key={f} className="flex items-center gap-1.5">
                      <Check size={11} strokeWidth={2.5} className="text-accent" />
                      {f}
                    </span>
                  ))}
                </div>
              </div>

              {/* ── Right: LARGE hero illustration ── */}
              <div className="lp-hero-illo relative">
                {/* Glow halo behind illustration */}
                <div
                  className="absolute inset-0 -m-6 rounded-3xl pointer-events-none"
                  style={{
                    background:
                      'radial-gradient(ellipse 80% 70% at 50% 50%, rgba(36,86,166,0.1) 0%, transparent 70%)',
                  }}
                />
                <div className="relative bg-surface rounded-2xl border border-border overflow-hidden p-1 shadow-2xl">
                  <HeroIllustration className="w-full h-auto" style={{ minHeight: 480 }} />
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            §2  PROOF BAND — key metrics
        ════════════════════════════════════════════════════════════════════ */}
        <section className="relative py-16 sm:py-20 bg-brand-gradient overflow-hidden">
          {/* Subtle pattern */}
          <svg className="absolute inset-0 w-full h-full opacity-5 pointer-events-none" aria-hidden="true">
            <defs>
              <pattern id="lp-dots" x="0" y="0" width="28" height="28" patternUnits="userSpaceOnUse">
                <circle cx="1" cy="1" r="1" fill="white" />
              </pattern>
            </defs>
            <rect width="100%" height="100%" fill="url(#lp-dots)" />
          </svg>

          <div className="relative max-w-6xl mx-auto px-4 sm:px-6 lg:px-8">
            <p className="text-center text-xs font-semibold tracking-widest uppercase mb-10 text-white/60">
              The structural advantage
            </p>

            <div className="grid grid-cols-2 sm:grid-cols-4 divide-x divide-y sm:divide-y-0 divide-white/10">
              <StatBadge value="≈ $0" label="marginal cost per dashboard view" accent="text-white" />
              <StatBadge value="1M+" label="points rendered at 60 fps via WebGL" accent="text-white" />
              <StatBadge value="10–50×" label="cost reduction vs naive warehouse usage¹" accent="text-white" />
              <StatBadge value="0 s" label="cold-start time for browser kernel" accent="text-white" />
            </div>

            <p className="text-center text-xs mt-8 text-white/30">
              ¹ Real at high cache-hit / pre-aggregation rates — e.g. 500 viewers of the same dashboard.
            </p>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            §3  DIFFERENTIATORS — alternating left/right, LARGE illustrations
        ════════════════════════════════════════════════════════════════════ */}
        <section className="py-24 sm:py-32 bg-bg">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">

            {/* Section header */}
            <div className="text-center mb-20 max-w-2xl mx-auto">
              <p className="text-xs font-semibold tracking-widest uppercase mb-4 text-brand-teal">
                Why Nubi
              </p>
              <h2 className="font-display text-4xl sm:text-5xl font-bold leading-tight mb-5 text-fg">
                Six decisions that make{' '}
                <span className="text-brand-gradient">everything cheaper.</span>
              </h2>
              <p className="text-base leading-relaxed text-muted">
                Each feature flows from one structural bet: push compute to the browser,
                fall through to a server only when you must.
              </p>
            </div>

            {/* Alternating rows — each illustration is min ~380px tall on desktop */}
            <div className="flex flex-col gap-24 sm:gap-32">
              <DiffRow
                icon={Zap}
                title="Kernel in the browser"
                badge="Core architecture"
                desc="Pyodide + DuckDB-WASM run inside the user's tab. Zero cold starts, zero per-session cloud cost. A server kernel exists as a metered escape hatch for native wheels — not the default path. Marginal cost per dashboard view ≈ $0."
                Illustration={KernelInBrowser}
                reverse={false}
              />

              <DiffRow
                icon={Globe}
                title="WebGL · 1M+ points at 60 fps"
                badge="Rendering"
                desc="Arrow IPC flows from DuckDB directly into regl GPU buffers. Cross-filter 1M+ row scatter plots at 60fps. The <nubi-chart> element auto-upgrades to WebGL above a row threshold — authors never touch WebGL code."
                Illustration={WebGLPerf}
                reverse={true}
              />

              <DiffRow
                icon={Database}
                title="Edge cache + auto pre-agg"
                badge="Cost architecture"
                desc="Content-hashed edge cache keyed on (plan + JWT claims): 500 viewers of the same dashboard collapse to 1 warehouse hit. The query log feeds a rollup suggester that builds pre-aggregations automatically — the Cube weapon, made automatic."
                Illustration={EdgeCache}
                reverse={false}
              />

              <DiffRow
                icon={Shield}
                title="Auth-as-code embedding"
                badge="Security"
                desc="One JWT primitive powers users, groups, and embedding. Policies live as YAML/SQL in your repo — diffable, PR-reviewable. Predicate injection is AST-based (never string concat). Mount <nubi-dashboard basePath getToken /> and you're done."
                Illustration={EmbedAuth}
                reverse={true}
              />

              <DiffRow
                icon={Bot}
                title="LLM-authorable dashboards"
                badge="AI-native"
                desc="A dashboard is sanitized HTML/CSS with declarative <nubi-*> custom elements. LLMs author HTML natively. Four MCP tools (create_dashboard, author_dashboard, run_query, get_lineage) let agents build and iterate dashboards end-to-end."
                Illustration={LlmDashboards}
                reverse={false}
              />

              <DiffRow
                icon={Code2}
                title="SQL-first connector SDK"
                badge="Extensibility"
                desc="Point at a warehouse and go — no hand-written semantic model to start. A Python connector SDK lets you wrap any Arrow-returning function as a first-class source. The capability gate enforces the security floor: predicate_rls=False → 501."
                Illustration={ConnectorSdk}
                reverse={true}
              />
            </div>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            §4  HOW IT WORKS — 3-step
        ════════════════════════════════════════════════════════════════════ */}
        <section className="py-24 sm:py-32 bg-surface-2">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="text-center mb-16">
              <p className="text-xs font-semibold tracking-widest uppercase mb-4 text-brand-teal">
                How it works
              </p>
              <h2 className="font-display text-4xl sm:text-5xl font-bold text-fg">
                Connect → Query → Embed
              </h2>
            </div>

            <div className="relative flex flex-col lg:flex-row items-start justify-center gap-10 lg:gap-0">
              {/* Connector line */}
              <div className="lp-connector hidden lg:block absolute top-7 left-1/2 -translate-x-1/2 h-0.5 opacity-30" style={{ width: '56%' }} />

              <Step
                num="1"
                title="Connect your warehouse"
                desc="Point Nubi at BigQuery, Snowflake, Redshift, Postgres, or any Arrow-returning Python function. No semantic model required to start."
                code="nubi connector add bigquery --project my-project"
              />

              <div className="hidden lg:block w-24 shrink-0" />

              <Step
                num="2"
                title="Query in SQL, Python, or plain English"
                desc="The DuckDB-WASM kernel runs in the browser. Results stream back as Arrow IPC. Cross-filters update in milliseconds — no round trip to a server kernel."
                code="SELECT month, SUM(revenue) FROM events GROUP BY 1"
              />

              <div className="hidden lg:block w-24 shrink-0" />

              <Step
                num="3"
                title="Embed anywhere, auth-as-code"
                desc="Mount <nubi-dashboard> in your host app, pass a getToken() callback, and row-level security is enforced server-side from JWT claims. Near-zero marginal cost per view."
                code={'<nubi-dashboard basePath getToken />'}
              />
            </div>

            {/* Architecture note */}
            <div className="mt-16 mx-auto max-w-3xl rounded-2xl p-6 text-sm leading-relaxed text-center bg-surface border border-border">
              <strong className="text-brand-blue font-semibold">One language, one engine, one wire format.</strong>
              {' '}Python everywhere (FastAPI + Pyodide + connector planner). DuckDB everywhere (WASM in browser, embedded in connector).
              Arrow IPC at every boundary — so a result hops browser ↔ edge ↔ kernel with no serialization tax.
              <span className="text-muted"> sqlglot rewrites SQL across all three tiers.</span>
            </div>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            §5  POSITIONING vs Hex / Cube
        ════════════════════════════════════════════════════════════════════ */}
        <section className="py-24 sm:py-32 bg-bg">
          <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="text-center mb-12">
              <p className="text-xs font-semibold tracking-widest uppercase mb-4 text-brand-teal">
                Honest comparison
              </p>
              <h2 className="font-display text-4xl sm:text-5xl font-bold mb-4 text-fg">
                Nubi vs the field
              </h2>
              <p className="text-base text-muted">
                Hex is great. Cube is great. But both run compute in their cloud —
                and that shapes everything about their pricing.
              </p>
            </div>

            <div className="rounded-2xl overflow-hidden border border-border">
              {/* Table header */}
              <div className="grid grid-cols-4 text-xs font-semibold tracking-wide uppercase py-3 px-4 bg-surface-2 border-b border-border text-muted">
                <span>Dimension</span>
                <span className="text-center">Hex</span>
                <span className="text-center">Cube</span>
                <span className="text-center text-brand-teal">Nubi</span>
              </div>

              {[
                {
                  dim: 'Compute kernel',
                  hex: 'Python/session, their cloud (10–30s cold)',
                  cube: 'n/a — warehouse + Cube Store',
                  nubi: 'Pyodide in browser; on-demand server kernel only',
                },
                {
                  dim: 'Wire format',
                  hex: 'JSON via pandas',
                  cube: 'JSON / SQL API',
                  nubi: 'Arrow IPC over WebSocket',
                },
                {
                  dim: 'Visualization',
                  hex: 'Plotly/SVG, chokes past ~50k rows',
                  cube: 'Bring your own',
                  nubi: 'WebGL/WebGPU on Arrow buffers, 1M+ pts',
                },
                {
                  dim: 'Edge caching',
                  hex: 'Per-session, weak cross-user',
                  cube: 'Pre-aggs in Cube Store',
                  nubi: 'Content-hashed edge cache + auto pre-aggs',
                },
                {
                  dim: 'Modeling tax',
                  hex: 'Medium',
                  cube: 'High (define cubes first)',
                  nubi: 'Low — point at a warehouse and go',
                },
                {
                  dim: 'Embedding',
                  hex: 'Separate product, bolt-on auth',
                  cube: 'Core strength, headless only',
                  nubi: 'Core surface; editor embeddable, not just output',
                },
                {
                  dim: 'LLM / MCP',
                  hex: '–',
                  cube: '–',
                  nubi: 'MCP server · 4 tools · LLM-authorable HTML dashboards',
                },
                {
                  dim: 'Real free tier',
                  hex: false,
                  cube: false,
                  nubi: true,
                  isBool: true,
                },
              ].map(({ dim, hex, cube, nubi, isBool }, i, arr) => (
                <div
                  key={dim}
                  className={`lp-compare-row grid grid-cols-4 py-3 px-4 text-xs ${i < arr.length - 1 ? 'border-b border-border' : ''}`}
                >
                  <span className="font-medium text-fg">{dim}</span>
                  <span className="text-center text-muted opacity-60">
                    {isBool ? <CompareCheck yes={hex} /> : hex}
                  </span>
                  <span className="text-center text-muted opacity-60">
                    {isBool ? <CompareCheck yes={cube} /> : cube}
                  </span>
                  <span className="text-center font-medium text-brand-teal">
                    {isBool ? <CompareCheck yes={nubi} /> : nubi}
                  </span>
                </div>
              ))}
            </div>

            <p className="text-center text-xs mt-6 text-muted opacity-50">
              Data sourced from public documentation and the Nubi roadmap. We&rsquo;re honest: check primary sources before switching.
            </p>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            §6  CLOSING CTA
        ════════════════════════════════════════════════════════════════════ */}
        <section className="relative py-28 sm:py-36 overflow-hidden bg-surface-2">
          {/* Brand gradient accent strip at top */}
          <div className="absolute top-0 left-0 right-0 h-1 bg-brand-gradient" />

          {/* Decorative dot grid */}
          <svg className="absolute inset-0 w-full h-full opacity-[0.035] pointer-events-none" aria-hidden="true">
            <defs>
              <pattern id="lp-cta-dots" x="0" y="0" width="32" height="32" patternUnits="userSpaceOnUse">
                <circle cx="1" cy="1" r="1.2" fill="currentColor" />
              </pattern>
            </defs>
            <rect width="100%" height="100%" fill="url(#lp-cta-dots)" className="text-brand-blue" />
          </svg>

          <div className="relative max-w-3xl mx-auto px-4 sm:px-6 text-center">
            <p className="text-xs font-semibold tracking-widest uppercase mb-6 text-brand-teal">
              Get started today
            </p>
            <h2 className="font-display text-4xl sm:text-6xl font-bold leading-tight mb-6 text-fg">
              Your first dashboard
              <br />
              <span className="text-brand-gradient">is free. Really.</span>
            </h2>
            <p className="text-base sm:text-lg leading-relaxed mb-10 text-muted">
              Marginal cost per dashboard view is ≈ $0. We charge for connector throughput,
              embed views, AI calls, and on-demand kernel time — not for compute that runs
              in your users&rsquo; browsers.
            </p>

            <div className="flex flex-col sm:flex-row gap-4 justify-center mb-12">
              <Link
                to="/register"
                className="lp-cta-pulse inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl text-base font-semibold transition-all bg-brand-gradient text-white hover:opacity-90 hover:-translate-y-0.5"
              >
                Create free account
                <ArrowRight size={16} strokeWidth={2.5} />
              </Link>
              <Link
                to="/compare"
                className="inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl text-base font-semibold transition-all bg-surface border border-border text-fg hover:border-brand-blue hover:text-brand-blue"
              >
                See pricing
              </Link>
            </div>

            {/* Micro-features */}
            <div className="flex flex-wrap justify-center gap-x-8 gap-y-2 text-xs font-medium text-muted">
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
        </section>

      </div>
    </>
  )
}
