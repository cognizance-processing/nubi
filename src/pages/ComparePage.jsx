/**
 * ComparePage — Nubi vs competitors.
 *
 * Content is driven by Markdown files in src/content/compare/
 * loaded via src/compare/registry.js (import.meta.glob eager pattern,
 * mirroring src/docs/registry.js).
 *
 * Design: Editorial/data-forward. Matches LandingPage spacing rhythm,
 * typography, and design-system usage.
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

/* ─────────────────────────────────────────────────────────────────────────── */
/*  Scoped styles                                                               */
/* ─────────────────────────────────────────────────────────────────────────── */

const ScopedStyles = () => (
  <style>{`
    /* Nubi column gradient */
    .cp-nubi-col {
      background: linear-gradient(
        160deg,
        rgba(27,35,99,0.07) 0%,
        rgba(36,86,166,0.07) 50%,
        rgba(23,179,163,0.07) 100%
      );
    }
    .cp-nubi-header {
      background: linear-gradient(
        160deg,
        rgba(27,35,99,0.13) 0%,
        rgba(36,86,166,0.12) 50%,
        rgba(23,179,163,0.12) 100%
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

    /* Card hover lift */
    .cp-card {
      transition: transform 0.22s cubic-bezier(0.34,1.56,0.64,1),
                  box-shadow 0.22s ease,
                  border-color 0.18s ease;
    }
    .cp-card:hover { transform: translateY(-3px); }

    /* Expand animation */
    .cp-expand-enter { animation: cp-expand 0.18s ease forwards; }
    @keyframes cp-expand {
      from { opacity: 0; transform: translateY(-4px); }
      to   { opacity: 1; transform: translateY(0); }
    }

    /* CTA pulse */
    @keyframes cp-pulse-primary {
      0%, 100% { box-shadow: 0 0 0 0 rgba(36, 86, 166, 0.4); }
      50%       { box-shadow: 0 0 0 10px rgba(36, 86, 166, 0); }
    }
    .cp-cta-pulse { animation: cp-pulse-primary 3s ease-in-out infinite; }
    .cp-cta-pulse:hover { animation: none; }

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

function EyebrowBadge({ children }) {
  return (
    <div className="inline-flex items-center gap-2 text-xs font-semibold tracking-widest uppercase
      px-3 py-1.5 rounded-full mb-6 bg-surface-2 border border-border text-muted">
      <span className="w-1.5 h-1.5 rounded-full bg-accent inline-block" />
      {children}
    </div>
  )
}

function EstBadge() {
  return (
    <span className="inline-flex items-center gap-0.5 text-[10px] font-medium px-1.5 py-0.5 rounded-full
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
/*  Hero Illustration (self-contained SVG — no external dep)                  */
/* ─────────────────────────────────────────────────────────────────────────── */

function CompareHeroIllustration() {
  // baseline + bars: competitors neutral, Nubi towers (teal, glowing)
  const baseY = 232
  return (
    <svg
      viewBox="0 0 440 280"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      width="100%"
      height="auto"
      preserveAspectRatio="xMidYMid meet"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id="chi-nubi-bar" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" />
          <stop offset="55%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2456a6" />
        </linearGradient>
        <linearGradient id="chi-glass" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" stopOpacity="0.10" />
          <stop offset="100%" stopColor="#1b2363" stopOpacity="0.03" />
        </linearGradient>
        <linearGradient id="chi-border" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.45" />
          <stop offset="60%" stopColor="#2456a6" stopOpacity="0.25" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.18" />
        </linearGradient>
        <linearGradient id="chi-bolt" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <radialGradient id="chi-bloom" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#17b3a3" stopOpacity="0.32" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0" />
        </radialGradient>
        <filter id="chi-glow" x="-80%" y="-80%" width="260%" height="260%">
          <feGaussianBlur stdDeviation="5" result="b" />
          <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <clipPath id="chi-clip">
          <rect x="12" y="14" width="416" height="252" rx="18" />
        </clipPath>
      </defs>

      {/* premium glass backdrop */}
      <rect x="12" y="14" width="416" height="252" rx="18" fill="url(#chi-glass)" />
      <rect x="12" y="14" width="416" height="252" rx="18" stroke="url(#chi-border)" strokeWidth="1.5" />

      <g clipPath="url(#chi-clip)">
        {/* bloom behind Nubi bar */}
        <ellipse cx="330" cy="150" rx="120" ry="120" fill="url(#chi-bloom)" />

        {/* gridlines + baseline */}
        {[188, 144, 100, 64].map((y, i) => (
          <line key={i} x1="40" y1={y} x2="404" y2={y}
            stroke="#2456a6" strokeWidth="1" strokeOpacity="0.07" strokeDasharray="4 6" />
        ))}
        <line x1="40" y1={baseY} x2="404" y2={baseY} stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.22" />

        {/* Hex bar (neutral) */}
        <rect x="92" y="142" width="58" height={baseY - 142} rx="7" fill="#2456a6" fillOpacity="0.14" />
        <rect x="92" y="142" width="58" height={baseY - 142} rx="7" stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.3" />
        <line x1="104" y1="142" x2="138" y2="142" stroke="#2456a6" strokeOpacity="0.35" strokeWidth="2" strokeLinecap="round" />
        <text x="121" y="252" textAnchor="middle" fontSize="12"
          fontFamily="Space Grotesk, system-ui, sans-serif" fontWeight="600"
          fill="#2456a6" fillOpacity="0.7">Hex</text>

        {/* Cube bar (neutral) */}
        <rect x="194" y="116" width="58" height={baseY - 116} rx="7" fill="#1b2363" fillOpacity="0.12" />
        <rect x="194" y="116" width="58" height={baseY - 116} rx="7" stroke="#1b2363" strokeWidth="1.5" strokeOpacity="0.26" />
        <line x1="206" y1="116" x2="240" y2="116" stroke="#1b2363" strokeOpacity="0.3" strokeWidth="2" strokeLinecap="round" />
        <text x="223" y="252" textAnchor="middle" fontSize="12"
          fontFamily="Space Grotesk, system-ui, sans-serif" fontWeight="600"
          fill="#1b2363" fillOpacity="0.62">Cube</text>

        {/* Nubi bar (towering, glowing) */}
        <g filter="url(#chi-glow)">
          <rect x="300" y="66" width="64" height={baseY - 66} rx="8" fill="url(#chi-nubi-bar)" />
        </g>
        <rect x="300" y="66" width="64" height={baseY - 66} rx="8" stroke="#ffffff" strokeOpacity="0.2" strokeWidth="1.5" />
        <rect x="306" y="72" width="16" height={baseY - 80} rx="4" fill="#ffffff" fillOpacity="0.10" />
        <text x="332" y="252" textAnchor="middle" fontSize="12.5"
          fontFamily="Space Grotesk, system-ui, sans-serif" fontWeight="700"
          fill="#17b3a3">Nubi</text>

        {/* "≈ $0 / view" callout pill — centered over the Nubi bar */}
        <rect x="287" y="30" width="90" height="22" rx="11" fill="#17b3a3" fillOpacity="0.12"
          stroke="#17b3a3" strokeOpacity="0.4" strokeWidth="1" />
        <text x="332" y="45" textAnchor="middle" fontSize="11.5"
          fontFamily="Space Grotesk, system-ui, sans-serif" fontWeight="700" fill="#17b3a3">≈ $0 / view</text>

        {/* lightning badge pinned on the Nubi bar top (cohesive with landing family) */}
        <g filter="url(#chi-glow)">
          <rect x="315" y="56" width="34" height="34" rx="11" fill="url(#chi-bolt)" />
        </g>
        <rect x="315" y="56" width="34" height="34" rx="11" stroke="#ffffff" strokeOpacity="0.35" strokeWidth="1.1" />
        <path d="M 334 63 L 326 75 L 332 75 L 329 83 L 338 70 L 332 70 Z" fill="#ffffff" fillOpacity="0.95" />

        {/* ascending trajectory dots (growth toward Nubi) */}
        {[[58, 196], [88, 176], [120, 152], [154, 130]].map(([cx, cy], i) => (
          <circle key={i} cx={cx} cy={cy} r={2 + i * 0.4}
            fill="#2dd4bf" fillOpacity={0.3 + i * 0.1} />
        ))}
      </g>
    </svg>
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
    nubi: 'Pyodide+DuckDB-WASM in browser by default; on-demand server (E2B/Modal, scale-to-zero) only when needed',
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
    nubi: 'WebGL/WebGPU (regl) on Arrow buffers — 1M+ points at 60 fps',
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
    nubi: 'Core product surface: <nubi-dashboard> → <nubi-widget> → <nubi-editor>; JWKS-native; no separate SDK',
  },
  {
    label: 'Pricing',
    hex:  'Per-seat + compute add-on (kernels cost real money)',
    cube: 'Per-developer + hourly infra (on top of seats)',
    nubi: 'Usage-based: connector bytes + embed views + AI tokens + kernel-seconds; genuine free tier',
  },
]

function PrimaryTable() {
  return (
    <div className="overflow-x-auto rounded-2xl border border-border shadow-sm">
      <table className="border-collapse w-full" style={{ minWidth: 640 }}>
        <thead>
          <tr>
            <th className="px-5 py-4 text-left bg-surface border-b border-r border-border"
              style={{ minWidth: 140, width: 140 }}>
              <span className="text-[10px] font-semibold uppercase tracking-widest text-muted">Dimension</span>
            </th>
            <th className="px-5 py-4 text-left bg-surface border-b border-r border-border"
              style={{ minWidth: 200 }}>
              <span className="text-[10px] font-semibold uppercase tracking-widest text-muted">Hex</span>
              <p className="text-[10px] text-muted mt-0.5 font-normal normal-case tracking-normal opacity-70">
                Notebook + apps
              </p>
            </th>
            <th className="px-5 py-4 text-left bg-surface border-b border-r border-border"
              style={{ minWidth: 200 }}>
              <span className="text-[10px] font-semibold uppercase tracking-widest text-muted">Cube</span>
              <p className="text-[10px] text-muted mt-0.5 font-normal normal-case tracking-normal opacity-70">
                Headless semantic layer
              </p>
            </th>
            <th className="cp-nubi-header px-5 py-4 text-left border-b border-r border-border last:border-r-0"
              style={{ minWidth: 200 }}>
              <span className="text-[10px] font-semibold uppercase tracking-widest text-brand-teal">
                ★ Nubi
              </span>
              <p className="text-[10px] text-brand-teal mt-0.5 font-normal normal-case tracking-normal opacity-80">
                Batteries-included BI + embed
              </p>
            </th>
          </tr>
        </thead>
        <tbody>
          {PRIMARY_ROWS.map((row) => (
            <tr key={row.label} className="cp-row border-b border-border last:border-0 transition-colors">
              <td className="cp-row-cell px-5 py-4 text-xs font-semibold text-muted align-top bg-surface border-r border-border transition-colors">
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
    <div className="overflow-x-auto rounded-2xl border border-border shadow-sm">
      <table className="border-collapse" style={{ minWidth: 1400, width: '100%' }}>
        <thead className="sticky top-0 z-20">
          <tr>
            {/* Dimension label — sticky left + top */}
            <th
              className="sticky left-0 z-30 px-5 py-4 text-left bg-surface-2 border-b border-r border-border"
              style={{ minWidth: 160, width: 160 }}
            >
              <span className="text-[10px] font-semibold uppercase tracking-widest text-muted">
                Dimension
              </span>
            </th>
            {cols.map(col => (
              col.isNubi ? (
                <th key={col.key}
                  className="cp-nubi-header px-4 py-4 text-left border-b border-r border-border"
                  style={{ minWidth: 200 }}>
                  <span className="text-[10px] font-semibold uppercase tracking-widest text-brand-teal">
                    ★ {col.label}
                  </span>
                  <p className="text-[10px] text-brand-teal mt-0.5 font-normal normal-case tracking-normal opacity-80">
                    {col.subtitle}
                  </p>
                </th>
              ) : (
                <th key={col.key}
                  className="px-4 py-4 text-left bg-surface-2 border-b border-r border-border last:border-r-0"
                  style={{ minWidth: 160 }}>
                  <span className="text-[10px] font-semibold uppercase tracking-widest text-muted">
                    {col.label}
                  </span>
                  <p className="text-[10px] text-muted mt-0.5 font-normal normal-case tracking-normal opacity-60">
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

function CompetitorCard({ competitor }) {
  const [expanded, setExpanded] = useState(false)
  const chips = getPricingChips(competitor.pricing)
  const selfHostYes = /^Yes/i.test(competitor.selfHost)
  const selfHostNo = /^No/i.test(competitor.selfHost)
  const sections = parseCompetitorSections(competitor.content)

  return (
    <article className="cp-card bg-surface border border-border rounded-2xl overflow-hidden flex flex-col">
      {/* Header */}
      <div className="px-5 pt-5 pb-4 border-b border-border">
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
              <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-brand-teal bg-brand-teal/10 px-2 py-0.5 rounded-full">
                <CheckCircle2 size={10} strokeWidth={2.5} />
                Self-host
              </span>
            )}
            {selfHostNo && (
              <span className="inline-flex items-center gap-1 text-[10px] font-medium text-muted bg-surface-2 px-2 py-0.5 rounded-full border border-border">
                <XCircle size={10} strokeWidth={2} />
                Cloud-only
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5 mt-2">
          {chips.map(c => (
            <span key={c.label} className={[
              'inline-flex items-center text-[10px] font-medium px-2 py-0.5 rounded-full border',
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
      <div className="px-5 py-4 flex-1 grid gap-3">
        {sections.strength && (
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-brand-teal mb-1">
              Strength
            </p>
            <p className="text-xs text-fg leading-relaxed">{sections.strength}</p>
          </div>
        )}
        {sections.limitation && (
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-muted mb-1">
              Limitation
            </p>
            <p className="text-xs text-muted leading-relaxed">{sections.limitation}</p>
          </div>
        )}
      </div>

      {/* Expandable pricing detail */}
      {expanded && (
        <div className="cp-expand-enter px-5 pb-4 border-t border-border pt-4 bg-surface-2/40">
          <p className="text-[10px] font-semibold uppercase tracking-widest text-muted mb-1.5">
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
      <div className="px-5 py-3 border-t border-border bg-surface-2/60 flex items-center justify-between gap-2">
        <button
          onClick={() => setExpanded(e => !e)}
          className="inline-flex items-center gap-1 text-[11px] font-medium text-brand-teal hover:text-brand-teal/80
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
                  className="inline-flex items-center gap-0.5 text-[10px] text-muted hover:text-brand-teal transition-colors">
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
/*  "Why Nubi" feature cards                                                   */
/* ─────────────────────────────────────────────────────────────────────────── */

function WhyCard({ icon, title, body }) {
  const WhyIcon = icon
  return (
    <div className="cp-card bg-surface border border-border rounded-2xl p-6 flex flex-col gap-4">
      <div className="w-10 h-10 rounded-xl bg-accent/10 text-accent flex items-center justify-center shrink-0">
        <WhyIcon size={20} strokeWidth={1.75} />
      </div>
      <div>
        <h3 className="font-display font-semibold text-base text-fg mb-2">{title}</h3>
        <p className="text-sm text-muted leading-relaxed">{body}</p>
      </div>
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
      <ScopedStyles />

      <div className="min-h-screen bg-bg text-fg font-sans overflow-x-hidden">

        {/* ══════════════════════════════════════════════════════════
            §1  HERO — copy from intro.md frontmatter
        ══════════════════════════════════════════════════════════ */}
        <section className="relative bg-bg">
          <div
            className="pointer-events-none absolute inset-0"
            style={{
              background:
                'radial-gradient(ellipse 55% 60% at 75% 50%, rgba(36,86,166,0.07) 0%, transparent 70%), ' +
                'radial-gradient(ellipse 40% 40% at 15% 70%, rgba(23,179,163,0.05) 0%, transparent 60%)',
            }}
          />

          <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-20 sm:py-28">
            <div className="grid lg:grid-cols-[1fr_1fr] gap-12 lg:gap-20 items-center">

              {/* Left: copy */}
              <div>
                <EyebrowBadge>
                  {introData?.eyebrow ?? 'Competitive overview · 2026'}
                </EyebrowBadge>

                <h1 className="font-display font-bold text-5xl sm:text-6xl lg:text-[4rem] xl:text-[4.5rem]
                  leading-[1.04] tracking-tight mb-6 text-fg">
                  How Nubi{' '}
                  <span className="text-brand-gradient">compares.</span>
                </h1>

                <p className="text-lg sm:text-xl leading-relaxed text-muted mb-6 max-w-lg">
                  {introData?.subtitle ?? 'An honest comparison against Hex, Cube, Metabase, Looker, Sigma, Tableau, Power BI, and Apache Superset.'}
                </p>

                <blockquote className="border-l-2 border-brand-teal pl-4 mb-8">
                  <p className="text-sm text-muted leading-relaxed">
                    <strong className="text-fg font-semibold">Nubi's structural edge:</strong>{' '}
                    The analytics kernel runs in the user's browser by default — so the marginal
                    cost of an embedded view is ≈&nbsp;$0 at high cache-hit rates.
                    Arrow IPC + WebGL handles 1M+ point datasets.
                    No hand-written semantic model required to start.
                  </p>
                </blockquote>

                <div className="flex flex-wrap gap-2.5">
                  {[
                    { icon: <Zap size={12} strokeWidth={2.5} />, text: '≈ $0 marginal cost / embed view' },
                    { icon: <Layers size={12} strokeWidth={2.5} />, text: '1M+ pts at 60 fps (WebGL)' },
                    { icon: <Shield size={12} strokeWidth={2.5} />, text: 'JWKS-native auth — no SDK bolt-on' },
                  ].map(({ icon, text }) => (
                    <span key={text}
                      className="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5
                        bg-surface border border-border rounded-full text-fg shadow-sm">
                      <span className="text-brand-teal">{icon}</span>
                      {text}
                    </span>
                  ))}
                </div>
              </div>

              {/* Right: illustration */}
              <div className="relative">
                <div
                  className="absolute inset-0 -m-6 rounded-3xl pointer-events-none"
                  style={{
                    background:
                      'radial-gradient(ellipse 80% 70% at 50% 50%, rgba(36,86,166,0.09) 0%, transparent 70%)',
                  }}
                />
                <div className="relative bg-surface rounded-2xl border border-border overflow-hidden p-4 shadow-xl">
                  <CompareHeroIllustration />
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            §2  PROOF BAND
        ══════════════════════════════════════════════════════════ */}
        <section className="relative py-14 sm:py-16 bg-brand-gradient overflow-hidden">
          <svg className="absolute inset-0 w-full h-full opacity-5 pointer-events-none" aria-hidden="true">
            <defs>
              <pattern id="cp-dots" x="0" y="0" width="28" height="28" patternUnits="userSpaceOnUse">
                <circle cx="1" cy="1" r="1" fill="white" />
              </pattern>
            </defs>
            <rect width="100%" height="100%" fill="url(#cp-dots)" />
          </svg>

          <div className="relative max-w-6xl mx-auto px-4 sm:px-6 lg:px-8">
            <p className="text-center text-xs font-semibold tracking-widest uppercase mb-10 text-white/50">
              The structural numbers — what kernel-in-the-browser actually means
            </p>
            <div className="grid grid-cols-2 sm:grid-cols-4 divide-x divide-y sm:divide-y-0 divide-white/10">
              {[
                { value: '≈ $0', label: 'marginal cost per dashboard view' },
                { value: '1M+', label: 'data points at 60 fps via WebGL' },
                { value: '10–50×', label: 'cost reduction vs naive warehouse usage¹' },
                { value: '0 s', label: 'cold-start — kernel runs in the tab' },
              ].map(({ value, label }) => (
                <div key={value} className="flex flex-col items-center px-6 py-5">
                  <span className="font-display text-4xl sm:text-5xl font-bold leading-none mb-1.5 text-white">
                    {value}
                  </span>
                  <span className="text-xs sm:text-sm font-medium tracking-wide uppercase text-white/60 text-center max-w-[10rem]">
                    {label}
                  </span>
                </div>
              ))}
            </div>
            <p className="text-center text-xs mt-8 text-white/30">
              ¹ Real at high cache-hit / pre-aggregation rates — e.g. 500 viewers of the same dashboard collapsing to 1 warehouse hit.
            </p>
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            §3  TRANSPARENCY NOTICE — content from caveat.md
        ══════════════════════════════════════════════════════════ */}
        <section className="bg-surface border-b border-border">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
            <div className="flex flex-col sm:flex-row gap-4 sm:gap-8">
              <div className="flex items-start gap-2.5 text-xs text-muted flex-1">
                <Info size={14} className="shrink-0 mt-0.5 text-brand-teal" />
                <p>
                  <span className="font-semibold text-fg">Data transparency: </span>
                  Competitor data researched June 2026 from public pricing pages and independent analysts.
                  Fields marked <EstBadge /> contain estimates. Sources linked on each card below.
                </p>
              </div>
              <div className="flex items-start gap-2.5 text-xs text-muted flex-1">
                <AlertTriangle size={14} className="shrink-0 mt-0.5 text-brand-teal" />
                <p>
                  <span className="font-semibold text-fg">Cost claim scope: </span>
                  The 10–50× advantage is real only at <em>high cache-hit rates</em> — e.g.,
                  500 viewers of the same dashboard. For 500 analysts each slicing differently,
                  cache hit rate craters. Auto pre-aggregations extend the advantage to diverse workloads.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            §4  PRIMARY TABLE — Nubi vs Hex vs Cube
        ══════════════════════════════════════════════════════════ */}
        <section className="py-20 sm:py-24 bg-bg">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">

            <div className="text-center mb-12 max-w-2xl mx-auto">
              <p className="text-xs font-semibold tracking-widest uppercase mb-4 text-brand-teal">
                Primary positioning
              </p>
              <h2 className="font-display font-bold text-4xl sm:text-5xl text-fg mb-4">
                Nubi vs Hex vs Cube
              </h2>
              <p className="text-base text-muted leading-relaxed">
                Hex and Cube bracket the space: Hex is the best collaborative notebook; Cube is the
                gold-standard headless semantic layer. Nubi is batteries-included BI built for embedding.
              </p>
            </div>

            <PrimaryTable />
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            §5  WHY NUBI — three structural differentiators
        ══════════════════════════════════════════════════════════ */}
        <section className="py-20 sm:py-24 bg-surface-2/50 border-t border-border">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">

            <div className="text-center mb-12 max-w-2xl mx-auto">
              <p className="text-xs font-semibold tracking-widest uppercase mb-4 text-brand-teal">
                Why Nubi
              </p>
              <h2 className="font-display font-bold text-4xl sm:text-5xl text-fg mb-4">
                {WHY_NUBI.data?.title ?? 'The structural difference'}
              </h2>
              <p className="text-base text-muted leading-relaxed">
                {WHY_NUBI.data?.tagline ?? 'Three architectural bets that change what\'s possible — and what it costs.'}
              </p>
            </div>

            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5 mb-6">
              <WhyCard
                icon={Zap}
                title="Near-zero marginal cost"
                body="Compute runs in the viewer's browser (Pyodide + DuckDB-WASM). 500 concurrent embedded viewers sharing the same dashboard collapse to 1 warehouse hit — the advantage is real at high cache-hit rates and extends to diverse workloads via automatic pre-aggregations."
              />
              <WhyCard
                icon={Layers}
                title="Arrow IPC end-to-end"
                body="Results move as columnar Arrow buffers over WebSocket. The viz layer reads them directly — no JSON serialisation round-trip. WebGL/WebGPU renders 1M+ points at 60 fps via regl; <nubi-chart> auto-upgrades to WebGL above a configurable row threshold."
              />
              <WhyCard
                icon={Shield}
                title="Auth as code"
                body="Publish your JWKS, implement getToken(), mount <nubi-dashboard>. JWT claims drive row-level security — enforced server-side in the connector before any buffer reaches the browser. No separate embed SDK. Policies are TypeScript/SQL in your repo, diffable in PRs."
              />
            </div>

            {/* Honest limitations from why-nubi.md — section after --- */}
            <div className="max-w-4xl mx-auto bg-surface border border-border rounded-2xl p-6">
              <p className="text-[10px] font-semibold uppercase tracking-widest text-muted mb-3 flex items-center gap-1.5">
                <AlertTriangle size={11} className="text-brand-teal" />
                Honest limitations
              </p>
              <p className="text-sm text-muted leading-relaxed">
                The cost advantage is real <strong className="text-fg">only at high cache-hit / pre-aggregation rates</strong> — 500 analysts each slicing differently reverts to warehouse scans. Browser memory cap (~4 GB) requires aggressive pushdown. Pyodide native-wheel gaps mean on-demand kernel is a launch requirement, not optional. NoSQL deliberately out of scope. M10 self-host stack not yet shipped.
              </p>
            </div>
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            §6  FULL FEATURE MATRIX — from matrix.md frontmatter
        ══════════════════════════════════════════════════════════ */}
        <section className="py-20 sm:py-24 bg-bg border-t border-border">
          <div className="max-w-[96rem] mx-auto px-4 sm:px-6 lg:px-8">

            <div className="text-center mb-12 max-w-2xl mx-auto">
              <p className="text-xs font-semibold tracking-widest uppercase mb-4 text-brand-teal">
                Full feature matrix
              </p>
              <h2 className="font-display font-bold text-4xl sm:text-5xl text-fg mb-4">
                {MATRIX_META?.data?.title ?? 'All tools, side by side'}
              </h2>
              <p className="text-base text-muted leading-relaxed">
                {MATRIX_META?.data?.subtitle ?? 'Scroll horizontally to compare all tools across every dimension. Hover row labels for context. Nubi column is highlighted.'}
              </p>
            </div>

            <FullMatrix />

            <p className="mt-4 text-xs text-muted text-right">
              Researched June 2026 · Features and pricing change frequently — verify before publishing.
            </p>
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            §7  COMPETITOR CARDS — from competitors/*.md
        ══════════════════════════════════════════════════════════ */}
        <section className="py-20 sm:py-24 bg-surface-2/50 border-t border-border">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">

            <div className="text-center mb-12 max-w-2xl mx-auto">
              <p className="text-xs font-semibold tracking-widest uppercase mb-4 text-brand-teal">
                Competitor profiles
              </p>
              <h2 className="font-display font-bold text-4xl sm:text-5xl text-fg mb-4">
                Know the field
              </h2>
              <p className="text-base text-muted leading-relaxed">
                Strengths, limitations, pricing model, and source links for each tool.
                Expand a card for full pricing detail.
              </p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-5">
              {COMPETITORS.map(c => (
                <CompetitorCard key={c.name} competitor={c} />
              ))}
            </div>
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            §8  WORKFLOW ORCHESTRATION — Nubi Flows vs Prefect/Airflow/Dagster/n8n
            This is a distinct product category from the BI tools above.
        ══════════════════════════════════════════════════════════ */}
        <section className="py-20 sm:py-24 bg-bg border-t-4 border-brand-teal/30">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">

            {/* Category separator — visually signals a new dimension */}
            <div className="flex items-center gap-4 mb-10">
              <div className="flex-1 h-px bg-gradient-to-r from-transparent via-border to-transparent" />
              <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-surface border border-border text-[11px] font-semibold tracking-widest uppercase text-muted">
                <GitFork size={12} className="text-brand-teal" />
                Different category — workflow orchestration
              </div>
              <div className="flex-1 h-px bg-gradient-to-r from-transparent via-border to-transparent" />
            </div>

            <div className="text-center mb-12 max-w-2xl mx-auto">
              <p className="text-xs font-semibold tracking-widest uppercase mb-4 text-brand-teal">
                Nubi Flows
              </p>
              <h2 className="font-display font-bold text-4xl sm:text-5xl text-fg mb-4">
                Workflow orchestration
              </h2>
              <p className="text-base text-muted leading-relaxed">
                Nubi Flows is a lightweight, LLM-native orchestrator built into the Nubi stack — a Prefect alternative
                for analytics workflows that need per-user RLS, agent steps, and zero extra infra.
                This is a <strong className="text-fg">separate category</strong> from the BI tools above;
                the tools below are orchestrators, not dashboarding products.
              </p>
            </div>

            {/* Nubi Flows highlight card */}
            <div className="mb-10 rounded-2xl border border-brand-teal/30 bg-surface overflow-hidden"
              style={{ background: 'linear-gradient(160deg, rgba(27,35,99,0.05) 0%, rgba(23,179,163,0.06) 100%)' }}>
              <div className="px-6 py-5 border-b border-brand-teal/20 flex flex-col sm:flex-row sm:items-center gap-4">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-[10px] font-semibold uppercase tracking-widest text-brand-teal">
                      ★ Nubi Flows
                    </span>
                    <span className="inline-flex items-center text-[10px] font-medium px-2 py-0.5 rounded-full border border-brand-teal/30 bg-brand-teal/10 text-brand-teal">
                      Included in Nubi
                    </span>
                  </div>
                  <h3 className="font-display font-semibold text-base text-fg">
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
                    <span key={tag} className="inline-flex items-center text-[10px] font-medium px-2 py-0.5 rounded-full border border-brand-teal/30 bg-brand-teal/10 text-brand-teal">
                      {tag}
                    </span>
                  ))}
                </div>
              </div>
              <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-0 divide-y sm:divide-y-0 sm:divide-x divide-brand-teal/10">
                {[
                  { label: 'DAG definition', value: 'Declarative JSON FlowSpec + visual React Flow canvas; LLM can author flows in natural language' },
                  { label: 'Execution infra', value: 'Postgres SKIP LOCKED claim worker — no Redis, no Celery, no K8s required' },
                  { label: 'RLS & multi-tenant', value: 'JWT claims flow through to every query/agent task; org-scoped; cross-org returns 404' },
                  { label: 'LLM integration', value: 'Agent task kind natively; AI tools create/run/generate flows; NullProvider keeps tests deterministic' },
                ].map(({ label, value }) => (
                  <div key={label} className="px-5 py-4">
                    <p className="text-[10px] font-semibold uppercase tracking-widest text-brand-teal mb-1.5">{label}</p>
                    <p className="text-xs text-fg leading-relaxed">{value}</p>
                  </div>
                ))}
              </div>
            </div>

            {/* Orchestrator comparison table */}
            <div className="overflow-x-auto rounded-2xl border border-border shadow-sm mb-10">
              <table className="border-collapse w-full" style={{ minWidth: 700 }}>
                <thead>
                  <tr>
                    <th className="px-5 py-4 text-left bg-surface border-b border-r border-border" style={{ minWidth: 140, width: 140 }}>
                      <span className="text-[10px] font-semibold uppercase tracking-widest text-muted">Dimension</span>
                    </th>
                    <th className="cp-nubi-header px-5 py-4 text-left border-b border-r border-border" style={{ minWidth: 180 }}>
                      <span className="text-[10px] font-semibold uppercase tracking-widest text-brand-teal">★ Nubi Flows</span>
                      <p className="text-[10px] text-brand-teal mt-0.5 font-normal normal-case tracking-normal opacity-80">embedded in Nubi</p>
                    </th>
                    {[
                      { key: 'Prefect', subtitle: 'Python decorators' },
                      { key: 'Apache Airflow', subtitle: 'DAG operators' },
                      { key: 'Dagster', subtitle: 'asset-centric' },
                      { key: 'n8n', subtitle: 'visual automation' },
                    ].map(col => (
                      <th key={col.key} className="px-5 py-4 text-left bg-surface border-b border-r border-border last:border-r-0" style={{ minWidth: 160 }}>
                        <span className="text-[10px] font-semibold uppercase tracking-widest text-muted">{col.key}</span>
                        <p className="text-[10px] text-muted mt-0.5 font-normal normal-case tracking-normal opacity-60">{col.subtitle}</p>
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
                    <tr key={row.label} className="cp-row border-b border-border last:border-0 transition-colors">
                      <td className="cp-row-cell px-5 py-4 text-xs font-semibold text-muted align-top bg-surface border-r border-border transition-colors">
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

            {/* Orchestrator cards */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
              {ORCHESTRATORS.map(o => (
                <CompetitorCard key={o.name} competitor={o} />
              ))}
            </div>

            <p className="mt-6 text-xs text-muted text-right">
              Researched June 2026 · Orchestration tooling evolves quickly — verify pricing and features before publishing.
            </p>
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            §9  CTA BAND
        ══════════════════════════════════════════════════════════ */}
        <section className="relative py-28 sm:py-36 overflow-hidden bg-surface-2 border-t border-border">
          <div className="absolute top-0 left-0 right-0 h-1 bg-brand-gradient" />

          <svg className="absolute inset-0 w-full h-full opacity-[0.035] pointer-events-none" aria-hidden="true">
            <defs>
              <pattern id="cp-cta-dots" x="0" y="0" width="32" height="32" patternUnits="userSpaceOnUse">
                <circle cx="1" cy="1" r="1.2" fill="currentColor" />
              </pattern>
            </defs>
            <rect width="100%" height="100%" fill="url(#cp-cta-dots)" className="text-brand-blue" />
          </svg>

          <div className="relative max-w-3xl mx-auto px-4 sm:px-6 text-center">
            <p className="text-xs font-semibold tracking-widest uppercase mb-6 text-brand-teal">
              Ready to try it?
            </p>
            <h2 className="font-display font-bold text-4xl sm:text-6xl leading-tight mb-6 text-fg">
              Embed live dashboards
              <br />
              <span className="text-brand-gradient">at near-zero cost.</span>
            </h2>
            <p className="text-base sm:text-lg leading-relaxed mb-10 text-muted max-w-xl mx-auto">
              Connect your warehouse, embed a dashboard in minutes.
              Generous free tier. No credit card required to start.
            </p>

            <div className="flex flex-col sm:flex-row gap-4 justify-center mb-10">
              <Link
                to="/register"
                className="cp-cta-pulse inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl text-base font-semibold transition-all bg-brand-gradient text-white hover:opacity-90 hover:-translate-y-0.5"
              >
                Get started free
                <ArrowRight size={16} strokeWidth={2.5} />
              </Link>
              <Link
                to="/docs"
                className="inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl text-base font-semibold transition-all bg-surface border border-border text-fg hover:border-brand-blue hover:text-brand-blue"
              >
                Read the docs
              </Link>
            </div>

            <div className="flex flex-wrap justify-center gap-x-8 gap-y-2 text-xs font-medium text-muted">
              {[
                'No credit card required',
                'Genuine free tier — no gotchas',
                'Self-host connector option',
                'Check primary sources before switching',
              ].map(f => (
                <span key={f} className="flex items-center gap-1.5">
                  <Check size={10} strokeWidth={3} className="text-brand-teal" />
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
