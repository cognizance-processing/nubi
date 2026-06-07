/**
 * FlowOrchestration — the Flows workflow orchestrator.
 * A clean left→right DAG: a source task branches into two parallel task cards
 * that fan back into a final card. One card is "running" (teal spark + glow +
 * pulsing status ring); the others are "done" (solid teal status dots). Glass
 * cards, connecting edges, brand gradients. Textless. Light + dark safe.
 */
export default function FlowOrchestration({ className = '', style }) {
  // Card geometry (top-left origin); all within the safe viewBox area.
  const CW = 92
  const CH = 54
  const NODES = [
    { id: 's', x: 40, y: 123, state: 'done' },   // source   center (86,150)
    { id: 'a', x: 196, y: 56, state: 'run' },     // task top center (242,83)
    { id: 'b', x: 196, y: 190, state: 'done' },   // task bot center (242,217)
    { id: 'f', x: 352, y: 123, state: 'done' },   // final    center (398,150)
  ]

  function Card({ x, y, state }) {
    const running = state === 'run'
    const cx = x + CW
    return (
      <g>
        {running && (
          <circle cx={x + CW / 2} cy={y + CH / 2} r="62" fill="url(#flo-bloom)" />
        )}
        {/* glass body + shadow */}
        <g filter="url(#flo-shadow)">
          <rect x={x} y={y} width={CW} height={CH} rx="13" fill="url(#flo-glass)" />
        </g>
        <rect
          x={x} y={y} width={CW} height={CH} rx="13"
          stroke="url(#flo-border)" strokeWidth={running ? 2 : 1.5}
          filter={running ? 'url(#flo-glow)' : undefined}
        />
        {/* kind dot */}
        <circle cx={x + 16} cy={y + 15} r="3.5" fill="#2dd4bf" fillOpacity="0.75" />
        {/* header + body bars (textless) */}
        <rect x={x + 26} y={y + 11} width="38" height="5" rx="2.5" fill="#2456a6" fillOpacity="0.22" />
        <rect x={x + 16} y={y + 29} width="60" height="4" rx="2" fill="#2456a6" fillOpacity="0.14" />
        <rect x={x + 16} y={y + 39} width="40" height="4" rx="2" fill="#2456a6" fillOpacity="0.14" />
        {/* status dot (top-right) */}
        {running ? (
          <>
            <circle cx={cx - 14} cy={y + 15} r="9" fill="none" stroke="#2dd4bf" strokeOpacity="0.4" strokeWidth="1.5" />
            <circle cx={cx - 14} cy={y + 15} r="4.5" fill="url(#flo-spark)" />
            <circle cx={cx - 14} cy={y + 15} r="1.8" fill="#ffffff" fillOpacity="0.7" />
          </>
        ) : (
          <circle cx={cx - 14} cy={y + 15} r="4" fill="#17b3a3" fillOpacity="0.85" />
        )}
      </g>
    )
  }

  return (
    <svg
      viewBox="0 0 480 300" fill="none" xmlns="http://www.w3.org/2000/svg"
      className={className} style={style} aria-hidden="true"
      width="100%" height="auto" preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        <radialGradient id="flo-bloom" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.55" />
          <stop offset="45%" stopColor="#17b3a3" stopOpacity="0.2" />
          <stop offset="100%" stopColor="#2dd4bf" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="flo-spark" cx="50%" cy="45%" r="60%">
          <stop offset="0%" stopColor="#caf7f1" />
          <stop offset="50%" stopColor="#2dd4bf" />
          <stop offset="100%" stopColor="#17b3a3" />
        </radialGradient>
        <linearGradient id="flo-glass" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" stopOpacity="0.14" />
          <stop offset="100%" stopColor="#1b2363" stopOpacity="0.05" />
        </linearGradient>
        <linearGradient id="flo-border" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.55" />
          <stop offset="60%" stopColor="#2456a6" stopOpacity="0.3" />
          <stop offset="100%" stopColor="#17b3a3" stopOpacity="0.2" />
        </linearGradient>
        <linearGradient id="flo-edge" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#2456a6" stopOpacity="0.5" />
          <stop offset="100%" stopColor="#2dd4bf" stopOpacity="0.85" />
        </linearGradient>
        <filter id="flo-shadow" x="-25%" y="-25%" width="150%" height="150%">
          <feDropShadow dx="0" dy="6" stdDeviation="10" floodColor="#1b2363" floodOpacity="0.2" />
        </filter>
        <filter id="flo-glow" x="-70%" y="-70%" width="240%" height="240%">
          <feGaussianBlur stdDeviation="4" result="b" />
          <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <clipPath id="flo-clip">
          <rect x="8" y="8" width="464" height="284" rx="22" />
        </clipPath>
      </defs>

      <g clipPath="url(#flo-clip)">
        {/* ── Edges (drawn under the cards) ── */}
        {/* source → task top (running edge: dashed) */}
        <path d="M 132 150 C 166 150, 166 83, 196 83"
          stroke="url(#flo-edge)" strokeWidth="2.5" strokeLinecap="round"
          strokeDasharray="4 7" fill="none" />
        {/* source → task bottom */}
        <path d="M 132 150 C 166 150, 166 217, 196 217"
          stroke="url(#flo-edge)" strokeWidth="2.5" strokeLinecap="round" fill="none" />
        {/* task top → final */}
        <path d="M 288 83 C 322 83, 322 150, 352 150"
          stroke="url(#flo-edge)" strokeWidth="2.5" strokeLinecap="round"
          strokeDasharray="4 7" fill="none" />
        {/* task bottom → final */}
        <path d="M 288 217 C 322 217, 322 150, 352 150"
          stroke="url(#flo-edge)" strokeWidth="2.5" strokeLinecap="round" fill="none" />

        {/* small junction dots where edges meet cards */}
        <circle cx="132" cy="150" r="3" fill="#2456a6" fillOpacity="0.4" />
        <circle cx="352" cy="150" r="3" fill="#2dd4bf" fillOpacity="0.6" />

        {/* ── Cards ── */}
        {NODES.map(n => (
          <Card key={n.id} x={n.x} y={n.y} state={n.state} />
        ))}

        {/* Ambient particles */}
        <circle cx="28" cy="60" r="2.5" fill="#2dd4bf" fillOpacity="0.3" />
        <circle cx="452" cy="240" r="2" fill="#17b3a3" fillOpacity="0.28" />
        <circle cx="240" cy="285" r="2" fill="#2456a6" fillOpacity="0.28" />
        <circle cx="244" cy="20" r="2" fill="#2dd4bf" fillOpacity="0.3" />
      </g>
    </svg>
  )
}
