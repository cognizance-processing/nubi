/**
 * EmbedAuth — auth-as-code embedding. Metaphor: a JWT token passes through a
 * policy shield (check), then mounts as a real embedded dashboard (chart + donut
 * + table) in a window. Flat line-art, no blur/glow. Reads on white + dark-navy.
 */
export default function EmbedAuth({ className = '' }) {
  const curve = 'M 250 168 C 266 164, 276 154, 294 150 C 312 146, 322 154, 340 138 C 354 126, 360 130, 368 122'
  const area = `${curve} L 368 182 L 250 182 Z`
  const C = 113.1 // donut r=18
  const segs = [
    { len: C * 0.44, off: 0, color: '#3b6fd0' },
    { len: C * 0.30, off: C * 0.44, color: '#17b3a3' },
    { len: C * 0.26, off: C * 0.74, color: '#2dd4bf' },
  ]
  return (
    <svg
      viewBox="26 50 444 260"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
      width="100%"
      height="auto"
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        <linearGradient id="emb-stroke" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#3b66c4" />
          <stop offset="45%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="emb-shield" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" />
          <stop offset="55%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2dd4bf" />
        </linearGradient>
        <linearGradient id="emb-area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.28" />
          <stop offset="100%" stopColor="#2456a6" stopOpacity="0.0" />
        </linearGradient>
        <clipPath id="emb-clip">
          <rect x="26" y="50" width="444" height="260" rx="16" />
        </clipPath>
      </defs>

      <g clipPath="url(#emb-clip)">
        {/* auth chain: token → segments → shield */}
        <line x1="74" y1="180" x2="96" y2="180" stroke="#a7bbdb" strokeWidth="2" strokeLinecap="round" />
        <rect x="40" y="162" width="34" height="36" rx="8" fill="#2456a6" fillOpacity="0.06" stroke="url(#emb-stroke)" strokeWidth="1.75" />
        <path d="M 53 172 L 49 180 L 53 188 M 61 172 L 65 180 L 61 188" stroke="#7c9aca" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        <rect x="98" y="170" width="20" height="20" rx="5" fill="#9cb3d7" />
        <rect x="122" y="170" width="16" height="20" rx="5" fill="#17b3a3" fillOpacity="0.6" />
        <rect x="142" y="170" width="12" height="20" rx="5" fill="#2dd4bf" fillOpacity="0.75" />

        {/* policy shield with check */}
        <path d="M 192 126 L 224 138 L 224 178 C 224 200, 209 211, 192 218 C 175 211, 160 200, 160 178 L 160 138 Z"
          fill="#2456a6" fillOpacity="0.06" stroke="url(#emb-stroke)" strokeWidth="2" />
        <path d="M 176 174 L 188 186 L 208 160" stroke="url(#emb-shield)" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        <line x1="226" y1="172" x2="244" y2="172" stroke="url(#emb-stroke)" strokeWidth="2.25" strokeLinecap="round" />
        <circle cx="244" cy="172" r="3.5" fill="#17b3a3" />

        {/* embedded dashboard window */}
        <rect x="240" y="64" width="216" height="232" rx="14"
          fill="#2456a6" fillOpacity="0.04" stroke="url(#emb-stroke)" strokeWidth="2" />
        <line x1="240" y1="90" x2="456" y2="90" stroke="#a7bbdb" strokeWidth="1.5" />
        <circle cx="254" cy="77" r="3" fill="#9cb3d7" />
        <circle cx="266" cy="77" r="3" fill="#17b3a3" fillOpacity="0.55" />

        {/* chart panel */}
        <rect x="250" y="100" width="124" height="92" rx="9" fill="#2456a6" fillOpacity="0.04" stroke="#c2d0e6" strokeWidth="1.25" />
        <path d={area} fill="url(#emb-area)" />
        <path d={curve} stroke="url(#emb-stroke)" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" />

        {/* donut panel */}
        <rect x="380" y="100" width="66" height="92" rx="9" fill="#2456a6" fillOpacity="0.04" stroke="#c2d0e6" strokeWidth="1.25" />
        <g transform="rotate(-90 413 146)">
          {segs.map((s, i) => (
            <circle key={i} cx="413" cy="146" r="18" fill="none" stroke={s.color} strokeWidth="9"
              strokeDasharray={`${s.len} ${C}`} strokeDashoffset={-s.off} />
          ))}
        </g>

        {/* table panel */}
        <rect x="250" y="200" width="196" height="86" rx="9" fill="#2456a6" fillOpacity="0.04" stroke="#c2d0e6" strokeWidth="1.25" />
        {[262, 330, 390].map((x, i) => (
          <rect key={i} x={x} y="212" width={i === 0 ? 48 : 36} height="6" rx="3" fill="#a7bbdb" />
        ))}
        <line x1="250" y1="226" x2="446" y2="226" stroke="#d8e1ef" strokeWidth="1" />
        {[0, 1, 2].map((r) => {
          const y = 240 + r * 15
          return (
            <g key={r}>
              <circle cx="270" cy={y} r="3.5" fill={['#3b6fd0', '#17b3a3', '#2dd4bf'][r]} />
              <rect x="282" y={y - 3} width="40" height="6" rx="3" fill="#bdcce4" />
              <rect x="330" y={y - 3} width="40" height="6" rx="3" fill="#d3dded" />
              <rect x="390" y={y - 5} width="40" height="11" rx="5.5" fill="#17b3a3" fillOpacity="0.14" stroke="#17b3a3" strokeWidth="1" strokeOpacity="0.45" />
            </g>
          )
        })}
      </g>
    </svg>
  )
}
