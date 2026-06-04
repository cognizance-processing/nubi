/**
 * ConnectorSdk — Large illustration of the SQL-first connector SDK.
 * Shows: connector compatibility matrix + Python @connector decorator + pipeline.
 * viewBox 560×380
 */
export default function ConnectorSdk({ className = '' }) {
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
        <linearGradient id="sdk-brand" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="50%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="sdk-bg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#0a1020" />
          <stop offset="100%" stopColor="#0c1422" />
        </linearGradient>
        <linearGradient id="sdk-code" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#070d1a" />
          <stop offset="100%" stopColor="#0a1020" />
        </linearGradient>
        <marker id="sdk-arr" markerWidth="7" markerHeight="7" refX="3.5" refY="3.5" orient="auto">
          <path d="M 0 0 L 7 3.5 L 0 7 Z" fill="#2dd4bf" fillOpacity="0.7" />
        </marker>
        <marker id="sdk-arr-blue" markerWidth="7" markerHeight="7" refX="3.5" refY="3.5" orient="auto">
          <path d="M 0 0 L 7 3.5 L 0 7 Z" fill="#4d8de0" fillOpacity="0.7" />
        </marker>
      </defs>

      {/* Background */}
      <rect width="560" height="380" rx="12" fill="url(#sdk-bg)" />
      <rect width="560" height="380" rx="12" stroke="url(#sdk-brand)" strokeOpacity="0.3" strokeWidth="1" fill="none" />

      {/* Title */}
      <text x="280" y="26" textAnchor="middle" fill="#4d8de0" fontSize="11" fontWeight="600" fontFamily="'Space Grotesk', sans-serif" letterSpacing="1">SQL-FIRST CONNECTOR SDK</text>

      {/* ═══════════════════════════════════
          LEFT: Connector table
      ═══════════════════════════════════ */}
      <rect x="14" y="38" width="320" height="218" rx="10"
        fill="#080e1c" stroke="#21304a" strokeWidth="1" />
      {/* Top brand bar */}
      <rect x="14" y="38" width="320" height="4" rx="2"
        fill="url(#sdk-brand)" />

      {/* Table header */}
      <rect x="14" y="42" width="320" height="26" fill="#070d1a" />
      <text x="30" y="59" fill="#4a6fa5" fontSize="9" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">Source</text>
      <text x="152" y="59" fill="#4a6fa5" fontSize="9" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">Arrow</text>
      <text x="198" y="59" fill="#4a6fa5" fontSize="9" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">Pushdown</text>
      <text x="258" y="59" fill="#4a6fa5" fontSize="9" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">RLS</text>
      <text x="295" y="59" fill="#4a6fa5" fontSize="9" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">Fit</text>
      <line x1="14" y1="68" x2="334" y2="68" stroke="#21304a" strokeWidth="0.75" />

      {/* Source rows */}
      {[
        { label: 'BigQuery', arrow: true, push: true, rls: true, fit: '●●●', fitColor: '#2dd4bf' },
        { label: 'Snowflake', arrow: true, push: true, rls: true, fit: '●●●', fitColor: '#2dd4bf' },
        { label: 'Redshift', arrow: true, push: true, rls: true, fit: '●●●', fitColor: '#2dd4bf' },
        { label: 'Postgres/Neon', arrow: false, push: true, rls: true, fit: '●●○', fitColor: '#4d8de0' },
        { label: 'DuckDB local', arrow: true, push: true, rls: true, fit: '●●●', fitColor: '#2dd4bf' },
        { label: 'HTTP/JSON API', arrow: false, push: false, rls: true, fit: '●○○', fitColor: '#4a6fa5' },
        { label: 'Python fn()', arrow: true, push: false, rls: true, fit: '●●○', fitColor: '#4d8de0' },
      ].map(({ label, arrow, push, rls, fit, fitColor }, i) => (
        <g key={label}>
          <rect x="14" y={68 + i * 26} width="320" height="26"
            fill={i % 2 === 0 ? '#080e1c' : '#0a1020'} />
          <text x="30" y={85 + i * 26} fill="#93a4bd" fontSize="10" fontFamily="'Inter', sans-serif">{label}</text>
          <text x="162" y={85 + i * 26} textAnchor="middle" fill={arrow ? '#2dd4bf' : '#f87171'} fontSize="12" fontFamily="monospace">{arrow ? '✓' : '—'}</text>
          <text x="218" y={85 + i * 26} textAnchor="middle" fill={push ? '#2dd4bf' : '#f87171'} fontSize="12" fontFamily="monospace">{push ? '✓' : '—'}</text>
          <text x="268" y={85 + i * 26} textAnchor="middle" fill={rls ? '#2dd4bf' : '#f87171'} fontSize="12" fontFamily="monospace">{rls ? '✓' : '—'}</text>
          <text x="298" y={85 + i * 26} fill={fitColor} fontSize="8" fontFamily="monospace">{fit}</text>
          <line x1="14" y1={94 + i * 26} x2="334" y2={94 + i * 26} stroke="#21304a" strokeWidth="0.3" />
        </g>
      ))}

      {/* 501 security gate badge */}
      <rect x="14" y="252" width="320" height="24" rx="0"
        fill="#120808" stroke="#f87171" strokeOpacity="0.35" strokeWidth="0.75" />
      <text x="174" y="267" textAnchor="middle" fill="#f87171" fillOpacity="0.8" fontSize="9" fontFamily="monospace">predicate_rls=False → 501 REFUSED · security floor enforced</text>

      {/* ═══════════════════════════════════
          RIGHT TOP: Python SDK code
      ═══════════════════════════════════ */}
      <rect x="344" y="38" width="202" height="232" rx="10"
        fill="url(#sdk-code)" stroke="#21304a" strokeWidth="1" />
      {/* File tab */}
      <rect x="344" y="38" width="100" height="20" rx="4" fill="#111a2e" />
      <text x="394" y="51" textAnchor="middle" fill="#4a6fa5" fontSize="8" fontFamily="monospace">connector.py</text>
      <rect x="344" y="55" width="202" height="1" fill="#21304a" />

      {/* Python code */}
      {[
        { y: 72, color: '#4d8de0', text: 'from nubi import connector' },
        { y: 88, color: '#4a6fa5', text: '' },
        { y: 100, color: '#17b3a3', text: '@connector' },
        { y: 114, color: '#4d8de0', text: 'def my_source(' },
        { y: 128, color: '#2dd4bf', text: '  query: str,' },
        { y: 142, color: '#2dd4bf', text: '  claims: dict' },
        { y: 156, color: '#4d8de0', text: ') -> ArrowTable:' },
        { y: 170, color: '#4a6fa5', text: '  # fetch your data' },
        { y: 184, color: '#93a4bd', text: '  df = fetch(query)' },
        { y: 198, color: '#4a6fa5', text: '  # enforce RLS' },
        { y: 212, color: '#93a4bd', text: '  return rls(df, claims)' },
        { y: 226, color: '#4d8de0', text: '' },
        { y: 240, color: '#2dd4bf', text: '# capabilities declared:' },
        { y: 254, color: '#17b3a3', text: '# native_arrow ✓' },
        { y: 264, color: '#17b3a3', text: '# predicate_rls ✓' },
      ].map(({ y, color, text }) => (
        <text key={y} x="354" y={y} fill={color} fillOpacity="0.85" fontSize="9.5" fontFamily="monospace">{text}</text>
      ))}

      {/* ═══════════════════════════════════
          BOTTOM: Arrow pipeline
      ═══════════════════════════════════ */}
      <rect x="14" y="286" width="532" height="80" rx="10"
        fill="#080e1c" stroke="#21304a" strokeWidth="1" />
      <rect x="14" y="286" width="532" height="4" rx="2"
        fill="url(#sdk-brand)" fillOpacity="0.5" />

      <text x="280" y="305" textAnchor="middle" fill="#4a6fa5" fontSize="9" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">Query pipeline</text>

      {/* Pipeline stages */}
      {[
        { x: 28, label: 'sqlglot', sub: 'planner', color: '#4d8de0' },
        { x: 156, label: 'PhysicalPlan', sub: 'optimizer', color: '#4d8de0' },
        { x: 284, label: 'executor', sub: 'Arrow fn()', color: '#17b3a3' },
        { x: 412, label: 'Arrow IPC', sub: 'WebSocket', color: '#2dd4bf' },
      ].map(({ x, label, sub, color }, i) => (
        <g key={i}>
          <rect x={x} y="312" width="116" height="40" rx="6"
            fill="#0d1526" stroke={color} strokeOpacity="0.4" strokeWidth="1" />
          <text x={x + 58} y="328" textAnchor="middle" fill={color} fillOpacity="0.9" fontSize="10" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">{label}</text>
          <text x={x + 58} y="342" textAnchor="middle" fill={color} fillOpacity="0.5" fontSize="8" fontFamily="monospace">{sub}</text>
          {i < 3 && (
            <line
              x1={x + 116} y1={332}
              x2={x + 140} y2={332}
              stroke={color} strokeOpacity="0.5" strokeWidth="1.5"
              markerEnd="url(#sdk-arr-blue)"
            />
          )}
        </g>
      ))}
    </svg>
  )
}
