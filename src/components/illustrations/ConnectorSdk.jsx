/**
 * ConnectorSdk — SQL-first connector SDK. Metaphor: distinct data sources (DB,
 * ƒ, cloud) fan-in to a central SDK {} module that turns them into a live
 * dashboard (chart + donut + table). Flat line-art. Reads on white + dark-navy.
 */
export default function ConnectorSdk({ className = '' }) {
  const sources = [100, 180, 256]
  const hub = { x: 200, y: 182 }
  const curve = 'M 280 168 C 294 164, 302 156, 318 152 C 334 148, 342 156, 356 142 C 366 132, 370 136, 376 130'
  const area = `${curve} L 376 180 L 280 180 Z`
  const C = 106.8 // donut r=17
  const segs = [
    { len: C * 0.46, off: 0, color: '#2456a6' },
    { len: C * 0.30, off: C * 0.46, color: '#17b3a3' },
    { len: C * 0.24, off: C * 0.76, color: '#2dd4bf' },
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
        <linearGradient id="con-stroke" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="45%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="con-mod" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" />
          <stop offset="55%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2dd4bf" />
        </linearGradient>
        <linearGradient id="con-area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.26" />
          <stop offset="100%" stopColor="#2456a6" stopOpacity="0.0" />
        </linearGradient>
        <clipPath id="con-clip">
          <rect x="16" y="40" width="448" height="280" rx="16" />
        </clipPath>
      </defs>

      <g clipPath="url(#con-clip)">
        {/* fan-in connectors */}
        {sources.map((y, i) => (
          <path key={i} d={`M 92 ${y} C 134 ${y}, 142 ${hub.y}, 160 ${hub.y}`}
            stroke="#2456a6" strokeWidth="1.75" strokeOpacity="0.4" fill="none" />
        ))}

        {/* source: database cylinder */}
        <ellipse cx="64" cy="92" rx="22" ry="7" fill="#2456a6" fillOpacity="0.08" stroke="url(#con-stroke)" strokeWidth="1.75" />
        <path d="M 42 92 L 42 120 A 22 7 0 0 0 86 120 L 86 92" fill="#2456a6" fillOpacity="0.05" stroke="url(#con-stroke)" strokeWidth="1.75" />
        {/* source: function ƒ */}
        <rect x="40" y="162" width="48" height="36" rx="9" fill="#2456a6" fillOpacity="0.05" stroke="url(#con-stroke)" strokeWidth="1.75" />
        <path d="M 70 172 C 64 172, 62 174, 61 180 L 56 180 M 67 180 L 54 180 C 52 188, 56 190, 58 188"
          stroke="#17b3a3" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        {/* source: cloud */}
        <path d="M 50 268 C 44 268, 40 262, 46 258 C 46 250, 58 248, 62 254 C 70 248, 82 254, 80 262 C 88 262, 90 270, 82 272 Z"
          fill="#2456a6" fillOpacity="0.05" stroke="url(#con-stroke)" strokeWidth="1.75" strokeLinejoin="round" />

        {/* SDK hub {} */}
        <rect x="160" y="152" width="80" height="60" rx="14" fill="#2456a6" fillOpacity="0.06" stroke="url(#con-stroke)" strokeWidth="2" />
        <path d="M 192 166 C 184 166, 186 178, 178 182 C 186 186, 184 198, 192 198"
          stroke="url(#con-mod)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        <path d="M 208 166 C 216 166, 214 178, 222 182 C 214 186, 216 198, 208 198"
          stroke="url(#con-mod)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />

        {/* hub → dashboard */}
        <line x1="240" y1="182" x2="262" y2="182" stroke="url(#con-stroke)" strokeWidth="2.25" strokeLinecap="round" />
        <circle cx="251" cy="182" r="3.5" fill="#17b3a3" />

        {/* output dashboard window */}
        <rect x="262" y="66" width="198" height="228" rx="14" fill="#2456a6" fillOpacity="0.04" stroke="url(#con-stroke)" strokeWidth="2" />
        <line x1="262" y1="92" x2="460" y2="92" stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.4" />
        <circle cx="276" cy="79" r="3" fill="#2456a6" fillOpacity="0.45" />
        <circle cx="288" cy="79" r="3" fill="#17b3a3" fillOpacity="0.55" />

        {/* chart panel */}
        <rect x="274" y="102" width="104" height="86" rx="9" fill="#2456a6" fillOpacity="0.04" stroke="#2456a6" strokeWidth="1.25" strokeOpacity="0.28" />
        <path d={area} fill="url(#con-area)" />
        <path d={curve} stroke="url(#con-stroke)" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" />

        {/* donut panel */}
        <rect x="384" y="102" width="64" height="86" rx="9" fill="#2456a6" fillOpacity="0.04" stroke="#2456a6" strokeWidth="1.25" strokeOpacity="0.28" />
        <g transform="rotate(-90 416 145)">
          {segs.map((s, i) => (
            <circle key={i} cx="416" cy="145" r="17" fill="none" stroke={s.color} strokeWidth="8.5"
              strokeDasharray={`${s.len} ${C}`} strokeDashoffset={-s.off} />
          ))}
        </g>

        {/* table panel */}
        <rect x="274" y="196" width="174" height="86" rx="9" fill="#2456a6" fillOpacity="0.04" stroke="#2456a6" strokeWidth="1.25" strokeOpacity="0.28" />
        {[286, 350, 408].map((x, i) => (
          <rect key={i} x={x} y="208" width={i === 0 ? 44 : 32} height="6" rx="3" fill="#2456a6" fillOpacity="0.4" />
        ))}
        <line x1="274" y1="222" x2="448" y2="222" stroke="#2456a6" strokeWidth="1" strokeOpacity="0.18" />
        {[0, 1, 2].map((r) => {
          const y = 236 + r * 15
          return (
            <g key={r}>
              <circle cx="294" cy={y} r="3.5" fill={['#2456a6', '#17b3a3', '#2dd4bf'][r]} />
              <rect x="306" y={y - 3} width="38" height="6" rx="3" fill="#2456a6" fillOpacity="0.3" />
              <rect x="350" y={y - 3} width="36" height="6" rx="3" fill="#2456a6" fillOpacity="0.2" />
              <rect x="408" y={y - 5} width="36" height="11" rx="5.5" fill="#17b3a3" fillOpacity="0.14" stroke="#17b3a3" strokeWidth="1" strokeOpacity="0.45" />
            </g>
          )
        })}
      </g>
    </svg>
  )
}
