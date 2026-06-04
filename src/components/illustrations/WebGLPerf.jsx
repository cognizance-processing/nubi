/**
 * WebGLPerf — Large illustration of 1M+ point scatter via Arrow → WebGL.
 * Shows: scatter plot with clusters, stats panel, data pipeline.
 * viewBox 560×380
 */
export default function WebGLPerf({ className = '' }) {
  // Deterministic pseudo-random scatter points
  const points = []
  let seed = 137
  function rand() {
    seed = (seed * 1664525 + 1013904223) & 0xffffffff
    return ((seed >>> 0) / 0xffffffff)
  }

  const clusters = [
    { cx: 80,  cy: 120, rx: 52, ry: 38, color: '#4d8de0', opacity: 0.75 },
    { cx: 190, cy: 90,  rx: 48, ry: 42, color: '#2dd4bf', opacity: 0.7 },
    { cx: 140, cy: 170, rx: 44, ry: 36, color: '#17b3a3', opacity: 0.65 },
    { cx: 250, cy: 130, rx: 40, ry: 34, color: '#2456a6', opacity: 0.7 },
  ]

  for (let i = 0; i < 200; i++) {
    const c = clusters[i % clusters.length]
    const angle = rand() * Math.PI * 2
    const radius = rand()
    const px = c.cx + Math.cos(angle) * c.rx * radius
    const py = c.cy + Math.sin(angle) * c.ry * radius
    const clampedX = Math.max(22, Math.min(292, px))
    const clampedY = Math.max(60, Math.min(244, py))
    points.push({ x: clampedX, y: clampedY, c: i % clusters.length })
  }

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
        <linearGradient id="wgl-brand" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="50%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="wgl-bg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#0a1020" />
          <stop offset="100%" stopColor="#0c1422" />
        </linearGradient>
        <filter id="wgl-glow">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <filter id="wgl-glow-sm">
          <feGaussianBlur stdDeviation="1.5" result="blur" />
          <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <marker id="wgl-arr" markerWidth="7" markerHeight="7" refX="3.5" refY="3.5" orient="auto">
          <path d="M 0 0 L 7 3.5 L 0 7 Z" fill="#2dd4bf" fillOpacity="0.7" />
        </marker>
      </defs>

      {/* Background */}
      <rect width="560" height="380" rx="12" fill="url(#wgl-bg)" />
      <rect width="560" height="380" rx="12" stroke="url(#wgl-brand)" strokeOpacity="0.3" strokeWidth="1" fill="none" />

      {/* Title */}
      <text x="280" y="26" textAnchor="middle" fill="#4d8de0" fontSize="11" fontWeight="600" fontFamily="'Space Grotesk', sans-serif" letterSpacing="1">WEBGL RENDERING · 1M+ POINTS AT 60 FPS</text>

      {/* ═══════════════════════════════════
          LEFT: Scatter plot
      ═══════════════════════════════════ */}
      <rect x="14" y="38" width="316" height="248" rx="10"
        fill="#080e1c" stroke="#21304a" strokeWidth="1" />
      {/* Chart header */}
      <rect x="14" y="38" width="316" height="26" rx="10" fill="#070d1a" />
      <rect x="14" y="56" width="316" height="8" fill="#070d1a" />
      <text x="172" y="54" textAnchor="middle" fill="#4a6fa5" fontSize="9" fontFamily="'Space Grotesk', sans-serif">scatter · 1.24M points · Arrow→WebGL→regl</text>

      {/* Chart grid */}
      {[80, 110, 140, 170, 200, 230, 260].map(y => (
        <line key={y} x1="30" y1={y} x2="320" y2={y}
          stroke="#21304a" strokeWidth="0.4" strokeDasharray="4,4" />
      ))}
      {[50, 100, 150, 200, 250, 300].map(x => (
        <line key={x} x1={x} y1="66" x2={x} y2="278"
          stroke="#21304a" strokeWidth="0.4" strokeDasharray="4,4" />
      ))}

      {/* Cluster glow backgrounds */}
      {clusters.map(({ cx, cy, rx, ry, color }, i) => (
        <ellipse key={i} cx={cx + 30} cy={cy + 10} rx={rx * 1.4} ry={ry * 1.4}
          fill={color} fillOpacity="0.06" />
      ))}

      {/* Scatter points */}
      {points.map((p, i) => {
        const c = clusters[p.c]
        const r = i % 11 === 0 ? 3.5 : i % 5 === 0 ? 2.5 : 1.5
        return (
          <circle
            key={i}
            cx={p.x + 30}
            cy={p.y + 10}
            r={r}
            fill={c.color}
            fillOpacity={c.opacity * (0.4 + (i % 7) / 10)}
          />
        )
      })}

      {/* Highlighted point with tooltip */}
      <circle cx="225" cy="100" r="7" fill="#2dd4bf" fillOpacity="0.2" />
      <circle cx="225" cy="100" r="4" fill="#2dd4bf" filter="url(#wgl-glow)" />
      <rect x="180" y="76" width="100" height="22" rx="5"
        fill="#0d1526" stroke="#2dd4bf" strokeOpacity="0.6" strokeWidth="0.75" />
      <text x="230" y="89" textAnchor="middle" fill="#2dd4bf" fontSize="9" fontFamily="monospace">revenue: $48.2K</text>
      <text x="230" y="99" textAnchor="middle" fill="#4a6fa5" fontSize="8" fontFamily="monospace">session: 8.4 min</text>

      {/* Axes */}
      <line x1="30" y1="270" x2="320" y2="270" stroke="#21304a" strokeWidth="1" />
      <line x1="30" y1="66" x2="30" y2="270" stroke="#21304a" strokeWidth="1" />

      {/* Axis labels */}
      <text x="172" y="282" textAnchor="middle" fill="#4a6fa5" fontSize="8" fontFamily="monospace">session_duration_ms</text>
      <text x="14" y="168" textAnchor="middle" fill="#4a6fa5" fontSize="8" fontFamily="monospace"
        transform="rotate(-90, 14, 168)">revenue_usd</text>

      {/* Legend */}
      {[
        { label: 'Enterprise', color: '#4d8de0' },
        { label: 'Mid-market', color: '#2dd4bf' },
        { label: 'SMB', color: '#17b3a3' },
        { label: 'Self-serve', color: '#2456a6' },
      ].map(({ label, color }, i) => (
        <g key={label}>
          <circle cx="42" cy={74 + i * 14} r="4" fill={color} fillOpacity="0.8" />
          <text x="52" y={78 + i * 14} fill={color} fillOpacity="0.8" fontSize="8" fontFamily="'Inter', sans-serif">{label}</text>
        </g>
      ))}

      {/* Cross-filter bar at bottom */}
      <rect x="14" y="288" width="316" height="28" rx="6"
        fill="#0d1526" stroke="#17b3a3" strokeOpacity="0.35" strokeWidth="0.75" />
      <text x="80" y="300" fill="#2dd4bf" fontSize="9" fontFamily="'Space Grotesk', sans-serif">Cross-filter active</text>
      <text x="80" y="310" fill="#4a6fa5" fontSize="8" fontFamily="monospace">1.24M → 48.2K visible · 48ms rerender</text>
      <circle cx="36" cy="302" r="5" fill="#2dd4bf" fillOpacity="0.25" />
      <circle cx="36" cy="302" r="3" fill="#2dd4bf" filter="url(#wgl-glow-sm)" />

      {/* ═══════════════════════════════════
          RIGHT: Stats panel
      ═══════════════════════════════════ */}
      <rect x="340" y="38" width="206" height="164" rx="10"
        fill="#080e1c" stroke="#21304a" strokeWidth="1" />
      <rect x="340" y="38" width="206" height="4" rx="2"
        fill="url(#wgl-brand)" />

      <text x="443" y="56" textAnchor="middle" fill="#4a6fa5" fontSize="9" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">Render Stats</text>

      {[
        { label: 'Points rendered', val: '1.24M', color: '#2dd4bf' },
        { label: 'Frame rate', val: '60 fps', color: '#17b3a3' },
        { label: 'Arrow buffer size', val: '48 MB', color: '#4d8de0' },
        { label: 'Query time', val: '112 ms', color: '#4d8de0' },
        { label: 'Render tier', val: 'WebGL', color: '#2dd4bf' },
      ].map(({ label, val, color }, i) => (
        <g key={label}>
          <rect x="350" y={62 + i * 28} width="186" height="24" rx="5"
            fill="#0d1526" stroke={color} strokeOpacity="0.2" strokeWidth="0.75" />
          <text x="362" y={75 + i * 28} fill="#4a6fa5" fontSize="8" fontFamily="'Inter', sans-serif">{label}</text>
          <text x="522" y={75 + i * 28} textAnchor="end" fill={color} fontSize="12" fontWeight="700" fontFamily="'Space Grotesk', sans-serif">{val}</text>
        </g>
      ))}

      {/* ═══════════════════════════════════
          RIGHT BOTTOM: Arrow data pipeline
      ═══════════════════════════════════ */}
      <rect x="340" y="212" width="206" height="160" rx="10"
        fill="#080e1c" stroke="#21304a" strokeWidth="1" />
      <rect x="340" y="212" width="206" height="4" rx="2"
        fill="url(#wgl-brand)" fillOpacity="0.5" />

      <text x="443" y="230" textAnchor="middle" fill="#4a6fa5" fontSize="9" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">Data pipeline</text>

      {/* Vertical pipeline */}
      {[
        { label: 'DuckDB-WASM', sub: 'columnar scan', color: '#4d8de0', y: 242 },
        { label: 'Arrow IPC', sub: 'zero-copy buffer', color: '#2dd4bf', y: 296 },
        { label: 'GPU buffers', sub: 'regl / WebGL', color: '#17b3a3', y: 322 },
      ].map(({ label, sub, color, y }) => (
        <g key={label}>
          <rect x="356" y={y} width="174" height="34" rx="6"
            fill="#0d1526" stroke={color} strokeOpacity="0.35" strokeWidth="1" />
          <circle cx="372" cy={y + 17} r="5" fill={color} fillOpacity="0.25" />
          <circle cx="372" cy={y + 17} r="3" fill={color} fillOpacity="0.7" />
          <text x="382" y={y + 13} fill={color} fillOpacity="0.9" fontSize="10" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">{label}</text>
          <text x="382" y={y + 25} fill={color} fillOpacity="0.5" fontSize="8" fontFamily="monospace">{sub}</text>
        </g>
      ))}

      {/* Connecting lines between pipeline stages */}
      <line x1="443" y1="276" x2="443" y2="296" stroke="#2dd4bf" strokeOpacity="0.3" strokeWidth="1.5" strokeDasharray="3,2" markerEnd="url(#wgl-arr)" />
      <line x1="443" y1="330" x2="443" y2="322" stroke="none" />
      <line x1="443" y1="330" x2="443" y2="350" stroke="none" />

      {/* Auto-upgrade badge */}
      <rect x="356" y="360" width="174" height="20" rx="5"
        fill="#17b3a3" fillOpacity="0.1" stroke="#17b3a3" strokeOpacity="0.4" strokeWidth="0.75" />
      <text x="443" y="373.5" textAnchor="middle" fill="#2dd4bf" fontSize="8" fontFamily="monospace">auto-upgrades to WebGL at threshold</text>

      {/* Bottom pipeline (full width) */}
      <rect x="14" y="326" width="316" height="40" rx="7"
        fill="#070d1a" stroke="#21304a" strokeWidth="0.75" />
      <text x="172" y="340" textAnchor="middle" fill="#4a6fa5" fontSize="8" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">Author never touches WebGL code</text>
      <text x="172" y="354" textAnchor="middle" fill="#4a6fa5" fillOpacity="0.6" fontSize="8" fontFamily="monospace">&lt;nubi-chart type="scatter"&gt; auto-selects renderer</text>
    </svg>
  )
}
