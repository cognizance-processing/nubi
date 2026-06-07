/**
 * EdgeCache — Edge cache + auto pre-aggregation.
 * Many granular requests converge into a glass "cache" module that rolls raw
 * rows up into a few aggregated bars (auto pre-agg), flagged by a lightning
 * edge badge, and serves an instant cached result. Premium glass + glow,
 * grounded in the Hero panel language. Textless. Light + dark safe.
 */
export default function EdgeCache({ className = '' }) {
  // Incoming request nodes (left)
  const inputs = [
    { x: 44, y: 96 },
    { x: 38, y: 132 },
    { x: 38, y: 168 },
    { x: 44, y: 204 },
  ]
  // Convergence point at the module's left edge
  const CX = 168, CY = 150

  // Raw table rows inside the module (granular data)
  const rows = [
    { y: 114, w: 46 }, { y: 124, w: 38 }, { y: 134, w: 44 },
    { y: 144, w: 34 }, { y: 154, w: 42 }, { y: 164, w: 30 },
    { y: 174, w: 40 }, { y: 184, w: 36 },
  ]
  // Aggregated output bars (auto pre-agg result)
  const aggBars = [
    { x: 272, h: 46 }, { x: 300, h: 72 }, { x: 328, h: 58 },
  ]
  const baseY = 194

  return (
    <svg viewBox="0 0 480 300" fill="none" xmlns="http://www.w3.org/2000/svg"
      className={className} aria-hidden="true" width="100%" height="auto"
      preserveAspectRatio="xMidYMid meet">
      <defs>
        {/* Brand signature */}
        <linearGradient id="ec-brand" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="45%" stopColor="#2456a6" />
          <stop offset="80%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2dd4bf" />
        </linearGradient>
        {/* Glass panel */}
        <linearGradient id="ec-glass" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" stopOpacity="0.14" />
          <stop offset="100%" stopColor="#1b2363" stopOpacity="0.05" />
        </linearGradient>
        {/* Glass border */}
        <linearGradient id="ec-border" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.55" />
          <stop offset="60%" stopColor="#2456a6" stopOpacity="0.32" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.2" />
        </linearGradient>
        {/* Aggregated bar fill */}
        <linearGradient id="ec-bar" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.55" />
        </linearGradient>
        {/* Lightning badge fill */}
        <linearGradient id="ec-bolt" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        {/* Node fill */}
        <radialGradient id="ec-node" cx="32%" cy="30%" r="70%">
          <stop offset="0%" stopColor="#a5f3ec" />
          <stop offset="55%" stopColor="#2dd4bf" />
          <stop offset="100%" stopColor="#17b3a3" />
        </radialGradient>
        {/* Source node */}
        <radialGradient id="ec-src" cx="35%" cy="30%" r="70%">
          <stop offset="0%" stopColor="#4a90d4" />
          <stop offset="100%" stopColor="#2456a6" />
        </radialGradient>
        {/* Hub bloom */}
        <radialGradient id="ec-bloom" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#17b3a3" stopOpacity="0.34" />
          <stop offset="55%" stopColor="#2456a6" stopOpacity="0.10" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0" />
        </radialGradient>
        <filter id="ec-shadow" x="-30%" y="-30%" width="160%" height="160%">
          <feDropShadow dx="0" dy="6" stdDeviation="10" floodColor="#1b2363" floodOpacity="0.22" />
        </filter>
        <filter id="ec-glow" x="-80%" y="-80%" width="260%" height="260%">
          <feGaussianBlur stdDeviation="4.5" result="b" />
          <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <clipPath id="ec-clip">
          <rect x="8" y="8" width="464" height="284" rx="22" />
        </clipPath>
      </defs>

      <g clipPath="url(#ec-clip)">
        {/* Ambient bloom behind the cache module */}
        <ellipse cx="258" cy="150" rx="180" ry="140" fill="url(#ec-bloom)" />

        {/* ── Incoming requests (left) — converge into the module ── */}
        {inputs.map((p, i) => (
          <path key={`flow${i}`}
            d={`M ${p.x + 8} ${p.y} C ${(p.x + CX) / 2} ${p.y}, ${CX - 46} ${CY}, ${CX - 8} ${CY}`}
            stroke="url(#ec-brand)" strokeWidth="2" strokeLinecap="round"
            strokeOpacity="0.5" strokeDasharray="5 8" />
        ))}
        {inputs.map((p, i) => (
          <circle key={`src${i}`} cx={p.x} cy={p.y} r="5.5" fill="url(#ec-src)" />
        ))}
        {/* convergence node */}
        <circle cx={CX - 8} cy={CY} r="5.5" fill="url(#ec-node)" />

        {/* ── Cache module (glass panel) ── */}
        <g filter="url(#ec-shadow)">
          <rect x="168" y="78" width="180" height="144" rx="20" fill="url(#ec-glass)" />
        </g>
        <rect x="168" y="78" width="180" height="144" rx="20"
          stroke="url(#ec-border)" strokeWidth="1.75" />
        <path d="M 190 79 L 326 79" stroke="#ffffff" strokeOpacity="0.18"
          strokeWidth="1.5" strokeLinecap="round" />

        {/* Raw table rows (granular data) */}
        {rows.map((r, i) => (
          <line key={`row${i}`} x1="190" y1={r.y} x2={190 + r.w} y2={r.y}
            stroke="#2456a6" strokeOpacity="0.32" strokeWidth="2" strokeLinecap="round" />
        ))}

        {/* Rollup arrow — raw rows → aggregates */}
        <path d="M 244 150 L 256 150 M 251 145 L 257 150 L 251 155"
          stroke="#2dd4bf" strokeOpacity="0.9" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round" />

        {/* Aggregated bars (auto pre-agg result) */}
        <line x1="266" y1={baseY} x2="342" y2={baseY}
          stroke="#2456a6" strokeOpacity="0.22" strokeWidth="1.5" strokeLinecap="round" />
        {aggBars.map((b, i) => (
          <rect key={`agg${i}`} x={b.x} y={baseY - b.h} width="17" height={b.h} rx="4"
            fill="url(#ec-bar)" />
        ))}

        {/* ── Lightning edge badge (top-right corner) ── */}
        <g filter="url(#ec-glow)">
          <rect x="322" y="62" width="38" height="38" rx="11" fill="url(#ec-bolt)" />
        </g>
        <rect x="322" y="62" width="38" height="38" rx="11"
          stroke="#ffffff" strokeOpacity="0.35" strokeWidth="1.25" />
        <path d="M 343 69 L 333 84 L 340 84 L 337 95 L 349 79 L 342 79 Z"
          fill="#ffffff" fillOpacity="0.95" />

        {/* ── Instant serve (right) ── */}
        <line x1="348" y1="150" x2="384" y2="150"
          stroke="url(#ec-bar)" strokeWidth="3" strokeLinecap="round" />
        {/* fast double chevron */}
        <path d="M 388 142 L 396 150 L 388 158 M 398 142 L 406 150 L 398 158"
          stroke="#2dd4bf" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
        {/* served result node */}
        <circle cx="436" cy="150" r="22" fill="url(#ec-bloom)" />
        <g filter="url(#ec-glow)">
          <circle cx="436" cy="150" r="13" fill="url(#ec-node)" />
        </g>
        <circle cx="436" cy="150" r="13" stroke="#ffffff" strokeOpacity="0.5" strokeWidth="1.5" />
        <circle cx="436" cy="150" r="4.5" fill="#ffffff" fillOpacity="0.8" />

        {/* Ambient particles */}
        <circle cx="26" cy="56" r="2.5" fill="#2dd4bf" fillOpacity="0.3" />
        <circle cx="26" cy="248" r="2" fill="#17b3a3" fillOpacity="0.28" />
        <circle cx="258" cy="40" r="2.5" fill="#2dd4bf" fillOpacity="0.26" />
        <circle cx="258" cy="262" r="2" fill="#2456a6" fillOpacity="0.3" />
      </g>
    </svg>
  )
}
