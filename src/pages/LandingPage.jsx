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

import { useState, useEffect } from 'react'
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
  Star,
  Headset,
  Users,
  Server,
  SlidersHorizontal,
  TrendingDown,
  CheckCircle2,
  XCircle,
  Wallet,
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
import { fetchPricingData } from '../lib/pricing.js'
import HeroIllustration from '../components/illustrations/HeroIllustration.jsx'
import QueryWorkspace from '../components/illustrations/QueryWorkspace.jsx'
import DashboardCanvas from '../components/illustrations/DashboardCanvas.jsx'
import KernelInBrowser from '../components/illustrations/KernelInBrowser.jsx'
import EdgeCache from '../components/illustrations/EdgeCache.jsx'
import WebGLPerf from '../components/illustrations/WebGLPerf.jsx'
// Dev-centric features read better as real code than abstract art.
import { ConnectorSdkCode, FlowCode, EmbedAuthCode, LlmDashboardCode } from '../components/illustrations/CodeTile.jsx'

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
          className="inline-flex items-center justify-center gap-2 px-6 py-3 rounded-xl text-sm font-semibold transition-all bg-surface border border-border text-fg hover:border-brand-blue hover:text-brand-blue min-h-[44px]"
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
            : 'bg-surface-2 border border-border text-fg hover:border-brand-blue hover:text-brand-blue'}`}
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
            <span className="font-display text-xl font-bold text-brand-blue">{fmtNum(viewers)}</span>
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
            <span className="font-display text-xl font-bold text-brand-blue">{editors}</span>
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
            <span className="font-display text-xl font-bold text-brand-blue">{envs}</span>
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
            <span className="font-display text-xl font-bold text-brand-blue">{fmtNum(gb)}</span>
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
          <rect width="100%" height="100%" fill="url(#lp-pricing-dots)" className="text-brand-blue" />
        </svg>
        <div className="relative max-w-3xl mx-auto px-4 sm:px-6 text-center">
          <p className="text-xs font-semibold tracking-widest uppercase mb-4 text-brand-teal">Pricing</p>
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
                      <span className="shrink-0 mt-0.5 w-8 h-8 rounded-lg bg-surface-2 border border-border flex items-center justify-center text-brand-blue">
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
            <p className="text-xs font-semibold tracking-widest uppercase mb-4 text-brand-teal inline-flex items-center gap-1.5">
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
              className="inline-flex items-center justify-center gap-2 px-6 sm:px-8 py-4 rounded-xl text-base font-semibold transition-all bg-surface border border-border text-fg hover:border-brand-blue hover:text-brand-blue min-h-[52px]"
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
                  <span className="text-brand-teal">Viewers are free.</span>{' '}
                  Every plan.
                </h1>

                <p className="text-base sm:text-lg lg:text-xl leading-relaxed mb-8 sm:mb-10 max-w-lg text-muted">
                  A DuckDB-WASM kernel computes inside the user&rsquo;s tab —
                  no per-session cloud kernel, no cold starts, so an extra
                  viewer costs ≈ $0 and we never charge for one. Unlimited
                  seats on every tier, Flows orchestration built in, open-core
                  and self-hostable. Embed it in your SaaS for a{' '}
                  <strong className="text-fg font-semibold">fraction of what per-seat BI charges.</strong>
                </p>

                {/* CTAs */}
                <div className="flex flex-col sm:flex-row flex-wrap gap-3 mb-8 sm:mb-10">
                  <Link
                    to="/register"
                    className="lp-cta-pulse inline-flex items-center justify-center gap-2 px-6 py-3.5 rounded-xl text-base font-semibold transition-all bg-brand-gradient text-white hover:opacity-90 hover:-translate-y-0.5 min-h-[48px]"
                  >
                    Start free
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
                    'Unlimited seats & viewers',
                    'No credit card to start',
                    'Apache-2.0 open core',
                    'Arrow IPC + WebGL rendering',
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
              <StatBadge value="1M" label="scatter points rendered in-browser via WebGL" />
              <StatBadge value="10–50×" label="cost reduction vs naive warehouse usage¹" />
              <StatBadge value="0 s" label="cold-start — kernel runs in the tab" />
            </div>

            <p className="text-center text-xs mt-8 text-white/30">
              ¹ Real at high cache-hit / pre-aggregation rates — e.g. 500 viewers of the same dashboard collapsing to 1 warehouse hit.
            </p>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            §2.5  PRODUCT SHOWCASE — what you actually work in
            id="product" — query workspace + dashboard builder, side by side
        ════════════════════════════════════════════════════════════════════ */}
        <section id="product" className="py-14 sm:py-20 lg:py-24 bg-bg scroll-mt-14">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="text-center mb-12 sm:mb-16">
              <p className="text-xs font-semibold tracking-widest uppercase mb-4 text-brand-teal">
                See it in action
              </p>
              <h2 className="font-display text-3xl sm:text-4xl lg:text-5xl font-bold text-fg">
                One workspace, from{' '}
                <span className="text-brand-blue">SQL</span> to{' '}
                <span className="text-brand-teal">embedded dashboard</span>.
              </h2>
              <p className="text-sm sm:text-base leading-relaxed mt-4 text-muted max-w-2xl mx-auto">
                Write a query, see results the instant you hit Run, then drag the
                charts into a dashboard your customers can open — no separate tools,
                no cold-start kernel, no per-viewer bill.
              </p>
            </div>

            <div className="grid md:grid-cols-2 gap-5 lg:gap-7">
              {[
                {
                  Illustration: QueryWorkspace,
                  tag: 'Query workspace',
                  title: 'Write SQL. See results instantly.',
                  body: 'The DuckDB-WASM kernel runs in the tab, so queries return with no cold start and no per-session cloud cost. Named {{params}}, AI text-to-SQL grounded on your real schema, and results that stream as Arrow IPC.',
                  chips: ['DuckDB-WASM', 'Named params', 'AI text-to-SQL'],
                },
                {
                  Illustration: DashboardCanvas,
                  tag: 'Dashboard builder',
                  title: 'Compose it. Embed it anywhere.',
                  body: 'Drag KPIs, charts, and tables onto a grid, then drop the <nubi-dashboard> web component into your app. Per-viewer row-level security travels in a signed JWT — and viewers are free at every plan.',
                  chips: ['Drag & drop', 'Embed anywhere', 'Viewers free'],
                },
              ].map((c) => (
                <div key={c.tag} className="flex flex-col bg-surface rounded-2xl border border-border overflow-hidden shadow-sm">
                  <div className="p-5 sm:p-7 bg-surface-2 border-b border-border">
                    <c.Illustration className="w-full h-auto" />
                  </div>
                  <div className="p-5 sm:p-7 flex flex-col gap-3">
                    <p className="text-xs font-semibold tracking-widest uppercase text-brand-teal">{c.tag}</p>
                    <h3 className="font-display text-xl sm:text-2xl font-bold text-fg">{c.title}</h3>
                    <p className="text-sm leading-relaxed text-muted">{c.body}</p>
                    <div className="flex flex-wrap gap-2 mt-1">
                      {c.chips.map((chip) => (
                        <span key={chip} className="text-xs font-medium px-2.5 py-1 rounded-full bg-brand-teal/10 text-brand-teal border border-brand-teal/20">
                          {chip}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              ))}
            </div>
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
                desc="A DuckDB-WASM analytics kernel runs inside the user's tab. Zero cold starts, zero per-session cloud cost. Python cells route to a metered, scale-to-zero server kernel — the escape hatch, not the default path. Marginal cost per dashboard view ≈ $0."
                Illustration={KernelInBrowser}
                reverse={false}
              />

              <DiffRow
                icon={Globe}
                title="WebGL · up to 1M points"
                badge="Rendering"
                desc="Arrow IPC flows from DuckDB directly into regl GPU buffers — scatter plots scale to ~1M points. The <nubi-chart> element auto-upgrades to WebGL above a configurable row threshold (20k by default) — authors never touch WebGL code."
                Illustration={WebGLPerf}
                reverse={true}
              />

              <DiffRow
                id="cache"
                icon={Database}
                title="Edge cache + auto pre-agg"
                badge="Cost architecture"
                desc="Content-hashed edge cache keyed on (plan + JWT claims): 500 viewers of the same dashboard collapse to 1 warehouse hit. A rollup suggester mines hot GROUP BY shapes from your query log — materialize the winners in one click, no hand-written cubes."
                Illustration={EdgeCache}
                reverse={false}
              />

              <DiffRow
                id="embedding"
                icon={Shield}
                title="Auth-as-code embedding"
                badge="Security"
                desc="One JWT primitive powers users, groups, and embedding. RLS policies are claims in a token your backend signs — auth logic lives in your repo, not a vendor UI. Predicate injection is AST-based (never string concat). Mount <nubi-dashboard get-token> and you're done."
                Illustration={EmbedAuthCode}
                reverse={true}
              />

              <DiffRow
                icon={Bot}
                title="LLM-authorable dashboards"
                badge="AI-native"
                desc="A dashboard is sanitized HTML/CSS with declarative <nubi-*> custom elements. LLMs author HTML natively. Six MCP tools — author_dashboard, create_dashboard, run_query, list_dashboards, list_lineage, propose_materialized_view — let agents build and iterate dashboards end-to-end."
                Illustration={LlmDashboardCode}
                reverse={false}
              />

              <DiffRow
                id="connectors"
                icon={Code2}
                title="SQL-first connector SDK"
                badge="Extensibility"
                desc="Point at a warehouse and go — no hand-written semantic model to start. A Python connector SDK lets you wrap any Arrow-returning function as a first-class source. The capability gate enforces the security floor: predicate_rls=False → 501."
                Illustration={ConnectorSdkCode}
                reverse={true}
              />

              <DiffRow
                id="flows"
                icon={Workflow}
                title="Flows · LLM-native orchestration"
                badge="Workflows"
                desc="A lightweight Prefect alternative built in. Three cell types — SQL, Python, and notes — wired into a DAG you edit as a notebook or a visual canvas. Materialization, fan-out, and conditional gates are cell settings, and it all runs on Postgres alone — no Redis, no Celery. Retries, timeouts, and result caching per task. AI tools let agents author and run flows in natural language."
                Illustration={FlowCode}
                reverse={false}
              />
            </div>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            §3.5  CONNECTORS — brand logo wall
            id="sources" — "connect to your whole stack"
        ════════════════════════════════════════════════════════════════════ */}
        <section id="sources" className="py-14 sm:py-20 lg:py-24 bg-surface-2 border-y border-border scroll-mt-14">
          <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="text-center mb-10 sm:mb-14 max-w-2xl mx-auto">
              <p className="text-xs font-semibold tracking-widest uppercase mb-4 text-brand-teal">
                Connectors
              </p>
              <h2 className="font-display text-3xl sm:text-4xl lg:text-5xl font-bold leading-tight mb-4 sm:mb-5 text-fg">
                Connect to your{' '}
                <span className="text-brand-gradient">whole stack.</span>
              </h2>
              <p className="text-sm sm:text-base leading-relaxed text-muted">
                Point Nubi at the warehouses, databases, and lakes you already run — no proprietary
                semantic model to start. Relational, cloud-managed, warehouse, query-engine, and
                object-storage sources are first-class, all enforcing the same security floor.
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
                color="linear-gradient(135deg, #17b3a3, #2dd4bf)"
                title="Embed"
                tagline="One JWT primitive. Per-viewer RLS. Cross-filtering dashboards."
                bullets={[
                  { icon: KeyRound, text: 'Signed JWT carries per-viewer claims. Predicate injection is AST-based — never string concat. Policies live as code in your repo, PR-reviewable.' },
                  { icon: Filter, text: 'Token-locked params prevent viewers from escaping their data scope. Column masking and row-level security enforced server-side before any data leaves the connector.' },
                  { icon: Globe, text: 'Cross-filtering dashboards with WebGL scatter plots that scale to ~1M points. The <nubi-chart> element auto-upgrades to GPU rendering — authors never touch WebGL code.' },
                  { icon: Code2, text: 'Drop the <nubi-dashboard> web component into your host app — UMD or ES module. Theme attribute, short-lived JWTs that refresh before expiry.' },
                ]}
                chips={[
                  { label: 'JWT RLS', accent: true },
                  { label: 'AST predicate inject', accent: true },
                  { label: 'Token-locked params', accent: false },
                  { label: 'WebGL cross-filter', accent: false },
                  { label: 'Web component', accent: false },
                ]}
                code={'<nubi-dashboard query="SELECT * FROM sales" get-token="getEmbedToken">'}
                lang="html"
              />
            </div>

            {/* Architecture note */}
            <div className="mt-10 sm:mt-12 mx-auto max-w-3xl rounded-2xl p-5 sm:p-6 text-sm leading-relaxed text-center bg-surface border border-border">
              <strong className="text-brand-blue font-semibold">One language, one engine, one wire format.</strong>
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
                    nubi: 'WebGL on Arrow buffers — scales to ~1M pts',
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
            <p className="text-xs font-semibold tracking-widest uppercase mb-4 text-brand-teal">About Nubi</p>
            <h2 className="font-display text-2xl sm:text-3xl lg:text-4xl font-bold mb-4 sm:mb-5 text-fg">
              Built on one structural bet.
            </h2>
            <p className="text-sm sm:text-base leading-relaxed text-muted mb-6">
              The analytics kernel runs in the user&rsquo;s browser by default. That single decision
              makes the marginal cost of a dashboard view ≈ $0 — not a marketing claim, a
              consequence of where compute runs. Hex runs a Python kernel per session in their
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
