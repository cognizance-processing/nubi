/**
 * ComparePage — Nubi vs competitors.
 *
 * Content is driven by Markdown files in src/content/compare/
 * loaded via src/compare/registry.js (import.meta.glob eager pattern,
 * mirroring src/docs/registry.js).
 *
 * Design: shares the landing page's marketing language — observatory
 * lp-hero-panel sections, glass bento cards, mono eyebrows, gradient
 * display text, scroll reveals (MarketingStyles + useReveal).
 *
 * Tokens: only bg-bg, bg-surface, bg-surface-2, text-fg, text-muted,
 * border-border, bg-primary, text-primary, text-primary-fg, bg-accent,
 * text-accent, ring-ring, text-brand-{navy,blue,teal,cyan},
 * bg-brand-gradient, text-brand-gradient.
 *
 * Light + dark: all semantic tokens; inline-style gradients use rgba
 * so they read on both white surfaces (light) and dark-navy (dark).
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import MarketingStyles from '../components/marketing/MarketingStyles.jsx'
import useReveal from '../components/marketing/useReveal.js'
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
  AlertTriangle,
  Check,
  GitFork,
  Users,
  Lock,
} from 'lucide-react'
import {
  INTRO,
  WHY_NUBI,
  MATRIX_META,
  COMPETITORS,
  ORCHESTRATORS,
  COMPARE_DIMENSIONS,
  MATRIX,
  MATRIX_COLUMNS,
} from '../compare/registry.js'
import FairnessNote from '../components/marketing/FairnessNote.jsx'

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Scoped styles                                                               */
/* ─────────────────────────────────────────────────────────────────────────── */

const ScopedStyles = () => (
  <style>{`
    /* Nubi column gradient + brand side rails so it reads as the hero column */
    .cp-nubi-col {
      background: linear-gradient(
        160deg,
        rgba(27,35,99,0.08) 0%,
        rgba(36,86,166,0.08) 50%,
        rgba(23,179,163,0.09) 100%
      );
      box-shadow: inset 1.5px 0 0 rgba(23,179,163,0.30), inset -1.5px 0 0 rgba(23,179,163,0.30);
    }
    .dark .cp-nubi-col {
      background: linear-gradient(
        160deg,
        rgba(125,170,240,0.07) 0%,
        rgba(72,124,214,0.08) 50%,
        rgba(45,212,191,0.09) 100%
      );
    }
    .cp-nubi-header {
      background: linear-gradient(
        160deg,
        rgba(27,35,99,0.15) 0%,
        rgba(36,86,166,0.14) 50%,
        rgba(23,179,163,0.16) 100%
      );
      box-shadow:
        inset 0 3px 0 #17b3a3,
        inset 1.5px 0 0 rgba(23,179,163,0.35),
        inset -1.5px 0 0 rgba(23,179,163,0.35);
    }
    .dark .cp-nubi-header {
      background: linear-gradient(
        160deg,
        rgba(125,170,240,0.14) 0%,
        rgba(72,124,214,0.13) 50%,
        rgba(45,212,191,0.15) 100%
      );
    }

    /* Row hover */
    .cp-row:hover .cp-row-cell {
      background-color: rgba(36, 86, 166, 0.04);
    }
    .cp-row:hover .cp-nubi-col {
      background: linear-gradient(
        160deg,
        rgba(27,35,99,0.11) 0%,
        rgba(36,86,166,0.11) 50%,
        rgba(23,179,163,0.11) 100%
      );
    }

    /* Stripe even rows */
    .cp-matrix-row:nth-child(even) .cp-row-cell {
      background-color: rgba(36, 86, 166, 0.025);
    }
    .cp-matrix-row:nth-child(even) .cp-nubi-col {
      background: linear-gradient(
        160deg,
        rgba(27,35,99,0.10) 0%,
        rgba(36,86,166,0.10) 50%,
        rgba(23,179,163,0.10) 100%
      );
    }

    /* Frozen Dimension column — must stay fully OPAQUE so data cells never show
       through it on horizontal scroll. Uses solid surface tokens (not the
       translucent stripe/hover backgrounds the data cells use). */
    .cp-dim-cell { background-color: var(--surface); }
    .cp-matrix-row:nth-child(even) .cp-dim-cell { background-color: var(--surface-2); }
    .cp-row:hover .cp-dim-cell { background-color: var(--surface-2); }

    /* Table shell — soft elevation + clipped corners */
    .cp-table-shell {
      box-shadow: 0 1px 2px rgba(27,35,99,0.05), 0 24px 56px -32px rgba(27,35,99,0.28);
    }
    .dark .cp-table-shell {
      box-shadow: 0 24px 56px -28px rgba(0,0,0,0.55);
    }

    /* ── Glass bento card (matches the landing's bento deck) ── */
    .cp-card {
      position: relative;
      border-radius: 1.25rem;
      border: 1px solid rgba(27,35,99,0.10);
      background: rgba(255,255,255,0.72);
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
      box-shadow: 0 1px 2px rgba(27,35,99,0.05), 0 18px 44px -28px rgba(27,35,99,0.30);
      transition: transform 0.25s cubic-bezier(0.34,1.56,0.64,1),
                  box-shadow 0.25s ease,
                  border-color 0.2s ease;
    }
    .dark .cp-card {
      border-color: rgba(255,255,255,0.09);
      background: rgba(13,20,48,0.55);
      box-shadow: 0 18px 48px -24px rgba(0,0,0,0.6);
    }
    .cp-card:hover {
      transform: translateY(-3px);
      border-color: color-mix(in srgb, var(--cp-accent, #17b3a3) 45%, transparent);
      box-shadow: 0 24px 52px -22px color-mix(in srgb, var(--cp-accent, #17b3a3) 35%, transparent);
    }

    /* brand hairline along the top edge of a feature card */
    .cp-hairline::before {
      content: '';
      position: absolute; left: 16px; right: 16px; top: 0; height: 2px;
      background: linear-gradient(90deg, transparent, rgba(23,179,163,0.55), rgba(36,86,166,0.55), transparent);
      border-radius: 2px;
    }

    /* Expand animation */
    .cp-expand-enter { animation: cp-expand 0.18s ease forwards; }
    @keyframes cp-expand {
      from { opacity: 0; transform: translateY(-4px); }
      to   { opacity: 1; transform: translateY(0); }
    }

    /* Markdown prose inside compare sections */
    .cp-prose p { margin: 0.85rem 0; line-height: 1.7; color: var(--text-muted); font-size: 0.9rem; }
    .cp-prose p strong { color: var(--text); }
    .cp-prose h2 { font-size: 1.15rem; font-weight: 700; color: var(--text); margin: 1.5rem 0 0.5rem; font-family: 'Space Grotesk', system-ui, sans-serif; }
    .cp-prose h3 { font-size: 1rem; font-weight: 600; color: var(--text); margin: 1.1rem 0 0.4rem; font-family: 'Space Grotesk', system-ui, sans-serif; }
    .cp-prose h4 { font-size: 0.875rem; font-weight: 600; color: var(--text); margin: 0.9rem 0 0.3rem; font-family: 'Space Grotesk', system-ui, sans-serif; text-transform: uppercase; letter-spacing: 0.05em; }
    .cp-prose ul { margin: 0.5rem 0 0.5rem 1.25rem; list-style: disc; }
    .cp-prose ul li { margin: 0.25rem 0; color: var(--text-muted); font-size: 0.9rem; }
    .cp-prose blockquote { border-left: 3px solid #17b3a3; padding: 0.5rem 0.75rem; margin: 1rem 0; background: rgba(23,179,163,0.05); border-radius: 0 0.5rem 0.5rem 0; }
    .cp-prose blockquote p { color: var(--text); font-style: italic; margin: 0; }
    .cp-prose code { font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 0.8em; background: var(--surface-2); color: #17b3a3; padding: 0.15em 0.4em; border-radius: 0.25rem; border: 1px solid var(--border); }
    .cp-prose hr { border-color: var(--border); margin: 1.25rem 0; }

    /* Tooltip */
    .cp-tooltip { position: relative; display: inline-flex; align-items: center; gap: 0.25rem; cursor: default; }
    .cp-tooltip-box {
      position: absolute; bottom: 100%; left: 0; margin-bottom: 0.5rem;
      width: 16rem; border-radius: 0.75rem; background: var(--surface);
      border: 1px solid var(--border); box-shadow: 0 10px 40px rgba(0,0,0,0.15);
      padding: 0.75rem; font-size: 0.75rem; color: var(--text-muted);
      line-height: 1.5; opacity: 0; pointer-events: none;
      transition: opacity 0.2s ease; z-index: 50;
    }
    .cp-tooltip:hover .cp-tooltip-box { opacity: 1; }
  `}</style>
)

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Helpers                                                                    */
/* ─────────────────────────────────────────────────────────────────────────── */

function isEstimate(val) {
  return typeof val === 'string' &&
    (val.toLowerCase().includes('unverified') || val.toLowerCase().includes('(est.)'))
}

/** Bold helper — readable on light panels and the dark observatory panel. */
const B = ({ children }) => (
  <strong className="font-semibold text-fg dark:text-white">{children}</strong>
)

/** One-shot scroll reveal wrapper (lp-reveal / lp-in from MarketingStyles). */
function Reveal({ children, className = '', delay = 0, id }) {
  const [ref, seen] = useReveal()
  return (
    <div
      ref={ref}
      id={id}
      className={`lp-reveal ${seen ? 'lp-in' : ''} ${className}`}
      style={delay ? { transitionDelay: `${delay}ms` } : undefined}
    >
      {children}
    </div>
  )
}

