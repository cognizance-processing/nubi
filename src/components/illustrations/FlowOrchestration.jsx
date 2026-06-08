/**
 * FlowOrchestration — visual DAG orchestration (a Prefect alternative). Metaphor:
 * a left→right directed graph: a source node branches into two task nodes that
 * merge into an end node. One node is "running" (gradient). Flat line-art, no
 * blur/glow. Reads on white + dark-navy.
 */
export default function FlowOrchestration({ className = '' }) {
  // node centers
  const A = { x: 96, y: 180 }   // source
  const B = { x: 240, y: 110 }  // task 1 (running)
  const C = { x: 240, y: 250 }  // task 2
  const D = { x: 384, y: 180 }  // end
  const node = (n) => ({ x: n.x - 42, y: n.y - 24, cx: n.x, cy: n.y })

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
        <linearGradient id="flw-stroke" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="45%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="flw-run" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" />
          <stop offset="55%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2dd4bf" />
        </linearGradient>
        <clipPath id="flw-clip">
          <rect x="20" y="40" width="440" height="280" rx="16" />
        </clipPath>
      </defs>

      <g clipPath="url(#flw-clip)">
        {/* edges */}
        <path d={`M ${A.x + 42} ${A.y} C 168 ${A.y}, 170 ${B.y}, ${B.x - 42} ${B.y}`}
          stroke="url(#flw-stroke)" strokeWidth="2.25" fill="none" />
        <path d={`M ${A.x + 42} ${A.y} C 168 ${A.y}, 170 ${C.y}, ${C.x - 42} ${C.y}`}
          stroke="#2456a6" strokeWidth="2.25" strokeOpacity="0.4" fill="none" />
        <path d={`M ${B.x + 42} ${B.y} C 312 ${B.y}, 314 ${D.y}, ${D.x - 42} ${D.y}`}
          stroke="url(#flw-stroke)" strokeWidth="2.25" fill="none" />
        <path d={`M ${C.x + 42} ${C.y} C 312 ${C.y}, 314 ${D.y}, ${D.x - 42} ${D.y}`}
          stroke="#2456a6" strokeWidth="2.25" strokeOpacity="0.4" fill="none" />
        {/* edge midpoint dots */}
        <circle cx="168" cy="145" r="3.5" fill="#17b3a3" />
        <circle cx="168" cy="215" r="3.5" fill="#2456a6" fillOpacity="0.5" />
        <circle cx="312" cy="145" r="3.5" fill="#17b3a3" />
        <circle cx="312" cy="215" r="3.5" fill="#2456a6" fillOpacity="0.5" />

        {/* source node A */}
        {(() => { const n = node(A); return (
          <g>
            <rect x={n.x} y={n.y} width="84" height="48" rx="12" fill="#2456a6" fillOpacity="0.05" stroke="url(#flw-stroke)" strokeWidth="2" />
            <rect x={n.x + 16} y={n.cy - 11} width="14" height="22" rx="3" fill="#17b3a3" fillOpacity="0.7" />
            <rect x={n.x + 38} y={n.cy - 6} width="30" height="5" rx="2.5" fill="#2456a6" fillOpacity="0.35" />
            <rect x={n.x + 38} y={n.cy + 4} width="22" height="5" rx="2.5" fill="#2456a6" fillOpacity="0.25" />
          </g>
        )})()}

        {/* task node B — RUNNING (gradient) */}
        {(() => { const n = node(B); return (
          <g>
            <rect x={n.x} y={n.y} width="84" height="48" rx="12" fill="url(#flw-run)" />
            <circle cx={n.x + 20} cy={n.cy} r="7" fill="#ffffff" fillOpacity="0.9" />
            <rect x={n.x + 36} y={n.cy - 6} width="32" height="5" rx="2.5" fill="#ffffff" fillOpacity="0.85" />
            <rect x={n.x + 36} y={n.cy + 4} width="22" height="5" rx="2.5" fill="#ffffff" fillOpacity="0.6" />
          </g>
        )})()}

        {/* task node C */}
        {(() => { const n = node(C); return (
          <g>
            <rect x={n.x} y={n.y} width="84" height="48" rx="12" fill="#2456a6" fillOpacity="0.05" stroke="url(#flw-stroke)" strokeWidth="2" />
            <circle cx={n.x + 20} cy={n.cy} r="7" stroke="#17b3a3" strokeWidth="2.25" fill="none" />
            <rect x={n.x + 36} y={n.cy - 6} width="30" height="5" rx="2.5" fill="#2456a6" fillOpacity="0.35" />
            <rect x={n.x + 36} y={n.cy + 4} width="22" height="5" rx="2.5" fill="#2456a6" fillOpacity="0.25" />
          </g>
        )})()}

        {/* end node D */}
        {(() => { const n = node(D); return (
          <g>
            <rect x={n.x} y={n.y} width="84" height="48" rx="12" fill="#2456a6" fillOpacity="0.05" stroke="url(#flw-stroke)" strokeWidth="2" />
            <path d={`M ${n.x + 24} ${n.cy} l 8 8 l 16 -18`} stroke="url(#flw-run)" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />
          </g>
        )})()}
      </g>
    </svg>
  )
}
