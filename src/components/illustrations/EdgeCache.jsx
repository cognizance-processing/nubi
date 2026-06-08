/**
 * EdgeCache — content-hashed edge cache: many viewers collapse to one warehouse
 * hit. Metaphor: a column of viewer nodes fan-in to a cache module, then a
 * single line out to a warehouse cylinder. Flat line-art, no blur/glow.
 */
export default function EdgeCache({ className = '' }) {
  const viewers = [78, 132, 186, 240, 294]
  const cacheX = 232, cacheY = 186 // center of cache module
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
        <linearGradient id="edg-stroke" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="45%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="edg-mod" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" />
          <stop offset="55%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2dd4bf" />
        </linearGradient>
        <clipPath id="edg-clip">
          <rect x="20" y="30" width="440" height="300" rx="16" />
        </clipPath>
      </defs>

      <g clipPath="url(#edg-clip)">
        {/* fan-in connectors: viewers → cache */}
        {viewers.map((y, i) => (
          <path key={i}
            d={`M 96 ${y} C 150 ${y}, 168 ${cacheY}, 188 ${cacheY}`}
            stroke="#2456a6" strokeWidth="1.75" strokeOpacity="0.4" fill="none" />
        ))}

        {/* viewer nodes (left) */}
        {viewers.map((y, i) => (
          <g key={i}>
            <rect x="46" y={y - 16} width="48" height="32" rx="8"
              fill="#2456a6" fillOpacity="0.05" stroke="url(#edg-stroke)" strokeWidth="1.75" />
            <circle cx="60" cy={y} r="4" fill="#17b3a3" />
            <line x1="72" y1={y - 4} x2="86" y2={y - 4} stroke="#2456a6" strokeWidth="2" strokeOpacity="0.4" strokeLinecap="round" />
            <line x1="72" y1={y + 4} x2="82" y2={y + 4} stroke="#2456a6" strokeWidth="2" strokeOpacity="0.4" strokeLinecap="round" />
          </g>
        ))}

        {/* cache module (center) */}
        <rect x="188" y="150" width="92" height="72" rx="14"
          fill="#2456a6" fillOpacity="0.06" stroke="url(#edg-stroke)" strokeWidth="2" />
        {/* stacked-layers motif = cache */}
        <rect x="206" y="166" width="56" height="12" rx="4" fill="url(#edg-mod)" />
        <rect x="206" y="184" width="56" height="12" rx="4" fill="url(#edg-mod)" fillOpacity="0.6" />
        <rect x="206" y="202" width="56" height="12" rx="4" fill="url(#edg-mod)" fillOpacity="0.35" />

        {/* single line out: cache → warehouse */}
        <path d="M 280 186 C 320 186, 332 186, 360 186"
          stroke="url(#edg-stroke)" strokeWidth="2.5" fill="none" strokeLinecap="round" />
        <circle cx="324" cy="186" r="4" fill="#17b3a3" />

        {/* warehouse cylinder (right) */}
        <g>
          <ellipse cx="404" cy="158" rx="32" ry="11" fill="#2456a6" fillOpacity="0.08" stroke="url(#edg-stroke)" strokeWidth="2" />
          <path d="M 372 158 L 372 214 A 32 11 0 0 0 436 214 L 436 158"
            fill="#2456a6" fillOpacity="0.05" stroke="url(#edg-stroke)" strokeWidth="2" />
          <path d="M 372 186 A 32 11 0 0 0 436 186" stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.35" fill="none" />
        </g>
      </g>
    </svg>
  )
}
