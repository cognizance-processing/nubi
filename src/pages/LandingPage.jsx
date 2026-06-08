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
 *  #how-it-works — How it works
 *  #pricing      — Closing CTA / pricing callout
 *  #compare      — Comparison table section
 *  #about        — Footer brand tagline (re-used for about anchor)
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
  Workflow,
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
} from 'lucide-react'
import HeroIllustration from '../components/illustrations/HeroIllustration.jsx'
import KernelInBrowser from '../components/illustrations/KernelInBrowser.jsx'
import EdgeCache from '../components/illustrations/EdgeCache.jsx'
import EmbedAuth from '../components/illustrations/EmbedAuth.jsx'
import LlmDashboards from '../components/illustrations/LlmDashboards.jsx'
import ConnectorSdk from '../components/illustrations/ConnectorSdk.jsx'
import FlowOrchestration from '../components/illustrations/FlowOrchestration.jsx'
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
  `}</style>
)

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Sub-components (all use tokens)                                            */
/* ─────────────────────────────────────────────────────────────────────────── */

function EyebrowBadge({ children }) {
  return (
    <div className="inline-flex items-center gap-2 text-xs font-semibold tracking-widest uppercase px-3 py-1.5 rounded-full mb-5 sm:mb-6 bg-surface-2 border border-border text-muted">
      <span className="w-1.5 h-1.5 rounded-full bg-accent inline-block" />
      {children}
    </div>
  )
}

function StatBadge({ value, label }) {
  return (
    <div className="flex flex-col items-center px-4 py-5 sm:px-6 text-white">
      <span className="font-display text-3xl sm:text-4xl lg:text-5xl font-bold leading-none mb-1.5 text-white">
        {value}
      </span>
      <span className="text-xs font-medium tracking-wide uppercase text-white/60 text-center max-w-[9rem]">
        {label}
      </span>
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

/* ── Tiny dependency-free code highlighter (SQL / shell / html) ──────────────
   Returns colored <span>s. Colors are mid-tones chosen to read on the code
   surface in BOTH light and dark themes. */
const HL = {
  kw:    '#4079c8', // keyword (blue)
  fn:    '#0d9488', // function / tag (teal)
  str:   '#c77b34', // string (amber)
  num:   '#0d9488',
  param: '#7c5cd6', // {{param}} / {expr} (violet)
  punc:  '#8190a6',
  plain: 'currentColor',
}
const HL_RULES = {
  sql: [
    [/\s+/y, 'plain'],
    [/'(?:[^'\\]|\\.)*'/y, 'str'],
    [/"(?:[^"\\]|\\.)*"/y, 'str'],
    [/\{\{[^}]*\}\}/y, 'param'],
    [/\b\d+(?:\.\d+)?\b/y, 'num'],
    [/\b(?:SELECT|FROM|WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|CROSS|ON|AS|AND|OR|NOT|IN|IS|NULL|LIKE|BETWEEN|DISTINCT|UNION|ALL|WITH|CASE|WHEN|THEN|ELSE|END|ASC|DESC|OVER|PARTITION)\b/iy, 'kw'],
    [/\b(?:SUM|COUNT|AVG|MIN|MAX|COALESCE|CAST|ROUND|DATE_TRUNC|NOW|EXTRACT|LOWER|UPPER|ABS|RANK|ROW_NUMBER)\b/iy, 'fn'],
    [/[a-zA-Z_][a-zA-Z0-9_]*/y, 'plain'],
    [/[(),.*=<>+\-/|]/y, 'punc'],
  ],
  shell: [
    [/\s+/y, 'plain'],
    [/'(?:[^'\\]|\\.)*'/y, 'str'],
    [/"(?:[^"\\]|\\.)*"/y, 'str'],
    [/--?[a-zA-Z][\w-]*/y, 'kw'],
    [/[a-zA-Z_][\w.-]*/y, 'plain'],
    [/[=:/]/y, 'punc'],
  ],
  html: [
    [/\s+/y, 'plain'],
    [/"(?:[^"\\]|\\.)*"/y, 'str'],
    [/\{[^}]*\}/y, 'param'],
    [/<\/?[a-zA-Z][\w-]*/y, 'kw'],
    [/\/?>/y, 'kw'],
    [/[a-zA-Z_][\w-]*(?==)/y, 'fn'],
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
      re.lastIndex = i
      const m = re.exec(code)
      if (m && m.index === i) {
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

function HowItWorksStep({ num, icon: Icon, title, color, tagline, bullets, code, lang, chips }) {
  return (
    <div className="lp-step-card flex flex-col bg-surface rounded-2xl border border-border overflow-hidden flex-1 min-w-0">
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
          <code className="block text-xs font-mono px-3 py-2.5 rounded-lg bg-surface-2 border border-border text-fg break-all leading-relaxed">
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
function DiffRow({ icon: Icon, title, desc, Illustration, reverse = false, badge, id }) {
  const IllustrationBlock = (
    <div className="w-full min-h-[220px] sm:min-h-[280px] lg:min-h-[320px] flex items-center justify-center px-4 py-6 sm:px-8 sm:py-8">
      <Illustration className="w-full h-auto max-w-[480px]" />
    </div>
  )
  const CopyBlock = (
    <div className={`flex flex-col gap-4 sm:gap-5 ${reverse ? 'lg:pr-8' : 'lg:pl-8'}`}>
      {badge && (
        <span className="inline-flex items-center self-start gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-full bg-surface-2 border border-border text-muted tracking-widest uppercase">
          {badge}
        </span>
      )}
      <div className="flex items-center gap-3">
        <span className="shrink-0 inline-flex items-center justify-center w-10 h-10 rounded-xl bg-surface-2 border border-border text-accent">
          <Icon size={20} strokeWidth={1.75} />
        </span>
        <h3 className="font-display font-bold text-xl sm:text-2xl lg:text-3xl text-fg leading-tight">{title}</h3>
      </div>
      <p className="text-sm sm:text-base lg:text-lg leading-relaxed text-muted">{desc}</p>
    </div>
  )

  // Render each block ONCE and reorder with CSS on desktop. Rendering the
  // illustration twice (mobile + desktop copies) duplicates its gradient ids in
  // the DOM; Chrome won't build gradient paint-servers from the display:none
  // copy, so the visible copy's gradient fills vanish. Single-render avoids it.
  return (
    <div
      id={id}
      className={`grid grid-cols-1 lg:grid-cols-2 gap-8 sm:gap-10 lg:gap-16 items-center ${id ? 'scroll-mt-20' : ''}`}
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
        <section id="hero" className="relative flex items-center bg-bg scroll-mt-14">
          {/* Subtle brand gradient wash behind illustration */}
          <div
            className="pointer-events-none absolute inset-0"
            style={{
              background:
                'radial-gradient(ellipse 55% 60% at 75% 50%, rgba(36,86,166,0.07) 0%, transparent 70%), ' +
                'radial-gradient(ellipse 35% 40% at 20% 70%, rgba(23,179,163,0.05) 0%, transparent 60%)',
            }}
          />

          <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-14 sm:py-20 lg:py-28 w-full">
            <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.25fr] gap-10 lg:gap-20 items-center">

              {/* ── Left: copy ── */}
              <div>
                <EyebrowBadge>Open beta · real free tier</EyebrowBadge>

                <h1 className="font-display text-4xl sm:text-5xl lg:text-[4.25rem] xl:text-7xl font-bold leading-[1.06] tracking-tight mb-5 sm:mb-6 text-fg">
                  BI that runs{' '}
                  <span className="text-brand-gradient">
                    in your browser.
                  </span>
                  <br />
                  <span className="text-brand-teal">Near-zero</span>{' '}
                  cost per view.
                </h1>

                <p className="text-base sm:text-lg lg:text-xl leading-relaxed mb-8 sm:mb-10 max-w-lg text-muted">
                  Pyodide + DuckDB-WASM run inside the user&rsquo;s tab —
                  no per-session cloud kernel, no cold starts.
                  Embed a cross-filtering, million-point dashboard inside
                  your SaaS for a{' '}
                  <strong className="text-fg font-semibold">fraction of what Hex or Cube charge.</strong>
                </p>

                {/* CTAs */}
                <div className="flex flex-col sm:flex-row flex-wrap gap-3 mb-8 sm:mb-10">
                  <Link
                    to="/register"
                    className="lp-cta-pulse inline-flex items-center justify-center gap-2 px-6 py-3.5 rounded-xl text-base font-semibold transition-all bg-brand-gradient text-white hover:opacity-90 hover:-translate-y-0.5 min-h-[48px]"
                  >
                    Get started free
                    <ArrowRight size={16} strokeWidth={2.5} />
                  </Link>
                  <Link
                    to="/docs"
                    className="inline-flex items-center justify-center gap-2 px-6 py-3.5 rounded-xl text-base font-semibold transition-all bg-surface-2 border border-border text-fg hover:border-brand-blue hover:text-brand-blue min-h-[48px]"
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

                {/* Trust strip */}
                <div className="flex flex-wrap gap-x-5 gap-y-2 text-xs font-medium text-muted">
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
              <div className="lp-hero-illo relative mt-6 lg:mt-0">
                {/* Glow halo behind illustration */}
                <div
                  className="absolute inset-0 -m-6 rounded-3xl pointer-events-none"
                  style={{
                    background:
                      'radial-gradient(ellipse 80% 70% at 50% 50%, rgba(36,86,166,0.1) 0%, transparent 70%)',
                  }}
                />
                <div className="relative bg-surface rounded-2xl border border-border overflow-hidden p-1 shadow-2xl">
                  <HeroIllustration className="w-full h-auto" style={{ minHeight: 280 }} />
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            §2  PROOF BAND — key metrics
        ════════════════════════════════════════════════════════════════════ */}
        <section className="relative py-12 sm:py-16 lg:py-20 bg-brand-gradient overflow-hidden">
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
            <p className="text-center text-xs font-semibold tracking-widest uppercase mb-8 sm:mb-10 text-white/60">
              The structural advantage — what kernel-in-the-browser actually means
            </p>

            <div className="grid grid-cols-2 sm:grid-cols-4 divide-x divide-y sm:divide-y-0 divide-white/10">
              <StatBadge value="≈ $0" label="marginal cost per dashboard view" />
              <StatBadge value="1M+" label="data points at 60 fps via WebGL" />
              <StatBadge value="10–50×" label="cost reduction vs naive warehouse usage¹" />
              <StatBadge value="0 s" label="cold-start — kernel runs in the tab" />
            </div>

            <p className="text-center text-xs mt-8 text-white/30">
              ¹ Real at high cache-hit / pre-aggregation rates — e.g. 500 viewers of the same dashboard collapsing to 1 warehouse hit.
            </p>
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
              <p className="text-xs font-semibold tracking-widest uppercase mb-4 text-brand-teal">
                Why Nubi
              </p>
              <h2 className="font-display text-3xl sm:text-4xl lg:text-5xl font-bold leading-tight mb-4 sm:mb-5 text-fg">
                Seven decisions that make{' '}
                <span className="text-brand-gradient">everything cheaper.</span>
              </h2>
              <p className="text-sm sm:text-base leading-relaxed text-muted">
                Each feature flows from one structural bet: push compute to the browser and
                fall through to a server only when you must. The result is near-zero marginal
                cost per dashboard view — regardless of how many viewers you have.
              </p>
            </div>

            {/* Alternating rows */}
            <div className="flex flex-col gap-12 sm:gap-16 lg:gap-20">
              <DiffRow
                id="kernel"
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
                id="cache"
                icon={Database}
                title="Edge cache + auto pre-agg"
                badge="Cost architecture"
                desc="Content-hashed edge cache keyed on (plan + JWT claims): 500 viewers of the same dashboard collapse to 1 warehouse hit. The query log feeds a rollup suggester that builds pre-aggregations automatically — the Cube weapon, made automatic."
                Illustration={EdgeCache}
                reverse={false}
              />

              <DiffRow
                id="embedding"
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
                id="connectors"
                icon={Code2}
                title="SQL-first connector SDK"
                badge="Extensibility"
                desc="Point at a warehouse and go — no hand-written semantic model to start. A Python connector SDK lets you wrap any Arrow-returning function as a first-class source. The capability gate enforces the security floor: predicate_rls=False → 501."
                Illustration={ConnectorSdk}
                reverse={true}
              />

              <DiffRow
                id="flows"
                icon={Workflow}
                title="Flows · LLM-native orchestration"
                badge="Workflows"
                desc="A lightweight Prefect alternative built in. Compose queries, Python, AI agents, multi-source materialized blends, archive extraction, and object-storage loads into a visual DAG that runs on Postgres alone — no Redis, no Celery. Retries, timeouts, and result caching per task; RLS-aware execution. Agents can author and run flows in natural language, or drag them together in the builder."
                Illustration={FlowOrchestration}
                reverse={false}
              />
            </div>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            §4  HOW IT WORKS — 3-step
            id="how-it-works" — scroll target for footer link
        ════════════════════════════════════════════════════════════════════ */}
        <section id="how-it-works" className="py-14 sm:py-20 lg:py-24 bg-surface-2 scroll-mt-14">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="text-center mb-12 sm:mb-16">
              <p className="text-xs font-semibold tracking-widest uppercase mb-4 text-brand-teal">
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
                code="nubi connector add bigquery --project my-project"
                lang="shell"
              />

              {/* Arrow connector */}
              <div className="flex lg:flex-col items-center justify-center shrink-0 py-1 lg:py-0 px-0 lg:px-1">
                <div className="hidden lg:flex flex-col items-center gap-2">
                  <div className="w-px h-8 bg-gradient-to-b from-transparent via-border to-transparent" />
                  <ArrowRightCircle size={22} strokeWidth={1.5} className="lp-step-arrow -rotate-90" />
                  <div className="w-px h-8 bg-gradient-to-b from-transparent via-border to-transparent" />
                </div>
                <div className="lg:hidden flex items-center gap-2">
                  <div className="h-px w-8 bg-gradient-to-r from-transparent via-border to-transparent" />
                  <ArrowRightCircle size={22} strokeWidth={1.5} className="lp-step-arrow" />
                  <div className="h-px w-8 bg-gradient-to-r from-transparent via-border to-transparent" />
                </div>
              </div>

              <HowItWorksStep
                num={2}
                icon={SearchCode}
                color="linear-gradient(135deg, #2456a6, #17b3a3)"
                title="Query"
                tagline="SQL, named params, and AI text-to-SQL — all in the browser."
                bullets={[
                  { icon: Database, text: 'DuckDB-WASM kernel runs in the user\'s tab — zero cold starts, zero per-session cloud cost. Results stream as Arrow IPC.' },
                  { icon: Sparkles, text: 'AI text-to-SQL grounded on your actual catalog and lineage graph — not hallucinated schemas. Four MCP tools for agent authoring.' },
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
                code="SELECT month, SUM(revenue) FROM events WHERE {{tenant_id}} GROUP BY 1"
                lang="sql"
              />

              {/* Arrow connector */}
              <div className="flex lg:flex-col items-center justify-center shrink-0 py-1 lg:py-0 px-0 lg:px-1">
                <div className="hidden lg:flex flex-col items-center gap-2">
                  <div className="w-px h-8 bg-gradient-to-b from-transparent via-border to-transparent" />
                  <ArrowRightCircle size={22} strokeWidth={1.5} className="lp-step-arrow -rotate-90" />
                  <div className="w-px h-8 bg-gradient-to-b from-transparent via-border to-transparent" />
                </div>
                <div className="lg:hidden flex items-center gap-2">
                  <div className="h-px w-8 bg-gradient-to-r from-transparent via-border to-transparent" />
                  <ArrowRightCircle size={22} strokeWidth={1.5} className="lp-step-arrow" />
                  <div className="h-px w-8 bg-gradient-to-r from-transparent via-border to-transparent" />
                </div>
              </div>

              <HowItWorksStep
                num={3}
                icon={Layers}
                color="linear-gradient(135deg, #17b3a3, #2dd4bf)"
                title="Embed"
                tagline="One JWT primitive. Per-viewer RLS. Cross-filtering dashboards."
                bullets={[
                  { icon: KeyRound, text: 'Signed JWT carries per-viewer claims. Predicate injection is AST-based — never string concat. Policies live as code in your repo, PR-reviewable.' },
                  { icon: Filter, text: 'Token-locked params prevent viewers from escaping their data scope. Column masking and row-level security enforced server-side before any data leaves the connector.' },
                  { icon: Globe, text: 'Cross-filtering, 1M+ point WebGL scatter plots at 60fps. The <nubi-chart> element auto-upgrades to GPU rendering — authors never touch WebGL code.' },
                  { icon: Code2, text: 'Mount <nubi-dashboard basePath getToken /> in your host app. CSS-var theming, iframe or web component, short-lived JWTs with silent refresh.' },
                ]}
                chips={[
                  { label: 'JWT RLS', accent: true },
                  { label: 'AST predicate inject', accent: true },
                  { label: 'Token-locked params', accent: false },
                  { label: 'WebGL cross-filter', accent: false },
                  { label: 'Web component', accent: false },
                ]}
                code={'<nubi-dashboard basePath="/api" getToken={getToken} />'}
                lang="html"
              />
            </div>

            {/* Architecture note */}
            <div className="mt-10 sm:mt-12 mx-auto max-w-3xl rounded-2xl p-5 sm:p-6 text-sm leading-relaxed text-center bg-surface border border-border">
              <strong className="text-brand-blue font-semibold">One language, one engine, one wire format.</strong>
              {' '}Python everywhere (FastAPI + Pyodide + connector planner). DuckDB everywhere (WASM in browser, embedded in connector).
              Arrow IPC at every boundary — so a result hops browser ↔ edge ↔ kernel with no serialization tax.
              <span className="text-muted"> sqlglot rewrites SQL across all three tiers.</span>
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
              <p className="text-xs font-semibold tracking-widest uppercase mb-4 text-brand-teal">
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
                    nubi: 'Pyodide in browser; on-demand server kernel only when needed',
                  },
                  {
                    dim: 'Wire format',
                    hex: 'JSON via pandas',
                    cube: 'JSON / SQL API',
                    nubi: 'Arrow IPC over WebSocket',
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
                    dim: 'Auto pre-aggregation',
                    hex: false,
                    cube: 'partial',
                    nubi: true,
                    isBool: true,
                  },
                  {
                    category: 'Visualization',
                    dim: 'Rendering engine',
                    hex: 'Plotly / SVG, chokes past ~50k rows',
                    cube: 'Bring your own',
                    nubi: 'WebGL on Arrow buffers — 1M+ pts at 60fps',
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
                    nubi: 'Core surface; editor + output embeddable',
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
                    nubi: 'MCP server · 4 tools · LLM-authorable HTML dashboards',
                    isBool: false,
                  },
                  {
                    dim: 'Real free tier',
                    hex: false,
                    cube: false,
                    nubi: true,
                    isBool: true,
                  },
                ].map(({ category, dim, hex, cube, nubi, isBool }, i, arr) => {
                  const isLastInBlock = i === arr.length - 1 || arr[i + 1]?.category
                  return (
                    <div key={dim}>
                      {/* Category separator row */}
                      {category && (
                        <div className="grid grid-cols-[1.6fr_1fr_1fr_1.15fr] bg-surface-2 border-t border-border">
                          <div className="py-2 px-5 col-span-1 flex items-center gap-2">
                            <span className="w-1.5 h-1.5 rounded-full bg-brand-teal" />
                            <span className="text-[11px] font-bold uppercase tracking-widest text-brand-blue">{category}</span>
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
                          <CompareCell value={isBool ? hex : hex} />
                        </div>
                        <div className="py-3.5 px-4 border-l border-border flex items-center justify-center">
                          <CompareCell value={isBool ? cube : cube} />
                        </div>
                        <div className="lp-nubi-col py-3.5 px-4 flex items-center justify-center">
                          <CompareCell value={isBool ? nubi : nubi} isNubi />
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
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            §6  CLOSING CTA / PRICING CALLOUT
            id="pricing" — scroll target for footer "Pricing" link
        ════════════════════════════════════════════════════════════════════ */}
        <section id="pricing" className="relative py-20 sm:py-28 lg:py-36 overflow-hidden bg-surface-2 scroll-mt-14">
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
              Pricing
            </p>
            <h2 className="font-display text-3xl sm:text-5xl lg:text-6xl font-bold leading-tight mb-5 sm:mb-6 text-fg">
              Your first dashboard
              <br />
              <span className="text-brand-gradient">is free. Really.</span>
            </h2>
            <p className="text-sm sm:text-base lg:text-lg leading-relaxed mb-8 text-muted">
              Marginal cost per dashboard view is ≈ $0 — compute runs in the user&rsquo;s browser,
              not our cloud. We charge for <strong className="text-fg font-medium">connector throughput</strong>,{' '}
              <strong className="text-fg font-medium">embed views</strong>,{' '}
              <strong className="text-fg font-medium">AI calls</strong>, and{' '}
              <strong className="text-fg font-medium">on-demand server kernel time</strong> — never for
              compute that runs in your users&rsquo; browsers.
            </p>

            {/* Pricing tiers */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-10 text-left">
              {[
                {
                  tier: 'Free',
                  price: '$0',
                  note: 'Real free tier, no gotchas',
                  bullets: ['Unlimited dashboard views', 'DuckDB-WASM kernel', '2 editors · 1 connector'],
                },
                {
                  tier: 'Pro',
                  price: '$49',
                  note: 'For growing teams',
                  bullets: ['Unlimited connectors', 'Edge cache + pre-aggs', 'AI / MCP · all Flow tasks'],
                  highlight: true,
                },
                {
                  tier: 'Scale',
                  price: '$1,000',
                  note: 'Dedicated support + SLA',
                  bullets: ['High-volume embedding', 'SSO · RBAC · audit', 'Named contact + Slack'],
                },
              ].map(({ tier, price, note, bullets, highlight }) => (
                <div
                  key={tier}
                  className={`rounded-xl p-5 border flex flex-col gap-3 ${
                    highlight
                      ? 'bg-brand-gradient text-white border-transparent'
                      : 'bg-surface border-border'
                  }`}
                >
                  <div>
                    <div className="flex items-baseline gap-1.5">
                      <p className={`font-display font-bold text-lg ${highlight ? 'text-white' : 'text-fg'}`}>{tier}</p>
                      <span className={`font-display font-bold text-lg ${highlight ? 'text-white' : 'text-fg'}`}>· {price}</span>
                      {tier !== 'Free' && <span className={`text-[11px] ${highlight ? 'text-white/60' : 'text-muted'}`}>/mo</span>}
                    </div>
                    <p className={`text-xs mt-0.5 ${highlight ? 'text-white/70' : 'text-muted'}`}>{note}</p>
                  </div>
                  <ul className="flex flex-col gap-1.5">
                    {bullets.map(b => (
                      <li key={b} className={`flex items-center gap-2 text-xs ${highlight ? 'text-white/90' : 'text-muted'}`}>
                        <Check size={11} strokeWidth={3} className={highlight ? 'text-white' : 'text-accent'} />
                        {b}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>

            <div className="flex flex-col sm:flex-row gap-3 sm:gap-4 justify-center mb-10">
              <Link
                to="/register"
                className="lp-cta-pulse inline-flex items-center justify-center gap-2 px-6 sm:px-8 py-4 rounded-xl text-base font-semibold transition-all bg-brand-gradient text-white hover:opacity-90 hover:-translate-y-0.5 min-h-[52px]"
              >
                Create free account
                <ArrowRight size={16} strokeWidth={2.5} />
              </Link>
              <Link
                to="/pricing"
                className="inline-flex items-center justify-center gap-2 px-6 sm:px-8 py-4 rounded-xl text-base font-semibold transition-all bg-surface border border-border text-fg hover:border-brand-blue hover:text-brand-blue min-h-[52px]"
              >
                See full pricing →
              </Link>
            </div>

            {/* Micro-features */}
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
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            §7  ABOUT — minimal "about" anchor for footer link
        ════════════════════════════════════════════════════════════════════ */}
        <section id="about" className="py-14 sm:py-16 bg-bg scroll-mt-14">
          <div className="max-w-3xl mx-auto px-4 sm:px-6 text-center">
            <p className="text-xs font-semibold tracking-widest uppercase mb-4 text-brand-teal">About Nubi</p>
            <h2 className="font-display text-2xl sm:text-3xl lg:text-4xl font-bold mb-4 sm:mb-5 text-fg">
              Built on one structural bet.
            </h2>
            <p className="text-sm sm:text-base leading-relaxed text-muted mb-6">
              The analytics kernel runs in the user&rsquo;s browser by default. That single decision
              makes the marginal cost of a dashboard view ≈ $0 — not a marketing claim, a
              consequence of where compute runs. Hex runs a Python kernel per session in their
              cloud. Cube runs the data plane in their cloud. Nubi pushes compute to the browser
              and only falls through to a server kernel for the ~10% of workloads that need it.
            </p>
            <p className="text-sm text-muted opacity-70">
              Apache-2.0 open source &middot; Real free tier &middot; Self-hostable connectors
            </p>
          </div>
        </section>

      </div>
    </>
  )
}
