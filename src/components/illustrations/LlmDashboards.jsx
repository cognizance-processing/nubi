/**
 * LlmDashboards — LLMs author dashboards. Metaphor: a natural-language prompt
 * card flows through an AI spark, which generates a full dashboard (chart +
 * donut + tiles) in a window. Flat line-art, no blur/glow. Light + dark safe.
 */
export default function LlmDashboards({ className = '' }) {
  // donut: r=15, C≈94.2
  const C = 94.2
  const segs = [
    { len: C * 0.45, off: 0, color: '#2456a6' },
    { len: C * 0.32, off: C * 0.45, color: '#17b3a3' },
    { len: C * 0.23, off: C * 0.77, color: '#2dd4bf' },
  ]
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
        <linearGradient id="llm-stroke" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="45%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="llm-spark" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="llm-area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.3" />
          <stop offset="100%" stopColor="#2456a6" stopOpacity="0.0" />
        </linearGradient>
        <clipPath id="llm-clip">
          <rect x="20" y="40" width="440" height="280" rx="16" />
        </clipPath>
      </defs>

      <g clipPath="url(#llm-clip)">
        {/* ── prompt card (input) ── */}
        <rect x="40" y="120" width="118" height="78" rx="12"
          fill="#2456a6" fillOpacity="0.05" stroke="url(#llm-stroke)" strokeWidth="2" />
        {/* prompt caret + text lines */}
        <rect x="54" y="138" width="8" height="14" rx="2" fill="#17b3a3" />
        <rect x="68" y="139" width="74" height="7" rx="3.5" fill="#2456a6" fillOpacity="0.34" />
        <rect x="54" y="158" width="88" height="7" rx="3.5" fill="#2456a6" fillOpacity="0.26" />
        <rect x="54" y="172" width="56" height="7" rx="3.5" fill="#2456a6" fillOpacity="0.2" />

        {/* prompt → spark connector */}
        <path d="M 158 159 C 174 159, 178 159, 190 159"
          stroke="url(#llm-stroke)" strokeWidth="2.25" strokeLinecap="round" fill="none" />

        {/* ── AI spark (the LLM) ── */}
        <path d="M 220 116 C 226 148, 238 160, 270 166 C 238 172, 226 184, 220 216 C 214 184, 202 172, 170 166 C 202 160, 214 148, 220 116 Z"
          fill="url(#llm-spark)" />
        {/* small twinkle */}
        <path d="M 256 196 C 258 210, 263 215, 277 217 C 263 219, 258 224, 256 238 C 254 224, 249 219, 235 217 C 249 215, 254 210, 256 196 Z"
          fill="#17b3a3" fillOpacity="0.55" />

        {/* spark → dashboard generation arrow */}
        <path d="M 282 159 L 314 159" stroke="url(#llm-stroke)" strokeWidth="2.25" strokeLinecap="round" />
        <path d="M 305 152 L 315 159 L 305 166"
          stroke="url(#llm-stroke)" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" fill="none" />

        {/* ── generated dashboard window ── */}
        <rect x="316" y="78" width="132" height="166" rx="13"
          fill="#2456a6" fillOpacity="0.05" stroke="url(#llm-stroke)" strokeWidth="2" />
        <line x1="316" y1="102" x2="448" y2="102" stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.4" />
        <circle cx="330" cy="90" r="3" fill="#2456a6" fillOpacity="0.45" />
        <circle cx="342" cy="90" r="3" fill="#17b3a3" fillOpacity="0.55" />

        {/* chart panel */}
        <rect x="328" y="112" width="108" height="62" rx="8"
          fill="#2456a6" fillOpacity="0.04" stroke="#2456a6" strokeWidth="1.25" strokeOpacity="0.28" />
        <path d="M 338 158 C 350 150, 358 142, 372 144 C 386 146, 396 132, 412 124 L 426 122 L 426 166 L 338 166 Z"
          fill="url(#llm-area)" />
        <path d="M 338 158 C 350 150, 358 142, 372 144 C 386 146, 396 132, 412 124 L 426 122"
          stroke="url(#llm-stroke)" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        <circle cx="426" cy="122" r="3.5" fill="url(#llm-spark)" />

        {/* donut tile */}
        <rect x="328" y="182" width="50" height="50" rx="8"
          fill="#2456a6" fillOpacity="0.04" stroke="#2456a6" strokeWidth="1.25" strokeOpacity="0.28" />
        <g transform="rotate(-90 353 207)">
          {segs.map((s, i) => (
            <circle key={i} cx="353" cy="207" r="15" fill="none" stroke={s.color} strokeWidth="7.5"
              strokeDasharray={`${s.len} ${C}`} strokeDashoffset={-s.off} />
          ))}
        </g>

        {/* KPI tile */}
        <rect x="386" y="182" width="50" height="50" rx="8"
          fill="#2456a6" fillOpacity="0.04" stroke="#2456a6" strokeWidth="1.25" strokeOpacity="0.28" />
        <path d="M 397 218 l 7 -9 l 6 5 l 9 -13"
          stroke="url(#llm-spark)" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        <rect x="397" y="192" width="22" height="6" rx="3" fill="#2456a6" fillOpacity="0.34" />
      </g>
    </svg>
  )
}
