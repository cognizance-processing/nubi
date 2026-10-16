/**
 * Dev-only illustration gallery: every static, empty-state, placement and live
 * figure on one scrolling page, with a light/dark toggle (flips the real app
 * theme, so what you see is what ships). Not linked in nav.
 * Route: /dev/illustrations
 */
import { Suspense, useEffect, useRef, useState, type ComponentType } from 'react'
import { Sun, Moon } from 'lucide-react'
import { useTheme } from '../../contexts/ThemeContext.jsx'
import * as I from '../../components/illustrations'
import ViewAnatomy from '../../components/illustrations/ViewAnatomy'
import EdgeCacheKeys from '../../components/illustrations/EdgeCacheKeys'
import LiveInstrument from '../../components/illustrations/LiveInstrument'

type Fig = ComponentType<{ className?: string; title?: string; compact?: boolean }>
type Item = { name: string; where: string; C: Fig; max?: string; small?: boolean }

const STATIC: Item[] = [
  { name: 'HeroIllustration', where: 'docs quickstart', C: I.HeroIllustration },
  { name: 'QueryWorkspace', where: 'docs queries · DocsHome card', C: I.QueryWorkspace },
  { name: 'DashboardCanvas', where: 'docs dashboards · DocsHome card', C: I.DashboardCanvas },
  { name: 'KernelInBrowser', where: 'docs kernel-security', C: I.KernelInBrowser },
  { name: 'EdgeCache', where: 'docs cache-key-spec, pre-aggregations', C: I.EdgeCache },
  { name: 'WebGLPerf', where: 'docs chart-types, performance', C: I.WebGLPerf },
  { name: 'EmbedAuth', where: 'docs embedding', C: I.EmbedAuth },
  { name: 'LlmDashboards', where: 'docs ai-and-mcp', C: I.LlmDashboards },
  { name: 'ConnectorSdk', where: 'docs connectors', C: I.ConnectorSdk },
  { name: 'FlowOrchestration', where: 'docs flows', C: I.FlowOrchestration },
  { name: 'OpenCoreSplit', where: 'docs open-core', C: I.OpenCoreSplit },
  { name: 'SelfHostTopology', where: 'docs self-host', C: I.SelfHostTopology },
  { name: 'TrustBoundary', where: 'docs embedding (security)', C: I.TrustBoundary },
]
const EMPTY: Item[] = [
  { name: 'EmptyBoards', where: 'dashboards empty · editor canvas', C: I.EmptyBoards, max: 'max-w-[240px]', small: true },
  { name: 'EmptyQueries', where: 'queries empty', C: I.EmptyQueries, max: 'max-w-[240px]', small: true },
  { name: 'EmptyConnectors', where: 'connectors empty', C: I.EmptyConnectors, max: 'max-w-[240px]', small: true },
  { name: 'EmptyData', where: 'data explorer empty', C: I.EmptyData, max: 'max-w-[240px]', small: true },
  { name: 'EmptyFlows', where: 'flows empty', C: I.EmptyFlows, max: 'max-w-[240px]', small: true },
  { name: 'EmptyWatches', where: 'watches empty', C: I.EmptyWatches, max: 'max-w-[240px]', small: true },
  { name: 'EmptySearch', where: 'filtered list, 0 results', C: I.EmptySearch, max: 'max-w-[240px]', small: true },
  { name: 'ErrorFigure', where: 'failed load', C: I.ErrorFigure, max: 'max-w-[240px]', small: true },
]
const PLACED: Item[] = [
  { name: 'AuthPoster', where: 'login / register art panel', C: I.AuthPoster, max: 'max-w-[720px]' },
  { name: 'NotFoundFigure', where: '404', C: I.NotFoundFigure },
  { name: 'OnboardingSteps', where: 'onboarding · Home checklist', C: I.OnboardingSteps },
  { name: 'OAuthConsentFlow', where: 'OAuth consent · Settings MCP', C: I.OAuthConsentFlow },
  { name: 'MeteringWallet', where: 'pricing usage wallet', C: I.MeteringWallet },
  { name: 'CostVsViewers', where: 'compare hero', C: I.CostVsViewers },
  { name: 'BridgeTopology', where: 'Settings Bridges · docs bridges', C: I.BridgeTopology },
  { name: 'CliTerminal', where: 'docs sdk-and-cli', C: I.CliTerminal },
  { name: 'SecretsVault', where: 'docs secrets · connector-security', C: I.SecretsVault },
  { name: 'RlsBoundary', where: 'docs embedding (security)', C: I.RlsBoundary },
]