/** Centered section header — mono eyebrow + display heading + body. */
function SectionHead({ eyebrow, title, children, wide = false }) {
  return (
    <Reveal className={`text-center mb-10 sm:mb-14 ${wide ? 'max-w-3xl' : 'max-w-2xl'} mx-auto`}>
      <p className="font-mono text-[11px] font-semibold tracking-[0.18em] uppercase mb-4 text-brand-teal">
        {eyebrow}
      </p>
      <h2 className="font-display text-3xl sm:text-4xl lg:text-5xl font-bold leading-[1.08] tracking-tight text-fg mb-4">
        {title}
      </h2>
      {children && (
        <p className="text-sm sm:text-base leading-relaxed text-muted">{children}</p>
      )}
    </Reveal>
  )
}

/** Style helper: render a string title with its last word in gradient text.
 *  Non-string titles (already-styled JSX) pass through untouched. */
function gradientLast(title, gradientClass = 'text-brand-gradient') {
  if (typeof title !== 'string') return title
  const words = title.trim().split(' ')
  if (words.length < 2) return title
  const last = words.pop()
  return (
    <>
      {words.join(' ')} <span className={gradientClass}>{last}</span>
    </>
  )
}

function EstBadge() {
  return (
    <span className="inline-flex items-center gap-0.5 font-mono text-[10px] font-medium px-1.5 py-0.5 rounded-full
      bg-surface-2 text-muted border border-border ml-1 shrink-0 whitespace-nowrap">
      <Clock size={9} />
      est.
    </span>
  )
}

