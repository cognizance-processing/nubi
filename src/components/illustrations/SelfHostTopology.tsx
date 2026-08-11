/**
 * SelfHostTopology — the self-host deployment: browser → app container →
 * Postgres + object storage. Metaphor: a vertical node chain that branches to
 * two storage primitives (cylinder + bucket). Flat line-art, no blur/glow.
 */
export default function SelfHostTopology({ className = '' }) {
  return (
    <svg
      viewBox="82 34 316 296"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
      width="100%"
      height="auto"
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        <linearGradient id="sht-stroke" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#3b66c4" />
          <stop offset="45%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="sht-app" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <clipPath id="sht-clip">
          <rect x="82" y="34" width="316" height="296" rx="16" />
        </clipPath>
      </defs>

      <g clipPath="url(#sht-clip)">
        {/* ── browser (top) ── */}
        <rect x="164" y="48" width="152" height="64" rx="12"
          fill="#2456a6" fillOpacity="0.05" stroke="url(#sht-stroke)" strokeWidth="2" />
        {/* traffic-light dots + address line */}
        <circle cx="182" cy="64" r="3.5" fill="#87a2ce" />
        <circle cx="194" cy="64" r="3.5" fill="#17b3a3" />
        <circle cx="206" cy="64" r="3.5" fill="#2dd4bf" />
        <line x1="180" y1="82" x2="262" y2="82" stroke="#bdcce4" strokeWidth="2" strokeLinecap="round" />
        <line x1="180" y1="96" x2="232" y2="96" stroke="#bdcce4" strokeWidth="2" strokeLinecap="round" />

        {/* browser → app */}
        <path d="M 240 112 L 240 142" stroke="#9cb3d7" strokeWidth="2" strokeLinecap="round" />
        <circle cx="240" cy="127" r="4.5" fill="#17b3a3" />

        {/* ── app container (middle, focal) ── */}
        <rect x="148" y="142" width="184" height="76" rx="14"
          fill="#2456a6" fillOpacity="0.06" stroke="url(#sht-app)" strokeWidth="2.25" />
        {/* two internal services: web + api */}
        <rect x="166" y="160" width="64" height="40" rx="9"
          fill="#17b3a3" fillOpacity="0.08" stroke="#17b3a3" strokeWidth="1.75" strokeOpacity="0.7" />
        <circle cx="182" cy="180" r="4" fill="#2dd4bf" />
        <line x1="192" y1="180" x2="216" y2="180" stroke="#17b3a3" strokeWidth="2" strokeOpacity="0.45" strokeLinecap="round" />
        <rect x="250" y="160" width="64" height="40" rx="9"
          fill="#2456a6" fillOpacity="0.07" stroke="#6689c1" strokeWidth="1.75" />
        <circle cx="266" cy="180" r="4" fill="#5b80bd" />
        <line x1="276" y1="180" x2="300" y2="180" stroke="#a7bbdb" strokeWidth="2" strokeLinecap="round" />
        {/* web → api link */}
        <line x1="230" y1="180" x2="250" y2="180" stroke="url(#sht-app)" strokeWidth="2" strokeLinecap="round" />

        {/* app → storage branches */}
        <path d="M 196 218 C 196 248, 148 240, 148 266" stroke="#9cb3d7" strokeWidth="2" strokeLinecap="round" fill="none" />
        <path d="M 284 218 C 284 248, 332 240, 332 266" stroke="#9cb3d7" strokeWidth="2" strokeLinecap="round" fill="none" />
        <circle cx="172" cy="242" r="4.5" fill="#17b3a3" />
        <circle cx="308" cy="242" r="4.5" fill="#2dd4bf" />

        {/* ── Postgres cylinder (bottom-left) ── */}
        <path d="M 96 274 C 96 265, 200 265, 200 274 L 200 306 C 200 315, 96 315, 96 306 Z"
          fill="#2456a6" fillOpacity="0.05" stroke="url(#sht-stroke)" strokeWidth="2" />
        <path d="M 96 274 C 96 283, 200 283, 200 274"
          stroke="#92abd3" strokeWidth="1.75" fill="none" />

        {/* ── object storage bucket (bottom-right): stacked parquet sheets ── */}
        <rect x="280" y="266" width="104" height="48" rx="11"
          fill="#17b3a3" fillOpacity="0.05" stroke="url(#sht-stroke)" strokeWidth="2" />
        <rect x="296" y="278" width="48" height="9" rx="4.5" fill="#17b3a3" fillOpacity="0.5" />
        <rect x="296" y="291" width="64" height="9" rx="4.5" fill="#b2c4e0" />
        <circle cx="368" cy="282" r="4" fill="#2dd4bf" />
      </g>
    </svg>
  )
}