/** Mounts children only once scrolled near the viewport (three.js figures are heavy). */
function WhenVisible({ children, minHeight = 480 }: { children: React.ReactNode; minHeight?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const [on, setOn] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setOn(true); io.disconnect() } }, { rootMargin: '200px' })
    io.observe(el)
    return () => io.disconnect()
  }, [])
  return <div ref={ref} style={{ minHeight }}>{on ? <Suspense fallback={null}>{children}</Suspense> : null}</div>
}

function Tile({ item }: { item: Item }) {
  const { name, where, C, max = 'max-w-[560px]' } = item
  return (
    <section data-fig={name} className="border-t-2 border-[var(--ink)] pt-3 pb-10">
      <div className="flex items-baseline gap-3 mb-4 font-mono text-[11px] tracking-[0.06em] uppercase">
        <b className="font-medium text-fg">{name}</b>
        <span className="text-subtle">{where}</span>
      </div>
      <div className={`border border-[var(--frame-line)] bg-surface p-6 ${max}`}>
        <C className="w-full h-auto" />
      </div>
      {item.small && (
        <div className="mt-3 flex items-end gap-4">
          <div className="border border-[var(--frame-line)] bg-surface p-3 w-[120px]" title="120px, the smallest placement"><C className="w-full h-auto" /></div>
          <div className="border border-[var(--frame-line)] bg-surface p-3 w-[120px] text-muted" title="retinted with a text colour"><C className="w-full h-auto" /></div>
          <span className="font-mono text-[11px] uppercase tracking-[0.06em] text-subtle">120px · retinted</span>
        </div>
      )}
    </section>
  )
}

function Group({ id, title, items }: { id: string; title: string; items: Item[] }) {
  if (!items.length) return null
  return (
    <div id={id} className="mb-16">
      <h2 className="text-[22px] font-semibold tracking-[-0.02em] mb-6">{title}</h2>
      <div className="grid gap-x-10 [grid-template-columns:repeat(auto-fill,minmax(min(100%,520px),1fr))]">
        {items.map(i => <Tile key={i.name} item={i} />)}
      </div>
    </div>
  )
}

export default function IllustrationGallery() {
  const { theme, toggleTheme } = useTheme()
  return (
    <div className="min-h-screen bg-bg text-fg">
      <header className="sticky top-0 z-20 bg-bg border-b border-[var(--frame-line)]">
        <div className="max-w-[1240px] mx-auto px-4 sm:px-8 h-14 flex items-center gap-4">
          <h1 className="font-mono text-[12px] font-bold tracking-[0.08em] uppercase">Illustrations · gallery</h1>
          <nav className="hidden sm:flex gap-4 font-mono text-[11px] uppercase tracking-[0.06em] text-muted">
            <a href="#static" className="hover:text-fg">Static</a>
            <a href="#empty" className="hover:text-fg">Empty</a>
            <a href="#placed" className="hover:text-fg">Placement</a>
            <a href="#live" className="hover:text-fg">Live</a>
          </nav>
          <button type="button" onClick={toggleTheme} aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            className="ml-auto inline-flex items-center gap-2 h-8 px-3 border border-[var(--ink)] text-[13px] nubi-focus-ring">
            {theme === 'dark' ? <Sun size={14} aria-hidden="true" /> : <Moon size={14} aria-hidden="true" />}
            {theme === 'dark' ? 'Light' : 'Dark'}
          </button>
        </div>
      </header>
      <main className="max-w-[1240px] mx-auto px-4 sm:px-8 py-10">
        <Group id="static" title="Static · docs and marketing" items={STATIC} />
        <Group id="empty" title="Empty states" items={EMPTY} />
        <Group id="placed" title="Placement figures" items={PLACED} />
        <div id="live" className="mb-16">
          <h2 className="text-[22px] font-semibold tracking-[-0.02em] mb-2">Live figures</h2>
          <p className="text-[14px] text-muted mb-6 max-w-[62ch]">Docs and landing only. Each one loads when it nears the viewport, pauses offscreen and on hidden tabs, idles when settled and honours reduced motion.</p>
          {[['ViewAnatomy', 'docs architecture-and-economics', ViewAnatomy], ['EdgeCacheKeys', 'docs cache-key-spec', EdgeCacheKeys], ['LiveInstrument', 'docs figure (not placed in a page yet)', LiveInstrument]].map(([name, where, F]: any) => (
            <section key={name} data-fig={name} className="border-t-2 border-[var(--ink)] pt-3 pb-10">
              <div className="flex items-baseline gap-3 mb-4 font-mono text-[11px] tracking-[0.06em] uppercase">
                <b className="font-medium text-fg">{name}</b><span className="text-subtle">{where}</span>
              </div>
              <WhenVisible><F /></WhenVisible>
            </section>
          ))}
        </div>
      </main>
    </div>
  )
}
