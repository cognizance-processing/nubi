/**
 * AuthArtwork — premium SVG illustration for the auth panel.
 * Abstract data/dashboard/analytics motif designed to sit ON a
 * navy→teal gradient background: light-on-dark, bold glows, glassmorphic
 * dashboard cards, flowing connection lines, orbiting data nodes.
 *
 * Uses unique prefix `auth-` for all gradient/filter/clip ids.
 * No text, no overflow. viewBox 480x560.
 */
export default function AuthArtwork({ className = '' }) {
  return (
    <svg
      viewBox="0 0 480 560"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
      width="100%"
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        {/* ── Gradients ── */}
        <linearGradient id="auth-brand" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="45%" stopColor="#2456a6" />
          <stop offset="80%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2dd4bf" />
        </linearGradient>

        <linearGradient id="auth-teal-cyan" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>

        <linearGradient id="auth-blue-teal" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>

        <linearGradient id="auth-line-h" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.0" />
          <stop offset="30%" stopColor="#2dd4bf" stopOpacity="0.7" />
          <stop offset="70%" stopColor="#17b3a3" stopOpacity="0.5" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.0" />
        </linearGradient>

        <linearGradient id="auth-area1" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.45" />
          <stop offset="100%" stopColor="#2dd4bf" stopOpacity="0.0" />
        </linearGradient>

        <linearGradient id="auth-area2" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2456a6" stopOpacity="0.35" />
          <stop offset="100%" stopColor="#2456a6" stopOpacity="0.0" />
        </linearGradient>

        <linearGradient id="auth-bar-v" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.9" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.3" />
        </linearGradient>

        <linearGradient id="auth-bar-v2" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#4d8de0" stopOpacity="0.8" />
          <stop offset="100%" stopColor="#2456a6" stopOpacity="0.25" />
        </linearGradient>

        {/* Glass card fill */}
        <linearGradient id="auth-glass-main" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#ffffff" stopOpacity="0.11" />
          <stop offset="100%" stopColor="#ffffff" stopOpacity="0.04" />
        </linearGradient>

        <linearGradient id="auth-glass-sm" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#ffffff" stopOpacity="0.13" />
          <stop offset="100%" stopColor="#ffffff" stopOpacity="0.05" />
        </linearGradient>

        {/* Glass border */}
        <linearGradient id="auth-gborder" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.5" />
          <stop offset="60%" stopColor="#2456a6" stopOpacity="0.28" />
          <stop offset="100%" stopColor="#ffffff" stopOpacity="0.12" />
        </linearGradient>

        <linearGradient id="auth-gborder2" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#ffffff" stopOpacity="0.25" />
          <stop offset="100%" stopColor="#2dd4bf" stopOpacity="0.2" />
        </linearGradient>

        {/* Radial blooms */}
        <radialGradient id="auth-bloom-tr" cx="75%" cy="18%" r="50%">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.35" />
          <stop offset="100%" stopColor="#2dd4bf" stopOpacity="0.0" />
        </radialGradient>

        <radialGradient id="auth-bloom-bl" cx="20%" cy="85%" r="45%">
          <stop offset="0%" stopColor="#2456a6" stopOpacity="0.4" />
          <stop offset="100%" stopColor="#2456a6" stopOpacity="0.0" />
        </radialGradient>

        <radialGradient id="auth-bloom-center" cx="50%" cy="48%" r="38%">
          <stop offset="0%" stopColor="#17b3a3" stopOpacity="0.18" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.0" />
        </radialGradient>

        {/* Node glow */}
        <radialGradient id="auth-node-glow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="1.0" />
          <stop offset="100%" stopColor="#2dd4bf" stopOpacity="0.0" />
        </radialGradient>

        <radialGradient id="auth-node-glow2" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#4d8de0" stopOpacity="1.0" />
          <stop offset="100%" stopColor="#4d8de0" stopOpacity="0.0" />
        </radialGradient>

        {/* Orbit ring gradient */}
        <linearGradient id="auth-orbit" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.0" />
          <stop offset="40%" stopColor="#2dd4bf" stopOpacity="0.35" />
          <stop offset="70%" stopColor="#17b3a3" stopOpacity="0.2" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.0" />
        </linearGradient>

        {/* Donut segment gradient */}
        <linearGradient id="auth-donut1" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>

        <linearGradient id="auth-donut2" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#1b2363" stopOpacity="0.6" />
        </linearGradient>

        {/* ── Filters ── */}
        <filter id="auth-glow-sm" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="4" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>

        <filter id="auth-glow-md" x="-100%" y="-100%" width="300%" height="300%">
          <feGaussianBlur stdDeviation="8" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>

        <filter id="auth-shadow" x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="8" stdDeviation="16" floodColor="#0a1020" floodOpacity="0.45" />
        </filter>

        <filter id="auth-shadow-sm" x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="4" stdDeviation="8" floodColor="#0a1020" floodOpacity="0.35" />
        </filter>

        {/* Clip path */}
        <clipPath id="auth-clip">
          <rect x="0" y="0" width="480" height="560" />
        </clipPath>
      </defs>

      <g clipPath="url(#auth-clip)">

        {/* ── Ambient background blooms ── */}
        <rect x="0" y="0" width="480" height="560" fill="url(#auth-bloom-tr)" />
        <rect x="0" y="0" width="480" height="560" fill="url(#auth-bloom-bl)" />
        <rect x="0" y="0" width="480" height="560" fill="url(#auth-bloom-center)" />

        {/* ── Subtle dot grid background ── */}
        {Array.from({ length: 8 }, (_, row) =>
          Array.from({ length: 10 }, (_, col) => (
            <circle
              key={`grid-${row}-${col}`}
              cx={24 + col * 48}
              cy={40 + row * 64}
              r="1.5"
              fill="#ffffff"
              fillOpacity="0.06"
            />
          ))
        )}

        {/* ── Flowing connection lines ── */}
        {/* Horizontal scan line 1 */}
        <line x1="0" y1="160" x2="480" y2="160"
          stroke="url(#auth-line-h)" strokeWidth="1" strokeOpacity="0.6" />
        {/* Horizontal scan line 2 */}
        <line x1="0" y1="390" x2="480" y2="390"
          stroke="url(#auth-line-h)" strokeWidth="0.8" strokeOpacity="0.4" />

        {/* Diagonal flow lines */}
        <path d="M 60 80 Q 180 200 120 320 Q 80 400 160 480"
          stroke="#2dd4bf" strokeOpacity="0.12" strokeWidth="1.5"
          strokeLinecap="round" fill="none" />
        <path d="M 380 40 Q 300 160 360 280 Q 420 380 340 500"
          stroke="#17b3a3" strokeOpacity="0.1" strokeWidth="1.5"
          strokeLinecap="round" fill="none" />

        {/* ── MAIN DASHBOARD CARD ── */}
        <g filter="url(#auth-shadow)">
          <rect x="32" y="120" width="416" height="220" rx="20" fill="url(#auth-glass-main)" />
        </g>
        <rect x="32" y="120" width="416" height="220" rx="20"
          stroke="url(#auth-gborder)" strokeWidth="1.5" />
        {/* top specular highlight */}
        <path d="M 56 121.5 L 424 121.5"
          stroke="#ffffff" strokeOpacity="0.2" strokeWidth="1.5" strokeLinecap="round" />

        {/* Card header strip */}
        <rect x="32" y="120" width="416" height="44" rx="20" fill="#ffffff" fillOpacity="0.04" />
        <rect x="32" y="144" width="416" height="20" fill="#ffffff" fillOpacity="0.03" />

        {/* Traffic light dots */}
        <circle cx="56" cy="142" r="4.5" fill="#2dd4bf" fillOpacity="0.7" />
        <circle cx="71" cy="142" r="4.5" fill="#17b3a3" fillOpacity="0.5" />
        <circle cx="86" cy="142" r="4.5" fill="#2456a6" fillOpacity="0.4" />

        {/* ── Area chart inside main card ── */}
        {/* Grid lines */}
        {[178, 200, 222, 248, 270, 294, 316].map((y) => (
          <line key={`grid-y-${y}`} x1="52" y1={y} x2="428" y2={y}
            stroke="#ffffff" strokeOpacity="0.05" strokeWidth="1" />
        ))}

        {/* Area 1 fill (teal) */}
        <path d="M 52 290 C 90 270, 130 252, 168 238 C 210 222, 240 230, 280 210
                 C 320 188, 360 175, 428 155 L 428 316 L 52 316 Z"
          fill="url(#auth-area1)" />
        {/* Area 2 fill (blue, behind) */}
        <path d="M 52 300 C 90 288, 130 278, 168 268 C 210 256, 240 264, 280 248
                 C 320 232, 360 220, 428 205 L 428 316 L 52 316 Z"
          fill="url(#auth-area2)" />

        {/* Chart line 1 (teal) */}
        <path d="M 52 290 C 90 270, 130 252, 168 238 C 210 222, 240 230, 280 210
                 C 320 188, 360 175, 428 155"
          stroke="url(#auth-teal-cyan)" strokeWidth="3"
          strokeLinecap="round" strokeLinejoin="round" />
        {/* Chart line 2 (blue, secondary) */}
        <path d="M 52 300 C 90 288, 130 278, 168 268 C 210 256, 240 264, 280 248
                 C 320 232, 360 220, 428 205"
          stroke="url(#auth-blue-teal)" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round" strokeOpacity="0.7" />

        {/* Chart baseline */}
        <line x1="52" y1="316" x2="428" y2="316"
          stroke="#ffffff" strokeOpacity="0.08" strokeWidth="1" />

        {/* Chart nodes on line 1 */}
        {[
          { cx: 168, cy: 238 },
          { cx: 280, cy: 210 },
          { cx: 428, cy: 155 },
        ].map(({ cx, cy }, i) => (
          <g key={`node-${i}`}>
            <circle cx={cx} cy={cy} r={i === 2 ? 22 : 14}
              fill="url(#auth-node-glow)" fillOpacity={i === 2 ? 0.45 : 0.3}
              filter="url(#auth-glow-sm)" />
            <circle cx={cx} cy={cy} r={i === 2 ? 6.5 : 5}
              fill={i === 2 ? 'url(#auth-teal-cyan)' : '#17b3a3'}
              stroke="#ffffff" strokeWidth="2" strokeOpacity="0.9" />
            {i === 2 && <circle cx={cx} cy={cy} r="2.5" fill="#ffffff" fillOpacity="0.85" />}
          </g>
        ))}

        {/* Peak tooltip card */}
        <g filter="url(#auth-shadow-sm)">
          <rect x="372" y="120" width="72" height="30" rx="8" fill="url(#auth-glass-sm)" />
        </g>
        <rect x="372" y="120" width="72" height="30" rx="8"
          stroke="url(#auth-gborder2)" strokeWidth="1" />
        {/* Tooltip pip rows */}
        <rect x="382" y="129" width="28" height="5" rx="2.5" fill="#2dd4bf" fillOpacity="0.8" />
        <rect x="414" y="129" width="18" height="5" rx="2.5" fill="#ffffff" fillOpacity="0.15" />
        <rect x="382" y="138" width="20" height="4" rx="2" fill="#ffffff" fillOpacity="0.12" />
        {/* Connecting line from tooltip to peak node */}
        <line x1="408" y1="150" x2="428" y2="155"
          stroke="#2dd4bf" strokeOpacity="0.5" strokeWidth="1" strokeDasharray="3 3" />

      </g>{/* end clip */}

      {/* ── SMALL CARD — KPI Ring (bottom-left) ── */}
      <g filter="url(#auth-shadow-sm)">
        <rect x="24" y="360" width="142" height="130" rx="18" fill="url(#auth-glass-main)" />
      </g>
      <rect x="24" y="360" width="142" height="130" rx="18"
        stroke="url(#auth-gborder)" strokeWidth="1.2" />
      <path d="M 48 361.5 L 142 361.5"
        stroke="#ffffff" strokeOpacity="0.16" strokeWidth="1.2" strokeLinecap="round" />

      {/* Donut / ring chart */}
      {/* Track */}
      <circle cx="95" cy="418" r="36" stroke="#ffffff" strokeOpacity="0.08" strokeWidth="9" fill="none" />
      {/* Segment 1 — teal (67%) */}
      <path d="M 95 382 A 36 36 0 1 1 60.8 440"
        stroke="url(#auth-donut1)" strokeWidth="9" strokeLinecap="round" fill="none" />
      {/* Segment 2 — blue remaining */}
      <path d="M 60.8 440 A 36 36 0 0 1 95 382"
        stroke="url(#auth-donut2)" strokeWidth="9" strokeLinecap="round" fill="none"
        strokeOpacity="0.5" />
      {/* Center glow dot */}
      <circle cx="95" cy="418" r="18" fill="url(#auth-glass-sm)" />
      <circle cx="95" cy="418" r="6" fill="url(#auth-teal-cyan)" />
      <circle cx="95" cy="418" r="6" stroke="#ffffff" strokeWidth="1.5" strokeOpacity="0.6" />

      {/* Legend rows */}
      <circle cx="38" cy="470" r="4" fill="url(#auth-teal-cyan)" />
      <rect x="46" y="467" width="36" height="5" rx="2.5" fill="#ffffff" fillOpacity="0.25" />
      <circle cx="38" cy="482" r="4" fill="url(#auth-donut2)" fillOpacity="0.7" />
      <rect x="46" y="479" width="26" height="5" rx="2.5" fill="#ffffff" fillOpacity="0.15" />

      {/* ── SMALL CARD — Sparkline / metric (bottom-center) ── */}
      <g filter="url(#auth-shadow-sm)">
        <rect x="178" y="360" width="140" height="84" rx="18" fill="url(#auth-glass-main)" />
      </g>
      <rect x="178" y="360" width="140" height="84" rx="18"
        stroke="url(#auth-gborder)" strokeWidth="1.2" />
      <path d="M 202 361.5 L 294 361.5"
        stroke="#ffffff" strokeOpacity="0.16" strokeWidth="1.2" strokeLinecap="round" />

      {/* Metric up-tick badge */}
      <rect x="190" y="374" width="34" height="16" rx="8" fill="url(#auth-teal-cyan)" fillOpacity="0.25" />
      <rect x="190" y="374" width="34" height="16" rx="8" stroke="#2dd4bf" strokeOpacity="0.5" strokeWidth="0.8" />
      {/* tiny up arrow */}
      <path d="M 200 386 L 204 380 L 208 386" stroke="#2dd4bf" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />

      {/* Sparkline */}
      <polyline
        points="190,424 205,416 218,420 232,408 248,412 264,398 280,402 295,390 310,394"
        stroke="url(#auth-teal-cyan)" strokeWidth="2.5"
        strokeLinecap="round" strokeLinejoin="round" fill="none" />
      {/* Sparkline area */}
      <path d="M 190 424 L 205 416 L 218 420 L 232 408 L 248 412 L 264 398 L 280 402 L 295 390 L 310 394 L 310 432 L 190 432 Z"
        fill="url(#auth-area1)" fillOpacity="0.3" />
      {/* End node */}
      <circle cx="310" cy="394" r="10" fill="url(#auth-node-glow)" fillOpacity="0.4" />
      <circle cx="310" cy="394" r="4" fill="#2dd4bf" stroke="#ffffff" strokeWidth="1.5" strokeOpacity="0.9" />

      {/* ── SMALL CARD — Bar chart (bottom-right) ── */}
      <g filter="url(#auth-shadow-sm)">
        <rect x="330" y="360" width="126" height="84" rx="18" fill="url(#auth-glass-main)" />
      </g>
      <rect x="330" y="360" width="126" height="84" rx="18"
        stroke="url(#auth-gborder)" strokeWidth="1.2" />
      <path d="M 354 361.5 L 432 361.5"
        stroke="#ffffff" strokeOpacity="0.16" strokeWidth="1.2" strokeLinecap="round" />

      {/* Grouped bars */}
      {[
        { x: 346, h1: 44, h2: 30 },
        { x: 368, h1: 34, h2: 50 },
        { x: 390, h1: 54, h2: 24 },
        { x: 412, h1: 40, h2: 44 },
        { x: 434, h1: 48, h2: 36 },
      ].map(({ x, h1, h2 }, i) => (
        <g key={`bar-${i}`}>
          <rect x={x} y={432 - h1} width="9" height={h1} rx="3"
            fill="url(#auth-bar-v)" />
          <rect x={x + 11} y={432 - h2} width="9" height={h2} rx="3"
            fill="url(#auth-bar-v2)" />
        </g>
      ))}

      {/* ── Floating metric pill (top) ── */}
      <g filter="url(#auth-shadow-sm)">
        <rect x="148" y="56" width="184" height="52" rx="26" fill="url(#auth-glass-main)" />
      </g>
      <rect x="148" y="56" width="184" height="52" rx="26"
        stroke="url(#auth-gborder2)" strokeWidth="1.2" />
      {/* Icon circle */}
      <circle cx="174" cy="82" r="16" fill="url(#auth-teal-cyan)" fillOpacity="0.2" />
      <circle cx="174" cy="82" r="8" fill="url(#auth-teal-cyan)" />
      <circle cx="174" cy="82" r="8" stroke="#ffffff" strokeWidth="1.5" strokeOpacity="0.5" />
      {/* Pulse rings */}
      <circle cx="174" cy="82" r="14" stroke="#2dd4bf" strokeOpacity="0.3" strokeWidth="1.5" fill="none" />
      <circle cx="174" cy="82" r="22" stroke="#2dd4bf" strokeOpacity="0.12" strokeWidth="1" fill="none" />
      {/* Metric placeholder bars */}
      <rect x="198" y="72" width="80" height="7" rx="3.5" fill="#ffffff" fillOpacity="0.3" />
      <rect x="198" y="84" width="56" height="5" rx="2.5" fill="#2dd4bf" fillOpacity="0.45" />
      <rect x="258" y="84" width="18" height="5" rx="2.5" fill="#ffffff" fillOpacity="0.12" />
      <rect x="198" y="94" width="36" height="4" rx="2" fill="#ffffff" fillOpacity="0.1" />

      {/* ── Orbital ring system (large, behind everything) ── */}
      {/* Outer orbit */}
      <ellipse cx="240" cy="285" rx="185" ry="110"
        stroke="url(#auth-orbit)" strokeWidth="1" fill="none"
        strokeDasharray="6 12" strokeOpacity="0.5" />
      {/* Inner orbit */}
      <ellipse cx="240" cy="285" rx="130" ry="74"
        stroke="#2dd4bf" strokeOpacity="0.1" strokeWidth="1"
        fill="none" strokeDasharray="4 10" />

      {/* Orbiting satellite nodes */}
      {/* Node A — top-right of outer orbit */}
      <circle cx="408" cy="225" r="20" fill="url(#auth-node-glow)" fillOpacity="0.3" />
      <circle cx="408" cy="225" r="7" fill="url(#auth-teal-cyan)"
        stroke="#ffffff" strokeWidth="2" strokeOpacity="0.8" />
      {/* Node B — bottom-left of outer orbit */}
      <circle cx="72" cy="345" r="14" fill="url(#auth-node-glow2)" fillOpacity="0.25" />
      <circle cx="72" cy="345" r="5.5" fill="url(#auth-blue-teal)"
        stroke="#ffffff" strokeWidth="1.5" strokeOpacity="0.7" />
      {/* Node C — top of inner orbit */}
      <circle cx="240" cy="212" r="10" fill="url(#auth-node-glow)" fillOpacity="0.2" />
      <circle cx="240" cy="212" r="4" fill="#17b3a3"
        stroke="#ffffff" strokeWidth="1.5" strokeOpacity="0.6" />

      {/* Connection threads from nodes to main card */}
      <line x1="408" y1="225" x2="388" y2="160"
        stroke="#2dd4bf" strokeOpacity="0.2" strokeWidth="1.2" strokeDasharray="4 6" />
      <line x1="72" y1="345" x2="80" y2="320"
        stroke="#4d8de0" strokeOpacity="0.2" strokeWidth="1.2" strokeDasharray="4 6" />

      {/* ── Decorative corner accents ── */}
      {/* Top-left corner mark */}
      <path d="M 16 40 L 16 20 L 36 20" stroke="#2dd4bf" strokeOpacity="0.3"
        strokeWidth="2" strokeLinecap="round" fill="none" />
      {/* Bottom-right corner mark */}
      <path d="M 464 520 L 464 540 L 444 540" stroke="#17b3a3" strokeOpacity="0.3"
        strokeWidth="2" strokeLinecap="round" fill="none" />

      {/* ── Tiny scattered data particles ── */}
      {[
        { cx: 440, cy: 80, r: 2.5, op: 0.5 },
        { cx: 456, cy: 100, r: 1.5, op: 0.3 },
        { cx: 448, cy: 110, r: 2, op: 0.4 },
        { cx: 30, cy: 510, r: 2.5, op: 0.4 },
        { cx: 16, cy: 498, r: 1.5, op: 0.25 },
        { cx: 42, cy: 524, r: 2, op: 0.35 },
        { cx: 200, cy: 30, r: 2, op: 0.35 },
        { cx: 290, cy: 20, r: 1.5, op: 0.25 },
        { cx: 460, cy: 300, r: 2, op: 0.3 },
        { cx: 470, cy: 320, r: 1.5, op: 0.2 },
      ].map(({ cx, cy, r, op }, i) => (
        <circle key={`particle-${i}`} cx={cx} cy={cy} r={r}
          fill="#2dd4bf" fillOpacity={op} />
      ))}

      {/* ── Connectivity line from sparkline card up to main card ── */}
      <line x1="248" y1="360" x2="240" y2="340"
        stroke="#2dd4bf" strokeOpacity="0.15" strokeWidth="1" strokeDasharray="3 5" />
      {/* From donut card up */}
      <line x1="95" y1="360" x2="80" y2="340"
        stroke="#4d8de0" strokeOpacity="0.15" strokeWidth="1" strokeDasharray="3 5" />
      {/* From bar card up */}
      <line x1="393" y1="360" x2="410" y2="340"
        stroke="#2dd4bf" strokeOpacity="0.12" strokeWidth="1" strokeDasharray="3 5" />

      {/* ── Bottom 3 KPI stat cards row — connector dots ── */}
      {/* Horizontal connector */}
      <line x1="166" y1="424" x2="178" y2="424"
        stroke="#2dd4bf" strokeOpacity="0.2" strokeWidth="1.5" strokeDasharray="3 4" />
      <line x1="318" y1="402" x2="330" y2="402"
        stroke="#2dd4bf" strokeOpacity="0.2" strokeWidth="1.5" strokeDasharray="3 4" />

    </svg>
  )
}
