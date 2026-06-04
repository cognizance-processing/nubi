/**
 * LlmDashboards — Large illustration of LLM-authorable HTML dashboards + MCP server.
 * Shows: chat prompt → MCP tool call → nubi-* custom elements → live dashboard output.
 * viewBox 560×380
 */
export default function LlmDashboards({ className = '' }) {
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
        <linearGradient id="llm-brand" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#1b2363" />
          <stop offset="50%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
        <linearGradient id="llm-bg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#0a1020" />
          <stop offset="100%" stopColor="#0c1422" />
        </linearGradient>
        <linearGradient id="llm-bar" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#1b2363" stopOpacity="0.4" />
        </linearGradient>
        <linearGradient id="llm-bar-hi" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2456a6" stopOpacity="0.5" />
        </linearGradient>
        <filter id="llm-glow">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <marker id="llm-arr" markerWidth="7" markerHeight="7" refX="3.5" refY="3.5" orient="auto">
          <path d="M 0 0 L 7 3.5 L 0 7 Z" fill="#4d8de0" fillOpacity="0.7" />
        </marker>
        <marker id="llm-arr-teal" markerWidth="7" markerHeight="7" refX="3.5" refY="3.5" orient="auto">
          <path d="M 0 0 L 7 3.5 L 0 7 Z" fill="#2dd4bf" fillOpacity="0.7" />
        </marker>
      </defs>

      {/* Background */}
      <rect width="560" height="380" rx="12" fill="url(#llm-bg)" />
      <rect width="560" height="380" rx="12" stroke="url(#llm-brand)" strokeOpacity="0.3" strokeWidth="1" fill="none" />

      {/* Title */}
      <text x="280" y="26" textAnchor="middle" fill="#4d8de0" fontSize="11" fontWeight="600" fontFamily="'Space Grotesk', sans-serif" letterSpacing="1">LLM-AUTHORABLE DASHBOARDS · MCP SERVER</text>

      {/* ═══════════════════════════════════
          LEFT: Chat / LLM panel
      ═══════════════════════════════════ */}
      <rect x="14" y="38" width="210" height="330" rx="10"
        fill="#080e1c" stroke="#21304a" strokeWidth="1" />
      {/* Panel header */}
      <rect x="14" y="38" width="210" height="30" rx="10" fill="#070d1a" />
      <rect x="14" y="58" width="210" height="10" fill="#070d1a" />
      <circle cx="30" cy="53" r="5" fill="#4a6fa5" fillOpacity="0.4" />
      <text x="110" y="57" textAnchor="middle" fill="#4a6fa5" fontSize="9" fontFamily="'Inter', sans-serif">Claude via MCP</text>

      {/* Chat bubbles */}
      {/* User message 1 */}
      <rect x="28" y="80" width="180" height="36" rx="7"
        fill="#111a2e" stroke="#21304a" strokeWidth="0.75" />
      <circle cx="38" cy="90" r="6" fill="#2456a6" fillOpacity="0.4" />
      <text x="50" y="91" fill="#93a4bd" fontSize="8" fontFamily="'Inter', sans-serif">Create a revenue dashboard</text>
      <text x="50" y="104" fill="#93a4bd" fontSize="8" fontFamily="'Inter', sans-serif">for Q2, grouped by region</text>

      {/* MCP tool response */}
      <rect x="24" y="126" width="192" height="68" rx="7"
        fill="#0d1526" stroke="#2456a6" strokeOpacity="0.4" strokeWidth="0.75" />
      <text x="34" y="140" fill="#4d8de0" fontSize="8" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">MCP tool call</text>
      <text x="34" y="152" fill="#4a6fa5" fontSize="9" fontFamily="monospace">create_dashboard({'{'})</text>
      <text x="34" y="163" fill="#2dd4bf" fillOpacity="0.8" fontSize="9" fontFamily="monospace">  title: "Q2 Revenue",</text>
      <text x="34" y="174" fill="#2dd4bf" fillOpacity="0.8" fontSize="9" fontFamily="monospace">  widgets: [kpi, bar, map]</text>
      <text x="34" y="185" fill="#4a6fa5" fontSize="9" fontFamily="monospace">{'}'}</text>

      {/* User message 2 */}
      <rect x="28" y="206" width="180" height="24" rx="7"
        fill="#111a2e" stroke="#21304a" strokeWidth="0.75" />
      <text x="50" y="221" fill="#93a4bd" fontSize="8" fontFamily="'Inter', sans-serif">Add a sparkline for daily trend</text>

      {/* Tool call 2 */}
      <rect x="24" y="240" width="192" height="50" rx="7"
        fill="#0d1526" stroke="#2456a6" strokeOpacity="0.4" strokeWidth="0.75" />
      <text x="34" y="254" fill="#4d8de0" fontSize="8" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">MCP tool call</text>
      <text x="34" y="266" fill="#4a6fa5" fontSize="9" fontFamily="monospace">author_dashboard({'{'})</text>
      <text x="34" y="277" fill="#2dd4bf" fillOpacity="0.8" fontSize="9" fontFamily="monospace">  add: "nubi-sparkline"</text>
      <text x="34" y="284" fill="#4a6fa5" fontSize="9" fontFamily="monospace">{'}'}</text>

      {/* MCP tools list */}
      <text x="119" y="308" textAnchor="middle" fill="#4a6fa5" fontSize="9" fontWeight="600" fontFamily="'Space Grotesk', sans-serif">4 MCP tools available</text>
      {[
        'create_dashboard',
        'author_dashboard',
        'run_query',
        'get_lineage',
      ].map((tool, i) => (
        <g key={tool}>
          <rect x="24" y={316 + i * 14} width="190" height="12" rx="3"
            fill="#111a2e" stroke="#21304a" strokeWidth="0.5" />
          <circle cx="33" cy={322 + i * 14} r="2.5" fill="#17b3a3" fillOpacity="0.7" />
          <text x="40" y={325 + i * 14} fill="#93a4bd" fontSize="8" fontFamily="monospace">{tool}</text>
        </g>
      ))}

      {/* Arrow center */}
      <line x1="224" y1="196" x2="256" y2="196"
        stroke="#4d8de0" strokeOpacity="0.7" strokeWidth="2" markerEnd="url(#llm-arr)" />
      <text x="240" y="190" textAnchor="middle" fill="#4d8de0" fontSize="8" fontFamily="monospace">HTML</text>

      {/* ═══════════════════════════════════
          RIGHT: Dashboard output
      ═══════════════════════════════════ */}
      <rect x="258" y="38" width="288" height="330" rx="10"
        fill="#080e1c" stroke="#21304a" strokeWidth="1" />

      {/* Browser-like topbar */}
      <rect x="258" y="38" width="288" height="28" rx="10" fill="#070d1a" />
      <rect x="258" y="58" width="288" height="8" fill="#070d1a" />
      <circle cx="272" cy="52" r="4" fill="#ff5f57" fillOpacity="0.6" />
      <circle cx="284" cy="52" r="4" fill="#febc2e" fillOpacity="0.6" />
      <circle cx="296" cy="52" r="4" fill="#28c840" fillOpacity="0.6" />
      <text x="402" y="55.5" textAnchor="middle" fill="#4a6fa5" fontSize="8" fontFamily="monospace">Q2 Revenue — auto-generated</text>

      {/* Dashboard title */}
      <text x="278" y="84" fill="#e7edf6" fontSize="12" fontWeight="700" fontFamily="'Space Grotesk', sans-serif">Q2 Revenue by Region</text>

      {/* KPI cards row */}
      {[
        { x: 266, label: 'Total', val: '$4.2M', delta: '+18%' },
        { x: 354, label: 'AMER', val: '$2.4M', delta: '+22%' },
        { x: 442, label: 'EMEA', val: '$1.8M', delta: '+11%' },
      ].map(({ x, label, val, delta }) => (
        <g key={label}>
          <rect x={x} y="94" width="82" height="54" rx="6"
            fill="#111a2e" stroke="#21304a" strokeWidth="0.75" />
          <rect x={x} y="94" width="82" height="3" rx="1.5"
            fill="url(#llm-brand)" fillOpacity="0.7" />
          <text x={x + 41} y="112" textAnchor="middle" fill="#4a6fa5" fontSize="8" fontFamily="'Inter', sans-serif">{label}</text>
          <text x={x + 41} y="130" textAnchor="middle" fill="#e7edf6" fontSize="15" fontWeight="700" fontFamily="'Space Grotesk', sans-serif">{val}</text>
          <text x={x + 41} y="142" textAnchor="middle" fill="#2dd4bf" fontSize="8" fontFamily="'Inter', sans-serif">{delta}</text>
        </g>
      ))}

      {/* nubi-chart bar widget */}
      <rect x="266" y="158" width="268" height="110" rx="7"
        fill="#0d1526" stroke="#21304a" strokeWidth="0.75" />
      <text x="280" y="173" fill="#4a6fa5" fontSize="8" fontFamily="monospace">&lt;nubi-chart type="bar"&gt; · WebGL auto</text>

      {/* Bars */}
      {[
        { x: 276, h: 42, hi: false, label: 'Jan' },
        { x: 306, h: 60, hi: false, label: 'Feb' },
        { x: 336, h: 48, hi: false, label: 'Mar' },
        { x: 366, h: 72, hi: false, label: 'Apr' },
        { x: 396, h: 55, hi: false, label: 'May' },
        { x: 426, h: 86, hi: true, label: 'Jun' },
        { x: 456, h: 68, hi: false, label: 'Jul' },
        { x: 486, h: 90, hi: true, label: 'Aug' },
      ].map(({ x, h, hi, label }, i) => (
        <g key={i}>
          <rect x={x} y={258 - h} width="22" height={h} rx="3"
            fill={hi ? 'url(#llm-bar-hi)' : 'url(#llm-bar)'} />
          <text x={x + 11} y="268" textAnchor="middle" fill="#4a6fa5" fontSize="7" fontFamily="monospace">{label}</text>
        </g>
      ))}
      <line x1="274" y1="258" x2="512" y2="258" stroke="#21304a" strokeWidth="0.75" />

      {/* nubi-table */}
      <rect x="266" y="278" width="268" height="74" rx="7"
        fill="#0d1526" stroke="#21304a" strokeWidth="0.75" />
      <text x="280" y="290" fill="#4a6fa5" fontSize="8" fontFamily="monospace">&lt;nubi-table&gt;</text>

      {/* Table header */}
      <rect x="266" y="294" width="268" height="14" fill="#111a2e" />
      <text x="290" y="304" fill="#4d8de0" fontSize="8" fontFamily="'Space Grotesk', sans-serif">Region</text>
      <text x="410" y="304" fill="#4d8de0" fontSize="8" fontFamily="'Space Grotesk', sans-serif">Revenue</text>
      <text x="480" y="304" fill="#4d8de0" fontSize="8" fontFamily="'Space Grotesk', sans-serif">Growth</text>

      {[
        { region: 'AMER', rev: '$2.4M', growth: '+22%', color: '#2dd4bf' },
        { region: 'EMEA', rev: '$1.8M', growth: '+11%', color: '#2dd4bf' },
        { region: 'APAC', rev: '$0.7M', growth: '+34%', color: '#17b3a3' },
        { region: 'LATAM', rev: '$0.3M', growth: '+8%', color: '#4d8de0' },
      ].map(({ region, rev, growth, color }, i) => (
        <g key={region}>
          <line x1="266" y1={308 + i * 11} x2="534" y2={308 + i * 11} stroke="#21304a" strokeWidth="0.5" />
          <text x="290" y={316 + i * 11} fill="#93a4bd" fontSize="8" fontFamily="'Inter', sans-serif">{region}</text>
          <text x="410" y={316 + i * 11} fill="#e7edf6" fontSize="8" fontFamily="monospace">{rev}</text>
          <text x="480" y={316 + i * 11} fill={color} fontSize="8" fontFamily="monospace">{growth}</text>
        </g>
      ))}

      {/* DOMPurify security badge */}
      <rect x="266" y="356" width="268" height="14" rx="4"
        fill="#070d1a" stroke="#17b3a3" strokeOpacity="0.3" strokeWidth="0.75" />
      <text x="400" y="366.5" textAnchor="middle" fill="#2dd4bf" fillOpacity="0.6" fontSize="8" fontFamily="monospace">DOMPurify sanitized · no &lt;script&gt; · safe custom elements</text>
    </svg>
  )
}
