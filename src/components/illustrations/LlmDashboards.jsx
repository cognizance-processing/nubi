/**
 * LlmDashboards — LLM-authorable dashboards.
 * A glowing AI spark (the LLM) emits into two freshly-authored dashboard cards
 * — a line chart and a bars+donut card. A small spark badge marks the output as
 * AI-generated. Premium glass + glow, grounded in the family. Textless. Light + dark safe.
 */
export default function LlmDashboards({ className = '' }) {
  const SX = 96, SY = 150   // spark center
  return (
    <svg viewBox="0 0 480 300" fill="none" xmlns="http://www.w3.org/2000/svg"
      className={className} aria-hidden="true" width="100%" height="auto"
      preserveAspectRatio="xMidYMid meet">
      <defs>
        <radialGradient id="llm-spark" cx="50%" cy="42%" r="60%">
          <stop offset="0%" stopColor="#caf7f1" />
          <stop offset="45%" stopColor="#2dd4bf" />
          <stop offset="100%" stopColor="#17b3a3" />
        </radialGradient>
        <radialGradient id="llm-bloom" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.6" />
          <stop offset="45%" stopColor="#17b3a3" stopOpacity="0.22" />
          <stop offset="100%" stopColor="#2dd4bf" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="llm-glass" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" stopOpacity="0.14" />
          <stop offset="100%" stopColor="#1b2363" stopOpacity="0.05" />
        </linearGradient>
        <linearGradient id="llm-border" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.55" />
          <stop offset="60%" stopColor="#2456a6" stopOpacity="0.32" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.2" />
        </linearGradient>
        <linearGradient id="llm-bar" x1="0" y1="1" x2="0" y2="0">
          <stop offset="0%" stopColor="#17b3a3" stopOpacity="0.55" />
          <stop offset="100%" stopColor="#2dd4bf" />
        </linearGradient>
        <linearGradient id="llm-line" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#2456a6" />
          <stop offset="55%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2dd4bf" />
        </linearGradient>
        <linearGradient id="llm-area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.3" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0" />
        </linearGradient>
        <linearGradient id="llm-donut" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#2dd4bf" />
        </linearGradient>
        <linearGradient id="llm-ray" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.85" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.2" />
        </linearGradient>
        <filter id="llm-shadow" x="-25%" y="-25%" width="150%" height="150%">
          <feDropShadow dx="0" dy="6" stdDeviation="10" floodColor="#1b2363" floodOpacity="0.22" />
        </filter>
        <filter id="llm-glow" x="-70%" y="-70%" width="240%" height="240%">
          <feGaussianBlur stdDeviation="6" result="b" />
          <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <clipPath id="llm-clip">
          <rect x="8" y="8" width="464" height="284" rx="22" />
        </clipPath>
      </defs>

      <g clipPath="url(#llm-clip)">
        {/* ── AI spark (hero, left) ── */}
        <circle cx={SX} cy={SY} r="80" fill="url(#llm-bloom)" />
        <ellipse cx={SX} cy={SY} rx="50" ry="50"
          stroke="#2dd4bf" strokeOpacity="0.16" strokeWidth="1.5" strokeDasharray="4 7" />
        <circle cx={SX + 41} cy={SY - 28} r="3.5" fill="#2dd4bf" fillOpacity="0.7" />
        <circle cx={SX - 43} cy={SY + 24} r="3" fill="#17b3a3" fillOpacity="0.6" />

        <g filter="url(#llm-glow)">
          <path d={`M ${SX} ${SY - 40}
                    C ${SX + 7} ${SY - 12}, ${SX + 12} ${SY - 7}, ${SX + 40} ${SY}
                    C ${SX + 12} ${SY + 7}, ${SX + 7} ${SY + 12}, ${SX} ${SY + 40}
                    C ${SX - 7} ${SY + 12}, ${SX - 12} ${SY + 7}, ${SX - 40} ${SY}
                    C ${SX - 12} ${SY - 7}, ${SX - 7} ${SY - 12}, ${SX} ${SY - 40} Z`}
            fill="url(#llm-spark)" />
        </g>
        <circle cx={SX} cy={SY} r="7" fill="#ffffff" fillOpacity="0.55" />

        {/* ── Authoring rays → cards ── */}
        <path d={`M ${SX + 40} ${SY - 14} C 168 116, 196 110, 230 108`}
          stroke="url(#llm-ray)" strokeWidth="2" strokeLinecap="round" strokeDasharray="4 7" />
        <path d={`M ${SX + 40} ${SY + 14} C 168 184, 196 190, 230 192`}
          stroke="url(#llm-ray)" strokeWidth="2" strokeLinecap="round" strokeDasharray="4 7" />

        {/* ── Card A: line chart (top) ── */}
        <g filter="url(#llm-shadow)">
          <rect x="232" y="80" width="212" height="60" rx="13" fill="url(#llm-glass)" />
        </g>
        <rect x="232" y="80" width="212" height="60" rx="13" stroke="url(#llm-border)" strokeWidth="1.5" />
        <circle cx="248" cy="93" r="2.5" fill="#2dd4bf" fillOpacity="0.6" />
        <circle cx="257" cy="93" r="2.5" fill="#17b3a3" fillOpacity="0.5" />
        <rect x="268" y="90" width="60" height="5" rx="2.5" fill="#2456a6" fillOpacity="0.2" />
        <path d="M 248 128 C 270 124, 286 130, 312 116 C 340 100, 368 110, 428 102 L 428 132 L 248 132 Z"
          fill="url(#llm-area)" />
        <path d="M 248 128 C 270 124, 286 130, 312 116 C 340 100, 368 110, 428 102"
          stroke="url(#llm-line)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="428" cy="102" r="4" fill="#2dd4bf" stroke="#ffffff" strokeWidth="1.5" strokeOpacity="0.7" />

        {/* small AI-authored spark badge on card A corner */}
        <path d="M 433 86 C 434.5 90, 435 90.5, 439 92 C 435 93.5, 434.5 94, 433 98
                 C 431.5 94, 431 93.5, 427 92 C 431 90.5, 431.5 90, 433 86 Z"
          fill="#2dd4bf" />

        {/* ── Card B: bars + donut (bottom) ── */}
        <g filter="url(#llm-shadow)">
          <rect x="232" y="158" width="212" height="62" rx="13" fill="url(#llm-glass)" />
        </g>
        <rect x="232" y="158" width="212" height="62" rx="13" stroke="url(#llm-border)" strokeWidth="1.5" />
        <circle cx="248" cy="171" r="2.5" fill="#2dd4bf" fillOpacity="0.6" />
        <circle cx="257" cy="171" r="2.5" fill="#17b3a3" fillOpacity="0.5" />
        <rect x="268" y="168" width="48" height="5" rx="2.5" fill="#2456a6" fillOpacity="0.2" />

        {/* donut */}
        <circle cx="266" cy="196" r="16" stroke="#2456a6" strokeOpacity="0.2" strokeWidth="7" />
        <path d="M 266 180 A 16 16 0 1 1 252 204"
          stroke="url(#llm-donut)" strokeWidth="7" strokeLinecap="round" fill="none" />
        {/* bars */}
        {[[306, 30], [326, 44], [346, 36], [366, 50]].map(([x, h], i) => (
          <rect key={`b${i}`} x={x} y={208 - h} width="13" height={h} rx="3.5" fill="url(#llm-bar)" />
        ))}
        {/* metric line */}
        <line x1="394" y1="184" x2="426" y2="184" stroke="#2456a6" strokeOpacity="0.18" strokeWidth="5" strokeLinecap="round" />
        <line x1="394" y1="184" x2="416" y2="184" stroke="url(#llm-bar)" strokeWidth="5" strokeLinecap="round" />
        <line x1="394" y1="200" x2="426" y2="200" stroke="#2456a6" strokeOpacity="0.18" strokeWidth="5" strokeLinecap="round" />
        <line x1="394" y1="200" x2="408" y2="200" stroke="url(#llm-bar)" strokeWidth="5" strokeLinecap="round" />

        {/* Ambient particles */}
        <circle cx="30" cy="66" r="2.5" fill="#2dd4bf" fillOpacity="0.3" />
        <circle cx="34" cy="238" r="2" fill="#17b3a3" fillOpacity="0.28" />
        <circle cx="180" cy="40" r="2" fill="#2dd4bf" fillOpacity="0.3" />
        <circle cx="180" cy="258" r="2" fill="#2456a6" fillOpacity="0.3" />
      </g>
    </svg>
  )
}
