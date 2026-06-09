/**
 * LakehouseFlow — queries flow through the DuckDB engine to Parquet files in
 * object storage. Metaphor: query node → engine (rounded hex, gradient) →
 * layered parquet sheets inside a storage tray; one continuous flow line.
 * Flat line-art, no blur/glow.
 */
export default function LakehouseFlow({ className = '' }) {
  return (
    <svg
      viewBox="0 0 480 360"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
      width="100%"
      height="auto"
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        <linearGradient id="lkh-stroke" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="45%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="lkh-engine" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" />
          <stop offset="60%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2dd4bf" />
        </linearGradient>
        <clipPath id="lkh-clip">
          <rect x="20" y="30" width="440" height="300" rx="16" />
        </clipPath>
      </defs>

      <g clipPath="url(#lkh-clip)">
        {/* ── query node (left) ── */}
        <rect x="48" y="146" width="96" height="68" rx="12"
          fill="#2456a6" fillOpacity="0.05" stroke="url(#lkh-stroke)" strokeWidth="2" />
        {/* sparkline motif inside */}
        <path d="M 62 196 L 78 178 L 92 188 L 108 164 L 128 172"
          stroke="#17b3a3" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        <circle cx="108" cy="164" r="4" fill="#2dd4bf" />

        {/* query → engine */}
        <path d="M 144 180 L 192 180" stroke="#2456a6" strokeWidth="2" strokeOpacity="0.45" strokeLinecap="round" />
        <circle cx="168" cy="180" r="4.5" fill="#17b3a3" />

        {/* ── engine (center, focal rounded hex) ── */}
        <path d="M 236 116 L 276 138 L 276 222 L 236 244 L 196 222 L 196 138 Z"
          fill="#2456a6" fillOpacity="0.06" stroke="url(#lkh-engine)" strokeWidth="2.5" strokeLinejoin="round" />
        {/* engine internals: three process bars */}
        <line x1="216" y1="164" x2="256" y2="164" stroke="#2456a6" strokeWidth="2" strokeOpacity="0.4" strokeLinecap="round" />
        <line x1="216" y1="180" x2="248" y2="180" stroke="#17b3a3" strokeWidth="2" strokeOpacity="0.7" strokeLinecap="round" />
        <line x1="216" y1="196" x2="256" y2="196" stroke="#2456a6" strokeWidth="2" strokeOpacity="0.4" strokeLinecap="round" />

        {/* engine → storage */}
        <path d="M 276 180 L 324 180" stroke="#2456a6" strokeWidth="2" strokeOpacity="0.45" strokeLinecap="round" />
        <circle cx="300" cy="180" r="4.5" fill="#2dd4bf" />

        {/* ── object storage tray (right) with layered parquet sheets ── */}
        <rect x="324" y="118" width="112" height="124" rx="14"
          fill="#17b3a3" fillOpacity="0.04" stroke="url(#lkh-stroke)" strokeWidth="2" />
        {/* three offset parquet sheets (columnar stripes) */}
        <g>
          <rect x="344" y="138" width="72" height="24" rx="7"
            fill="#2456a6" fillOpacity="0.07" stroke="#2456a6" strokeOpacity="0.55" strokeWidth="1.75" />
          <line x1="356" y1="144" x2="356" y2="156" stroke="#2456a6" strokeWidth="2.5" strokeOpacity="0.4" strokeLinecap="round" />
          <line x1="368" y1="144" x2="368" y2="156" stroke="#17b3a3" strokeWidth="2.5" strokeOpacity="0.6" strokeLinecap="round" />
          <line x1="380" y1="144" x2="380" y2="156" stroke="#2456a6" strokeWidth="2.5" strokeOpacity="0.4" strokeLinecap="round" />
        </g>
        <g>
          <rect x="344" y="170" width="72" height="24" rx="7"
            fill="#2456a6" fillOpacity="0.07" stroke="#2456a6" strokeOpacity="0.55" strokeWidth="1.75" />
          <line x1="356" y1="176" x2="356" y2="188" stroke="#17b3a3" strokeWidth="2.5" strokeOpacity="0.6" strokeLinecap="round" />
          <line x1="368" y1="176" x2="368" y2="188" stroke="#2456a6" strokeWidth="2.5" strokeOpacity="0.4" strokeLinecap="round" />
          <line x1="380" y1="176" x2="380" y2="188" stroke="#2dd4bf" strokeWidth="2.5" strokeOpacity="0.7" strokeLinecap="round" />
        </g>
        <g>
          <rect x="344" y="202" width="72" height="24" rx="7"
            fill="#2456a6" fillOpacity="0.07" stroke="#2456a6" strokeOpacity="0.55" strokeWidth="1.75" />
          <line x1="356" y1="208" x2="356" y2="220" stroke="#2456a6" strokeWidth="2.5" strokeOpacity="0.4" strokeLinecap="round" />
          <line x1="368" y1="208" x2="368" y2="220" stroke="#2dd4bf" strokeWidth="2.5" strokeOpacity="0.7" strokeLinecap="round" />
          <line x1="380" y1="208" x2="380" y2="220" stroke="#17b3a3" strokeWidth="2.5" strokeOpacity="0.6" strokeLinecap="round" />
        </g>
      </g>
    </svg>
  )
}
