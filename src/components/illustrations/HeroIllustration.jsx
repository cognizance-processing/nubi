/**
 * HeroIllustration — Large, rich depiction of a browser running a live Nubi dashboard.
 * Features: browser chrome, sidebar, KPI cards, bar chart, line chart, scatter plot,
 * Arrow IPC indicator, SQL bar, kernel badge.
 * viewBox 800×560 — parent controls displayed size (should be ~480px+ tall on desktop).
 * Uses brand gradient navy→blue→teal as defining motif.
 */
export default function HeroIllustration({ className = '' }) {
  return (
    <svg
      viewBox="0 0 800 560"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
      style={{ width: '100%', height: 'auto' }}
    >
      <defs>
        {/* Brand gradient: navy → blue → teal */}
        <linearGradient id="h-brand" x1="0" y1="0" x2="800" y2="560" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="50%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="h-brand-h" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="50%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        {/* Browser shell bg */}
        <linearGradient id="h-shell" x1="0" y1="0" x2="0" y2="560" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#0d1526" />
          <stop offset="100%" stopColor="#0a1020" />
        </linearGradient>
        {/* Card bg */}
        <linearGradient id="h-card" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#111a2e" />
          <stop offset="100%" stopColor="#0d1526" />
        </linearGradient>
        {/* Bar chart gradient */}
        <linearGradient id="h-bar" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#1b2363" stopOpacity="0.4" />
        </linearGradient>
        <linearGradient id="h-bar-hi" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2456a6" stopOpacity="0.5" />
        </linearGradient>
        {/* Sparkline area */}
        <linearGradient id="h-area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.35" />
          <stop offset="100%" stopColor="#2dd4bf" stopOpacity="0.02" />
        </linearGradient>
        {/* Sidebar bg */}
        <linearGradient id="h-sidebar" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#080e1c" />
          <stop offset="100%" stopColor="#0c1422" />
        </linearGradient>
        {/* Glow filters */}
        <filter id="h-glow-teal" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="4" result="blur" />
          <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <filter id="h-glow-blue" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="6" result="blur" />
          <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <filter id="h-shadow" x="-10%" y="-10%" width="120%" height="130%">
          <feDropShadow dx="0" dy="8" stdDeviation="20" floodColor="#1b2363" floodOpacity="0.5" />
        </filter>
        {/* Clip browser content */}
        <clipPath id="h-browser-clip">
          <rect x="18" y="48" width="764" height="494" rx="4" />
        </clipPath>
        {/* Clip for chart area */}
        <clipPath id="h-chart-clip">
          <rect x="212" y="196" width="310" height="178" />
        </clipPath>
        <clipPath id="h-scatter-clip">
          <rect x="536" y="196" width="230" height="178" />
        </clipPath>
        {/* Grid pattern */}
        <pattern id="h-grid" x="0" y="0" width="40" height="40" patternUnits="userSpaceOnUse">
          <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#2456a6" strokeOpacity="0.04" strokeWidth="0.5" />
        </pattern>
        {/* Marker arrows */}
        <marker id="h-arrow" markerWidth="6" markerHeight="6" refX="3" refY="3" orient="auto">
          <path d="M 0 0 L 6 3 L 0 6 Z" fill="#2dd4bf" fillOpacity="0.7" />
        </marker>
      </defs>

      {/* ── Outer glow / atmosphere ── */}
      <ellipse cx="400" cy="280" rx="380" ry="260" fill="#2456a6" fillOpacity="0.06" />

      {/* ── Browser shell ── */}
      <rect x="8" y="8" width="784" height="544" rx="14" fill="url(#h-shell)" filter="url(#h-shadow)" />
      <rect x="8" y="8" width="784" height="544" rx="14" stroke="url(#h-brand)" strokeOpacity="0.6" strokeWidth="1.5" fill="none" />

      {/* Grid texture overlay */}
      <rect x="8" y="8" width="784" height="544" rx="14" fill="url(#h-grid)" />

      {/* ── Title bar ── */}
      <rect x="8" y="8" width="784" height="40" rx="14" fill="#070d1a" />
      <rect x="8" y="34" width="784" height="14" fill="#070d1a" />

      {/* Traffic lights */}
      <circle cx="36" cy="28" r="6.5" fill="#ff5f57" fillOpacity="0.9" />
      <circle cx="57" cy="28" r="6.5" fill="#febc2e" fillOpacity="0.9" />
      <circle cx="78" cy="28" r="6.5" fill="#28c840" fillOpacity="0.9" />

      {/* URL bar */}
      <rect x="110" y="16" width="420" height="24" rx="12" fill="#0c1828" stroke="#2456a6" strokeOpacity="0.4" strokeWidth="0.75" />
      {/* Lock icon */}
      <path d="M 124 28 m -4 0 a 4 4 0 0 1 8 0 v 3 h -8 Z" fill="#17b3a3" fillOpacity="0.7" />
      <text x="136" y="32.5" fill="#2dd4bf" fillOpacity="0.85" fontSize="10" fontFamily="monospace">app.nubi.dev/d/revenue-overview</text>

      {/* Reload / nav icons */}
      <circle cx="564" cy="28" r="9" fill="#0c1828" stroke="#2456a6" strokeOpacity="0.2" strokeWidth="0.5" />
      <circle cx="584" cy="28" r="9" fill="#0c1828" stroke="#2456a6" strokeOpacity="0.2" strokeWidth="0.5" />

      {/* ── Dashboard content ── */}
      <g clipPath="url(#h-browser-clip)">

        {/* ── Sidebar ── */}
        <rect x="18" y="48" width="178" height="494" fill="url(#h-sidebar)" />
        <rect x="194" y="48" width="1" height="494" fill="url(#h-brand-h)" fillOpacity="0.2" />

        {/* Logo in sidebar */}
        <text x="38" y="82" fill="url(#h-brand)" fontSize="15" fontWeight="700" fontFamily="'Space Grotesk', sans-serif" letterSpacing="1">nubi</text>
        <rect x="38" y="88" width="34" height="2" rx="1" fill="url(#h-brand-h)" fillOpacity="0.6" />

        {/* Sidebar nav items */}
        {[
          { y: 116, label: 'Overview', icon: '▦', active: true },
          { y: 148, label: 'Revenue', icon: '◈', active: false },
          { y: 180, label: 'Users', icon: '◎', active: false },
          { y: 212, label: 'Queries', icon: '◧', active: false },
          { y: 244, label: 'Connectors', icon: '⬡', active: false },
          { y: 276, label: 'Settings', icon: '⚙', active: false },
        ].map(({ y, label, icon, active }) => (
          <g key={label}>
            {active && (
              <rect x="24" y={y - 10} width="158" height="28" rx="6"
                fill="#2456a6" fillOpacity="0.18" />
            )}
            {active && (
              <rect x="24" y={y - 10} width="3" height="28" rx="1.5"
                fill="url(#h-brand-h)" />
            )}
            <text x="40" y={y + 7} fill={active ? '#2dd4bf' : '#4a6fa5'} fontSize="10" fontFamily="monospace">{icon}</text>
            <text x="58" y={y + 7} fill={active ? '#e7edf6' : '#4a6fa5'} fontSize="11" fontFamily="'Inter', sans-serif">
              {label}
            </text>
          </g>
        ))}

        {/* Workspace section */}
        <text x="38" y="360" fill="#2456a6" fillOpacity="0.5" fontSize="8" fontFamily="'Inter', sans-serif" letterSpacing="1">WORKSPACE</text>
        {[
          { y: 378, label: 'Revenue Q2' },
          { y: 396, label: 'User Cohorts' },
          { y: 414, label: 'Query Perf' },
        ].map(({ y, label }) => (
          <g key={label}>
            <circle cx="44" cy={y - 2} r="2" fill="#2456a6" fillOpacity="0.4" />
            <text x="52" y={y + 2} fill="#4a6fa5" fillOpacity="0.7" fontSize="10" fontFamily="'Inter', sans-serif">{label}</text>
          </g>
        ))}

        {/* Kernel status badge in sidebar */}
        <rect x="28" y="460" width="148" height="48" rx="8"
          fill="#0d1526" stroke="#17b3a3" strokeOpacity="0.4" strokeWidth="0.75" />
        <circle cx="46" cy="476" r="5" fill="#17b3a3" fillOpacity="0.25" />
        <circle cx="46" cy="476" r="3" fill="#2dd4bf" filter="url(#h-glow-teal)" />
        <text x="56" y="479" fill="#2dd4bf" fontSize="9" fontWeight="600" fontFamily="'Inter', sans-serif">Kernel active</text>
        <text x="36" y="499" fill="#17b3a3" fillOpacity="0.6" fontSize="8" fontFamily="monospace">Pyodide + DuckDB-WASM</text>
        <text x="36" y="510" fill="#17b3a3" fillOpacity="0.4" fontSize="8" fontFamily="monospace">≈ $0 / view · in-browser</text>

        {/* ── Main canvas ── */}
        <rect x="195" y="48" width="587" height="494" fill="#0a1020" />

        {/* ── Top bar ── */}
        <rect x="195" y="48" width="587" height="42" fill="#080e1c" />
        <text x="215" y="75" fill="#e7edf6" fontSize="14" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">Revenue Overview</text>

        {/* Date range pill */}
        <rect x="590" y="58" width="90" height="22" rx="11" fill="#111a2e" stroke="#2456a6" strokeOpacity="0.4" strokeWidth="0.75" />
        <text x="635" y="73" textAnchor="middle" fill="#93a4bd" fontSize="9" fontFamily="'Inter', sans-serif">Apr – Jun 2025</text>

        {/* Add chart button */}
        <rect x="688" y="58" width="80" height="22" rx="11"
          fill="#2456a6" fillOpacity="0.2" stroke="#2456a6" strokeOpacity="0.5" strokeWidth="0.75" />
        <text x="728" y="73" textAnchor="middle" fill="#4d8de0" fontSize="9" fontWeight="600" fontFamily="'Inter', sans-serif">+ New chart</text>

        {/* ── KPI cards row ── */}
        {[
          { x: 215, label: 'MRR', value: '$48.2K', delta: '+12.4%', up: true },
          { x: 370, label: 'Active Users', value: '1.24M', delta: '+8.1%', up: true },
          { x: 525, label: 'Queries / day', value: '892K', delta: '–2.3%', up: false },
          { x: 680, label: 'Cache Hit Rate', value: '94.2%', delta: '+3.1%', up: true },
        ].map(({ x, label, value, delta, up }) => (
          <g key={label}>
            <rect x={x} y="100" width="140" height="78" rx="8"
              fill="#111a2e" stroke="#21304a" strokeWidth="1" />
            {/* Top accent bar */}
            <rect x={x} y="100" width="140" height="3" rx="1.5"
              fill="url(#h-brand-h)" fillOpacity={up ? 0.8 : 0.3} />
            <text x={x + 14} y="122" fill="#93a4bd" fontSize="10" fontFamily="'Inter', sans-serif">{label}</text>
            <text x={x + 14} y="148" fill="#e7edf6" fontSize="22" fontWeight="700" fontFamily="'Space Grotesk', sans-serif">{value}</text>
            <text x={x + 14} y="167" fill={up ? '#2dd4bf' : '#f87171'} fontSize="10" fontFamily="'Inter', sans-serif">{delta} vs prev</text>
          </g>
        ))}

        {/* ── Bar chart ── */}
        <rect x="215" y="196" width="306" height="178" rx="8"
          fill="#111a2e" stroke="#21304a" strokeWidth="1" />
        <text x="232" y="218" fill="#93a4bd" fontSize="10" fontWeight="600" fontFamily="'Inter', sans-serif">Monthly Revenue</text>
        <text x="492" y="218" textAnchor="end" fill="#4a6fa5" fontSize="8" fontFamily="monospace">USD</text>

        {/* Y-axis grid lines */}
        {[240, 262, 284, 306, 328, 350].map(y => (
          <line key={y} x1="250" y1={y} x2="506" y2={y}
            stroke="#21304a" strokeWidth="0.5" strokeDasharray="4,4" />
        ))}

        {/* Bar chart bars */}
        {[
          { x: 258, h: 78, hi: false },
          { x: 280, h: 60, hi: false },
          { x: 302, h: 96, hi: false },
          { x: 324, h: 72, hi: false },
          { x: 346, h: 108, hi: false },
          { x: 368, h: 84, hi: false },
          { x: 390, h: 120, hi: false },
          { x: 412, h: 100, hi: false },
          { x: 434, h: 134, hi: false },
          { x: 456, h: 112, hi: false },
          { x: 478, h: 148, hi: true },
        ].map(({ x, h, hi }, i) => (
          <g key={i}>
            <rect x={x} y={362 - h} width="18" height={h} rx="2.5"
              fill={hi ? 'url(#h-bar-hi)' : 'url(#h-bar)'} />
            {hi && (
              <text x={x + 9} y={358 - h} textAnchor="middle"
                fill="#2dd4bf" fontSize="7" fontFamily="monospace">↑</text>
            )}
          </g>
        ))}

        {/* X axis */}
        <line x1="250" y1="362" x2="507" y2="362" stroke="#21304a" strokeWidth="1" />

        {/* Month labels */}
        {['Jan', 'Mar', 'May', 'Jul', 'Sep', 'Nov'].map((m, i) => (
          <text key={m} x={258 + i * 44} y="374" fill="#4a6fa5" fontSize="8" fontFamily="monospace">{m}</text>
        ))}

        {/* ── Sparkline / live query chart ── */}
        <rect x="536" y="196" width="232" height="178" rx="8"
          fill="#111a2e" stroke="#21304a" strokeWidth="1" />
        <text x="554" y="218" fill="#93a4bd" fontSize="10" fontWeight="600" fontFamily="'Inter', sans-serif">Live query volume</text>

        {/* Live dot indicator */}
        <circle cx="750" cy="214" r="5" fill="#17b3a3" fillOpacity="0.2" />
        <circle cx="750" cy="214" r="3" fill="#2dd4bf" filter="url(#h-glow-teal)" />
        <text x="742" y="218" textAnchor="end" fill="#2dd4bf" fontSize="8" fontFamily="monospace">LIVE</text>

        {/* Grid lines for sparkline */}
        {[240, 268, 296, 324, 352].map(y => (
          <line key={y} x1="550" y1={y} x2="758" y2={y}
            stroke="#21304a" strokeWidth="0.5" strokeDasharray="3,3" />
        ))}

        {/* Sparkline area fill */}
        <path
          d="M 552 356 L 568 332 L 585 340 L 602 314 L 619 320 L 636 295 L 652 302 L 669 274 L 686 280 L 703 258 L 720 262 L 737 245 L 754 250 L 758 248 L 758 360 L 552 360 Z"
          fill="url(#h-area)"
        />

        {/* Sparkline path */}
        <path
          d="M 552 356 L 568 332 L 585 340 L 602 314 L 619 320 L 636 295 L 652 302 L 669 274 L 686 280 L 703 258 L 720 262 L 737 245 L 754 250 L 758 248"
          stroke="#2dd4bf"
          strokeWidth="2.5"
          fill="none"
          strokeLinecap="round"
          strokeLinejoin="round"
          filter="url(#h-glow-teal)"
        />

        {/* Live dot at end */}
        <circle cx="758" cy="248" r="6" fill="#2dd4bf" fillOpacity="0.25" />
        <circle cx="758" cy="248" r="3.5" fill="#2dd4bf" filter="url(#h-glow-teal)" />

        {/* Tooltip */}
        <rect x="696" y="228" width="64" height="22" rx="4"
          fill="#0d1526" stroke="#2dd4bf" strokeOpacity="0.5" strokeWidth="0.75" />
        <text x="728" y="242" textAnchor="middle" fill="#2dd4bf" fontSize="9" fontFamily="monospace">8,432 q/s</text>

        {/* ── Arrow IPC + Kernel info bar ── */}
        <rect x="215" y="388" width="553" height="44" rx="8"
          fill="#080e1c" stroke="#21304a" strokeWidth="1" />

        {/* Arrow IPC badge */}
        <rect x="230" y="400" width="120" height="20" rx="5"
          fill="#17b3a3" fillOpacity="0.1" stroke="#17b3a3" strokeOpacity="0.4" strokeWidth="0.75" />
        <circle cx="244" cy="410" r="4" fill="#2dd4bf" fillOpacity="0.8" />
        <text x="252" y="413.5" fill="#2dd4bf" fontSize="9" fontFamily="monospace">Arrow IPC · 48ms</text>

        {/* Row count */}
        <rect x="360" y="400" width="100" height="20" rx="5"
          fill="#2456a6" fillOpacity="0.1" stroke="#2456a6" strokeOpacity="0.35" strokeWidth="0.75" />
        <text x="410" y="413.5" textAnchor="middle" fill="#4d8de0" fontSize="9" fontFamily="monospace">1.24M rows</text>

        {/* WebGL badge */}
        <rect x="470" y="400" width="100" height="20" rx="5"
          fill="#1b2363" fillOpacity="0.3" stroke="#2456a6" strokeOpacity="0.4" strokeWidth="0.75" />
        <text x="520" y="413.5" textAnchor="middle" fill="#4d8de0" fontSize="9" fontFamily="monospace">WebGL · 60 fps</text>

        {/* Cache status */}
        <rect x="580" y="400" width="78" height="20" rx="5"
          fill="#17b3a3" fillOpacity="0.08" stroke="#17b3a3" strokeOpacity="0.3" strokeWidth="0.75" />
        <text x="619" y="413.5" textAnchor="middle" fill="#2dd4bf" fontSize="9" fontFamily="monospace">CACHE HIT</text>

        {/* ── SQL query bar ── */}
        <rect x="215" y="442" width="553" height="50" rx="8"
          fill="#070d1a" stroke="#21304a" strokeWidth="1" />

        {/* SQL prompt */}
        <text x="232" y="460" fill="#17b3a3" fontSize="11" fontFamily="monospace" opacity="0.6">▷</text>
        <text x="248" y="460" fill="#4d8de0" fontSize="10" fontFamily="monospace">SELECT</text>
        <text x="296" y="460" fill="#e7edf6" fillOpacity="0.8" fontSize="10" fontFamily="monospace"> month, SUM(revenue) AS mrr, COUNT(*) AS events</text>
        <text x="248" y="476" fill="#4d8de0" fontSize="10" fontFamily="monospace">FROM</text>
        <text x="286" y="476" fill="#e7edf6" fillOpacity="0.8" fontSize="10" fontFamily="monospace"> events WHERE event_type = &apos;payment&apos; GROUP BY 1 ORDER BY 1</text>

        {/* Run button */}
        <rect x="704" y="452" width="52" height="26" rx="6"
          fill="url(#h-brand-h)" fillOpacity="0.9" />
        <text x="730" y="469" textAnchor="middle" fill="#ffffff" fontSize="10" fontWeight="600" fontFamily="'Inter', sans-serif">Run ↵</text>

        {/* ── Brand gradient accent bar at bottom of browser ── */}
        <rect x="195" y="534" width="587" height="8" fill="url(#h-brand-h)" fillOpacity="0.7" />

      </g>

      {/* ── Outer frame ── */}
      <rect x="8" y="8" width="784" height="544" rx="14" stroke="url(#h-brand)" strokeOpacity="0.4" strokeWidth="1" fill="none" />

      {/* ── Floating badges outside browser for wow-factor ── */}
      {/* DuckDB-WASM chip */}
      <g transform="translate(18, 430)">
        <rect width="148" height="36" rx="8"
          fill="#0d1526" stroke="#2456a6" strokeOpacity="0.5" strokeWidth="1" />
        <circle cx="16" cy="18" r="6" fill="#2456a6" fillOpacity="0.3" />
        <circle cx="16" cy="18" r="3.5" fill="#4d8de0" />
        <text x="28" y="14" fill="#4d8de0" fontSize="9" fontWeight="600" fontFamily="'Inter', sans-serif">DuckDB-WASM</text>
        <text x="28" y="26" fill="#4a6fa5" fontSize="8" fontFamily="monospace">in-browser kernel active</text>
      </g>
    </svg>
  )
}
