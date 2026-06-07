/**
 * ConnectorSdk — SQL-first connector SDK.
 * Source connectors (DB, function, API) plug into a SQL console showing a bold,
 * legible query (prompt + syntax tokens + cursor) over a clean result — the
 * SQL-first interface — which exposes one unified, queryable dataset.
 * Premium, high-contrast. Textless. Light + dark safe.
 */
export default function ConnectorSdk({ className = '' }) {
  // source connectors (left) → console ports
  const ports = [120, 150, 180]
  return (
    <svg viewBox="0 0 480 300" fill="none" xmlns="http://www.w3.org/2000/svg"
      className={className} aria-hidden="true" width="100%" height="auto"
      preserveAspectRatio="xMidYMid meet">
      <defs>
        <linearGradient id="csk-brand" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="45%" stopColor="#2456a6" />
          <stop offset="80%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2dd4bf" />
        </linearGradient>
        <linearGradient id="csk-src" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#3b6fd4" /><stop offset="100%" stopColor="#2456a6" />
        </linearGradient>
        <linearGradient id="csk-cyl-top" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" /><stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="csk-glass" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" stopOpacity="0.2" />
          <stop offset="100%" stopColor="#1b2363" stopOpacity="0.08" />
        </linearGradient>
        <linearGradient id="csk-chrome" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#2456a6" stopOpacity="0.26" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.16" />
        </linearGradient>
        <linearGradient id="csk-border" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.7" />
          <stop offset="60%" stopColor="#2456a6" stopOpacity="0.4" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.3" />
        </linearGradient>
        <linearGradient id="csk-layer" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#2dd4bf" /><stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <radialGradient id="csk-bloom" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#17b3a3" stopOpacity="0.4" />
          <stop offset="55%" stopColor="#2456a6" stopOpacity="0.12" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0" />
        </radialGradient>
        <filter id="csk-shadow" x="-30%" y="-30%" width="160%" height="160%">
          <feDropShadow dx="0" dy="7" stdDeviation="12" floodColor="#1b2363" floodOpacity="0.26" />
        </filter>
        <filter id="csk-glow" x="-80%" y="-80%" width="260%" height="260%">
          <feGaussianBlur stdDeviation="4.5" result="b" />
          <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <clipPath id="csk-clip">
          <rect x="8" y="8" width="464" height="284" rx="22" />
        </clipPath>
      </defs>

      <g clipPath="url(#csk-clip)">
        {/* Ambient bloom behind console */}
        <ellipse cx="262" cy="150" rx="180" ry="135" fill="url(#csk-bloom)" />

        {/* ── Plug cables: sources → console ports ── */}
        <path d="M 76 100 C 120 102, 134 120, 168 120"
          stroke="url(#csk-brand)" strokeWidth="2.25" strokeLinecap="round" strokeDasharray="5 7" />
        <path d="M 78 150 C 120 150, 134 150, 168 150"
          stroke="url(#csk-brand)" strokeWidth="2.25" strokeLinecap="round" strokeDasharray="5 7" />
        <path d="M 76 200 C 120 198, 134 180, 168 180"
          stroke="url(#csk-brand)" strokeWidth="2.25" strokeLinecap="round" strokeDasharray="5 7" />
        {ports.map((y, i) => (
          <circle key={`port${i}`} cx="169" cy={y} r="4" fill="#2dd4bf" />
        ))}

        {/* ── Source 1: DB cylinder ── */}
        <g filter="url(#csk-shadow)">
          <path d="M 38 90 L 38 110 A 18 5.5 0 0 0 74 110 L 74 90 Z" fill="url(#csk-src)" />
          <ellipse cx="56" cy="90" rx="18" ry="5.5" fill="url(#csk-cyl-top)" />
        </g>
        <line x1="40" y1="99" x2="72" y2="99" stroke="#2dd4bf" strokeOpacity="0.3" strokeWidth="1.5" />
        <ellipse cx="56" cy="90" rx="18" ry="5.5" stroke="#ffffff" strokeOpacity="0.25" strokeWidth="1.25" />

        {/* ── Source 2: function ── */}
        <g filter="url(#csk-shadow)"><circle cx="56" cy="150" r="21" fill="url(#csk-src)" /></g>
        <path d="M 50 162 C 50 153, 53 153, 53 146 C 53 141, 56 141, 60 141"
          stroke="#ffffff" strokeOpacity="0.92" strokeWidth="2.2" strokeLinecap="round" fill="none" />
        <line x1="48" y1="151" x2="62" y2="151" stroke="#ffffff" strokeOpacity="0.7" strokeWidth="2.2" strokeLinecap="round" />

        {/* ── Source 3: API hexagon ── */}
        <g filter="url(#csk-shadow)">
          <path d="M 56 184 L 74 194 L 74 214 L 56 224 L 38 214 L 38 194 Z" fill="url(#csk-src)" />
        </g>
        <path d="M 50 200 L 45 204 L 50 208 M 62 200 L 67 204 L 62 208 M 59 198 L 53 210"
          stroke="#ffffff" strokeOpacity="0.9" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" fill="none" />

        {/* ── SQL console (hero) ── */}
        <g filter="url(#csk-shadow)">
          <rect x="168" y="80" width="184" height="140" rx="16" fill="url(#csk-glass)" />
        </g>
        <rect x="168" y="80" width="184" height="140" rx="16" stroke="url(#csk-border)" strokeWidth="2" />
        {/* editor title bar */}
        <path d="M 168 102 L 168 96 Q 168 80 184 80 L 336 80 Q 352 80 352 96 L 352 102 Z" fill="url(#csk-chrome)" />
        <line x1="168" y1="102" x2="352" y2="102" stroke="#2dd4bf" strokeOpacity="0.22" strokeWidth="1" />
        <circle cx="184" cy="91" r="3" fill="#2dd4bf" fillOpacity="0.75" />
        <circle cx="194" cy="91" r="3" fill="#17b3a3" fillOpacity="0.6" />
        <circle cx="204" cy="91" r="3" fill="#2456a6" fillOpacity="0.55" />

        {/* SQL query — prompt + syntax tokens + cursor */}
        <path d="M 184 122 L 190 128 L 184 134" stroke="#2dd4bf" strokeWidth="2.2"
          strokeLinecap="round" strokeLinejoin="round" />
        <rect x="200" y="124.5" width="40" height="7" rx="3.5" fill="#2dd4bf" />
        <rect x="246" y="124.5" width="26" height="7" rx="3.5" fill="#2456a6" fillOpacity="0.32" />
        <rect x="278" y="124.5" width="14" height="7" rx="3.5" fill="#7af0e4" />
        {/* line 2 (indented) */}
        <rect x="200" y="143" width="32" height="7" rx="3.5" fill="#3b6fd4" />
        <rect x="238" y="143" width="50" height="7" rx="3.5" fill="#2456a6" fillOpacity="0.28" />
        <rect x="294" y="141.5" width="7" height="10" rx="1.5" fill="#2dd4bf" />

        {/* divider → result */}
        <line x1="184" y1="164" x2="336" y2="164" stroke="#2456a6" strokeOpacity="0.2" strokeWidth="1" />
        {[176, 192].map((y, r) => (
          <g key={`row${r}`}>
            <rect x="184" y={y} width="38" height="9" rx="3"
              fill={r === 0 ? '#2dd4bf' : '#2456a6'} fillOpacity={r === 0 ? 0.5 : 0.24} />
            <rect x="228" y={y} width="46" height="9" rx="3" fill="#2456a6" fillOpacity="0.16" />
            <rect x="280" y={y} width="52" height="9" rx="3" fill="#2456a6" fillOpacity="0.16" />
          </g>
        ))}

        {/* ── Unified queryable dataset (right) ── */}
        <line x1="352" y1="150" x2="378" y2="150" stroke="url(#csk-layer)" strokeWidth="3" strokeLinecap="round" />
        <path d="M 382 143 L 389 150 L 382 157 M 392 143 L 399 150 L 392 157"
          stroke="#2dd4bf" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="424" cy="150" r="30" fill="url(#csk-bloom)" />
        <g filter="url(#csk-glow)">
          <rect x="406" y="132" width="38" height="12" rx="4" fill="url(#csk-layer)" fillOpacity="0.55" />
          <rect x="406" y="146" width="38" height="12" rx="4" fill="url(#csk-layer)" fillOpacity="0.8" />
          <rect x="406" y="160" width="38" height="12" rx="4" fill="url(#csk-layer)" />
        </g>
        <rect x="406" y="160" width="38" height="12" rx="4" stroke="#ffffff" strokeOpacity="0.35" strokeWidth="1" />

        {/* Ambient particles */}
        <circle cx="26" cy="58" r="2.5" fill="#2dd4bf" fillOpacity="0.3" />
        <circle cx="26" cy="244" r="2" fill="#17b3a3" fillOpacity="0.28" />
        <circle cx="262" cy="40" r="2.5" fill="#2dd4bf" fillOpacity="0.26" />
        <circle cx="262" cy="262" r="2" fill="#2456a6" fillOpacity="0.3" />
      </g>
    </svg>
  )
}
