/**
 * DashboardCanvas — "Compose a dashboard, embed it anywhere."
 * A dashboard builder: a left widget palette rail, a canvas with arranged
 * widgets (KPI strip, area chart, donut, table) on a faint grid, and a tile
 * mid-drag with a dashed drop target — then an embed < > glyph showing it
 * drops into any app. Same flat brand style. Single strokes, contained.
 */
export default function DashboardCanvas({ className = '', style }) {
  const curve = 'M 214 196 C 232 192, 242 184, 260 180 C 280 175, 292 184, 312 170 C 328 159, 342 164, 356 156'
  const area = `${curve} L 356 224 L 214 224 Z`
  // donut r=22, C≈138.2
  const C = 138.2
  const segs = [
    { len: C * 0.46, off: 0, color: '#2456a6' },
    { len: C * 0.32, off: C * 0.46, color: '#17b3a3' },
    { len: C * 0.22, off: C * 0.78, color: '#2dd4bf' },
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
        <linearGradient id="dc-stroke" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="45%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="dc-area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.28" />
          <stop offset="100%" stopColor="#2456a6" stopOpacity="0.0" />
        </linearGradient>
        <linearGradient id="dc-accent" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="dc-drag" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2456a6" />
        </linearGradient>
        <clipPath id="dc-clip">
          <rect x="30" y="28" width="500" height="344" rx="18" />
        </clipPath>
      </defs>

      {/* Window */}
      <rect x="30" y="28" width="500" height="344" rx="18"
        fill="#2456a6" fillOpacity="0.035" stroke="url(#dc-stroke)" strokeWidth="2" />
      <line x1="30" y1="64" x2="530" y2="64" stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.45" />
      <circle cx="50" cy="46" r="4" fill="#2456a6" fillOpacity="0.45" />
      <circle cx="66" cy="46" r="4" fill="#17b3a3" fillOpacity="0.55" />
      <circle cx="82" cy="46" r="4" fill="#2dd4bf" fillOpacity="0.7" />
      <rect x="150" y="39" width="150" height="14" rx="7" fill="#2456a6" fillOpacity="0.07"
        stroke="#2456a6" strokeWidth="1" strokeOpacity="0.25" />

      <g clipPath="url(#dc-clip)">
        {/* ── Palette rail (left) ── */}
        <rect x="48" y="84" width="58" height="272" rx="11"
          fill="#2456a6" fillOpacity="0.05" stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.3" />
        {[0, 1, 2, 3].map((i) => {
          const y = 100 + i * 44
          return (
            <g key={i}>
              <rect x="62" y={y} width="30" height="30" rx="7"
                fill="#2456a6" fillOpacity="0.06" stroke="#2456a6" strokeWidth="1.25" strokeOpacity="0.35" />
              {/* tiny glyph per palette item */}
              {i === 0 && <path d="M 70 122 l 6 -8 l 5 4 l 6 -9" stroke="url(#dc-accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" fill="none" />}
              {i === 1 && [0, 1, 2].map((b) => <rect key={b} x={69 + b * 6} y={160 - b * 3} width="3.5" height={8 + b * 3} rx="1.5" fill="#17b3a3" fillOpacity="0.7" />)}
              {i === 2 && <circle cx="77" cy="203" r="9" fill="none" stroke="#2dd4bf" strokeWidth="3" strokeDasharray="14 28" />}
              {i === 3 && [0, 1, 2].map((b) => <rect key={b} x="69" y={234 + b * 6} width="16" height="3.5" rx="1.75" fill="#2456a6" fillOpacity="0.4" />)}
            </g>
          )
        })}

        {/* ── Canvas with faint grid ── */}
        {[0, 1, 2, 3].map((i) => (
          <line key={`v${i}`} x1={122 + i * 98} y1="84" x2={122 + i * 98} y2="356"
            stroke="#2456a6" strokeWidth="1" strokeOpacity="0.06" />
        ))}
        {[0, 1, 2].map((i) => (
          <line key={`h${i}`} x1="118" y1={140 + i * 70} x2="514" y2={140 + i * 70}
            stroke="#2456a6" strokeWidth="1" strokeOpacity="0.06" />
        ))}

        {/* KPI strip (2 tiles) */}
        {[122, 232].map((x, i) => (
          <g key={i}>
            <rect x={x} y="96" width="100" height="40" rx="9"
              fill="#2456a6" fillOpacity="0.05" stroke="#2456a6" strokeWidth="1.25" strokeOpacity="0.3" />
            <circle cx={x + 16} cy="116" r="5" fill={['#2456a6', '#17b3a3'][i]} />
            <rect x={x + 28} y="106" width="34" height="6" rx="3" fill="#2456a6" fillOpacity="0.3" />
            <rect x={x + 28} y="119" width="48" height="8" rx="4" fill="#2456a6" fillOpacity="0.45" />
          </g>
        ))}

        {/* Area chart widget */}
        <rect x="122" y="148" width="244" height="96" rx="10"
          fill="#2456a6" fillOpacity="0.04" stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.3" />
        <rect x="136" y="158" width="52" height="6" rx="3" fill="#2456a6" fillOpacity="0.35" />
        <path d={area} fill="url(#dc-area)" />
        <path d={curve} stroke="url(#dc-stroke)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="356" cy="156" r="4.5" fill="url(#dc-accent)" />

        {/* Donut widget */}
        <rect x="378" y="96" width="136" height="148" rx="10"
          fill="#2456a6" fillOpacity="0.04" stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.3" />
        <g transform="rotate(-90 446 176)">
          {segs.map((s, i) => (
            <circle key={i} cx="446" cy="176" r="22" fill="none" stroke={s.color} strokeWidth="11"
              strokeDasharray={`${s.len} ${C}`} strokeDashoffset={-s.off} />
          ))}
        </g>

        {/* Table widget */}
        <rect x="122" y="256" width="392" height="84" rx="10"
          fill="#2456a6" fillOpacity="0.04" stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.3" />
        {[138, 280, 380, 456].map((x, i) => (
          <rect key={i} x={x} y="268" width={i === 0 ? 64 : 48} height="7" rx="3.5" fill="#2456a6" fillOpacity="0.4" />
        ))}
        <line x1="122" y1="284" x2="514" y2="284" stroke="#2456a6" strokeWidth="1" strokeOpacity="0.18" />
        {[0, 1].map((r) => {
          const y = 300 + r * 18
          return (
            <g key={r}>
              <circle cx="142" cy={y} r="3.5" fill={['#2456a6', '#17b3a3'][r]} />
              <rect x="154" y={y - 3} width="44" height="6" rx="3" fill="#2456a6" fillOpacity="0.3" />
              <rect x="280" y={y - 3} width="52" height="6" rx="3" fill="#2456a6" fillOpacity="0.22" />
              <rect x="380" y={y - 3} width="44" height="6" rx="3" fill="#2456a6" fillOpacity="0.22" />
              <rect x="456" y={y - 5} width="44" height="11" rx="5.5" fill="#17b3a3" fillOpacity="0.14"
                stroke="#17b3a3" strokeWidth="1" strokeOpacity="0.45" />
            </g>
          )
        })}

        {/* ── Embed glyph: dashboard drops into any app ── */}
        <circle cx="486" cy="120" r="15" fill="#1b2363" fillOpacity="0.05" stroke="url(#dc-drag)" strokeWidth="1.75" />
        <path d="M 482 113 L 477 120 L 482 127 M 490 113 L 495 120 L 490 127"
          stroke="#17b3a3" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" fill="none" />
      </g>
    </svg>
  )
}
