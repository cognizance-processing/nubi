/**
 * KernelInBrowser — the compute kernel (DuckDB-WASM) runs INSIDE the
 * user's browser tab. Metaphor: a processor chip living within a browser window,
 * emitting a result chart. Flat line-art, no blur/glow. Light + dark safe.
 */
export default function KernelInBrowser({ className = '' }) {
  const pins = [0, 1, 2, 3, 4, 5]
  return (
    <svg
      viewBox="28 28 424 304"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
      width="100%"
      height="auto"
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        <linearGradient id="krn-stroke" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#3b66c4" />
          <stop offset="45%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="krn-chip" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" />
          <stop offset="55%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2dd4bf" />
        </linearGradient>
        <linearGradient id="krn-accent" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <clipPath id="krn-clip">
          <rect x="40" y="40" width="400" height="280" rx="16" />
        </clipPath>
      </defs>

      {/* Browser window */}
      <rect x="40" y="40" width="400" height="280" rx="16"
        fill="#2456a6" fillOpacity="0.04" stroke="url(#krn-stroke)" strokeWidth="2" />
      <line x1="40" y1="76" x2="440" y2="76" stroke="#92abd3" strokeWidth="1.5" />
      <circle cx="60" cy="58" r="4" fill="#9cb3d7" />
      <circle cx="76" cy="58" r="4" fill="#17b3a3" fillOpacity="0.55" />
      <circle cx="92" cy="58" r="4" fill="#2dd4bf" fillOpacity="0.7" />

      <g clipPath="url(#krn-clip)">
        {/* chip — scaled up to fill the browser frame */}
        <g transform="translate(240 186) scale(1.22) translate(-240 -180)">
        {/* chip pins */}
        {pins.map((i) => {
          const x = 178 + i * 21.6
          return <rect key={`t${i}`} x={x} y="118" width="9" height="14" rx="2.5" fill="#7c9aca" />
        })}
        {pins.map((i) => {
          const x = 178 + i * 21.6
          return <rect key={`b${i}`} x={x} y="228" width="9" height="14" rx="2.5" fill="#7c9aca" />
        })}
        {pins.map((i) => {
          const y = 150 + i * 11.5
          return <rect key={`l${i}`} x="146" y={y} width="14" height="7" rx="2.5" fill="#7c9aca" />
        })}
        {pins.map((i) => {
          const y = 150 + i * 11.5
          return <rect key={`r${i}`} x="320" y={y} width="14" height="7" rx="2.5" fill="#7c9aca" />
        })}

        {/* chip body */}
        <rect x="166" y="132" width="148" height="96" rx="16"
          fill="#2456a6" fillOpacity="0.06" stroke="url(#krn-stroke)" strokeWidth="2" />
        {/* chip core with compute-core grid */}
        <rect x="196" y="158" width="88" height="44" rx="10" fill="url(#krn-chip)" />
        {[0, 1, 2, 3].map((c) =>
          [0, 1].map((r) => (
            <rect key={`${c}-${r}`} x={210 + c * 18} y={170 + r * 13} width="11" height="9" rx="2"
              fill="#ffffff" fillOpacity={0.55 + ((c + r) % 2) * 0.3} />
          ))
        )}
        </g>

        {/* result emitted below — tiny chart */}
        <polyline points="166,296 204,284 240,290 278,268 322,276"
          stroke="url(#krn-accent)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        <circle cx="322" cy="276" r="4" fill="#17b3a3" />
      </g>
    </svg>
  )
}
