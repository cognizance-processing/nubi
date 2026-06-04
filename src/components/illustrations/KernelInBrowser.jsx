/**
 * KernelInBrowser — Large illustration contrasting browser-native kernel vs cloud kernel.
 * Shows: browser frame → Pyodide/DuckDB-WASM inside → Arrow IPC → viz output
 * vs faded cloud rack on right showing cold start / cost.
 * viewBox 560×380 — parent controls display size (aim ~320px+ height).
 */
export default function KernelInBrowser({ className = '' }) {
  return (
    <svg
      viewBox="0 0 560 380"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
      style={{ width: '100%', height: 'auto' }}
    >
      <defs>
        <linearGradient id="kib-brand" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="50%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="kib-bg" x1="0" y1="0" x2="560" y2="380" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#0a1020" />
          <stop offset="100%" stopColor="#0c1422" />
        </linearGradient>
        <linearGradient id="kib-browser-bg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#111a2e" />
          <stop offset="100%" stopColor="#0d1526" />
        </linearGradient>
        <filter id="kib-glow">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <filter id="kib-glow-sm">
          <feGaussianBlur stdDeviation="1.5" result="blur" />
          <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <marker id="kib-arr" markerWidth="7" markerHeight="7" refX="3.5" refY="3.5" orient="auto">
          <path d="M 0 0 L 7 3.5 L 0 7 Z" fill="#2dd4bf" fillOpacity="0.8" />
        </marker>
        <marker id="kib-arr-dim" markerWidth="7" markerHeight="7" refX="3.5" refY="3.5" orient="auto">
          <path d="M 0 0 L 7 3.5 L 0 7 Z" fill="#4a6fa5" fillOpacity="0.5" />
        </marker>
      </defs>

      {/* Background */}
      <rect width="560" height="380" rx="12" fill="url(#kib-bg)" />
      <rect width="560" height="380" rx="12" stroke="url(#kib-brand)" strokeOpacity="0.3" strokeWidth="1" fill="none" />

      {/* Section label */}
      <text x="280" y="26" textAnchor="middle" fill="#4d8de0" fontSize="11" fontWeight="600" fontFamily="'Space Grotesk', sans-serif" letterSpacing="1">KERNEL ARCHITECTURE</text>

      {/* ═══════════════════════════════════
          LEFT PANEL: Browser-native kernel
      ═══════════════════════════════════ */}
      <rect x="16" y="38" width="300" height="300" rx="10"
        fill="url(#kib-browser-bg)" stroke="#2456a6" strokeOpacity="0.5" strokeWidth="1.5" />
      {/* Top brand accent */}
      <rect x="16" y="38" width="300" height="4" rx="2"
        fill="url(#kib-brand)" />

      {/* Browser chrome */}
      <rect x="16" y="42" width="300" height="28" rx="0" fill="#070d1a" />
      <circle cx="36" cy="56" r="5" fill="#ff5f57" fillOpacity="0.85" />
      <circle cx="52" cy="56" r="5" fill="#febc2e" fillOpacity="0.85" />
      <circle cx="68" cy="56" r="5" fill="#28c840" fillOpacity="0.85" />
      <rect x="88" y="48" width="168" height="16" rx="8" fill="#0c1828" stroke="#2456a6" strokeOpacity="0.3" strokeWidth="0.5" />
      <text x="172" y="59.5" textAnchor="middle" fill="#2dd4bf" fillOpacity="0.75" fontSize="8" fontFamily="monospace">app.nubi.dev</text>

      {/* Kernel box: Pyodide */}
      <rect x="30" y="88" width="120" height="72" rx="8"
        fill="#0d1526" stroke="#2456a6" strokeOpacity="0.6" strokeWidth="1" />
      <text x="90" y="108" textAnchor="middle" fill="#4d8de0" fontSize="12" fontWeight="700" fontFamily="'Space Grotesk', sans-serif">Pyodide</text>
      <text x="90" y="122" textAnchor="middle" fill="#93a4bd" fontSize="9" fontFamily="monospace">Python 3.12</text>
      <text x="90" y="134" textAnchor="middle" fill="#93a4bd" fontSize="9" fontFamily="monospace">in WebAssembly</text>
      {/* Pulse rings */}
      <circle cx="90" cy="148" r="10" fill="#2456a6" fillOpacity="0.12" />
      <circle cx="90" cy="148" r="6" fill="#2456a6" fillOpacity="0.2" />
      <circle cx="90" cy="148" r="3.5" fill="#4d8de0" filter="url(#kib-glow-sm)" />

      {/* DuckDB-WASM box */}
      <rect x="162" y="88" width="140" height="72" rx="8"
        fill="#0d1526" stroke="#17b3a3" strokeOpacity="0.6" strokeWidth="1" />
      <text x="232" y="106" textAnchor="middle" fill="#2dd4bf" fontSize="11" fontWeight="700" fontFamily="'Space Grotesk', sans-serif">DuckDB-WASM</text>
      <text x="232" y="120" textAnchor="middle" fill="#93a4bd" fontSize="9" fontFamily="monospace">columnar engine</text>
      {/* Mini column chart */}
      {[0.4, 0.7, 0.5, 0.9, 0.65, 0.8, 1.0].map((h, i) => (
        <rect key={i} x={176 + i * 17} y={152 - h * 26} width="12" height={h * 26} rx="2"
          fill="#17b3a3" fillOpacity={0.3 + h * 0.35} />
      ))}
      <line x1="170" y1="152" x2="298" y2="152" stroke="#17b3a3" strokeOpacity="0.2" strokeWidth="0.5" />

      {/* Arrow: Pyodide → DuckDB */}
      <line x1="152" y1="124" x2="160" y2="124"
        stroke="#2dd4bf" strokeOpacity="0.7" strokeWidth="1.5" markerEnd="url(#kib-arr)" />

      {/* Arrow IPC section */}
      <rect x="30" y="178" width="272" height="48" rx="7"
        fill="#070d1a" stroke="#17b3a3" strokeOpacity="0.4" strokeWidth="0.75" />
      <text x="44" y="196" fill="#2dd4bf" fontSize="11" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">Arrow IPC</text>
      <text x="44" y="210" fill="#93a4bd" fontSize="9" fontFamily="monospace">columnar wire format · zero copy · WebSocket</text>
      {/* Arrow stream dots */}
      {[130, 152, 174, 196, 218, 240, 262, 284].map((x, i) => (
        <circle key={i} cx={x} cy="200" r="3" fill="#2dd4bf"
          fillOpacity={i % 2 === 0 ? 0.7 : 0.3} />
      ))}
      <line x1="120" y1="200" x2="290" y2="200"
        stroke="#2dd4bf" strokeOpacity="0.15" strokeWidth="1" />

      {/* Viz output mini chart */}
      <rect x="30" y="238" width="272" height="60" rx="7"
        fill="#0d1526" stroke="#2456a6" strokeOpacity="0.3" strokeWidth="0.75" />
      <text x="44" y="254" fill="#4d8de0" fontSize="9" fontFamily="'Inter', sans-serif">Chart output (WebGL/regl)</text>
      {/* Sparkline */}
      <path d="M 42 288 L 58 272 L 78 280 L 98 262 L 118 268 L 138 252 L 158 256 L 178 240 L 198 244 L 218 232 L 238 236 L 258 225 L 278 228 L 290 226"
        stroke="#17b3a3" strokeWidth="2" fill="none" strokeLinecap="round" />
      {/* Area */}
      <path d="M 42 292 L 58 272 L 78 280 L 98 262 L 118 268 L 138 252 L 158 256 L 178 240 L 198 244 L 218 232 L 238 236 L 258 225 L 278 228 L 290 226 L 290 292 Z"
        fill="#17b3a3" fillOpacity="0.1" />

      {/* Cost badge */}
      <rect x="30" y="308" width="272" height="24" rx="6"
        fill="#17b3a3" fillOpacity="0.1" stroke="#17b3a3" strokeOpacity="0.5" strokeWidth="0.75" />
      <circle cx="48" cy="320" r="5" fill="#2dd4bf" fillOpacity="0.8" />
      <text x="60" y="323.5" fill="#2dd4bf" fontSize="11" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">Marginal cost per view ≈ $0</text>

      {/* Label */}
      <text x="166" y="350" textAnchor="middle" fill="#4d8de0" fontSize="11" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">Browser-native kernel</text>
      <text x="166" y="364" textAnchor="middle" fill="#4a6fa5" fontSize="9" fontFamily="'Inter', sans-serif">Zero cold starts · Zero per-session cost</text>

      {/* ═══════════════════════════════════
          RIGHT PANEL: Cloud kernel (contrast)
      ═══════════════════════════════════ */}
      <rect x="326" y="38" width="218" height="300" rx="10"
        fill="#080e18" stroke="#21304a" strokeOpacity="0.6" strokeWidth="1" />

      {/* Dim label */}
      <text x="435" y="60" textAnchor="middle" fill="#4a6fa5" fillOpacity="0.6" fontSize="10" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">Cloud kernel</text>
      <text x="435" y="74" textAnchor="middle" fill="#4a6fa5" fillOpacity="0.4" fontSize="8" fontFamily="'Inter', sans-serif">(Hex model)</text>

      {/* Server rack */}
      {[90, 108, 126, 144, 162, 180, 198, 216, 234, 252, 270, 288].map((y, i) => (
        <g key={y}>
          <rect x="346" y={y} width="178" height="14" rx="2"
            fill="#0c1422" stroke="#21304a" strokeOpacity="0.5" strokeWidth="0.5" />
          <rect x="346" y={y} width="6" height="14"
            fill="#1b2363" fillOpacity="0.4" />
          <circle cx="512" cy={y + 7} r="2.5"
            fill={i % 3 === 0 ? '#2456a6' : '#21304a'} fillOpacity={i % 3 === 0 ? 0.5 : 0.3} />
          <circle cx="502" cy={y + 7} r="2.5"
            fill={i % 5 === 0 ? '#17b3a3' : '#21304a'} fillOpacity={i % 5 === 0 ? 0.4 : 0.2} />
        </g>
      ))}

      {/* Cold start warning */}
      <rect x="346" y="306" width="178" height="22" rx="5"
        fill="#1a0a0a" stroke="#f87171" strokeOpacity="0.4" strokeWidth="0.75" />
      <text x="435" y="320.5" textAnchor="middle" fill="#f87171" fillOpacity="0.7" fontSize="10" fontFamily="'Inter', sans-serif">10–30s cold start · $$ / session</text>

      {/* X cross overlay */}
      <line x1="336" y1="44" x2="536" y2="332" stroke="#f87171" strokeOpacity="0.12" strokeWidth="2" strokeLinecap="round" />
      <line x1="536" y1="44" x2="336" y2="332" stroke="#f87171" strokeOpacity="0.12" strokeWidth="2" strokeLinecap="round" />

      {/* Label */}
      <text x="435" y="352" textAnchor="middle" fill="#4a6fa5" fillOpacity="0.5" fontSize="10" fontFamily="'Space Grotesk', sans-serif">Their cloud</text>
      <text x="435" y="366" textAnchor="middle" fill="#4a6fa5" fillOpacity="0.35" fontSize="9" fontFamily="'Inter', sans-serif">Session-scoped · $$$ warm</text>

      {/* VS divider */}
      <rect x="314" y="155" width="24" height="24" rx="12"
        fill="#111a2e" stroke="#21304a" strokeWidth="1" />
      <text x="326" y="171" textAnchor="middle" fill="#4a6fa5" fontSize="10" fontWeight="700" fontFamily="'Space Grotesk', sans-serif">vs</text>
    </svg>
  )
}
