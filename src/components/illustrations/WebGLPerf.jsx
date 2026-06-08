/**
 * WebGLPerf — 1M+ points at 60fps on the GPU. Metaphor: a dense scatter plot
 * with hundreds of crisp points + a cross-filter brush selection. Deterministic
 * point field (seeded), flat, no blur/glow. Reads on white + dark-navy.
 */
export default function WebGLPerf({ className = '' }) {
  // Deterministic seeded point field (stable across renders, no Math.random).
  const pts = []
  let s = 1337
  const rnd = () => ((s = (s * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff)
  for (let i = 0; i < 460; i++) {
    const t = i / 460
    // positive-correlation cloud across the plot (dense = "1M+ points")
    const jx = (rnd() - 0.5) * 0.24
    const jy = (rnd() - 0.5) * 0.24
    const x = 96 + (t + jx) * 304
    const y = 288 - (t + jy) * 222
    if (x < 92 || x > 408 || y < 60 || y > 290) continue
    pts.push({ x, y, t: Math.max(0, Math.min(1, t + jy)) })
  }
  // color stops along navy→teal by height
  const col = (t) => (t > 0.66 ? '#2dd4bf' : t > 0.33 ? '#17b3a3' : '#2456a6')

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
        <linearGradient id="wgl-stroke" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="45%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <clipPath id="wgl-clip">
          <rect x="40" y="40" width="400" height="280" rx="16" />
        </clipPath>
      </defs>

      {/* plot frame */}
      <rect x="40" y="40" width="400" height="280" rx="16"
        fill="#2456a6" fillOpacity="0.03" stroke="url(#wgl-stroke)" strokeWidth="2" />

      <g clipPath="url(#wgl-clip)">
        {/* axes */}
        <line x1="84" y1="62" x2="84" y2="294" stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.3" />
        <line x1="84" y1="294" x2="416" y2="294" stroke="#2456a6" strokeWidth="1.5" strokeOpacity="0.3" />

        {/* point cloud */}
        {pts.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r={2.1} fill={col(p.t)}
            fillOpacity={0.4 + p.t * 0.5} />
        ))}

        {/* cross-filter brush selection */}
        <rect x="250" y="96" width="130" height="104" rx="8"
          fill="#2dd4bf" fillOpacity="0.08" stroke="#17b3a3" strokeWidth="1.75" strokeDasharray="5 5" />
      </g>
    </svg>
  )
}
