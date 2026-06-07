/**
 * EmbedAuth — Auth-as-code embedding.
 * A JWT token written as code (segmented header.payload.signature chain with a
 * </> mark) plugs through a bold shield-check lock into a solid embedded
 * dashboard window. One strong focal (secured embed), one distinctive auth
 * element (the code-token). Premium, high-contrast. Textless. Light + dark safe.
 */
export default function EmbedAuth({ className = '' }) {
  // JWT token segments (header . payload . signature) — auth as code
  const segs = [
    { x: 84, w: 30, fill: 'url(#ea-seg-a)' },
    { x: 120, w: 30, fill: 'url(#ea-seg-b)' },
    { x: 156, w: 26, fill: 'url(#ea-seg-c)' },
  ]
  return (
    <svg viewBox="0 0 480 300" fill="none" xmlns="http://www.w3.org/2000/svg"
      className={className} aria-hidden="true" width="100%" height="auto"
      preserveAspectRatio="xMidYMid meet">
      <defs>
        <linearGradient id="ea-glass" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" stopOpacity="0.2" />
          <stop offset="100%" stopColor="#1b2363" stopOpacity="0.08" />
        </linearGradient>
        <linearGradient id="ea-chrome" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#2456a6" stopOpacity="0.26" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.16" />
        </linearGradient>
        <linearGradient id="ea-border" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.7" />
          <stop offset="60%" stopColor="#2456a6" stopOpacity="0.4" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.3" />
        </linearGradient>
        <linearGradient id="ea-shield" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" />
          <stop offset="55%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2456a6" />
        </linearGradient>
        <linearGradient id="ea-area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.4" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0" />
        </linearGradient>
        <linearGradient id="ea-line" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#2456a6" />
          <stop offset="55%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2dd4bf" />
        </linearGradient>
        <linearGradient id="ea-seg-a" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" /><stop offset="100%" stopColor="#1b2363" />
        </linearGradient>
        <linearGradient id="ea-seg-b" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#3b6fd4" /><stop offset="100%" stopColor="#2456a6" />
        </linearGradient>
        <linearGradient id="ea-seg-c" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" /><stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <radialGradient id="ea-bloom" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#17b3a3" stopOpacity="0.42" />
          <stop offset="55%" stopColor="#2456a6" stopOpacity="0.12" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0" />
        </radialGradient>
        <filter id="ea-shadow" x="-30%" y="-30%" width="160%" height="160%">
          <feDropShadow dx="0" dy="7" stdDeviation="12" floodColor="#1b2363" floodOpacity="0.28" />
        </filter>
        <filter id="ea-glow" x="-80%" y="-80%" width="260%" height="260%">
          <feGaussianBlur stdDeviation="5" result="b" />
          <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <clipPath id="ea-clip">
          <rect x="8" y="8" width="464" height="284" rx="22" />
        </clipPath>
      </defs>

      <g clipPath="url(#ea-clip)">
        {/* Ambient bloom behind the shield */}
        <ellipse cx="212" cy="150" rx="160" ry="130" fill="url(#ea-bloom)" />

        {/* ── Embedded dashboard window (hero, right) ── */}
        <g filter="url(#ea-shadow)">
          <rect x="214" y="68" width="220" height="168" rx="18" fill="url(#ea-glass)" />
        </g>
        <rect x="214" y="68" width="220" height="168" rx="18" stroke="url(#ea-border)" strokeWidth="2" />
        {/* chrome bar */}
        <path d="M 214 92 L 214 86 Q 214 68 232 68 L 416 68 Q 434 68 434 86 L 434 92 Z" fill="url(#ea-chrome)" />
        <line x1="214" y1="96" x2="434" y2="96" stroke="#2dd4bf" strokeOpacity="0.25" strokeWidth="1" />
        <circle cx="234" cy="84" r="3.5" fill="#2dd4bf" fillOpacity="0.8" />
        <circle cx="246" cy="84" r="3.5" fill="#17b3a3" fillOpacity="0.65" />
        <circle cx="258" cy="84" r="3.5" fill="#2456a6" fillOpacity="0.6" />
        <rect x="276" y="80" width="120" height="9" rx="4.5" fill="#2456a6" fillOpacity="0.2" />
        {/* embedded chart */}
        <path d="M 248 200 C 274 192, 292 204, 320 176 C 350 146, 380 162, 412 132 L 412 218 L 248 218 Z"
          fill="url(#ea-area)" />
        <path d="M 248 200 C 274 192, 292 204, 320 176 C 350 146, 380 162, 412 132"
          stroke="url(#ea-line)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
        <line x1="248" y1="218" x2="412" y2="218" stroke="#2456a6" strokeOpacity="0.22" strokeWidth="1" />
        <circle cx="320" cy="176" r="4" fill="#17b3a3" stroke="#ffffff" strokeWidth="1.5" strokeOpacity="0.8" />
        <g filter="url(#ea-glow)"><circle cx="412" cy="132" r="5.5" fill="#2dd4bf" /></g>

        {/* ── Auth-as-code token chain (left) ── */}
        {/* </> mark */}
        <path d="M 50 142 L 43 150 L 50 158 M 58 160 L 66 140 M 74 142 L 81 150 L 74 158"
          stroke="#2dd4bf" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
        {/* segments + connector dots — aligned on one line into the shield */}
        {segs.map((s, i) => (
          <g key={`seg${i}`}>
            {i > 0 && <circle cx={s.x - 4} cy="150" r="2.5" fill="#2dd4bf" fillOpacity="0.7" />}
            <rect x={s.x} y="142" width={s.w} height="16" rx="8" fill={s.fill} />
            <line x1={s.x + 7} y1="150" x2={s.x + s.w - 7} y2="150"
              stroke="#ffffff" strokeOpacity="0.4" strokeWidth="1.5" strokeDasharray="2 3" strokeLinecap="round" />
          </g>
        ))}

        {/* ── Shield-check lock (clamped on window's left edge) ── */}
        <circle cx="212" cy="150" r="44" fill="url(#ea-bloom)" />
        <g filter="url(#ea-glow)">
          <path d="M 212 110 L 240 122 L 240 150 C 240 173, 228 187, 212 195
                   C 196 187, 184 173, 184 150 L 184 122 Z" fill="url(#ea-shield)" />
        </g>
        <path d="M 212 116 L 234 125 L 234 150 C 234 169, 224 181, 212 188"
          stroke="#ffffff" strokeOpacity="0.22" strokeWidth="1.75" strokeLinecap="round" fill="none" />
        <path d="M 199 151 L 208 160 L 226 139"
          stroke="#ffffff" strokeWidth="3.6" strokeLinecap="round" strokeLinejoin="round" />

        {/* Ambient particles */}
        <circle cx="28" cy="76" r="2.5" fill="#2dd4bf" fillOpacity="0.3" />
        <circle cx="30" cy="220" r="2" fill="#17b3a3" fillOpacity="0.28" />
        <circle cx="120" cy="110" r="2" fill="#2dd4bf" fillOpacity="0.3" />
        <circle cx="452" cy="250" r="2" fill="#2456a6" fillOpacity="0.3" />
      </g>
    </svg>
  )
}
