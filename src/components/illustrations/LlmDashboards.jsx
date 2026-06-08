/**
 * LlmDashboards — LLMs author dashboards. Metaphor: an AI sparkle + prompt line
 * emits an arrow that builds a dashboard window (chart + tiles). Flat line-art,
 * no blur/glow. Reads on white + dark-navy.
 */
export default function LlmDashboards({ className = '' }) {
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
        <clipPath id="llm-clip">
          <rect x="20" y="40" width="440" height="280" rx="16" />
        </clipPath>
      </defs>

      <g clipPath="url(#llm-clip)">
        {/* AI spark (4-point star) — the LLM */}
        <path d="M 96 110 C 100 138, 110 148, 138 152 C 110 156, 100 166, 96 194 C 92 166, 82 156, 54 152 C 82 148, 92 138, 96 110 Z"
          fill="url(#llm-spark)" />
        {/* small twinkle */}
        <path d="M 138 196 C 140 208, 144 212, 156 214 C 144 216, 140 220, 138 232 C 136 220, 132 216, 120 214 C 132 212, 136 208, 138 196 Z"
          fill="#17b3a3" fillOpacity="0.55" />

        {/* prompt lines under spark */}
        <rect x="54" y="240" width="92" height="9" rx="4.5" fill="#2456a6" fillOpacity="0.3" />
        <rect x="54" y="256" width="64" height="9" rx="4.5" fill="#2456a6" fillOpacity="0.22" />

        {/* generation arrow */}
        <path d="M 168 168 L 244 168" stroke="url(#llm-stroke)" strokeWidth="2.5" strokeLinecap="round" />
        <path d="M 234 160 L 246 168 L 234 176" stroke="url(#llm-stroke)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />

        {/* authored dashboard window */}
        <rect x="262" y="96" width="166" height="148" rx="14"
          fill="#2456a6" fillOpacity="0.05" stroke="url(#llm-stroke)" strokeWidth="2" />
        <line x1="262" y1="122" x2="428" y2="122" stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.4" />
        <circle cx="276" cy="109" r="3" fill="#2456a6" fillOpacity="0.45" />
        <circle cx="288" cy="109" r="3" fill="#17b3a3" fillOpacity="0.55" />
        {/* chart */}
        <polyline points="278,184 304,168 330,176 358,150 408,160"
          stroke="url(#llm-spark)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        {/* tiles */}
        <rect x="278" y="206" width="60" height="22" rx="6" fill="#2456a6" fillOpacity="0.08" stroke="#2456a6" strokeWidth="1.25" strokeOpacity="0.3" />
        <rect x="348" y="206" width="60" height="22" rx="6" fill="#2456a6" fillOpacity="0.08" stroke="#2456a6" strokeWidth="1.25" strokeOpacity="0.3" />
      </g>
    </svg>
  )
}
