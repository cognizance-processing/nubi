/**
 * HeroIllustration — premium analytics dashboard scene.
 * Multi-panel layout: bold area chart (flagship), KPI ring-stat cards,
 * mini bar chart, subtle grid lines, glowing data nodes, soft depth.
 * Textless. Reads beautifully on white (light) and dark-navy (dark).
 */
export default function HeroIllustration({ className = '' }) {
  // Area chart path (bold, sweeping curve)
  const areaCurve =
    'M 52 196 C 90 188, 112 172, 148 162 C 188 150, 210 164, 248 140 C 288 114, 312 122, 348 88'
  const areaFill = `${areaCurve} L 348 220 L 52 220 Z`

  // Mini bar heights
  const bars = [42, 62, 50, 76, 58, 80]

  return (
    <svg
      viewBox="0 0 420 320"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
      width="100%"
      height="auto"
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        {/* Brand signature gradient */}
        <linearGradient id="hero-brand" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="45%" stopColor="#2456a6" />
          <stop offset="80%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2dd4bf" />
        </linearGradient>

        {/* Chart line gradient */}
        <linearGradient id="hero-line-grad" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#2456a6" />
          <stop offset="55%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2dd4bf" />
        </linearGradient>

        {/* Area fill — vertical fade */}
        <linearGradient id="hero-area-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.38" />
          <stop offset="50%" stopColor="#17b3a3" stopOpacity="0.14" />
          <stop offset="100%" stopColor="#2456a6" stopOpacity="0.0" />
        </linearGradient>

        {/* Glass panel fill */}
        <linearGradient id="hero-glass" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" stopOpacity="0.13" />
          <stop offset="100%" stopColor="#1b2363" stopOpacity="0.05" />
        </linearGradient>

        {/* Glass border */}
        <linearGradient id="hero-border" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.55" />
          <stop offset="60%" stopColor="#2456a6" stopOpacity="0.35" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.2" />
        </linearGradient>

        {/* KPI panel */}
        <linearGradient id="hero-kpi" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" stopOpacity="0.18" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.08" />
        </linearGradient>

        {/* Teal chip accent */}
        <linearGradient id="hero-chip" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>

        {/* Ambient bloom top-right */}
        <radialGradient id="hero-bloom-tr" cx="80%" cy="28%" r="52%">
          <stop offset="0%" stopColor="#17b3a3" stopOpacity="0.28" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.0" />
        </radialGradient>

        {/* Ambient bloom bottom-left */}
        <radialGradient id="hero-bloom-bl" cx="18%" cy="82%" r="44%">
          <stop offset="0%" stopColor="#2456a6" stopOpacity="0.22" />
          <stop offset="100%" stopColor="#2456a6" stopOpacity="0.0" />
        </radialGradient>

        {/* Node glow */}
        <radialGradient id="hero-node-glow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.9" />
          <stop offset="100%" stopColor="#2dd4bf" stopOpacity="0.0" />
        </radialGradient>

        {/* Bar gradient */}
        <linearGradient id="hero-bar" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.75" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.3" />
        </linearGradient>

        {/* Soft shadow */}
        <filter id="hero-shadow" x="-30%" y="-30%" width="160%" height="160%">
          <feDropShadow dx="0" dy="6" stdDeviation="10" floodColor="#1b2363" floodOpacity="0.2" />
        </filter>

        {/* Strong glow for peak node */}
        <filter id="hero-glow" x="-80%" y="-80%" width="260%" height="260%">
          <feGaussianBlur stdDeviation="5" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>

        {/* Safe clip inset */}
        <clipPath id="hero-safe-clip">
          <rect x="8" y="8" width="404" height="304" rx="24" />
        </clipPath>
      </defs>

      {/* Ambient background blooms */}
      <rect x="8" y="8" width="404" height="304" rx="24" fill="url(#hero-bloom-tr)" />
      <rect x="8" y="8" width="404" height="304" rx="24" fill="url(#hero-bloom-bl)" />

      <g clipPath="url(#hero-safe-clip)">

        {/* ── Main dashboard card ── */}
        <g filter="url(#hero-shadow)">
          <rect x="18" y="20" width="384" height="282" rx="20" fill="url(#hero-glass)" />
        </g>
        <rect x="18" y="20" width="384" height="282" rx="20" stroke="url(#hero-border)" strokeWidth="1.5" />
        {/* top highlight line */}
        <path d="M 42 21 L 380 21" stroke="#ffffff" strokeOpacity="0.18" strokeWidth="1.5" strokeLinecap="round" />

        {/* ── Horizontal grid lines (subtle) ── */}
        {[96, 124, 152, 180, 208].map((y) => (
          <line key={y} x1="44" y1={y} x2="376" y2={y}
            stroke="#2456a6" strokeOpacity="0.12" strokeWidth="1" strokeDasharray="4 6" />
        ))}

        {/* ── Area chart ── */}
        <path d={areaFill} fill="url(#hero-area-fill)" />
        <path d={areaCurve}
          stroke="url(#hero-line-grad)" strokeWidth="3.5"
          strokeLinecap="round" strokeLinejoin="round" />

        {/* Chart baseline */}
        <line x1="44" y1="220" x2="376" y2="220"
          stroke="#2456a6" strokeOpacity="0.22" strokeWidth="1.5" />

        {/* Tick marks on baseline */}
        {[100, 148, 200, 248, 298, 348].map((x) => (
          <line key={x} x1={x} y1="220" x2={x} y2="227"
            stroke="#2456a6" strokeOpacity="0.3" strokeWidth="1.5" strokeLinecap="round" />
        ))}

        {/* ── Chart data nodes ── */}
        {/* Node at 148,162 */}
        <circle cx="148" cy="162" r="18" fill="url(#hero-node-glow)" opacity="0.4" />
        <circle cx="148" cy="162" r="5.5" fill="#17b3a3" stroke="#ffffff" strokeWidth="2" strokeOpacity="0.8" />

        {/* Node at 248,140 */}
        <circle cx="248" cy="140" r="16" fill="url(#hero-node-glow)" opacity="0.35" />
        <circle cx="248" cy="140" r="5" fill="#17b3a3" stroke="#ffffff" strokeWidth="2" strokeOpacity="0.8" />

        {/* Peak node at 348,88 — glowing accent */}
        <circle cx="348" cy="88" r="30" fill="url(#hero-node-glow)" opacity="0.45" />
        <circle cx="348" cy="88" r="10" fill="url(#hero-chip)" />
        <circle cx="348" cy="88" r="10" stroke="#ffffff" strokeWidth="2.5" strokeOpacity="0.85" />
        {/* inner dot */}
        <circle cx="348" cy="88" r="4" fill="#ffffff" fillOpacity="0.7" />

        {/* Vertical drop line from peak */}
        <line x1="348" y1="98" x2="348" y2="220"
          stroke="#2dd4bf" strokeOpacity="0.2" strokeWidth="1.5" strokeDasharray="3 5" />

        {/* ── KPI stat chips (top-left area) ── */}
        {/* KPI card 1 */}
        <g filter="url(#hero-shadow)">
          <rect x="30" y="32" width="110" height="64" rx="14" fill="url(#hero-kpi)" />
        </g>
        <rect x="30" y="32" width="110" height="64" rx="14" stroke="url(#hero-border)" strokeWidth="1.2" />
        {/* Arc / ring indicator */}
        <circle cx="62" cy="64" r="18" stroke="#2456a6" strokeOpacity="0.25" strokeWidth="3" />
        <path d="M 62 46 A 18 18 0 1 1 44.5 72"
          stroke="url(#hero-chip)" strokeWidth="3" strokeLinecap="round" fill="none" />
        <circle cx="62" cy="64" r="6" fill="url(#hero-chip)" />
        {/* Bar strip */}
        {[0, 1, 2].map((i) => (
          <rect key={i} x={92 + i * 12} y={60 - i * 6} width="8" height={12 + i * 6} rx="2.5"
            fill="url(#hero-chip)" fillOpacity={0.55 + i * 0.15} />
        ))}

        {/* KPI card 2 */}
        <g filter="url(#hero-shadow)">
          <rect x="152" y="32" width="96" height="56" rx="14" fill="url(#hero-kpi)" />
        </g>
        <rect x="152" y="32" width="96" height="56" rx="14" stroke="url(#hero-border)" strokeWidth="1.2" />
        {/* Sparkline */}
        <polyline
          points="164,74 176,66 190,70 204,58 218,62 232,50 238,54"
          stroke="url(#hero-line-grad)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
          fill="none" />
        <circle cx="238" cy="54" r="3.5" fill="#2dd4bf" />

        {/* ── Mini bar chart (bottom-left) ── */}
        <g filter="url(#hero-shadow)">
          <rect x="30" y="238" width="140" height="56" rx="14" fill="url(#hero-kpi)" />
        </g>
        <rect x="30" y="238" width="140" height="56" rx="14" stroke="url(#hero-border)" strokeWidth="1.2" />
        {bars.map((h, i) => (
          <rect key={i} x={44 + i * 19} y={282 - h * 0.45} width="12" height={h * 0.45} rx="3"
            fill="url(#hero-bar)" />
        ))}

        {/* ── Connector lines between KPI chips ── */}
        <line x1="140" y1="64" x2="152" y2="60"
          stroke="#2dd4bf" strokeOpacity="0.35" strokeWidth="1.5" strokeDasharray="3 4" />

        {/* ── Decorative dot grid (far right) ── */}
        {[0, 1, 2, 3].map((row) =>
          [0, 1, 2].map((col) => (
            <circle key={`${row}-${col}`}
              cx={370 - col * 14} cy={250 + row * 14}
              r="2" fill="#2456a6" fillOpacity="0.25" />
          ))
        )}

        {/* ── Secondary accent line (right panel divider) ── */}
        <line x1="260" y1="238" x2="260" y2="290"
          stroke="#17b3a3" strokeOpacity="0.18" strokeWidth="1" />

        {/* Donut ring accent (bottom-right) */}
        <circle cx="320" cy="264" r="28" stroke="#2456a6" strokeOpacity="0.2" strokeWidth="6" fill="none" />
        <path d="M 320 236 A 28 28 0 0 1 344 276"
          stroke="url(#hero-chip)" strokeWidth="6" strokeLinecap="round" fill="none" />
        <circle cx="320" cy="264" r="10" fill="url(#hero-glass)" stroke="url(#hero-border)" strokeWidth="1.5" />
        <circle cx="320" cy="264" r="4" fill="url(#hero-chip)" />

        {/* ── Three tiny status dots (top decorative) ── */}
        <circle cx="358" cy="38" r="4" fill="#2dd4bf" fillOpacity="0.65" />
        <circle cx="372" cy="38" r="4" fill="#17b3a3" fillOpacity="0.45" />
        <circle cx="386" cy="38" r="4" fill="#2456a6" fillOpacity="0.35" />

      </g>
    </svg>
  )
}
