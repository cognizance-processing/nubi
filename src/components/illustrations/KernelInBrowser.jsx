/**
 * KernelInBrowser — a compute kernel (processor chip) running inside a browser.
 * Browser chrome at top with address bar + traffic lights. Central chip with
 * grid of compute cores, circuit traces radiating outward, pin pads on edges,
 * and a soft teal glow halo. Textless. Reads on white and dark-navy cards.
 */
export default function KernelInBrowser({ className = '' }) {
  // Chip center
  const cx = 210
  const cy = 170

  // Circuit traces: clean, symmetric pin-outs — 3 per edge, evenly spaced.
  const TRACE_OUT = 40            // how far traces extend past the chip edge
  const edgeOff = [-26, 0, 26]    // even offsets along each edge
  const traces = []
  edgeOff.forEach((d) => {
    traces.push({ x1: cx + d, y1: cy - 52, x2: cx + d, y2: cy - 52 - TRACE_OUT }) // top
    traces.push({ x1: cx + d, y1: cy + 52, x2: cx + d, y2: cy + 52 + TRACE_OUT }) // bottom
    traces.push({ x1: cx - 52, y1: cy + d, x2: cx - 52 - TRACE_OUT, y2: cy + d }) // left
    traces.push({ x1: cx + 52, y1: cy + d, x2: cx + 52 + TRACE_OUT, y2: cy + d }) // right
  })

  // One pad dot at each trace's outer end
  const pads = traces.map((t) => ({ cx: t.x2, cy: t.y2 }))

  // Chip internal grid of compute cores (3x3). Center core is the bright kernel;
  // the rest are calm base tiles — no busy inner highlights.
  const cores = []
  for (let row = 0; row < 3; row++) {
    for (let col = 0; col < 3; col++) {
      cores.push({
        x: cx - 30 + col * 30,
        y: cy - 30 + row * 30,
        key: `c${row}${col}`,
        accent: row === 1 && col === 1,   // only the center core glows
      })
    }
  }

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
        {/* Brand signature */}
        <linearGradient id="kib-brand" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="45%" stopColor="#2456a6" />
          <stop offset="80%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2dd4bf" />
        </linearGradient>

        {/* Chip body fill */}
        <linearGradient id="kib-chip-fill" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" />
          <stop offset="50%" stopColor="#1e3b8a" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>

        {/* Core accent fill */}
        <linearGradient id="kib-core-accent" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>

        {/* Core base fill */}
        <linearGradient id="kib-core-base" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" stopOpacity="0.7" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.5" />
        </linearGradient>

        {/* Browser glass panel */}
        <linearGradient id="kib-glass" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" stopOpacity="0.14" />
          <stop offset="100%" stopColor="#1b2363" stopOpacity="0.04" />
        </linearGradient>

        {/* Glass border */}
        <linearGradient id="kib-border" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.5" />
          <stop offset="50%" stopColor="#2456a6" stopOpacity="0.35" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.2" />
        </linearGradient>

        {/* Trace gradient */}
        <linearGradient id="kib-trace" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.7" />
          <stop offset="100%" stopColor="#2456a6" stopOpacity="0.4" />
        </linearGradient>

        {/* Halo bloom */}
        <radialGradient id="kib-halo" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#17b3a3" stopOpacity="0.3" />
          <stop offset="60%" stopColor="#2456a6" stopOpacity="0.1" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.0" />
        </radialGradient>

        {/* Ambient teal top-right */}
        <radialGradient id="kib-amb" cx="75%" cy="22%" r="45%">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.2" />
          <stop offset="100%" stopColor="#2dd4bf" stopOpacity="0.0" />
        </radialGradient>

        {/* Chip glow filter */}
        <filter id="kib-chip-glow" x="-40%" y="-40%" width="180%" height="180%">
          <feDropShadow dx="0" dy="0" stdDeviation="14" floodColor="#17b3a3" floodOpacity="0.5" />
        </filter>

        {/* Card shadow */}
        <filter id="kib-shadow" x="-25%" y="-25%" width="150%" height="150%">
          <feDropShadow dx="0" dy="6" stdDeviation="10" floodColor="#1b2363" floodOpacity="0.22" />
        </filter>

        {/* Safe clip */}
        <clipPath id="kib-safe-clip">
          <rect x="8" y="8" width="404" height="304" rx="24" />
        </clipPath>
      </defs>

      {/* Ambient blooms */}
      <rect x="8" y="8" width="404" height="304" rx="24" fill="url(#kib-amb)" />
      <circle cx={cx} cy={cy} r="130" fill="url(#kib-halo)" />

      <g clipPath="url(#kib-safe-clip)">

        {/* ── Browser window card ── */}
        <g filter="url(#kib-shadow)">
          <rect x="18" y="18" width="384" height="284" rx="20" fill="url(#kib-glass)" />
        </g>
        <rect x="18" y="18" width="384" height="284" rx="20" stroke="url(#kib-border)" strokeWidth="1.5" />
        {/* top highlight */}
        <path d="M 42 19 L 380 19" stroke="#ffffff" strokeOpacity="0.18" strokeWidth="1.5" strokeLinecap="round" />

        {/* Browser chrome bar */}
        <rect x="18" y="18" width="384" height="44" rx="20" fill="url(#kib-glass)" />
        <rect x="18" y="50" width="384" height="12" fill="url(#kib-glass)" />
        {/* Divider line */}
        <line x1="18" y1="62" x2="402" y2="62" stroke="#2456a6" strokeOpacity="0.25" strokeWidth="1.2" />

        {/* Traffic lights */}
        <circle cx="42" cy="40" r="5.5" fill="#2456a6" fillOpacity="0.45" />
        <circle cx="58" cy="40" r="5.5" fill="#2456a6" fillOpacity="0.3" />
        <circle cx="74" cy="40" r="5.5" fill="#17b3a3" fillOpacity="0.55" />

        {/* Address bar */}
        <rect x="100" y="29" width="220" height="22" rx="8" fill="url(#kib-glass)" stroke="#2456a6" strokeOpacity="0.25" strokeWidth="1" />
        {/* Address bar shield icon dots */}
        <circle cx="116" cy="40" r="4" fill="#17b3a3" fillOpacity="0.55" />
        <rect x="126" y="37" width="60" height="5" rx="2.5" fill="#2456a6" fillOpacity="0.3" />
        <rect x="190" y="37" width="30" height="5" rx="2.5" fill="#2456a6" fillOpacity="0.18" />

        {/* Reload + menu icons (right side of chrome) */}
        <circle cx="350" cy="40" r="4.5" stroke="#2456a6" strokeOpacity="0.3" strokeWidth="1.5" />
        <circle cx="366" cy="40" r="4.5" stroke="#2456a6" strokeOpacity="0.3" strokeWidth="1.5" />
        <circle cx="382" cy="40" r="4.5" stroke="#2456a6" strokeOpacity="0.3" strokeWidth="1.5" />

        {/* ── Circuit traces ── */}
        {traces.map((t, i) => (
          <line key={i}
            x1={t.x1} y1={t.y1} x2={t.x2} y2={t.y2}
            stroke="url(#kib-trace)" strokeWidth="1.75" strokeLinecap="round" strokeOpacity="0.7" />
        ))}

        {/* Terminal pads */}
        {pads.map((p, i) => (
          <circle key={i} cx={p.cx} cy={p.cy} r="3"
            fill="url(#kib-core-accent)" fillOpacity="0.7" />
        ))}

        {/* ── Chip body (main chip) ── */}
        <g filter="url(#kib-chip-glow)">
          <rect x={cx - 52} y={cy - 52} width="104" height="104" rx="16"
            fill="url(#kib-chip-fill)" />
        </g>
        {/* Chip border */}
        <rect x={cx - 52} y={cy - 52} width="104" height="104" rx="16"
          stroke="#ffffff" strokeOpacity="0.18" strokeWidth="1.5" />
        {/* Chip top highlight */}
        <path d={`M ${cx - 36} ${cy - 51} L ${cx + 36} ${cy - 51}`}
          stroke="#ffffff" strokeOpacity="0.22" strokeWidth="1.5" strokeLinecap="round" />

        {/* Internal grid lines */}
        <line x1={cx - 52} y1={cy - 16} x2={cx + 52} y2={cy - 16}
          stroke="#2dd4bf" strokeOpacity="0.18" strokeWidth="1" />
        <line x1={cx - 52} y1={cy + 16} x2={cx + 52} y2={cy + 16}
          stroke="#2dd4bf" strokeOpacity="0.18" strokeWidth="1" />
        <line x1={cx - 16} y1={cy - 52} x2={cx - 16} y2={cy + 52}
          stroke="#2dd4bf" strokeOpacity="0.18" strokeWidth="1" />
        <line x1={cx + 16} y1={cy - 52} x2={cx + 16} y2={cy + 52}
          stroke="#2dd4bf" strokeOpacity="0.18" strokeWidth="1" />

        {/* Compute cores (3×3 grid) — clean tiles, center core is the kernel */}
        {cores.map((core) => (
          <rect key={core.key}
            x={core.x - 10} y={core.y - 10} width="20" height="20" rx="5"
            fill={core.accent ? 'url(#kib-core-accent)' : 'url(#kib-core-base)'}
            stroke={core.accent ? '#a5f3ec' : 'none'}
            strokeWidth={core.accent ? 1.25 : 0}
            strokeOpacity="0.6" />
        ))}

        {/* Center kernel pulse — soft glow on the active core */}
        <circle cx={cx} cy={cy} r="13" fill="url(#kib-core-accent)" fillOpacity="0.0" />
        <circle cx={cx} cy={cy} r="20" fill="url(#kib-halo)" />

      </g>
    </svg>
  )
}
