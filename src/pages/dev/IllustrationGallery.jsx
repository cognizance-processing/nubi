/**
 * Dev-only illustration gallery — renders every illustration LARGE on both a
 * light and a dark card, so they can be screenshotted and critiqued in
 * isolation (see .claude/skills/nubi-illustrations). Not linked in nav.
 * Route: /dev/illustrations
 */

import HeroIllustration from '../../components/illustrations/HeroIllustration.jsx'
import KernelInBrowser from '../../components/illustrations/KernelInBrowser.jsx'
import WebGLPerf from '../../components/illustrations/WebGLPerf.jsx'
import EdgeCache from '../../components/illustrations/EdgeCache.jsx'
import EmbedAuth from '../../components/illustrations/EmbedAuth.jsx'
import LlmDashboards from '../../components/illustrations/LlmDashboards.jsx'
import ConnectorSdk from '../../components/illustrations/ConnectorSdk.jsx'
import FlowOrchestration from '../../components/illustrations/FlowOrchestration.jsx'

const ITEMS = [
  ['HeroIllustration', HeroIllustration],
  ['KernelInBrowser', KernelInBrowser],
  ['WebGLPerf', WebGLPerf],
  ['EdgeCache', EdgeCache],
  ['EmbedAuth', EmbedAuth],
  ['LlmDashboards', LlmDashboards],
  ['ConnectorSdk', ConnectorSdk],
  ['FlowOrchestration', FlowOrchestration],
]

function Tile({ name, Illo }) {
  return (
    <div style={{ marginBottom: 48 }}>
      <div style={{ fontFamily: 'monospace', fontWeight: 700, marginBottom: 12, color: '#0e1729' }}>
        {name}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
        {/* light card */}
        <div style={{ background: '#ffffff', border: '1px solid #e2e8f0', borderRadius: 16, padding: 24 }}>
          <Illo className="w-full h-auto" />
        </div>
        {/* dark card */}
        <div style={{ background: '#111a2e', border: '1px solid #21304a', borderRadius: 16, padding: 24 }}>
          <Illo className="w-full h-auto" />
        </div>
      </div>
    </div>
  )
}

export default function IllustrationGallery() {
  return (
    <div style={{ background: '#f6f8fb', minHeight: '100vh', padding: 40 }}>
      <h1 style={{ fontFamily: 'monospace', fontSize: 22, fontWeight: 800, marginBottom: 8, color: '#0e1729' }}>
        Illustration gallery (dev)
      </h1>
      <p style={{ color: '#566377', marginBottom: 32 }}>
        Left = light card · Right = dark card. Each must read cleanly on both.
      </p>
      <div style={{ maxWidth: 1100 }}>
        {ITEMS.map(([name, Illo]) => (
          <Tile key={name} name={name} Illo={Illo} />
        ))}
      </div>
    </div>
  )
}
