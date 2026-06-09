/**
 * OpenCoreSplit — the open-core architecture: a large OSS core module with an
 * optional EE module docking into it through a single feature-gate socket.
 * Metaphor: solid core block (gradient) + dashed EE block plugging in via a
 * connector stem; a gate dot marks the seam. Flat line-art, no blur/glow.
 */
export default function OpenCoreSplit({ className = '' }) {
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
        <linearGradient id="ocs-stroke" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="45%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="ocs-gate" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#2dd4bf" />
        </linearGradient>
        <clipPath id="ocs-clip">
          <rect x="20" y="30" width="440" height="300" rx="16" />
        </clipPath>
      </defs>

      <g clipPath="url(#ocs-clip)">
        {/* ── OSS core module (left, dominant) ── */}
        <rect x="48" y="84" width="212" height="192" rx="18"
          fill="#2456a6" fillOpacity="0.05" stroke="url(#ocs-stroke)" strokeWidth="2" />

        {/* circuit motif inside the core: node dots + connecting lines */}
        <circle cx="92" cy="128" r="5" fill="#17b3a3" />
        <circle cx="158" cy="128" r="5" fill="#2456a6" fillOpacity="0.55" />
        <circle cx="118" cy="180" r="5" fill="#2456a6" fillOpacity="0.55" />
        <circle cx="188" cy="180" r="5" fill="#17b3a3" />
        <circle cx="92" cy="232" r="5" fill="#2dd4bf" />
        <circle cx="158" cy="232" r="5" fill="#2456a6" fillOpacity="0.55" />
        <path d="M 97 128 L 153 128" stroke="#2456a6" strokeWidth="1.75" strokeOpacity="0.35" strokeLinecap="round" />
        <path d="M 158 133 L 122 175" stroke="#2456a6" strokeWidth="1.75" strokeOpacity="0.35" strokeLinecap="round" />
        <path d="M 123 180 L 183 180" stroke="#2456a6" strokeWidth="1.75" strokeOpacity="0.35" strokeLinecap="round" />
        <path d="M 118 185 L 96 227" stroke="#2456a6" strokeWidth="1.75" strokeOpacity="0.35" strokeLinecap="round" />
        <path d="M 97 232 L 153 232" stroke="#2456a6" strokeWidth="1.75" strokeOpacity="0.35" strokeLinecap="round" />
        <path d="M 188 185 L 162 227" stroke="#2456a6" strokeWidth="1.75" strokeOpacity="0.35" strokeLinecap="round" />

        {/* gate socket on the core's right edge */}
        <rect x="252" y="158" width="16" height="44" rx="6"
          fill="#2456a6" fillOpacity="0.08" stroke="url(#ocs-gate)" strokeWidth="2" />

        {/* connector stem: socket → EE plug (the single seam) */}
        <path d="M 268 180 L 312 180" stroke="url(#ocs-gate)" strokeWidth="2.25" strokeLinecap="round" />
        {/* gate dot on the seam */}
        <circle cx="290" cy="180" r="6" fill="#17b3a3" />
        <circle cx="290" cy="180" r="11" stroke="#17b3a3" strokeOpacity="0.3" strokeWidth="1.75" />

        {/* ── EE module (right, optional → dashed) ── */}
        <rect x="312" y="120" width="120" height="120" rx="16"
          fill="#17b3a3" fillOpacity="0.05" stroke="url(#ocs-stroke)" strokeWidth="2"
          strokeDasharray="7 6" />

        {/* plug prongs reaching into the seam */}
        <path d="M 312 168 L 296 168" stroke="#2456a6" strokeWidth="2" strokeOpacity="0.45" strokeLinecap="round" />
        <path d="M 312 192 L 296 192" stroke="#2456a6" strokeWidth="2" strokeOpacity="0.45" strokeLinecap="round" />

        {/* EE contents: two compact feature tiles */}
        <rect x="332" y="142" width="80" height="32" rx="8"
          fill="#2456a6" fillOpacity="0.06" stroke="#2456a6" strokeOpacity="0.5" strokeWidth="1.75" />
        <circle cx="346" cy="158" r="4" fill="#2dd4bf" />
        <line x1="358" y1="158" x2="398" y2="158" stroke="#2456a6" strokeWidth="2" strokeOpacity="0.35" strokeLinecap="round" />
        <rect x="332" y="186" width="80" height="32" rx="8"
          fill="#2456a6" fillOpacity="0.06" stroke="#2456a6" strokeOpacity="0.5" strokeWidth="1.75" />
        <circle cx="346" cy="202" r="4" fill="#17b3a3" />
        <line x1="358" y1="202" x2="386" y2="202" stroke="#2456a6" strokeWidth="2" strokeOpacity="0.35" strokeLinecap="round" />
      </g>
    </svg>
  )
}