function Tooltip({ text, children }) {
  return (
    <span className="cp-tooltip">
      {children}
      <span className="cp-tooltip-box">{text}</span>
    </span>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  All-Competitors pricing table — 14 platforms, scrupulously fair           */
/* ─────────────────────────────────────────────────────────────────────────── */

/**
 * Each row: name, category, pricingModel, entryPrice, seatModel, embedding,
 * selfHost, strongerThanNubi (acknowledged honest strengths), sourceUrl.
 *
 * All prices from June 2026 public pricing pages or third-party analysts.
 * Estimated values are annotated.
 */
const ALL_COMPETITORS = [
  /* ── NUBI ── */
  {
    name: 'Nubi',
    isNubi: true,
    category: 'Embedded BI + Flows',
    pricingModel: 'Usage-based flat tiers',
    entryPrice: '$0 free / $9/mo Starter',
    entryEst: false,
    seatModel: 'Unlimited users & viewers at every tier — no per-seat charge',
    embedding: 'Core surface: JWKS-native JWT RLS; unlimited viewers all paid tiers',
    selfHost: 'Connector self-host (cloud control plane); full self-host planned',
    strengths: 'Unlimited seats; ZAR billing; browser compute; transparent pricing',
    weaknesses: 'Newer product; smaller ecosystem; full self-host not yet shipped',
    sourceUrl: 'https://nubi.io/pricing',
  },
  /* ── GENERAL BI ── */
  {
    name: 'Metabase',
    isNubi: false,
    category: 'General BI',
    pricingModel: 'Per-seat tiered',
    entryPrice: 'OSS free; Pro $575/mo + $12/viewer',
    entryEst: false,
    seatModel: 'Every embedded viewer = full paid seat (~$150k/yr for 1k viewers on Pro)',
    embedding: 'White-label on Pro ($575+/mo); per-viewer seat cost at scale is prohibitive',
    selfHost: 'Yes — AGPL OSS free; Pro self-hosted same fee as cloud',
    strengths: 'Largest open-source community; lowest barrier for non-technical users; free AGPL self-host; Data Studio semantic layer (v59, 2026)',
    weaknesses: 'Per-viewer seat penalty; AGPL compliance burden for SaaS; no Arrow path; no ZAR billing',
    sourceUrl: 'https://www.metabase.com/pricing/',
  },
  {
    name: 'Hex',
    isNubi: false,
    category: 'Notebooks + Apps',
    pricingModel: 'Per-editor seat + compute add-on',
    entryPrice: 'Community free; Team $75/editor/mo',
    entryEst: false,
    seatModel: 'Per-editor; compute billed separately by kernel-minute',
    embedding: 'Enterprise add-on only; not a core surface; expensive',
    selfHost: 'No — cloud-only SaaS',
    strengths: 'Best-in-class collaborative Python notebook UX; strong AI (Magic AI, agents); broad connectivity',
    weaknesses: 'Per-session cloud kernel scales linearly with concurrent users; embedding expensive and bolt-on; no self-host; SVG viz ceiling ~50k rows',
    sourceUrl: 'https://hex.tech/pricing/',
  },
  {
    name: 'Cube Cloud',
    isNubi: false,
    category: 'Headless Semantic Layer',
    pricingModel: 'Per-developer + hourly CCU infra',
    entryPrice: 'Free hobbyist; Starter ~$40/dev/mo',
    entryEst: false,
    seatModel: 'Developer seats + Cube Consumption Units (CCU) for infra; viewer $20/user/mo (Premium+)',
    embedding: 'Core strength (headless only); JWT→SQL RLS; no built-in viz — bring your own frontend',
    selfHost: 'Yes — Cube Core open source (MIT); production needs Redis + Cube Store cluster',
    strengths: 'Gold-standard headless semantic layer; strong JWT-driven RLS; MIT open core; warehouse-native pre-aggregations',
    weaknesses: 'No built-in viz — must build full frontend; high schema upfront cost (JS/YAML required before any query); CCU billing unpredictable at scale',
    sourceUrl: 'https://cube.dev/pricing',
  },
  {
    name: 'Looker',
    isNubi: false,
    category: 'Enterprise BI (LookML)',
    pricingModel: 'Sales-only (no public price)',
    entryPrice: 'Est. from $60k/year',
    entryEst: true,
    seatModel: 'Per-user; BigQuery compute billed separately on Google Cloud',
    embedding: 'Separate Embed SKU; iFrame + signed URLs; strong JWT RLS; est. $60k+ entry',
    selfHost: 'No — Google Cloud hosted only (since 2019)',
    strengths: 'Strongest governance and audit trail; best LookML ecosystem; Gemini AI (Conversational Analytics, LookML Assistant, Viz Assistant); Google Cloud integrations',
    weaknesses: 'No public pricing; very high LookML modeling tax (est. 40-60% of total investment); cloud-only since 2019; expensive for SMB',
    sourceUrl: 'https://cloud.google.com/looker/pricing',
  },
  {
    name: 'Tableau',
    isNubi: false,
    category: 'Viz Industry Standard',
    pricingModel: 'Per-seat tiered',
    entryPrice: 'Standard Viewer $15/user/mo; Creator $75/user/mo',
    entryEst: false,
    seatModel: 'Every viewer = paid seat; OEM SaaS embedding from $60k–$150k/year',
    embedding: 'Embedding API v3; per-viewer seat cost; OEM from $60k–$150k/year',
    selfHost: 'Yes — Tableau Server; Creator $70/user/mo for on-prem',
    strengths: 'Largest ecosystem; 40+ viz types; Hyper extract for fast in-memory queries; Tableau Pulse automated insights; industry recognition (Gartner MQ leader)',
    weaknesses: 'Per-viewer seat cost makes SaaS embedding expensive; high total contract value; slower innovation pace vs newer tools; no browser compute',
    sourceUrl: 'https://www.tableau.com/pricing/teams-orgs',
  },
  {
    name: 'Power BI',
    isNubi: false,
    category: 'Microsoft Ecosystem BI',
    pricingModel: 'Per-seat or Fabric F-SKU capacity',
    entryPrice: 'Pro $14/user/mo; Fabric F4 ~$400/mo',
    entryEst: false,
    seatModel: 'Pro per-seat ($14/user/mo) or Fabric F-SKU capacity (no per-viewer for F-SKU)',
    embedding: 'App-owns-data via Fabric F-SKU; F4 ~$400/mo covers ~100 concurrent embedded users — good value for Microsoft shops',
    selfHost: 'Yes — Power BI Report Server (requires Premium/SQL Server EE with SA); feature lag vs cloud',
    strengths: 'Deepest Microsoft 365/Teams/Excel integration; Copilot AI (DAX gen, narratives) on F2+ capacity; strong DAX + Power Query M ecosystem; 100+ custom visuals on AppSource; F-SKU removes per-viewer penalty',
    weaknesses: 'Significant value degradation outside Microsoft ecosystem; DAX/M learning curve; feature lag in Report Server vs cloud; complex licensing with multiple tiers',
    sourceUrl: 'https://powerbi.microsoft.com/en-us/pricing/',
  },
  {
    name: 'Apache Superset / Preset',
    isNubi: false,
    category: 'Open-Source BI',
    pricingModel: 'Free OSS; Preset managed cloud per-seat',
    entryPrice: 'Superset OSS free; Preset Pro $20/user/mo',
    entryEst: false,
    seatModel: 'Superset: unlimited (self-manage infra cost); Preset: per-seat cloud; embed add-on $500/mo for 50 viewers',
    embedding: 'iframe + Guest Token (OSS); Preset embed viewer add-on $500/mo for 50 viewers — penalises scale',
    selfHost: 'Yes — Apache Superset is free open-source (Apache 2.0); Preset Enterprise offers managed private cloud',
    strengths: 'Apache 2.0 licence (no AGPL compliance burden); large active community; broad connector support; Preset removes operational burden; no seat limit on self-hosted Superset',
    weaknesses: 'Superset DevOps overhead significant (Redis, Celery, Postgres); Preset embed per-viewer pricing penalises SaaS scale; no in-browser compute; limited AI maturity',
    sourceUrl: 'https://preset.io/pricing/',
  },
  {
    name: 'Count',
    isNubi: false,
    category: 'Data Canvas / Narrative BI',
    pricingModel: 'Per-editor; viewers always free',
    entryPrice: 'Free (3 editors); Pro $49/editor/mo',
    entryEst: false,
    seatModel: 'Editors pay; viewers are always free at every tier — closest to Nubi seat philosophy',
    embedding: 'Limited; Enterprise-only; not a core product surface',
    selfHost: 'No — cloud-only SaaS',
    strengths: 'Viewers genuinely free at every tier (aligns with no-viewer-seat philosophy); distinctive canvas layout for narrative analytics; strong SQL + Python + dbt integration',
    weaknesses: 'Scale tier requires minimum 15 editors ($1,035/mo minimum); embedding is limited and Enterprise-only; cloud-only; no Arrow path; no ZAR billing',
    sourceUrl: 'https://count.co/pricing',
  },
  /* ── EMBEDDED ANALYTICS SPECIALISTS ── */
  {
    name: 'Embeddable',
    isNubi: false,
    category: 'Embedded Analytics SDK',
    pricingModel: 'Session-based flat tiers',
    entryPrice: 'Free (200 sessions/mo); Lite $499/mo (1k sessions)',
    entryEst: false,
    seatModel: 'Unlimited end-users; pricing by dashboard sessions — not per viewer',
    embedding: 'Core product; React/Vue SDK; $499/mo for 1,000 sessions; $200 per additional 500 sessions overage',
    selfHost: 'No — cloud-only SaaS',
    strengths: 'Developer-first SDK; purpose-built for embedding; session-based model is predictable; strong React/Vue component library; good CI/CD workflow support on Premium',
    weaknesses: '$499/mo for only 1,000 sessions is expensive vs Nubi Starter ($9/mo for 1,000 sessions) or Team ($49/mo for 5,000 sessions); steep overage at $200/500 additional sessions; no open-source core; no in-browser compute',
    sourceUrl: 'https://embeddable.com/pricing',
  },
  {
    name: 'Holistics',
    isNubi: false,
    category: 'Embedded Analytics Platform',
    pricingModel: 'Flat platform fee, unlimited viewers',
    entryPrice: '$800/mo annual (Entry); $1,000/mo annual (Standard)',
    entryEst: false,
    seatModel: 'Flat fee includes 10 seats; unlimited embedded viewers at all tiers; extra seats $15–$18/seat/mo',
    embedding: 'Unlimited embedded viewers on flat fee; RLS on SCS tier ($2,000/mo annual); SAML/SCIM',
    selfHost: 'No — cloud-only SaaS',
    strengths: 'Genuinely unlimited embedded viewers at flat fee — the most viewer-generous model in this range; strong RLS passthrough auth; mature ISV track record; unlimited reports on Standard+',
    weaknesses: '$800/mo annual entry is 16× Nubi Team ($49/mo) or 89× Nubi Starter ($9/mo); cloud-only; no in-browser compute; limited AI maturity; no ZAR billing; no self-host',
    sourceUrl: 'https://www.holistics.io/pricing/',
  },
  {
    name: 'Luzmo',
    isNubi: false,
    category: 'Embedded Analytics (MAU)',
    pricingModel: 'Monthly active users (MAU), EUR',
    entryPrice: '€495/mo (~$540) annual Starter',
    entryEst: false,
    seatModel: 'Unlimited registered users; billing based on monthly active users (MAU)',
    embedding: 'Core product; React SDK; white-label on Premium (€1,995/mo annual); AI NL queries (30/user/day on Premium)',
    selfHost: 'No — cloud-only SaaS',
    strengths: 'MAU-based model economical for apps with many registered but low-activity users; strong React SDK and component library; AI NL queries on Premium; good documentation',
    weaknesses: 'EUR-only pricing — no ZAR or Africa support; MAU model can be unpredictable with engagement spikes; Premium at ~$2,175/mo is expensive; no open-source core; no self-host',
    sourceUrl: 'https://www.luzmo.com/pricing',
  },
  {
    name: 'Omni Analytics',
    isNubi: false,
    category: 'Full-Stack BI + Embed (via Explo)',
    pricingModel: 'Sales-only (no public price)',
    entryPrice: 'Est. ~$70/creator/mo; est. ~$420/viewer/yr',
    entryEst: true,
    seatModel: 'Per-creator for internal BI; per-viewer for embedded (estimates, not public)',
    embedding: 'Via Explo acquisition; embedded viewer pricing est. ~$420/user/year; Explo customers migrating',
    selfHost: 'No — cloud-only SaaS',
    strengths: 'Full-stack BI + embedded in one platform; strong SQL + no-code modeling; acquired Explo\'s embedded capabilities and customer base',
    weaknesses: 'No public pricing — requires sales call; per-viewer cost model (est.); Explo customers face migration uncertainty; relatively new combined product; no self-host',
    sourceUrl: 'https://aws.amazon.com/marketplace/pp/prodview-6ohhb7zzk5brq',
  },
  {
    name: 'GoodData',
    isNubi: false,
    category: 'Enterprise Analytics Platform',
    pricingModel: 'Sales-only, workspace-based (no public price)',
    entryPrice: 'Est. ~$1,500+/mo platform fee',
    entryEst: true,
    seatModel: 'Unlimited users; pricing by workspace count; contract-negotiated',
    embedding: 'Core strength; headless InsightView React components; strong multi-tenant; workspace-based; no public price',
    selfHost: 'Yes — GoodData Cloud Native (Kubernetes); commercial license required',
    strengths: 'Longest track record in embedded analytics (founded 2008); HIPAA and FedRAMP compliant; Kubernetes-native for on-prem enterprise; Analytics Lake for federated data; mature multi-tenant architecture',
    weaknesses: 'No public pricing; high implementation complexity; not accessible for SMB/early-stage SaaS; primarily enterprise-focused; pricing requires sales cycle',
    sourceUrl: 'https://www.gooddata.ai/pricing/',
  },
]

const ALL_COL_HEADERS = [
  { label: 'Platform', width: 150 },
  { label: 'Pricing Model', width: 160 },
  { label: 'Entry Price', width: 170 },
  { label: 'Seat / Viewer Model', width: 195 },
  { label: 'Embedding', width: 200 },
  { label: 'Self-Host', width: 140 },
  { label: 'Where this tool is genuinely stronger', width: 195 },
]

/** Category divider row inside the all-competitors table. */
function CategoryRow({ label }) {
  return (
    <tr>
      <td colSpan={ALL_COL_HEADERS.length}
        className="bg-surface-2 px-4 py-2.5 border-b border-border">
        <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.18em] text-brand-teal">
          ── {label}
        </span>
      </td>
    </tr>
  )
}

/**
 * AllCompetitorsTable — the scrupulously fair comparison.
 * Categories: General BI, Embedded Specialists.
 * Columns: Platform, Pricing Model, Entry Price, Seat / Viewer Model,
 *          Embedding, Self-Host, Where competitors are genuinely stronger.
 */
function AllCompetitorsTable() {
  const generalBI = ALL_COMPETITORS.filter(c =>
    ['General BI','Notebooks + Apps','Headless Semantic Layer','Enterprise BI (LookML)',
     'Viz Industry Standard','Microsoft Ecosystem BI','Open-Source BI','Data Canvas / Narrative BI'].includes(c.category)
  )
  const embedded = ALL_COMPETITORS.filter(c =>
    ['Embedded Analytics SDK','Embedded Analytics Platform','Embedded Analytics (MAU)',
     'Full-Stack BI + Embed (via Explo)','Enterprise Analytics Platform',
     'Embedded BI + Flows'].includes(c.category)
  )

  function renderRow(c) {
    return (
      <tr key={c.name} className={`cp-row cp-matrix-row border-b border-border last:border-0 transition-colors`}>
        {/* Platform — Nubi row uses gradient, others use solid dim-cell */}
        <td className={[
          'sticky left-0 z-10 px-4 py-3.5 align-top border-b border-r border-border transition-colors',
          c.isNubi ? 'cp-nubi-col' : 'cp-dim-cell',
        ].join(' ')} style={{ minWidth: 150 }}>
          <span className={`text-xs font-semibold ${c.isNubi ? 'text-brand-teal' : 'text-fg'}`}>
            {c.isNubi && '★ '}{c.name}
          </span>
          <p className="font-mono text-[10px] text-muted mt-0.5 leading-snug">{c.category}</p>
        </td>
        {/* Pricing model */}
        <td className={['cp-row-cell px-4 py-3.5 text-xs align-top border-b border-r border-border leading-relaxed transition-colors',
          c.isNubi ? 'cp-nubi-col font-medium text-fg' : 'bg-surface text-muted'].join(' ')}
          style={{ minWidth: 160 }}>
          {c.pricingModel}
        </td>
        {/* Entry price */}
        <td className={['cp-row-cell px-4 py-3.5 text-xs align-top border-b border-r border-border leading-relaxed transition-colors',
          c.isNubi ? 'cp-nubi-col font-medium text-fg' : 'bg-surface text-muted'].join(' ')}
          style={{ minWidth: 170 }}>
          {c.entryPrice}
          {c.entryEst && <EstBadge />}
        </td>
        {/* Seat / viewer model */}
        <td className={['cp-row-cell px-4 py-3.5 text-xs align-top border-b border-r border-border leading-relaxed transition-colors',
          c.isNubi ? 'cp-nubi-col font-medium text-fg' : 'bg-surface text-muted'].join(' ')}
          style={{ minWidth: 200 }}>
          {c.seatModel}
        </td>
        {/* Embedding */}
        <td className={['cp-row-cell px-4 py-3.5 text-xs align-top border-b border-r border-border leading-relaxed transition-colors',
          c.isNubi ? 'cp-nubi-col font-medium text-fg' : 'bg-surface text-muted'].join(' ')}
          style={{ minWidth: 200 }}>
          {c.embedding}
        </td>
        {/* Self-Host */}
        <td className={['cp-row-cell px-4 py-3.5 text-xs align-top border-b border-r border-border leading-relaxed transition-colors',
          c.isNubi ? 'cp-nubi-col font-medium text-fg' : 'bg-surface text-muted'].join(' ')}
          style={{ minWidth: 140 }}>
          {c.selfHost}
        </td>
        {/* Where they're stronger (honest acknowledgement) */}
        <td className={['cp-row-cell px-4 py-3.5 text-xs align-top border-b border-r border-border last:border-r-0 leading-relaxed transition-colors',
          c.isNubi ? 'cp-nubi-col font-medium text-fg' : 'bg-surface text-muted'].join(' ')}
          style={{ minWidth: 200 }}>
          {c.isNubi
            ? <span className="text-muted italic">{c.weaknesses}</span>
            : c.strengths}
        </td>
      </tr>
    )
  }

  return (
    <div className="cp-table-shell overflow-x-auto overscroll-x-contain rounded-2xl border border-border">
      <table className="border-collapse" style={{ minWidth: 1100, width: '100%' }}>
        <thead className="sticky top-0 z-20">
          <tr>
            {ALL_COL_HEADERS.map((col, i) => (
              i === 0 ? (
                <th key={col.label}
                  className="sticky left-0 z-30 px-4 py-4 text-left bg-surface-2 border-b border-r border-border"
                  style={{ minWidth: col.width, width: col.width }}>
                  <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
                    {col.label}
                  </span>
                </th>
              ) : (
                <th key={col.label}
                  className="px-4 py-4 text-left bg-surface-2 border-b border-r border-border last:border-r-0"
                  style={{ minWidth: col.width }}>
                  <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
                    {col.label}
                  </span>
                </th>
              )
            ))}
          </tr>
        </thead>
        <tbody>
          {/* Nubi row first */}
          {ALL_COMPETITORS.filter(c => c.isNubi).map(renderRow)}

          {/* General BI section */}
          <CategoryRow label="General BI tools" />
          {generalBI.map(renderRow)}

          {/* Embedded analytics section */}
          <CategoryRow label="Embedded analytics specialists" />
          {embedded.map(renderRow)}
        </tbody>
      </table>
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Primary positioning table (Nubi vs Hex vs Cube) — rendered from MD        */
/* ─────────────────────────────────────────────────────────────────────────── */

const PRIMARY_ROWS = [
  {
    label: 'Shape',
    hex:  'Notebook + published apps',
    cube: 'Headless semantic layer + API',
    nubi: 'Batteries-included BI + embed — authoring and output included',
  },
  {
    label: 'Compute kernel',
    hex:  'Python per session, Hex cloud (10–30 s cold, per-minute billing)',
    cube: 'n/a — warehouse + Cube Store; hourly infra billing',
    nubi: 'DuckDB-WASM (SQL) in browser by default; on-demand server (E2B/Modal, scale-to-zero) for Python and heavy workloads',
  },
  {
    label: 'Result transport',
    hex:  'JSON via pandas — no Arrow path',
    cube: 'JSON / SQL API — no Arrow IPC',
    nubi: 'Arrow IPC over WebSocket — columnar, zero-copy to viz',
  },
  {
    label: 'Viz ceiling',
    hex:  'Plotly/SVG — chokes past ~50 k rows',
    cube: 'Bring-your-own frontend; no built-in viz',
    nubi: 'Apache ECharts (canvas) on Arrow buffers — fast on large result sets',
  },
  {
    label: 'Caching',
    hex:  'Per-session; weak cross-user sharing; no auto pre-agg',
    cube: 'Pre-aggs in Cube Store (hand-written schema required)',
    nubi: 'Content-hashed edge cache + automatic pre-aggregations mined from query log',
  },
  {
    label: 'Modeling tax',
    hex:  'Medium — notebook cells; no formal semantic layer',
    cube: 'High — must define cube schema (JS/YAML) before any query works',
    nubi: 'Low — point at a warehouse and go; auth-as-code in repo',
  },
  {
    label: 'Embedding',
    hex:  'Enterprise add-on only; bolt-on auth; not a core surface',
    cube: 'Core strength (headless only); JWT→SQL RLS; viewer seats $20+/month',
    nubi: 'Core product surface: <nubi-dashboard> plus cell-level <nubi-kpi>/<nubi-table>/<nubi-chart>; JWKS-native; no separate SDK',
  },
  {
    label: 'Pricing',
    hex:  'Per-seat + compute add-on (kernels cost real money)',
    cube: 'Per-developer + hourly infra (on top of seats)',
    nubi: 'No per-seat pricing at any tier: Starter $9/mo | Team $49/mo | Pro $149/mo | Enterprise from $1,000/mo. Pay for compute, storage, AI calls, and embed sessions — never for users. Billed in ZAR via Paystack.',
  },
]

function PrimaryTable() {
  return (
    <div className="cp-table-shell overflow-x-auto overscroll-x-contain rounded-2xl border border-border">
      <table className="border-collapse w-full" style={{ minWidth: 640 }}>
        <thead>
          <tr>
            <th className="px-5 py-4 text-left bg-surface-2 border-b border-r border-border"
              style={{ minWidth: 140, width: 140 }}>
              <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">Dimension</span>
            </th>
            <th className="px-5 py-4 text-left bg-surface-2 border-b border-r border-border"
              style={{ minWidth: 200 }}>
              <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">Hex</span>
              <p className="font-mono text-[10px] text-muted mt-0.5 font-normal normal-case tracking-normal opacity-70">
                Notebook + apps
              </p>
            </th>
            <th className="px-5 py-4 text-left bg-surface-2 border-b border-r border-border"
              style={{ minWidth: 200 }}>
              <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">Cube</span>
              <p className="font-mono text-[10px] text-muted mt-0.5 font-normal normal-case tracking-normal opacity-70">
                Headless semantic layer
              </p>
            </th>
            <th className="cp-nubi-header px-5 py-4 text-left border-b border-r border-border last:border-r-0"
              style={{ minWidth: 200 }}>
              <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-brand-teal">
                ★ Nubi
              </span>
              <p className="font-mono text-[10px] text-brand-teal mt-0.5 font-normal normal-case tracking-normal opacity-80">
                Batteries-included BI + embed
              </p>
            </th>
          </tr>
        </thead>
        <tbody>
          {PRIMARY_ROWS.map((row) => (
            <tr key={row.label} className="cp-row cp-matrix-row border-b border-border last:border-0 transition-colors">
              <td className="cp-row-cell px-5 py-4 text-xs font-semibold text-fg align-top bg-surface border-r border-border transition-colors">
                {row.label}
              </td>
              <td className="cp-row-cell px-5 py-4 text-xs text-muted align-top bg-surface border-r border-border transition-colors leading-relaxed">
                {row.hex}
              </td>
              <td className="cp-row-cell px-5 py-4 text-xs text-muted align-top bg-surface border-r border-border transition-colors leading-relaxed">
                {row.cube}
              </td>
              <td className="cp-nubi-col px-5 py-4 text-xs text-fg font-medium align-top leading-relaxed">
                {row.nubi}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Full matrix — content from MATRIX (matrix.md frontmatter)                 */
/* ─────────────────────────────────────────────────────────────────────────── */

function FullMatrix() {
  const dims = COMPARE_DIMENSIONS.length > 0 ? COMPARE_DIMENSIONS : []
  const cols = MATRIX_COLUMNS

  return (
    <div>
      {/* The matrix is intentionally wider than any viewport (14 tools, sticky
          first column) — say so, so the scrollbar reads as designed. */}
      <p className="flex items-center justify-end gap-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-muted mb-2">
        {cols.length} tools · scroll horizontally
        <ArrowRight size={11} strokeWidth={2.5} aria-hidden="true" />
      </p>
      <div className="cp-table-shell overflow-x-auto overscroll-x-contain rounded-2xl border border-border">
      <table className="border-collapse" style={{ minWidth: 1400, width: '100%' }}>
        <thead className="sticky top-0 z-20">
          <tr>
            {/* Dimension label — sticky left + top */}
            <th
              className="sticky left-0 z-30 px-5 py-4 text-left bg-surface-2 border-b border-r border-border"
              style={{ minWidth: 160, width: 160 }}
            >
              <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
                Dimension
              </span>
            </th>
            {cols.map(col => (
              col.isNubi ? (
                <th key={col.key}
                  className="cp-nubi-header px-4 py-4 text-left border-b border-r border-border"
                  style={{ minWidth: 200 }}>
                  <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-brand-teal">
                    ★ {col.label}
                  </span>
                  <p className="font-mono text-[10px] text-brand-teal mt-0.5 font-normal normal-case tracking-normal opacity-80">
                    {col.subtitle}
                  </p>
                </th>
              ) : (
                <th key={col.key}
                  className="px-4 py-4 text-left bg-surface-2 border-b border-r border-border last:border-r-0"
                  style={{ minWidth: 160 }}>
                  <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
                    {col.label}
                  </span>
                  <p className="font-mono text-[10px] text-muted mt-0.5 font-normal normal-case tracking-normal opacity-60">
                    {col.subtitle}
                  </p>
                </th>
              )
            ))}
          </tr>
        </thead>
        <tbody>
          {dims.map(dim => (
            <tr key={dim.key} className="cp-row cp-matrix-row">
              {/* Row label (sticky left) */}
              <td
                className="cp-dim-cell sticky left-0 z-10 px-5 py-3.5 align-top border-b border-r border-border transition-colors"
                style={{ minWidth: 160, width: 160 }}
              >
                <Tooltip text={dim.description}>
                  <span className="text-xs font-semibold text-fg">
                    {dim.label}
                  </span>
                  <Info size={11} className="text-muted opacity-50 shrink-0" />
                </Tooltip>
              </td>
              {/* Data cells */}
              {cols.map(col => {
                const value = MATRIX[dim.key]?.[col.key] ?? '—'
                const est = isEstimate(value)
                return col.isNubi ? (
                  <td key={col.key}
                    className="cp-nubi-col px-4 py-3.5 text-xs align-top border-b border-r border-border leading-relaxed font-medium text-fg"
                    style={{ minWidth: 200 }}>
                    {value}
                  </td>
                ) : (
                  <td key={col.key}
                    className="cp-row-cell px-4 py-3.5 text-xs align-top bg-surface border-b border-r border-border last:border-r-0 leading-relaxed text-muted transition-colors"
                    style={{ minWidth: 160 }}>
                    {value}
                    {est && <EstBadge />}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Competitor card — content from competitors/*.md                            */
/* ─────────────────────────────────────────────────────────────────────────── */

function getPricingChips(text) {
  const chips = []
  if (/free tier|free forever|community free|open source|OSS|free \(|Starter free/i.test(text))
    chips.push({ label: 'Free tier', good: true })
  if (/per.seat|per-seat|\$.*\/user|\$.*\/editor/i.test(text))
    chips.push({ label: 'Per-seat', good: false })
  if (/usage.based|per.view|per.token|per.second|connector bytes/i.test(text))
    chips.push({ label: 'Usage-based', good: true })
  if (/contact sales|no public|negotiated|custom pricing/i.test(text))
    chips.push({ label: 'Contact sales', good: false })
  if (/capacity|F-SKU|SKU/i.test(text))
    chips.push({ label: 'Capacity-based', good: false })
  return chips.slice(0, 3)
}

/** Parse competitor markdown content into sections */
function parseCompetitorSections(content) {
  const sections = { strength: '', limitation: '', notes: '' }
  const parts = content.split(/^##\s+/m)
  for (const part of parts) {
    const lines = part.split('\n')
    const heading = lines[0]?.trim().toLowerCase()
    const body = lines.slice(1).join('\n').trim()
    if (heading?.includes('strength')) sections.strength = body
    else if (heading?.includes('limitation')) sections.limitation = body
    else if (heading?.includes('note')) sections.notes = body
  }
  return sections
}

function CompetitorCard({ competitor, delay = 0 }) {
  const [expanded, setExpanded] = useState(false)
  const chips = getPricingChips(competitor.pricing)
  const selfHostYes = /^Yes/i.test(competitor.selfHost)
  const selfHostNo = /^No/i.test(competitor.selfHost)
  const sections = parseCompetitorSections(competitor.content)

  return (
    <Reveal delay={delay} className="h-full">
      <article className="cp-card h-full overflow-hidden flex flex-col">
        {/* Header */}
        <div className="px-5 pt-5 pb-4 border-b border-border dark:border-white/[0.07]">
          <div className="flex items-start justify-between gap-2 mb-2">
            <div className="flex-1 min-w-0">
              <h3 className="font-display font-semibold text-base text-fg leading-tight truncate">
                {competitor.name}
              </h3>
              <p className="text-[11px] text-muted mt-0.5 leading-snug line-clamp-2">
                {competitor.tagline}
              </p>
            </div>
            <div className="shrink-0">
              {selfHostYes && (
                <span className="inline-flex items-center gap-1 font-mono text-[10px] font-semibold text-brand-teal bg-brand-teal/10 px-2 py-0.5 rounded-full">
                  <CheckCircle2 size={10} strokeWidth={2.5} />
                  Self-host
                </span>
              )}
              {selfHostNo && (
                <span className="inline-flex items-center gap-1 font-mono text-[10px] font-medium text-muted bg-surface-2 px-2 py-0.5 rounded-full border border-border">
                  <XCircle size={10} strokeWidth={2} />
                  Cloud-only
                </span>
              )}
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5 mt-2">
            {chips.map(c => (
              <span key={c.label} className={[
                'inline-flex items-center font-mono text-[10px] font-medium px-2 py-0.5 rounded-full border',
                c.good
                  ? 'text-brand-teal border-brand-teal/30 bg-brand-teal/10'
                  : 'text-muted border-border bg-surface-2',
              ].join(' ')}>
                {c.label}
              </span>
            ))}
          </div>
        </div>

        {/* Strength / Limitation from MD */}
        <div className="px-5 py-4 flex-1 grid gap-3 content-start">
          {sections.strength && (
            <div>
              <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-brand-teal mb-1">
                Strength
              </p>
              <p className="text-xs text-fg leading-relaxed">{sections.strength}</p>
            </div>
          )}
          {sections.limitation && (
            <div>
              <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-muted mb-1">
                Limitation
              </p>
              <p className="text-xs text-muted leading-relaxed">{sections.limitation}</p>
            </div>
          )}
        </div>

        {/* Expandable pricing detail */}
        {expanded && (
          <div className="cp-expand-enter px-5 pb-4 border-t border-border dark:border-white/[0.07] pt-4">
            <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-muted mb-1.5">
              Pricing detail
            </p>
            <p className="text-xs text-muted leading-relaxed">{competitor.pricing}</p>
            {competitor.pricingUnverified && (
              <p className="text-[10px] text-muted/60 mt-2 flex items-center gap-1">
                <Clock size={9} />
                Some pricing data is estimated — re-verify before publishing.
              </p>
            )}
          </div>
        )}

        {/* Footer */}
        <div className="px-5 py-3 border-t border-border dark:border-white/[0.07] flex items-center justify-between gap-2">
          <button
            onClick={() => setExpanded(e => !e)}
            className="inline-flex items-center gap-1 font-mono text-[11px] font-medium text-brand-teal hover:text-brand-teal/80
              focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded transition-colors"
          >
            {expanded
              ? <><ChevronUp size={11} /> Less</>
              : <><ChevronDown size={11} /> Pricing detail</>}
          </button>
          {competitor.sourceUrls?.length > 0 && (
            <div className="flex items-center gap-2 flex-wrap justify-end">
              {competitor.sourceUrls.slice(0, 2).map((url, i) => {
                let label
                try { label = new URL(url).hostname.replace('www.', '') }
                catch { label = `Source ${i + 1}` }
                return (
                  <a key={url} href={url} target="_blank" rel="noopener noreferrer"
                    className="inline-flex items-center gap-0.5 font-mono text-[10px] text-muted hover:text-brand-teal transition-colors">
                    <ExternalLink size={9} />
                    {label}
                  </a>
                )
              })}
            </div>
          )}
        </div>
      </article>
    </Reveal>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  "Why Nubi" bento cards — glass deck on the observatory panel               */
/* ─────────────────────────────────────────────────────────────────────────── */

const WHY_CARDS = [
  {
    index: '01', tag: 'cost architecture', icon: Zap, accent: '#17b3a3',
    title: 'Near-zero marginal cost',
    body: (
      <>
        Compute runs in the viewer&apos;s browser (<B>DuckDB-WASM, SQL</B>). 500 concurrent
        embedded viewers sharing the same dashboard <B>collapse to 1 warehouse hit</B> — the
        advantage is real at high cache-hit rates and extends to diverse workloads via
        automatic pre-aggregations.
      </>
    ),
    chip: '500 viewers → 1 warehouse query',
  },
  {
    index: '02', tag: 'rendering', icon: Layers, accent: '#38bdf8',
    title: 'Arrow IPC end-to-end',
    body: (
      <>
        Results move as <B>columnar Arrow buffers over WebSocket</B>. The viz layer reads
        them directly — no JSON serialisation round-trip. <B>&lt;nubi-chart&gt; renders on
        canvas via Apache ECharts</B>, so charts stay fast and responsive even on large
        result sets.
      </>
    ),
    chip: 'arrow ipc → echarts canvas',
  },
  {
    index: '03', tag: 'security', icon: Shield, accent: '#2456a6',
    title: 'Auth as code',
    body: (
      <>
        Publish your JWKS, implement getToken(), mount &lt;nubi-dashboard&gt;.{' '}
        <B>JWT claims drive row-level security</B> — enforced server-side in the connector
        before any buffer reaches the browser. No separate embed SDK.{' '}
        <B>Policies are TypeScript/SQL in your repo, diffable in PRs.</B>
      </>
    ),
    chip: 'policies reviewed in pull requests',
  },
]

function WhyBentoCard({ card, idx }) {
  const [ref, seen] = useReveal()
  const Icon = card.icon
  return (
    <div
      ref={ref}
      style={{ '--cp-accent': card.accent, transitionDelay: `${(idx % 3) * 90}ms` }}
      className={`lp-reveal ${seen ? 'lp-in' : ''} cp-card flex flex-col p-6 sm:p-7`}
    >
      <div className="flex items-center justify-between mb-4">
        <span
          className="inline-flex items-center justify-center w-10 h-10 rounded-xl text-white shadow-md"
          style={{ background: `linear-gradient(135deg, ${card.accent}, ${card.accent}cc)` }}
        >
          <Icon size={18} strokeWidth={1.9} />
        </span>
        <span className="font-mono text-[11px] font-bold" style={{ color: card.accent }}>
          /{card.index}
        </span>
      </div>
      <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-muted mb-1.5">
        {card.tag}
      </p>
      <h3 className="font-display text-lg sm:text-[1.35rem] font-bold text-fg dark:text-white leading-snug mb-2">
        {card.title}
      </h3>
      <p className="text-[13.5px] sm:text-sm leading-relaxed text-muted dark:text-slate-300/85 flex-1">
        {card.body}
      </p>
      <p
        className="mt-4 inline-flex items-center gap-1.5 self-start font-mono text-[11px] font-semibold px-2.5 py-1.5 rounded-lg border"
        style={{ color: card.accent, borderColor: `${card.accent}45`, background: `${card.accent}0f` }}
      >
        <ArrowRight size={11} strokeWidth={2.5} className="shrink-0" />
        {card.chip}
      </p>
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Main page                                                                  */
/* ─────────────────────────────────────────────────────────────────────────── */

export default function ComparePage() {
  const { data: introData } = INTRO

  return (
    <>
      <MarketingStyles />
      <ScopedStyles />

      <div className="nubi-lp min-h-screen bg-bg text-fg font-sans overflow-x-hidden">

        {/* ══════════════════════════════════════════════════════════
            §1  HERO — observatory panel: copy | real product frame,
            with the structural numbers fused into the panel's lower band
        ══════════════════════════════════════════════════════════ */}
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

            {/* perspective data-grid floor */}
            <svg
              className="lp-hero-grid pointer-events-none absolute inset-x-0 bottom-0 h-[55%] w-full"
              preserveAspectRatio="none"
              viewBox="0 0 1200 400"
              aria-hidden="true"
            >
              <defs>
                <linearGradient id="cp-gridfade" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0" stopColor="#8db4f5" stopOpacity="0" />
                  <stop offset="1" stopColor="#8db4f5" stopOpacity="0.8" />
                </linearGradient>
              </defs>
              {Array.from({ length: 13 }, (_, i) => (
                <line key={`v${i}`} x1={600 + (i - 6) * 100} y1="0" x2={600 + (i - 6) * 260} y2="400" stroke="url(#cp-gridfade)" strokeWidth="1" />
              ))}
              {Array.from({ length: 7 }, (_, i) => (
                <line key={`h${i}`} x1="0" y1={60 + i * 56 + i * i * 2} x2="1200" y2={60 + i * 56 + i * i * 2} stroke="url(#cp-gridfade)" strokeWidth="1" />
              ))}
            </svg>

            {/* film grain */}
            <div className="lp-noise pointer-events-none absolute inset-0" aria-hidden="true" />

            <div className="relative px-5 sm:px-10 lg:px-14 pt-12 sm:pt-16 lg:pt-20">
              <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.12fr] gap-12 lg:gap-14 items-center">

                {/* ── Left: copy ── */}
                <div>
                  {/* terminal-flavoured eyebrow */}
                  <p className="inline-flex items-center gap-2 font-mono text-[11px] sm:text-xs font-medium tracking-wide text-brand-teal dark:text-teal-300/90 border border-border dark:border-white/10 bg-white/60 dark:bg-white/[0.04] rounded-full px-3.5 py-1.5 mb-6 sm:mb-8">
                    <span className="relative flex h-1.5 w-1.5">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-teal-400 opacity-60" />
                      <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-teal-300" />
                    </span>
                    {introData?.eyebrow ?? 'competitive overview · 2026'}
                  </p>

                  <h1 className="font-display text-4xl sm:text-5xl lg:text-[3.9rem] xl:text-[4.3rem] font-bold leading-[1.04] tracking-tight mb-5 sm:mb-7 text-fg">
                    How Nubi
                    <br />
                    <span className="lp-hero-gradient-text">compares.</span>
                  </h1>

                  <p className="text-base sm:text-lg leading-relaxed mb-7 max-w-lg text-muted dark:text-slate-300/90">
                    {introData?.subtitle ?? 'An honest comparison against 14 platforms — Metabase, Hex, Cube, Holistics, Embeddable, Luzmo, Omni, GoodData, Looker, Sigma, Tableau, Power BI, Preset, and Count.'}
                  </p>

                  {/* structural edges — checked claims */}
                  <ul className="grid gap-2.5 mb-8 max-w-lg">
                    {[
                      <><B>Analytics compute runs in the user&apos;s browser</B> by default — marginal cost per embed view ≈&nbsp;$0 at high cache-hit rates.</>,
                      <><B>Unlimited users and viewers at every tier</B> — no per-seat penalty.</>,
                      <><B>ZAR-native billing via Paystack</B> — no competitor prices in ZAR.</>,
                      <><B>Arrow IPC + ECharts canvas rendering</B> keeps large result sets fast in the browser.</>,
                    ].map((line, i) => (
                      <li key={i} className="flex items-start gap-2.5 text-[13.5px] sm:text-sm leading-relaxed text-muted dark:text-slate-300/90">
                        <span className="shrink-0 mt-0.5 inline-flex items-center justify-center w-5 h-5 rounded-full bg-brand-teal/10 border border-brand-teal/30">
                          <Check size={11} strokeWidth={3} className="text-brand-teal dark:text-teal-300" />
                        </span>
                        <span>{line}</span>
                      </li>
                    ))}
                  </ul>

                  {/* CTAs */}
                  <div className="flex flex-col sm:flex-row flex-wrap gap-3 mb-8 sm:mb-9">
                    <Link
                      to="/register"
                      className="lp-cta-glow inline-flex items-center justify-center gap-2 px-7 py-3.5 rounded-xl text-base font-semibold transition-all bg-brand-gradient text-white hover:-translate-y-0.5 min-h-[48px]"
                    >
                      Start free
                      <ArrowRight size={16} strokeWidth={2.5} />
                    </Link>
                    <a
                      href="#matrix"
                      className="inline-flex items-center justify-center gap-2 px-6 py-3.5 rounded-xl text-base font-semibold transition-all bg-surface border border-border text-fg hover:border-brand-blue dark:bg-white/[0.06] dark:border-white/15 dark:text-white dark:hover:bg-white/[0.12] dark:hover:border-white/25 min-h-[48px]"
                    >
                      Jump to the matrix
                    </a>
                  </div>

                  {/* trust strip — mono, data-tool flavour */}
                  <div className="flex flex-wrap gap-x-5 gap-y-2 font-mono text-[11px] font-medium text-muted">
                    {[
                      '≈ $0 marginal cost / embed view',
                      'arrow ipc — zero-copy to charts',
                      'jwks-native auth — no SDK bolt-on',
                      'zar billing via paystack',
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
                        app.nubi.dev/dashboards/view
                      </span>
                      <span className="hidden sm:inline-flex font-mono text-[9.5px] text-brand-teal dark:text-teal-300/80 border border-brand-teal/25 dark:border-teal-400/20 bg-brand-teal/[0.07] dark:bg-teal-400/[0.07] rounded px-1.5 py-0.5">
                        viewers free
                      </span>
                    </div>
                    <img
                      src="/docs/screenshots/dashboard-view.png"
                      alt="A live Nubi dashboard — KPIs, trend lines, and breakdowns rendered by the in-browser kernel"
                      width="2880"
                      height="1800"
                      fetchPriority="high"
                      className="block w-full h-auto dark:hidden"
                    />
                    <img
                      src="/docs/screenshots/dashboard-view-dark.png"
                      alt=""
                      aria-hidden="true"
                      width="2880"
                      height="1800"
                      loading="lazy"
                      className="hidden w-full h-auto dark:block"
                    />
                  </div>

                  {/* floating stat chips */}
                  <div className="lp-float-2 lp-hero-chip absolute -left-3 sm:-left-6 top-20 hidden md:flex items-center gap-2.5 rounded-xl px-3.5 py-2.5">
                    <Zap size={15} className="text-teal-300" />
                    <span className="font-mono text-[11px] leading-tight text-fg dark:text-white">
                      ≈ $0 / embed view
                      <span className="block text-[9.5px] text-muted">kernel runs in the tab</span>
                    </span>
                  </div>
                  <div className="lp-float-3 lp-hero-chip absolute -right-2 sm:-right-5 -bottom-5 hidden md:flex items-center gap-2.5 rounded-xl px-3.5 py-2.5">
                    <Users size={15} className="text-sky-300" />
                    <span className="font-mono text-[11px] leading-tight text-fg dark:text-white">
                      unlimited viewers
                      <span className="block text-[9.5px] text-muted">no per-seat pricing, any tier</span>
                    </span>
                  </div>
                </div>
              </div>

              {/* ── Structural numbers — fused into the panel ── */}
              <div className="relative mt-12 sm:mt-16 lg:mt-20 border-t border-border dark:border-white/10 py-8 sm:py-10">
                <p className="text-center font-mono text-[10.5px] font-semibold tracking-[0.18em] uppercase mb-8 text-muted">
                  The structural numbers — what kernel-in-the-browser actually means
                </p>
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-y-8 divide-x divide-border dark:divide-white/[0.07]">
                  {[
                    { v: '≈ $0', l: 'marginal cost per dashboard view' },
                    { v: '0 s', l: 'cold-start — kernel runs in the tab' },
                    { v: '∞', l: 'users & viewers — no per-seat pricing' },
                    { v: '$0', l: 'competitor prices in ZAR (only Nubi does)' },
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
                <p className="text-center font-mono text-[10px] mt-7 text-muted opacity-70 max-w-2xl mx-auto leading-relaxed">
                  browser compute advantage is real at high cache-hit / pre-aggregation rates · ZAR conversion uses a daily live rate via Paystack · no competitor publishes ZAR pricing as of June 2026
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            §2  TRANSPARENCY NOTICE + FAIRNESS COMMITMENT
        ══════════════════════════════════════════════════════════ */}
        <section className="bg-bg py-10 sm:py-14">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            {/* Fairness commitment note */}
            <Reveal className="mb-5">
              <FairnessNote asOf="June 2026" />
            </Reveal>

            <div className="grid sm:grid-cols-2 gap-4 sm:gap-5">
              <Reveal>
                <div className="cp-card h-full p-5 flex items-start gap-3" style={{ '--cp-accent': '#17b3a3' }}>
                  <span className="shrink-0 mt-0.5 inline-flex items-center justify-center w-8 h-8 rounded-lg bg-brand-teal/10 border border-brand-teal/25">
                    <Info size={14} className="text-brand-teal" />
                  </span>
                  <div>
                    <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-brand-teal mb-1.5">
                      Data transparency
                    </p>
                    <p className="text-xs text-muted leading-relaxed">
                      Competitor data researched June 2026 from{' '}
                      <strong className="text-fg font-semibold">public pricing pages and independent analysts</strong>.
                      Fields marked <EstBadge /> contain estimates. Sources linked on each card below.
                    </p>
                  </div>
                </div>
              </Reveal>
              <Reveal delay={90}>
                <div className="cp-card h-full p-5 flex items-start gap-3" style={{ '--cp-accent': '#2456a6' }}>
                  <span className="shrink-0 mt-0.5 inline-flex items-center justify-center w-8 h-8 rounded-lg bg-brand-blue/10 border border-brand-blue/25">
                    <AlertTriangle size={14} className="text-brand-blue" />
                  </span>
                  <div>
                    <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-brand-blue mb-1.5">
                      Cost claim scope
                    </p>
                    <p className="text-xs text-muted leading-relaxed">
                      The 10–50× advantage is real only at{' '}
                      <strong className="text-fg font-semibold">high cache-hit rates</strong> — e.g.,
                      500 viewers of the same dashboard. For 500 analysts each slicing differently,
                      cache hit rate craters. Auto pre-aggregations extend the advantage to diverse workloads.
                    </p>
                  </div>
                </div>
              </Reveal>
            </div>
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            §3  PRIMARY TABLE — Nubi vs Hex vs Cube
        ══════════════════════════════════════════════════════════ */}
        <section className="py-16 sm:py-24 bg-bg">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">

            <SectionHead
              eyebrow="Primary positioning"
              title={<>Nubi vs <span className="text-brand-gradient">Hex vs Cube.</span></>}
            >
              Hex and Cube bracket the space: <strong className="text-fg font-semibold">Hex is the best
              collaborative notebook</strong>; <strong className="text-fg font-semibold">Cube is the
              gold-standard headless semantic layer</strong>. Nubi is batteries-included BI built for embedding.
            </SectionHead>

            <Reveal>
              <PrimaryTable />
            </Reveal>
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            §3b  ALL-COMPETITORS TABLE — 14 platforms, scrupulously fair
            Covers all platforms from the June 2026 research:
            Metabase, Hex, Cube, Looker, Sigma, Tableau, Power BI,
            Superset/Preset, Count, Embeddable, Holistics, Luzmo,
            Omni, GoodData (+ Nubi)
        ══════════════════════════════════════════════════════════ */}
        <section className="py-16 sm:py-24 bg-surface-2 border-y border-border">
          <div className="max-w-[96rem] mx-auto px-4 sm:px-6 lg:px-8">

            <SectionHead
              wide
              eyebrow="All 14 platforms compared"
              title={<>The full picture — <span className="text-brand-gradient">no cherry-picking.</span></>}
            >
              Every platform covered in the June 2026 research. Pricing from{' '}
              <strong className="text-fg font-semibold">public pages or third-party analysts</strong>{' '}
              (estimated figures marked <EstBadge />). The last column honestly acknowledges{' '}
              <strong className="text-fg font-semibold">where each competitor is genuinely stronger than Nubi</strong>.
            </SectionHead>

            {/* No per-seat callout */}
            <Reveal className="flex justify-center mb-10">
              <div className="cp-card inline-flex items-start sm:items-center gap-3 px-5 py-4 max-w-2xl text-left">
                <span className="shrink-0 inline-flex items-center justify-center w-9 h-9 rounded-xl text-white shadow-md"
                  style={{ background: 'linear-gradient(135deg, #2456a6, #17b3a3)' }}>
                  <Users size={16} strokeWidth={2} />
                </span>
                <span className="text-sm text-muted leading-relaxed">
                  <strong className="font-semibold text-fg">Nubi&apos;s headline differentiator:</strong>{' '}
                  unlimited users and viewers at every tier — no per-seat charge.
                  You pay for compute, storage, AI calls, and embed sessions,{' '}
                  <strong className="font-semibold text-fg">never for headcount</strong>.
                </span>
              </div>
            </Reveal>

            <Reveal>
              <AllCompetitorsTable />
            </Reveal>

            <p className="mt-4 font-mono text-[10.5px] text-muted text-right">
              data as of June 2026 · prices change frequently — verify at each platform&apos;s pricing page before making a decision
            </p>
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            §4  WHY NUBI — bento deck on an observatory panel
        ══════════════════════════════════════════════════════════ */}
        <section className="relative bg-bg px-3 sm:px-5 py-8 sm:py-12">
          <div className="lp-hero-panel relative max-w-[1440px] mx-auto rounded-[1.5rem] sm:rounded-[2rem] overflow-hidden border border-border dark:border-white/[0.06]">
            <div className="lp-noise pointer-events-none absolute inset-0" aria-hidden="true" />
            <div
              className="lp-mesh-blob pointer-events-none absolute -top-32 -right-40 w-[36rem] h-[36rem] rounded-full"
              style={{ background: 'radial-gradient(circle, rgba(45,212,191,0.14) 0%, transparent 65%)' }}
              aria-hidden="true"
            />

            <div className="relative px-5 sm:px-10 lg:px-14 py-12 sm:py-16 lg:py-20">
              <div className="text-center mb-10 sm:mb-14 max-w-2xl mx-auto">
                <p className="font-mono text-[11px] font-semibold tracking-[0.18em] uppercase mb-4 text-brand-teal">
                  Why Nubi
                </p>
                <h2 className="font-display text-3xl sm:text-4xl lg:text-[3.2rem] font-bold leading-[1.08] tracking-tight mb-4 text-fg">
                  {WHY_NUBI.data?.title
                    ? gradientLast(WHY_NUBI.data.title, 'lp-hero-gradient-text')
                    : <>The <span className="lp-hero-gradient-text">structural</span> difference.</>}
                </h2>
                <p className="text-sm sm:text-base lg:text-lg leading-relaxed text-muted dark:text-slate-300/90">
                  {WHY_NUBI.data?.tagline ?? 'Three architectural bets that change what\'s possible — and what it costs.'}
                </p>
              </div>

              <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4 sm:gap-5 mb-5">
                {WHY_CARDS.map((card, i) => (
                  <WhyBentoCard key={card.index} card={card} idx={i} />
                ))}
              </div>

              {/* Honest limitations from why-nubi.md — section after --- */}
              <Reveal className="max-w-4xl mx-auto">
                <div className="cp-card cp-hairline p-6" style={{ '--cp-accent': '#e8a35c' }}>
                  <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-muted mb-3 flex items-center gap-1.5">
                    <AlertTriangle size={11} className="text-brand-teal" />
                    Honest limitations
                  </p>
                  <p className="text-sm text-muted dark:text-slate-300/85 leading-relaxed">
                    The cost advantage is real <B>only at high cache-hit / pre-aggregation rates</B> — 500 analysts each slicing differently reverts to warehouse scans. Browser memory cap (~4 GB) requires aggressive pushdown. The browser only runs SQL (DuckDB-WASM), so Python and native-wheel workloads route to the on-demand server kernel — a launch requirement for those, not optional. NoSQL deliberately out of scope. M10 self-host stack not yet shipped.
                  </p>
                </div>
              </Reveal>
            </div>
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            §5  FULL FEATURE MATRIX — from matrix.md frontmatter
        ══════════════════════════════════════════════════════════ */}
        <section id="matrix" className="py-16 sm:py-24 bg-bg scroll-mt-20">
          <div className="max-w-[96rem] mx-auto px-4 sm:px-6 lg:px-8">

            <SectionHead
              eyebrow="Full feature matrix"
              title={MATRIX_META?.data?.title
                ? gradientLast(MATRIX_META.data.title)
                : <>All tools, <span className="text-brand-gradient">side by side.</span></>}
            >
              {MATRIX_META?.data?.subtitle ?? 'Scroll horizontally to compare all tools across every dimension. Hover row labels for context. Nubi column is highlighted.'}
            </SectionHead>

            <Reveal>
              <FullMatrix />
            </Reveal>

            <p className="mt-4 font-mono text-[10.5px] text-muted text-right">
              researched June 2026 · features and pricing change frequently — verify before publishing
            </p>
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            §6  COMPETITOR CARDS — from competitors/*.md
        ══════════════════════════════════════════════════════════ */}
        <section className="py-16 sm:py-24 bg-surface-2 border-y border-border">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">

            <SectionHead
              eyebrow="Competitor profiles"
              title={<>Know <span className="text-brand-gradient">the field.</span></>}
            >
              <strong className="text-fg font-semibold">Strengths, limitations, pricing model, and source links</strong>{' '}
              for each tool. Expand a card for full pricing detail.
            </SectionHead>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 sm:gap-5">
              {COMPETITORS.map((c, i) => (
                <CompetitorCard key={c.name} competitor={c} delay={(i % 4) * 70} />
              ))}
            </div>
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            §7  WORKFLOW ORCHESTRATION — Nubi Flows vs Prefect/Airflow/Dagster/n8n
            This is a distinct product category from the BI tools above.
        ══════════════════════════════════════════════════════════ */}
        <section className="py-16 sm:py-24 bg-bg">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">

            {/* Category separator — visually signals a new dimension */}
            <Reveal className="flex items-center gap-4 mb-10">
              <div className="flex-1 h-px bg-gradient-to-r from-transparent via-border to-transparent" />
              <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-surface border border-border font-mono text-[10.5px] font-semibold tracking-[0.16em] uppercase text-muted">
                <GitFork size={12} className="text-brand-teal" />
                Different category — workflow orchestration
              </div>
              <div className="flex-1 h-px bg-gradient-to-r from-transparent via-border to-transparent" />
            </Reveal>

            <SectionHead
              eyebrow="Nubi Flows"
              title={<>Workflow <span className="text-brand-gradient">orchestration.</span></>}
            >
              Nubi Flows is a <strong className="text-fg font-semibold">lightweight, LLM-native orchestrator built into the Nubi stack</strong> — a Prefect alternative
              for analytics workflows that need per-user RLS, agent steps, and zero extra infra.
              This is a <strong className="text-fg font-semibold">separate category</strong> from the BI tools above;
              the tools below are orchestrators, not dashboarding products.
            </SectionHead>

            {/* Nubi Flows highlight card */}
            <Reveal className="mb-10">
              <div className="cp-card cp-hairline overflow-hidden" style={{ '--cp-accent': '#17b3a3' }}>
                <div className="px-6 py-5 border-b border-border dark:border-white/[0.07] flex flex-col sm:flex-row sm:items-center gap-4">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-brand-teal">
                        ★ Nubi Flows
                      </span>
                      <span className="inline-flex items-center font-mono text-[10px] font-medium px-2 py-0.5 rounded-full border border-brand-teal/30 bg-brand-teal/10 text-brand-teal">
                        Included in Nubi
                      </span>
                    </div>
                    <h3 className="font-display font-semibold text-base sm:text-lg text-fg">
                      Lightweight, LLM-native workflow orchestrator — no Redis, no Celery
                    </h3>
                  </div>
                  <div className="flex flex-wrap gap-2 shrink-0">
                    {[
                      'Postgres-backed (SKIP LOCKED)',
                      'RLS-aware',
                      'Agent task kind',
                      'React Flow DAG builder',
                    ].map(tag => (
                      <span key={tag} className="inline-flex items-center font-mono text-[10px] font-medium px-2 py-0.5 rounded-full border border-brand-teal/30 bg-brand-teal/10 text-brand-teal">
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-0 divide-y sm:divide-y-0 sm:divide-x divide-border dark:divide-white/[0.07]">
                  {[
                    { label: 'DAG definition', value: 'Declarative JSON FlowSpec + visual React Flow canvas; LLM can author flows in natural language' },
                    { label: 'Execution infra', value: 'Postgres SKIP LOCKED claim worker — no Redis, no Celery, no K8s required' },
                    { label: 'RLS & multi-tenant', value: 'JWT claims flow through to every query/agent task; org-scoped; cross-org returns 404' },
                    { label: 'LLM integration', value: 'Agent task kind natively; AI tools create/run/generate flows; NullProvider keeps tests deterministic' },
                  ].map(({ label, value }) => (
                    <div key={label} className="px-5 py-4">
                      <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-brand-teal mb-1.5">{label}</p>
                      <p className="text-xs text-fg dark:text-slate-200 leading-relaxed">{value}</p>
                    </div>
                  ))}
                </div>
              </div>
            </Reveal>

            {/* Orchestrator comparison table */}
            <Reveal className="mb-10">
              <div className="cp-table-shell overflow-x-auto overscroll-x-contain rounded-2xl border border-border">
                <table className="border-collapse w-full" style={{ minWidth: 700 }}>
                  <thead>
                    <tr>
                      <th className="px-5 py-4 text-left bg-surface-2 border-b border-r border-border" style={{ minWidth: 140, width: 140 }}>
                        <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">Dimension</span>
                      </th>
                      <th className="cp-nubi-header px-5 py-4 text-left border-b border-r border-border" style={{ minWidth: 180 }}>
                        <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-brand-teal">★ Nubi Flows</span>
                        <p className="font-mono text-[10px] text-brand-teal mt-0.5 font-normal normal-case tracking-normal opacity-80">embedded in Nubi</p>
                      </th>
                      {[
                        { key: 'Prefect', subtitle: 'Python decorators' },
                        { key: 'Apache Airflow', subtitle: 'DAG operators' },
                        { key: 'Dagster', subtitle: 'asset-centric' },
                        { key: 'n8n', subtitle: 'visual automation' },
                      ].map(col => (
                        <th key={col.key} className="px-5 py-4 text-left bg-surface-2 border-b border-r border-border last:border-r-0" style={{ minWidth: 160 }}>
                          <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">{col.key}</span>
                          <p className="font-mono text-[10px] text-muted mt-0.5 font-normal normal-case tracking-normal opacity-60">{col.subtitle}</p>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      {
                        label: 'DAG definition',
                        nubi: 'Declarative JSON FlowSpec; visual React Flow canvas; LLM can author flows in NL',
                        Prefect: '@flow/@task Python decorators; code-only; no visual builder',
                        'Apache Airflow': 'Python DAG files with Operators; global-scope instantiation; no visual builder',
                        Dagster: '@asset + @job Python; Software-Defined Assets; Dagit UI (read-only graph)',
                        n8n: 'Visual drag-and-drop node canvas; 400+ pre-built integration nodes',
                      },
                      {
                        label: 'Execution infra',
                        nubi: 'Postgres SKIP LOCKED — no Redis, no Celery, no K8s required',
                        Prefect: 'Postgres metadata DB + customer-managed workers (Docker, K8s, cloud VMs)',
                        'Apache Airflow': 'Postgres + Redis/RabbitMQ (Celery) or Kubernetes; significant DevOps overhead',
                        Dagster: 'Postgres + dagster-daemon + Dagit; no Redis; Dagster+ Serverless $0.01/min',
                        n8n: 'Node.js + Postgres or SQLite; no broker; execution capped by cloud plan tier',
                      },
                      {
                        label: 'RLS / multi-tenant',
                        nubi: 'JWT claims flow to every query/agent task; org-scoped execution; cross-org 404',
                        Prefect: 'None — flows run as a service account; no per-user data isolation',
                        'Apache Airflow': 'None — single execution context; UI RBAC only',
                        Dagster: 'None natively; Dagster+ RBAC per deployment; no JWT-scoped task execution',
                        n8n: 'None — single service credential; Projects for workspace separation only',
                      },
                      {
                        label: 'LLM / agent tasks',
                        nubi: 'Native agent task kind; AI tools create/run/generate flows in NL; MCP-compatible',
                        Prefect: 'No native kind; PythonTask can call any LLM API; no built-in MCP',
                        'Apache Airflow': 'No native kind; PythonOperator + community LLM providers; no built-in MCP',
                        Dagster: 'No native kind; @asset can call LLM APIs; community OpenAI/Anthropic resources',
                        n8n: 'First-class AI Agent + LLM Chain nodes; 12+ LLM providers; AI Workflow Builder',
                      },
                      {
                        label: 'Self-host infra',
                        nubi: 'Runs inside Nubi (uses existing Postgres); no additional broker',
                        Prefect: 'Prefect Server: Postgres only (Apache 2.0). Workers: customer-managed.',
                        'Apache Airflow': 'Postgres + Redis (Celery) or Kubernetes; managed options from ~$300–$1,400/month',
                        Dagster: 'Postgres + dagster-daemon + Dagit (no Redis); Dagster+ cloud available',
                        n8n: 'Docker + Postgres/SQLite; fair-code license; Business license needed for SSO/Git',
                      },
                      {
                        label: 'Pricing',
                        nubi: 'Included in Nubi usage-based pricing; no separate orchestration SKU',
                        Prefect: 'Hobby free; paid from ~$75–$100/month (seat-based); compute separate',
                        'Apache Airflow': 'OSS free; managed from ~$100/month (Astronomer) to $1,400/month (MWAA large)',
                        Dagster: 'OSS free; Dagster+ Solo $10/month + $0.040/credit; Starter $100/month + $0.035/credit',
                        n8n: 'Community free (self-host); Cloud Starter €20/month (2,500 exec); Pro €50/month (10k exec)',
                      },
                    ].map(row => (
                      <tr key={row.label} className="cp-row cp-matrix-row border-b border-border last:border-0 transition-colors">
                        <td className="cp-row-cell px-5 py-4 text-xs font-semibold text-fg align-top bg-surface border-r border-border transition-colors">
                          {row.label}
                        </td>
                        <td className="cp-nubi-col px-5 py-4 text-xs text-fg font-medium align-top leading-relaxed">
                          {row.nubi}
                        </td>
                        {['Prefect', 'Apache Airflow', 'Dagster', 'n8n'].map(tool => (
                          <td key={tool} className="cp-row-cell px-5 py-4 text-xs text-muted align-top bg-surface border-r border-border last:border-r-0 transition-colors leading-relaxed">
                            {row[tool]}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Reveal>

            {/* Orchestrator cards */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 sm:gap-5">
              {ORCHESTRATORS.map((o, i) => (
                <CompetitorCard key={o.name} competitor={o} delay={(i % 4) * 70} />
              ))}
            </div>

            <p className="mt-6 font-mono text-[10.5px] text-muted text-right">
              researched June 2026 · orchestration tooling evolves quickly — verify pricing and features before publishing
            </p>
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            §8  CLOSING CTA — observatory-panel bookend
        ══════════════════════════════════════════════════════════ */}
        <section className="relative bg-bg px-3 sm:px-5 py-8 sm:py-12">
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

            <div className="relative max-w-3xl mx-auto px-5 sm:px-10 py-14 sm:py-20 text-center">
              <p className="font-mono text-[11px] font-semibold tracking-[0.18em] uppercase mb-4 text-brand-teal">
                Ready to try it?
              </p>
              <h2 className="font-display text-3xl sm:text-4xl lg:text-[3.4rem] font-bold leading-[1.08] tracking-tight mb-4 sm:mb-6 text-fg">
                Embed live dashboards
                <br />
                <span className="lp-hero-gradient-text">at near-zero cost.</span>
              </h2>
              <p className="text-sm sm:text-base lg:text-lg leading-relaxed mb-8 sm:mb-10 text-muted dark:text-slate-300/90 max-w-xl mx-auto">
                Connect your warehouse, <B>embed a dashboard in minutes</B>.
                Generous free tier. <B>No credit card required to start.</B>
              </p>

              <div className="flex flex-col sm:flex-row gap-3 justify-center mb-9">
                <Link
                  to="/register"
                  className="lp-cta-glow inline-flex items-center justify-center gap-2 px-7 py-3.5 rounded-xl text-base font-semibold transition-all bg-brand-gradient text-white hover:-translate-y-0.5 min-h-[48px]"
                >
                  Get started free
                  <ArrowRight size={16} strokeWidth={2.5} />
                </Link>
                <Link
                  to="/docs"
                  className="inline-flex items-center justify-center gap-2 px-6 py-3.5 rounded-xl text-base font-semibold transition-all bg-surface border border-border text-fg hover:border-brand-blue dark:bg-white/[0.06] dark:border-white/15 dark:text-white dark:hover:bg-white/[0.12] dark:hover:border-white/25 min-h-[48px]"
                >
                  Read the docs
                </Link>
              </div>

              <div className="flex flex-wrap justify-center gap-x-6 gap-y-2 font-mono text-[11px] font-medium text-muted">
                {[
                  'no credit card required',
                  'unlimited users at every tier',
                  'zar billing via paystack',
                  'self-host connector option',
                  'check primary sources before switching',
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
