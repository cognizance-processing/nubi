/**
 * HeroIllustration — "BI that runs in your browser."
 * A realistic dashboard rendered inside a browser window: a KPI strip, an area
 * chart, a donut/pie, and a data table — a true react-grid-layout dashboard.
 * Flat, crisp, brand palette. No blur/glow. Reads on white + dark-navy.
 */
export default function HeroIllustration({ className = '', style }) {
  const curve = 'M 62 232 C 96 226, 116 210, 150 202 C 188 193, 210 206, 244 184 C 282 159, 302 167, 332 146'
  const area = `${curve} L 332 250 L 62 250 Z`
  const bars = [18, 27, 22, 34, 28, 38, 31]
  // donut: r=26, C≈163.4; segments 42/28/18/12 %
  const C = 163.4
  const segs = [
    { len: C * 0.42, off: 0, color: '#2456a6' },
    { len: C * 0.28, off: C * 0.42, color: '#17b3a3' },
    { len: C * 0.18, off: C * 0.70, color: '#2dd4bf' },
    { len: C * 0.12, off: C * 0.88, color: '#1b2363' },
  ]
  return (
    <svg
      viewBox="0 0 560 400"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      style={style}
      aria-hidden="true"
      width="100%"
      height="auto"
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        <linearGradient id="hro-stroke" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="45%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="hro-area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.28" />
          <stop offset="100%" stopColor="#2456a6" stopOpacity="0.0" />
        </linearGradient>
        <linearGradient id="hro-accent" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="hro-bar" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2456a6" />
        </linearGradient>
        <clipPath id="hro-clip">
          <rect x="30" y="28" width="500" height="344" rx="18" />
        </clipPath>
      </defs>

      {/* Browser window */}
      <rect x="30" y="28" width="500" height="344" rx="18"
        fill="#2456a6" fillOpacity="0.035" stroke="url(#hro-stroke)" strokeWidth="2" />
      <line x1="30" y1="64" x2="530" y2="64" stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.45" />
      <circle cx="50" cy="46" r="4" fill="#2456a6" fillOpacity="0.45" />
      <circle cx="66" cy="46" r="4" fill="#17b3a3" fillOpacity="0.55" />
      <circle cx="82" cy="46" r="4" fill="#2dd4bf" fillOpacity="0.7" />
      <rect x="150" y="39" width="240" height="14" rx="7" fill="#2456a6" fillOpacity="0.07"
        stroke="#2456a6" strokeWidth="1" strokeOpacity="0.25" />

      <g clipPath="url(#hro-clip)">
        {/* KPI strip — 3 tiles */}
        {[48, 206, 364].map((x, i) => (
          <g key={i}>
            <rect x={x} y="80" width="146" height="40" rx="9"
              fill="#2456a6" fillOpacity="0.05" stroke="#2456a6" strokeWidth="1.25" strokeOpacity="0.28" />
            <circle cx={x + 16} cy="100" r="5" fill={['#2456a6', '#17b3a3', '#2dd4bf'][i]} />
            <rect x={x + 28} y="90" width="40" height="6" rx="3" fill="#2456a6" fillOpacity="0.3" />
            <rect x={x + 28} y="103" width="58" height="8" rx="4" fill="#2456a6" fillOpacity="0.45" />
            <path d={`M ${x + 118} 106 l 8 -10 l 6 5`} stroke="url(#hro-accent)" strokeWidth="2.25"
              strokeLinecap="round" strokeLinejoin="round" fill="none" />
          </g>
        ))}

        {/* Chart panel (left) */}
        <rect x="48" y="130" width="290" height="128" rx="11"
          fill="#2456a6" fillOpacity="0.04" stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.3" />
        <rect x="62" y="142" width="56" height="7" rx="3.5" fill="#2456a6" fillOpacity="0.35" />
        <path d={area} fill="url(#hro-area)" />
        <path d={curve} stroke="url(#hro-stroke)" strokeWidth="2.75" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="244" cy="184" r="3.5" fill="#17b3a3" />
        <circle cx="332" cy="146" r="5" fill="url(#hro-accent)" />

        {/* Donut panel (right) */}
        <rect x="350" y="130" width="160" height="128" rx="11"
          fill="#2456a6" fillOpacity="0.04" stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.3" />
        <g transform="rotate(-90 404 188)">
          {segs.map((s, i) => (
            <circle key={i} cx="404" cy="188" r="26" fill="none" stroke={s.color} strokeWidth="13"
              strokeDasharray={`${s.len} ${C}`} strokeDashoffset={-s.off} />
          ))}
        </g>
        <circle cx="404" cy="188" r="13" fill="#2456a6" fillOpacity="0.04" />
        {/* legend */}
        {[0, 1, 2].map((i) => (
          <g key={i}>
            <circle cx="452" cy={170 + i * 16} r="4" fill={['#2456a6', '#17b3a3', '#2dd4bf'][i]} />
            <rect x="462" y={167 + i * 16} width="38" height="6" rx="3" fill="#2456a6" fillOpacity="0.3" />
          </g>
        ))}

        {/* Table panel (full width) */}
        <rect x="48" y="270" width="462" height="86" rx="11"
          fill="#2456a6" fillOpacity="0.04" stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.3" />
        {/* header */}
        {[64, 220, 320, 420].map((x, i) => (
          <rect key={i} x={x} y="282" width={i === 0 ? 70 : 56} height="7" rx="3.5" fill="#2456a6" fillOpacity="0.4" />
        ))}
        <line x1="48" y1="298" x2="510" y2="298" stroke="#2456a6" strokeWidth="1" strokeOpacity="0.18" />
        {/* rows */}
        {[0, 1, 2].map((r) => {
          const y = 310 + r * 16
          return (
            <g key={r}>
              <circle cx="68" cy={y} r="4" fill={['#2456a6', '#17b3a3', '#2dd4bf'][r]} />
              <rect x="80" y={y - 3.5} width="48" height="7" rx="3.5" fill="#2456a6" fillOpacity="0.32" />
              <rect x="220" y={y - 3.5} width="56" height="7" rx="3.5" fill="#2456a6" fillOpacity="0.22" />
              <rect x="320" y={y - 3.5} width="44" height="7" rx="3.5" fill="#2456a6" fillOpacity="0.22" />
              {/* status pill */}
              <rect x="420" y={y - 6} width="46" height="13" rx="6.5" fill="#17b3a3" fillOpacity="0.14"
                stroke="#17b3a3" strokeWidth="1" strokeOpacity="0.45" />
            </g>
          )
        })}
      </g>
    </svg>
  )
}
