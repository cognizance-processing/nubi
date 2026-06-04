/**
 * EmbedAuth — Large illustration of auth-as-code embedding.
 * Shows: host app code → JWT → <nubi-dashboard> component → RLS predicate injection.
 * Policy YAML panel on left; shield/lock motif.
 * viewBox 560×380
 */
export default function EmbedAuth({ className = '' }) {
  return (
    <svg
      viewBox="0 0 560 380"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
      style={{ width: '100%', height: 'auto' }}
    >
      <defs>
        <linearGradient id="ea-brand" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="50%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="ea-bg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#0a1020" />
          <stop offset="100%" stopColor="#0c1422" />
        </linearGradient>
        <linearGradient id="ea-code-bg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#070d1a" />
          <stop offset="100%" stopColor="#0a1020" />
        </linearGradient>
        <filter id="ea-glow">
          <feGaussianBlur stdDeviation="4" result="blur" />
          <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <marker id="ea-arr" markerWidth="7" markerHeight="7" refX="3.5" refY="3.5" orient="auto">
          <path d="M 0 0 L 7 3.5 L 0 7 Z" fill="#4d8de0" fillOpacity="0.8" />
        </marker>
        <marker id="ea-arr-teal" markerWidth="7" markerHeight="7" refX="3.5" refY="3.5" orient="auto">
          <path d="M 0 0 L 7 3.5 L 0 7 Z" fill="#2dd4bf" fillOpacity="0.8" />
        </marker>
      </defs>

      {/* Background */}
      <rect width="560" height="380" rx="12" fill="url(#ea-bg)" />
      <rect width="560" height="380" rx="12" stroke="url(#ea-brand)" strokeOpacity="0.3" strokeWidth="1" fill="none" />

      {/* Title */}
      <text x="280" y="26" textAnchor="middle" fill="#4d8de0" fontSize="11" fontWeight="600" fontFamily="'Space Grotesk', sans-serif" letterSpacing="1">AUTH-AS-CODE EMBEDDING</text>

      {/* ═══════════════════════════════════
          LEFT: Host app code panel
      ═══════════════════════════════════ */}
      <rect x="16" y="38" width="238" height="210" rx="8"
        fill="url(#ea-code-bg)" stroke="#21304a" strokeWidth="1" />
      {/* File tab */}
      <rect x="16" y="38" width="100" height="20" rx="4" fill="#111a2e" />
      <text x="66" y="51" textAnchor="middle" fill="#4a6fa5" fontSize="8" fontFamily="monospace">host-app.ts</text>
      <rect x="16" y="55" width="238" height="1" fill="#21304a" />

      {/* Code content */}
      {[
        { y: 76, color: '#4d8de0', text: 'async function getToken() {' },
        { y: 92, color: '#4d8de0', text: '  return await sign({' },
        { y: 108, color: '#2dd4bf', text: '    sub:    user.id,' },
        { y: 124, color: '#2dd4bf', text: '    tenant: org.slug,' },
        { y: 140, color: '#17b3a3', text: '    rls: {' },
        { y: 156, color: '#17b3a3', text: '      col: "tenant_id",' },
        { y: 172, color: '#17b3a3', text: '      val: org.id' },
        { y: 188, color: '#17b3a3', text: '    },' },
        { y: 204, color: '#4d8de0', text: '  }, SECRET, { expiresIn: "15m" })' },
        { y: 220, color: '#4d8de0', text: '}' },
      ].map(({ y, color, text }) => (
        <text key={y} x="26" y={y} fill={color} fillOpacity="0.85" fontSize="10" fontFamily="monospace">{text}</text>
      ))}

      {/* Line numbers */}
      {[76, 92, 108, 124, 140, 156, 172, 188, 204, 220].map((y, i) => (
        <text key={y} x="20" y={y} fill="#21304a" fontSize="8" fontFamily="monospace">{i + 1}</text>
      ))}

      {/* ═══════════════════════════════════
          LEFT BOTTOM: Policy YAML
      ═══════════════════════════════════ */}
      <rect x="16" y="260" width="238" height="104" rx="8"
        fill="url(#ea-code-bg)" stroke="#21304a" strokeWidth="1" />
      <rect x="16" y="260" width="120" height="20" rx="4" fill="#111a2e" />
      <text x="76" y="273" textAnchor="middle" fill="#4a6fa5" fontSize="8" fontFamily="monospace">policies/revenue.yaml</text>
      <rect x="16" y="277" width="238" height="1" fill="#21304a" />

      {[
        { y: 296, color: '#4a6fa5', text: '# policy as code — PR-reviewable' },
        { y: 310, color: '#93a4bd', text: 'rls:' },
        { y: 324, color: '#2dd4bf', text: '  - col: tenant_id' },
        { y: 338, color: '#2dd4bf', text: '    val: $claims.tenant' },
        { y: 352, color: '#17b3a3', text: '  # AST injection · no string concat' },
      ].map(({ y, color, text }) => (
        <text key={y} x="26" y={y} fill={color} fillOpacity="0.85" fontSize="10" fontFamily="monospace">{text}</text>
      ))}

      {/* ═══════════════════════════════════
          CENTER: JWT flow
      ═══════════════════════════════════ */}
      {/* Arrow: host app → JWT */}
      <line x1="254" y1="142" x2="286" y2="142"
        stroke="#4d8de0" strokeOpacity="0.7" strokeWidth="2" markerEnd="url(#ea-arr)" />

      {/* JWT badge */}
      <rect x="288" y="122" width="120" height="40" rx="8"
        fill="#111a2e" stroke="#2456a6" strokeOpacity="0.6" strokeWidth="1.5" />
      <text x="348" y="140" textAnchor="middle" fill="#4d8de0" fontSize="12" fontWeight="700" fontFamily="'Space Grotesk', sans-serif">JWT · HS256</text>
      <text x="348" y="154" textAnchor="middle" fill="#4a6fa5" fontSize="9" fontFamily="monospace">exp: 15m · tenant claim</text>

      {/* Arrow down: JWT → component */}
      <line x1="348" y1="162" x2="348" y2="196"
        stroke="#4d8de0" strokeOpacity="0.6" strokeWidth="2" markerEnd="url(#ea-arr)" />

      {/* nubi-dashboard component */}
      <rect x="270" y="198" width="188" height="64" rx="8"
        fill="#0d1526" stroke="#2456a6" strokeOpacity="0.7" strokeWidth="1.5" />
      <rect x="270" y="198" width="188" height="4" rx="2"
        fill="url(#ea-brand)" />
      <text x="364" y="222" textAnchor="middle" fill="#4d8de0" fontSize="11" fontWeight="700" fontFamily="'Space Grotesk', sans-serif">&lt;nubi-dashboard&gt;</text>
      <text x="364" y="238" textAnchor="middle" fill="#93a4bd" fontSize="9" fontFamily="monospace">basePath  getToken  scopes</text>
      <text x="364" y="252" textAnchor="middle" fill="#4a6fa5" fontSize="8" fontFamily="monospace">origin-pinned · auto-refresh</text>

      {/* Arrow down: component → RLS */}
      <line x1="364" y1="262" x2="364" y2="294"
        stroke="#2dd4bf" strokeOpacity="0.6" strokeWidth="2" markerEnd="url(#ea-arr-teal)" />

      {/* RLS predicate injection */}
      <rect x="256" y="296" width="216" height="68" rx="8"
        fill="#070d1a" stroke="#17b3a3" strokeOpacity="0.6" strokeWidth="1.5" />
      <rect x="256" y="296" width="216" height="4" rx="2"
        fill="#17b3a3" fillOpacity="0.6" />
      <text x="364" y="316" textAnchor="middle" fill="#2dd4bf" fontSize="12" fontWeight="700" fontFamily="'Space Grotesk', sans-serif">Predicate injection (AST)</text>
      <text x="364" y="332" textAnchor="middle" fill="#17b3a3" fillOpacity="0.8" fontSize="9" fontFamily="monospace">WHERE tenant_id = :claim</text>
      <text x="364" y="346" textAnchor="middle" fill="#4a6fa5" fontSize="9" fontFamily="monospace">server-side · never string concat</text>
      <text x="364" y="358" textAnchor="middle" fill="#2dd4bf" fillOpacity="0.5" fontSize="8" fontFamily="monospace">diffable · PR-reviewable</text>

      {/* Shield icon — right side */}
      <path d="M 500 60 L 544 78 L 544 114 Q 544 148 522 164 Q 500 148 500 114 Z"
        fill="#2456a6" fillOpacity="0.08" stroke="#2456a6" strokeOpacity="0.4" strokeWidth="1" />
      {/* Shield inner glow */}
      <path d="M 508 76 L 536 90 L 536 118 Q 536 142 522 154 Q 508 142 508 118 Z"
        fill="#17b3a3" fillOpacity="0.06" />
      {/* Lock body */}
      <rect x="514" y="112" width="16" height="14" rx="3" fill="#2dd4bf" fillOpacity="0.7" filter="url(#ea-glow)" />
      <path d="M 517 112 a 5 5 0 0 1 10 0 v -4 a 5 5 0 0 0 -10 0 Z"
        fill="#2dd4bf" fillOpacity="0.5" />

      <text x="522" y="178" textAnchor="middle" fill="#2dd4bf" fontSize="10" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">Zero-trust</text>
      <text x="522" y="192" textAnchor="middle" fill="#4a6fa5" fontSize="8" fontFamily="'Inter', sans-serif">embed security</text>

      {/* Token lifecycle */}
      <rect x="478" y="210" width="76" height="64" rx="8"
        fill="#111a2e" stroke="#21304a" strokeWidth="0.75" />
      <text x="516" y="228" textAnchor="middle" fill="#4a6fa5" fontSize="9" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">Lifecycle</text>
      {[
        { y: 242, text: '✓ sign' },
        { y: 254, text: '✓ verify' },
        { y: 266, text: '✓ refresh' },
      ].map(({ y, text }) => (
        <text key={y} x="494" y={y} fill="#2dd4bf" fillOpacity="0.7" fontSize="9" fontFamily="monospace">{text}</text>
      ))}
    </svg>
  )
}
