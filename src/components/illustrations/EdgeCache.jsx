/**
 * EdgeCache — Large illustration of content-hashed edge cache + auto pre-aggregations.
 * Shows: 500 viewer nodes fan-in → edge cache node (HIT/MISS) → 1 warehouse hit
 * + auto pre-agg rollup at bottom.
 * viewBox 560×380
 */
export default function EdgeCache({ className = '' }) {
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
        <linearGradient id="ec-brand" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="50%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="ec-bg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#0a1020" />
          <stop offset="100%" stopColor="#0c1422" />
        </linearGradient>
        <linearGradient id="ec-cache-glow" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#111a2e" />
          <stop offset="100%" stopColor="#0d1526" />
        </linearGradient>
        <filter id="ec-glow">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <marker id="ec-arr-teal" markerWidth="7" markerHeight="7" refX="3.5" refY="3.5" orient="auto">
          <path d="M 0 0 L 7 3.5 L 0 7 Z" fill="#2dd4bf" fillOpacity="0.7" />
        </marker>
        <marker id="ec-arr-blue" markerWidth="7" markerHeight="7" refX="3.5" refY="3.5" orient="auto">
          <path d="M 0 0 L 7 3.5 L 0 7 Z" fill="#4d8de0" fillOpacity="0.7" />
        </marker>
        <marker id="ec-arr-dim" markerWidth="7" markerHeight="7" refX="3.5" refY="3.5" orient="auto">
          <path d="M 0 0 L 7 3.5 L 0 7 Z" fill="#4a6fa5" fillOpacity="0.5" />
        </marker>
      </defs>

      {/* Background */}
      <rect width="560" height="380" rx="12" fill="url(#ec-bg)" />
      <rect width="560" height="380" rx="12" stroke="url(#ec-brand)" strokeOpacity="0.3" strokeWidth="1" fill="none" />

      {/* Title */}
      <text x="280" y="26" textAnchor="middle" fill="#4d8de0" fontSize="11" fontWeight="600" fontFamily="'Space Grotesk', sans-serif" letterSpacing="1">EDGE CACHE + AUTO PRE-AGGREGATIONS</text>

      {/* ═══════════════════════════════════
          LEFT: Viewer nodes
      ═══════════════════════════════════ */}
      <text x="64" y="50" textAnchor="middle" fill="#17b3a3" fillOpacity="0.8" fontSize="10" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">500 viewers</text>

      {/* 8 viewer nodes */}
      {[
        { y: 58, label: 'User A', offset: 0 },
        { y: 84, label: 'User B', offset: 4 },
        { y: 110, label: 'User C', offset: -3 },
        { y: 136, label: 'User D', offset: 6 },
        { y: 162, label: 'User E', offset: -2 },
        { y: 188, label: 'User F', offset: 5 },
        { y: 214, label: 'User G', offset: -4 },
        { y: 240, label: '…496 more', offset: 2 },
      ].map(({ y, label, offset }, i) => (
        <g key={label}>
          <rect x="10" y={y} width="108" height="22" rx="6"
            fill="#111a2e" stroke="#21304a" strokeWidth="0.75"
            opacity={i === 7 ? 0.5 : 1} />
          {/* Avatar */}
          <circle cx="24" cy={y + 11} r="7" fill="#1b2363" fillOpacity="0.6" />
          <circle cx="24" cy={y + 11} r="4" fill="#2456a6" fillOpacity="0.6" />
          <text x="36" y={y + 15.5} fill="#93a4bd" fontSize="9" fontFamily="'Inter', sans-serif">{label}</text>
          {/* Fan-in line to cache */}
          <line
            x1="118" y1={y + 11}
            x2="198" y2="170"
            stroke="#2456a6" strokeOpacity={i === 7 ? 0.1 : 0.25} strokeWidth="1"
            markerEnd="url(#ec-arr-dim)"
          />
        </g>
      ))}

      {/* ═══════════════════════════════════
          CENTER: Edge cache
      ═══════════════════════════════════ */}
      <rect x="200" y="100" width="156" height="140" rx="10"
        fill="url(#ec-cache-glow)" stroke="#2456a6" strokeOpacity="0.7" strokeWidth="2" />
      {/* Top accent */}
      <rect x="200" y="100" width="156" height="4" rx="2"
        fill="url(#ec-brand)" />

      <text x="278" y="124" textAnchor="middle" fill="#4d8de0" fontSize="13" fontWeight="700" fontFamily="'Space Grotesk', sans-serif">Edge Cache</text>
      <text x="278" y="138" textAnchor="middle" fill="#4a6fa5" fontSize="9" fontFamily="monospace">content-addressed</text>

      {/* Hash key visual */}
      <rect x="212" y="144" width="132" height="20" rx="5" fill="#0a1020" stroke="#21304a" strokeWidth="0.75" />
      <text x="278" y="157.5" textAnchor="middle" fill="#93a4bd" fillOpacity="0.7" fontSize="8" fontFamily="monospace">sha256(plan + JWT claims)</text>

      {/* HIT badge */}
      <rect x="212" y="170" width="56" height="22" rx="6"
        fill="#17b3a3" fillOpacity="0.12" stroke="#17b3a3" strokeOpacity="0.6" strokeWidth="1" />
      <circle cx="224" cy="181" r="4" fill="#2dd4bf" fillOpacity="0.8" filter="url(#ec-glow)" />
      <text x="244" y="184.5" textAnchor="middle" fill="#2dd4bf" fontSize="11" fontWeight="700" fontFamily="'Space Grotesk', sans-serif">HIT</text>

      {/* MISS badge */}
      <rect x="276" y="170" width="68" height="22" rx="6"
        fill="#2456a6" fillOpacity="0.1" stroke="#2456a6" strokeOpacity="0.4" strokeWidth="0.75" />
      <text x="310" y="184.5" textAnchor="middle" fill="#4d8de0" fontSize="11" fontFamily="'Space Grotesk', sans-serif">MISS →</text>

      {/* 1 warehouse hit stat */}
      <rect x="212" y="200" width="132" height="30" rx="7"
        fill="#17b3a3" fillOpacity="0.08" stroke="#17b3a3" strokeOpacity="0.4" strokeWidth="0.75" />
      <text x="278" y="217" textAnchor="middle" fill="#2dd4bf" fontSize="16" fontWeight="700" fontFamily="'Space Grotesk', sans-serif">1</text>
      <text x="278" y="228" textAnchor="middle" fill="#17b3a3" fillOpacity="0.7" fontSize="8" fontFamily="'Inter', sans-serif">warehouse hit for 500 viewers</text>

      {/* Hit-back arrows: cache → viewers */}
      <text x="278" y="252" textAnchor="middle" fill="#2dd4bf" fillOpacity="0.6" fontSize="8" fontFamily="monospace">← serve from cache →</text>

      {/* ═══════════════════════════════════
          RIGHT: Warehouse
      ═══════════════════════════════════ */}
      <rect x="380" y="88" width="164" height="152" rx="10"
        fill="#080e1c" stroke="#21304a" strokeOpacity="0.6" strokeWidth="1" />

      <text x="462" y="110" textAnchor="middle" fill="#4a6fa5" fontSize="11" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">Warehouse</text>

      {/* Cylinder */}
      <ellipse cx="462" cy="130" rx="44" ry="12" fill="#0d1526" stroke="#21304a" strokeWidth="1" />
      <rect x="418" y="130" width="88" height="64" fill="#0d1526" />
      <ellipse cx="462" cy="194" rx="44" ry="12" fill="#0d1526" stroke="#21304a" strokeWidth="1" />

      {/* DB label */}
      <text x="462" y="156" textAnchor="middle" fill="#4a6fa5" fillOpacity="0.6" fontSize="9" fontFamily="monospace">BigQuery</text>
      <text x="462" y="170" textAnchor="middle" fill="#4a6fa5" fillOpacity="0.5" fontSize="9" fontFamily="monospace">Snowflake</text>
      <text x="462" y="184" textAnchor="middle" fill="#4a6fa5" fillOpacity="0.4" fontSize="9" fontFamily="monospace">Redshift</text>

      {/* Miss arrow: cache → warehouse */}
      <line x1="356" y1="178" x2="376" y2="162"
        stroke="#4d8de0" strokeOpacity="0.6" strokeWidth="1.5" strokeDasharray="5,3"
        markerEnd="url(#ec-arr-blue)" />
      <text x="367" y="168" textAnchor="middle" fill="#4d8de0" fontSize="8" fontFamily="monospace">MISS</text>

      {/* Result back arrow: warehouse → cache */}
      <line x1="378" y1="200" x2="358" y2="216"
        stroke="#2dd4bf" strokeOpacity="0.5" strokeWidth="1.5"
        markerEnd="url(#ec-arr-teal)" />

      {/* ═══════════════════════════════════
          BOTTOM: Auto pre-agg section
      ═══════════════════════════════════ */}
      <line x1="278" y1="244" x2="278" y2="274"
        stroke="#2456a6" strokeOpacity="0.4" strokeWidth="1.5" markerEnd="url(#ec-arr-blue)" />

      <rect x="60" y="276" width="440" height="80" rx="10"
        fill="#0d1526" stroke="#17b3a3" strokeOpacity="0.5" strokeWidth="1.5" />
      <rect x="60" y="276" width="440" height="4" rx="2"
        fill="url(#ec-brand)" fillOpacity="0.5" />

      <text x="280" y="298" textAnchor="middle" fill="#2dd4bf" fontSize="13" fontWeight="700" fontFamily="'Space Grotesk', sans-serif">Auto Pre-aggregations</text>
      <text x="280" y="312" textAnchor="middle" fill="#4a6fa5" fontSize="9" fontFamily="'Inter', sans-serif">Query log feeds rollup suggester → builds pre-agg tables automatically</text>

      {/* Rollup bar chart mini */}
      {[
        { x: 90, h: 26, label: 'daily' },
        { x: 142, h: 40, label: 'weekly' },
        { x: 194, h: 20, label: 'monthly' },
        { x: 246, h: 50, label: 'by region' },
        { x: 298, h: 32, label: 'by product' },
        { x: 350, h: 44, label: 'by channel' },
        { x: 402, h: 22, label: 'cohorts' },
        { x: 454, h: 36, label: 'funnels' },
      ].map(({ x, h, label }, i) => (
        <g key={label}>
          <rect x={x} y={348 - h} width="36" height={h} rx="3"
            fill="#2456a6" fillOpacity={0.2 + (i / 7) * 0.4} />
          <rect x={x} y={348 - h} width="36" height="3" rx="1.5"
            fill="url(#ec-brand)" fillOpacity="0.7" />
          <text x={x + 18} y="355" textAnchor="middle" fill="#4a6fa5" fontSize="7" fontFamily="monospace">{label}</text>
        </g>
      ))}
    </svg>
  )
}
