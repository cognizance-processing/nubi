/**
 * TrustBoundary — connector credentials are split from plain config: a config
 * card (readable rows) links to a sealed secrets card (lock + cipher dots)
 * sitting inside a shield outline. Metaphor: split + seal. Flat line-art.
 */
export default function TrustBoundary({ className = '' }) {
  return (
    <svg
      viewBox="40 70 400 230"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
      width="100%"
      height="auto"
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        <linearGradient id="tbd-stroke" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#3b66c4" />
          <stop offset="45%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="tbd-seal" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2dd4bf" />
        </linearGradient>
        <clipPath id="tbd-clip">
          <rect x="40" y="70" width="400" height="230" rx="16" />
        </clipPath>
      </defs>

      <g clipPath="url(#tbd-clip)">
        {/* ── config card (left, plain/readable) ── */}
        <rect x="56" y="104" width="148" height="152" rx="14"
          fill="#2456a6" fillOpacity="0.05" stroke="url(#tbd-stroke)" strokeWidth="2" />
        {/* readable config rows: dot + line pairs */}
        <circle cx="80" cy="136" r="4" fill="#7c9aca" />
        <line x1="92" y1="136" x2="180" y2="136" stroke="#b2c4e0" strokeWidth="2" strokeLinecap="round" />
        <circle cx="80" cy="166" r="4" fill="#7c9aca" />
        <line x1="92" y1="166" x2="164" y2="166" stroke="#b2c4e0" strokeWidth="2" strokeLinecap="round" />
        <circle cx="80" cy="196" r="4" fill="#17b3a3" />
        <line x1="92" y1="196" x2="172" y2="196" stroke="#b2c4e0" strokeWidth="2" strokeLinecap="round" />
        <circle cx="80" cy="226" r="4" fill="#7c9aca" />
        <line x1="92" y1="226" x2="152" y2="226" stroke="#b2c4e0" strokeWidth="2" strokeLinecap="round" />

        {/* link: config → sealed secrets */}
        <path d="M 204 180 L 252 180" stroke="#9cb3d7" strokeWidth="2" strokeLinecap="round" />
        <circle cx="228" cy="180" r="4.5" fill="#17b3a3" />

        {/* ── shield (right) wrapping the sealed card ── */}
        <path d="M 340 84 L 424 110 L 424 196 C 424 240, 388 268, 340 286 C 292 268, 256 240, 256 196 L 256 110 Z"
          fill="#17b3a3" fillOpacity="0.04" stroke="url(#tbd-stroke)" strokeWidth="2" strokeLinejoin="round" />

        {/* sealed secrets card inside the shield */}
        <rect x="288" y="142" width="104" height="92" rx="12"
          fill="#2456a6" fillOpacity="0.06" stroke="url(#tbd-seal)" strokeWidth="2.25" />
        {/* cipher rows: dots only (unreadable) */}
        <g fill="#92abd3">
          <circle cx="306" cy="206" r="3" />
          <circle cx="318" cy="206" r="3" />
          <circle cx="330" cy="206" r="3" />
          <circle cx="342" cy="206" r="3" />
          <circle cx="354" cy="206" r="3" />
          <circle cx="366" cy="206" r="3" />
        </g>

        {/* lock: shackle + body, centered on the sealed card */}
        <path d="M 326 170 L 326 162 C 326 150, 354 150, 354 162 L 354 170"
          stroke="url(#tbd-seal)" strokeWidth="2.5" strokeLinecap="round" fill="none" />
        <rect x="318" y="170" width="44" height="22" rx="7"
          fill="#17b3a3" fillOpacity="0.14" stroke="url(#tbd-seal)" strokeWidth="2.25" />
        <circle cx="340" cy="181" r="3.5" fill="#17b3a3" />
      </g>
    </svg>
  )
}
