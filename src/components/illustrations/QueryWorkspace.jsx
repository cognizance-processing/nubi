/**
 * QueryWorkspace — "Write SQL, see results instantly — in the browser."
 * A query editor: a SQL pane (line numbers + tokenized lines + a {{param}}
 * chip + Run pill) on the left, and the results that appear instantly on the
 * right — a small result grid topped by a mini area chart. Same flat brand
 * style as HeroIllustration. Single strokes, contained, reads on light + dark.
 */
export default function QueryWorkspace({ className = '', style }) {
  // mini area chart in the results pane
  const curve = 'M 348 196 C 366 192, 376 182, 396 178 C 418 173, 430 182, 452 166 C 470 153, 486 158, 502 150'
  const area = `${curve} L 502 232 L 348 232 Z`
  // tokenized SQL lines: [indent x, [ {w, kind} ... ]]  kind → color
  const KIND = { kw: '#2456a6', fn: '#17b3a3', str: '#2dd4bf', id: '#64748b', par: '#2dd4bf' }
  const lines = [
    [{ w: 34, k: 'kw' }, { w: 60, k: 'id' }, { w: 16, k: 'id' }, { w: 48, k: 'fn' }],
    [{ w: 30, k: 'kw' }, { w: 70, k: 'id' }],
    [{ w: 34, k: 'kw' }, { w: 44, k: 'id' }, { w: 22, k: 'kw' }, { w: 54, k: 'par' }],
    [{ w: 56, k: 'kw' }, { w: 30, k: 'id' }],
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
        <linearGradient id="qw-stroke" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="45%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="qw-area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.28" />
          <stop offset="100%" stopColor="#2456a6" stopOpacity="0.0" />
        </linearGradient>
        <linearGradient id="qw-run" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2dd4bf" />
        </linearGradient>
        <clipPath id="qw-clip">
          <rect x="30" y="28" width="500" height="344" rx="18" />
        </clipPath>
      </defs>

      {/* Window */}
      <rect x="30" y="28" width="500" height="344" rx="18"
        fill="#2456a6" fillOpacity="0.035" stroke="url(#qw-stroke)" strokeWidth="2" />
      <line x1="30" y1="64" x2="530" y2="64" stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.45" />
      <circle cx="50" cy="46" r="4" fill="#2456a6" fillOpacity="0.45" />
      <circle cx="66" cy="46" r="4" fill="#17b3a3" fillOpacity="0.55" />
      <circle cx="82" cy="46" r="4" fill="#2dd4bf" fillOpacity="0.7" />
      {/* tab label */}
      <rect x="150" y="39" width="120" height="14" rx="7" fill="#2456a6" fillOpacity="0.07"
        stroke="#2456a6" strokeWidth="1" strokeOpacity="0.25" />

      <g clipPath="url(#qw-clip)">
        {/* ── SQL editor pane (left) ── */}
        <rect x="48" y="84" width="266" height="272" rx="11"
          fill="#2456a6" fillOpacity="0.04" stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.3" />
        {/* gutter */}
        <line x1="78" y1="98" x2="78" y2="300" stroke="#2456a6" strokeWidth="1" strokeOpacity="0.18" />
        {lines.map((toks, r) => {
          const y = 112 + r * 26
          let x = 92
          return (
            <g key={r}>
              {/* line number dot */}
              <circle cx="63" cy={y} r="2.2" fill="#2456a6" fillOpacity="0.35" />
              {toks.map((t, i) => {
                const seg = <rect key={i} x={x} y={y - 4} width={t.w} height="8" rx="4"
                  fill={KIND[t.k]} fillOpacity={t.k === 'id' ? 0.3 : 0.62} />
                x += t.w + 10
                return seg
              })}
            </g>
          )
        })}
        {/* {{param}} chip on the WHERE line */}
        <rect x="196" y="160" width="58" height="16" rx="8" fill="#2dd4bf" fillOpacity="0.16"
          stroke="#2dd4bf" strokeWidth="1.25" strokeOpacity="0.6" />
        <text x="225" y="172" textAnchor="middle" fontFamily="ui-monospace, monospace" fontSize="10"
          fill="#17b3a3" fontWeight="600">tenant</text>
        {/* Run pill (bottom-left of editor) */}
        <rect x="62" y="312" width="86" height="30" rx="15" fill="url(#qw-run)" />
        <path d="M 80 320 L 80 334 L 92 327 Z" fill="#ffffff" fillOpacity="0.95" />
        <rect x="100" y="323" width="36" height="7" rx="3.5" fill="#ffffff" fillOpacity="0.9" />

        {/* ── Results (right): instant ── */}
        {/* mini chart card */}
        <rect x="330" y="84" width="184" height="170" rx="11"
          fill="#2456a6" fillOpacity="0.04" stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.3" />
        <rect x="344" y="96" width="60" height="7" rx="3.5" fill="#2456a6" fillOpacity="0.35" />
        {/* "instant" bolt badge */}
        <circle cx="498" cy="100" r="11" fill="#2dd4bf" fillOpacity="0.16" stroke="#17b3a3" strokeWidth="1.25" strokeOpacity="0.6" />
        <path d="M 500 93 L 494 102 L 499 102 L 496 108 L 503 99 L 498 99 Z" fill="#17b3a3" />
        <path d={area} fill="url(#qw-area)" />
        <path d={curve} stroke="url(#qw-stroke)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="452" cy="166" r="3.25" fill="#17b3a3" />
        <circle cx="502" cy="150" r="4.5" fill="url(#qw-run)" />

        {/* result grid card */}
        <rect x="330" y="270" width="184" height="86" rx="11"
          fill="#2456a6" fillOpacity="0.04" stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.3" />
        {/* header row */}
        {[344, 410, 466].map((x, i) => (
          <rect key={i} x={x} y="282" width={i === 0 ? 50 : 40} height="7" rx="3.5" fill="#2456a6" fillOpacity="0.4" />
        ))}
        <line x1="330" y1="298" x2="514" y2="298" stroke="#2456a6" strokeWidth="1" strokeOpacity="0.18" />
        {[0, 1, 2].map((r) => {
          const y = 310 + r * 15
          return (
            <g key={r}>
              <circle cx="350" cy={y} r="3.5" fill={['#2456a6', '#17b3a3', '#2dd4bf'][r]} />
              <rect x="362" y={y - 3} width="34" height="6" rx="3" fill="#2456a6" fillOpacity="0.3" />
              <rect x="410" y={y - 3} width="40" height="6" rx="3" fill="#2456a6" fillOpacity="0.22" />
              <rect x="466" y={y - 3} width="40" height="6" rx="3" fill="#2456a6" fillOpacity="0.22" />
            </g>
          )
        })}
      </g>
    </svg>
  )
}
