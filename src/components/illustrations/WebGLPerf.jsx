/**
 * WebGLPerf — GPU point-cloud rendered at speed.
 * A clean comet of data particles flows along converging streamlines from the
 * left and resolves into a bright focal node on the right — the moment a frame
 * is rasterized. Deterministic LCG, fully clipped, no text. Reads on light + dark.
 */
export default function WebGLPerf({ className = '' }) {
  // Seeded LCG — deterministic, no Math.random at runtime
  let s = 0x51ed270b
  const rnd = () => {
    s = (Math.imul(1664525, s) + 1013904223) >>> 0
    return s / 0x100000000
  }

  const W = 480
  const H = 320

  // Safe inset bounds
  const X0 = 24
  const X1 = W - 24
  const Y0 = 24
  const Y1 = H - 24

  // Focal attractor — upper-right; the comet lifts toward it for energy
  const FX = 358
  const FY = 138

  // Cubic bezier helper
  const bez = (P0, P1, P2, P3, t) => {
    const mt = 1 - t
    return {
      x: mt*mt*mt*P0.x + 3*mt*mt*t*P1.x + 3*mt*t*t*P2.x + t*t*t*P3.x,
      y: mt*mt*mt*P0.y + 3*mt*mt*t*P1.y + 3*mt*t*t*P2.y + t*t*t*P3.y,
    }
  }

  // Build organized streamlines fanning from the left edge into the focal.
  // Particles ride these lines (small perpendicular jitter) → a coherent comet,
  // not a chaotic dust field.
  const LINES = 7
  const PER = 64
  const focal = { x: FX, y: FY }
  const pts = []

  for (let li = 0; li < LINES; li++) {
    const f = LINES === 1 ? 0.5 : li / (LINES - 1)        // 0..1 across the fan
    const sy = (FY + 34) + (f - 0.5) * 150                  // source sits lower → upward lift
    const sx = 56 + (f - 0.5) * 18
    const P0 = { x: sx, y: sy }
    // pull up/in early, then neck-converge toward focal
    const P1 = { x: sx + 70, y: sy + (FY - sy) * 0.25 }
    const P2 = { x: FX - 118, y: FY + (sy - FY) * 0.18 }

    for (let j = 0; j < PER; j++) {
      const u = j / (PER - 1)
      const t = Math.pow(u, 0.78)                           // bias density toward focal
      const base = bez(P0, P1, P2, focal, t)

      // perpendicular jitter — wide in the tail, tightening to nothing at head
      const spread = (1 - t) * (10 + (1 - f) * 6) + 1.5
      const jx = (rnd() - 0.5) * 2 * spread
      const jy = (rnd() - 0.5) * 2 * spread

      const x = Math.max(X0 + 2, Math.min(X1 - 2, base.x + jx))
      const y = Math.max(Y0 + 2, Math.min(Y1 - 2, base.y + jy))

      const r = 0.6 + Math.pow(t, 1.4) * 2.3 + rnd() * 0.5
      const o = Math.min(0.06 + Math.pow(t, 1.25) * 0.82 + rnd() * 0.08, 0.95)

      pts.push({ x, y, r, o, t, k: li * PER + j })
    }
  }

  // Color ramp: tail blue → head cyan
  const col = (t) => {
    if (t > 0.74) return '#2dd4bf'
    if (t > 0.48) return '#17b3a3'
    if (t > 0.24) return '#2b6cb0'
    return '#2456a6'
  }

  // A few elongated motion streaks just behind the focal — sense of velocity
  const streaks = []
  for (let i = 0; i < 6; i++) {
    const t = 0.62 + rnd() * 0.22
    const off = (rnd() - 0.5) * 30
    const p = bez({ x: 56, y: FY }, { x: 140, y: FY }, { x: FX - 118, y: FY }, focal, t)
    streaks.push({
      x1: p.x - 22, y1: p.y + off * 0.5,
      x2: p.x + 6, y2: p.y + off * 0.5 + off * 0.06,
      o: 0.10 + rnd() * 0.10, k: i,
    })
  }

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
      width="100%"
      height="auto"
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        {/* Focal bloom */}
        <radialGradient id="wgl-bloom" cx="50%" cy="50%" r="50%">
          <stop offset="0%"   stopColor="#2dd4bf" stopOpacity="0.55" />
          <stop offset="38%"  stopColor="#17b3a3" stopOpacity="0.22" />
          <stop offset="100%" stopColor="#2dd4bf" stopOpacity="0"    />
        </radialGradient>

        {/* Soft brand halo behind the comet head */}
        <radialGradient id="wgl-halo" cx="50%" cy="50%" r="50%">
          <stop offset="0%"   stopColor="#2456a6" stopOpacity="0.20" />
          <stop offset="100%" stopColor="#2456a6" stopOpacity="0"    />
        </radialGradient>

        {/* Lead node fill */}
        <radialGradient id="wgl-node" cx="32%" cy="30%" r="70%">
          <stop offset="0%"   stopColor="#a5f3ec" />
          <stop offset="55%"  stopColor="#2dd4bf" />
          <stop offset="100%" stopColor="#17b3a3" />
        </radialGradient>

        {/* Streak gradient — fades into the head */}
        <linearGradient id="wgl-streak" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%"   stopColor="#2dd4bf" stopOpacity="0" />
          <stop offset="100%" stopColor="#2dd4bf" stopOpacity="1" />
        </linearGradient>

        {/* Glow filter */}
        <filter id="wgl-glow" x="-120%" y="-120%" width="340%" height="340%">
          <feGaussianBlur stdDeviation="4.5" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>

        {/* Clip — inset rounded rect */}
        <clipPath id="wgl-clip">
          <rect x="12" y="12" width={W - 24} height={H - 24} rx="22" />
        </clipPath>
      </defs>

      <g clipPath="url(#wgl-clip)">
        {/* Transparent background — only soft atmosphere, no opaque panel */}
        <ellipse cx={FX - 10} cy={FY} rx="190" ry="150" fill="url(#wgl-halo)" />

        {/* Subtle render-canvas grid (even, very faint) */}
        {[0.25, 0.5, 0.75].map((g, i) => (
          <line key={`gh${i}`}
            x1={X0} y1={Y0 + g * (Y1 - Y0)} x2={X1} y2={Y0 + g * (Y1 - Y0)}
            stroke="#2456a6" strokeOpacity="0.08" strokeWidth="1" />
        ))}
        {[0.25, 0.5, 0.75].map((g, i) => (
          <line key={`gv${i}`}
            x1={X0 + g * (X1 - X0)} y1={Y0} x2={X0 + g * (X1 - X0)} y2={Y1}
            stroke="#2456a6" strokeOpacity="0.06" strokeWidth="1" />
        ))}

        {/* Motion streaks behind the head */}
        {streaks.map((st) => (
          <line key={`st${st.k}`}
            x1={st.x1} y1={st.y1} x2={st.x2} y2={st.y2}
            stroke="url(#wgl-streak)" strokeOpacity={st.o}
            strokeWidth="2" strokeLinecap="round" />
        ))}

        {/* Main point cloud — back-to-front (tail first, head on top) */}
        {pts.map((p) => (
          <circle key={`p${p.k}`}
            cx={p.x} cy={p.y} r={p.r}
            fill={col(p.t)} fillOpacity={p.o} />
        ))}

        {/* Focal bloom + concentric rings */}
        <circle cx={FX} cy={FY} r="50" fill="url(#wgl-bloom)" />
        <circle cx={FX} cy={FY} r="26"
          stroke="#2dd4bf" strokeOpacity="0.20" strokeWidth="1.5" />
        <circle cx={FX} cy={FY} r="16"
          stroke="#2dd4bf" strokeOpacity="0.42" strokeWidth="1.5" />

        {/* Lead node with glow */}
        <g filter="url(#wgl-glow)">
          <circle cx={FX} cy={FY} r="8.5" fill="url(#wgl-node)" />
          <circle cx={FX} cy={FY} r="8.5"
            stroke="#caf7f1" strokeWidth="1.5" strokeOpacity="0.8" />
        </g>

        {/* Status dots — top-left corner */}
        {[0, 1, 2].map((i) => (
          <circle key={`dot${i}`}
            cx={X0 + 14 + i * 11} cy={Y0 + 14} r="3.5"
            fill={['#2456a6', '#17b3a3', '#2dd4bf'][i]}
            fillOpacity="0.7" />
        ))}

        {/* Frame border + top shimmer */}
        <rect x="12" y="12" width={W - 24} height={H - 24} rx="22"
          stroke="#2456a6" strokeOpacity="0.30" strokeWidth="1.5" />
        <line x1="38" y1="12" x2={W - 38} y2="12"
          stroke="#2dd4bf" strokeOpacity="0.20" strokeWidth="1.5" strokeLinecap="round" />
      </g>
    </svg>
  )
}
