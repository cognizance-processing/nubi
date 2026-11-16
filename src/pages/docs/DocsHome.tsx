/**
 * DocsHome — the docs landing page (slug "home").
 *
 * One search box, six illustrated workflows, three sections. Hairline cells,
 * mono indices, the same kit-drawn figures as the landing and the gallery.
 */
import { Link } from 'react-router-dom'
import { Search, ArrowRight } from 'lucide-react'
import { DOC_ILLUSTRATIONS } from '../../components/illustrations/docMap.js'
import { isMacLike } from '../../components/marketing/PublicPalette.jsx'

// Core product workflows, each linking to the deep doc.
const WORKFLOWS = [
  { slug: 'queries-and-params', illo: 'QueryWorkspace', title: 'Queries and parameters', body: 'Write SQL, add typed {{params}}, reuse a query library, and let AI text-to-SQL ground on your real schema.' },
  { slug: 'dashboards', illo: 'DashboardCanvas', title: 'Dashboards and widgets', body: 'Compose KPIs, charts, tables and filters on a grid. Nine widget types, variables and a code view.' },
  { slug: 'flows', illo: 'FlowOrchestration', title: 'Flows', body: 'Cell-based pipelines: SQL, Python and note cells in a notebook or canvas view, with scheduling.' },
  { slug: 'ai-and-mcp', illo: 'LlmDashboards', title: 'AI, chat and MCP', body: 'Grounded text-to-SQL, an agentic chat loop, and an MCP server so agents can author dashboards.' },
  { slug: 'embedding', illo: 'EmbedAuth', title: 'Embedding and security', body: 'Drop in <nubi-dashboard>, mint per-viewer JWTs and enforce row-level security as signed claims.' },
  { slug: 'connectors', illo: 'ConnectorSdk', title: 'Connectors', body: 'Point at any Postgres-compatible warehouse, or wrap your own source with the Python connector SDK.' },
]

const SECTIONS = [
  { slug: 'getting-started', title: 'Using Nubi', body: 'How to use the product, for self-host and Cloud alike.' },
  { slug: 'cloud', title: 'Nubi Cloud', body: 'The thin managed layer: plans, the usage wallet and billing.' },
  { slug: 'self-host', title: 'Open-source project', body: 'Self-host, architecture, security internals and building on Nubi.' },
]

export default function DocsHome({ onOpenSearch }: { onOpenSearch: () => void }) {
  const mod = isMacLike() ? '⌘K' : 'Ctrl K'
  return (
    <div style={{ maxWidth: 1080 }}>
      <div className="mk-idx"><b>NUBI</b><span>DOCUMENTATION</span></div>
      <h1 className="mk-h1" style={{ marginTop: 28, fontSize: 'clamp(40px, 5.4vw, 72px)' }}>Build with <em>Nubi.</em></h1>
      <p className="mk-lead" style={{ marginTop: 18 }}>
        Embedded-first BI where the kernel runs in the browser. Connect a warehouse, write a query, compose a dashboard and
        embed it with per-viewer security. Start here.
      </p>

      <button
        type="button"
        onClick={onOpenSearch}
        aria-label={`Search the docs (${mod})`}
        style={{ marginTop: 28, width: '100%', maxWidth: 560, height: 48, display: 'flex', alignItems: 'center', gap: 12, padding: '0 14px', border: '1px solid var(--ink)', borderRadius: 2, background: 'var(--surface)', color: 'var(--text-muted)', font: '15px var(--font-ui)', cursor: 'pointer', textAlign: 'left' }}
      >
        <Search size={17} strokeWidth={1.75} aria-hidden="true" />
        <span style={{ flex: 1 }}>Search the docs</span>
        <kbd className="mk-kbd">{mod}</kbd>
      </button>

      <div style={{ marginTop: 64 }}>
        <div className="mk-idx"><b>01</b><span>CORE WORKFLOWS</span></div>
        <div className="mk-cells mk-cells-3" style={{ marginTop: 24 }}>
          {WORKFLOWS.map(w => {
            const Illo = (DOC_ILLUSTRATIONS as Record<string, any>)[w.illo]
            return (
              <Link key={w.slug} to={`/docs/${w.slug}`} className="mk-cell" style={{ padding: 0, gap: 0 }}>
                <div aria-hidden="true" style={{ background: 'var(--surface-2)', borderBottom: '1px solid var(--border)', padding: '16px 18px 8px' }}>
                  {Illo ? <Illo className="w-full h-auto" style={{ maxHeight: 150, margin: '0 auto' }} /> : null}
                </div>
                <div style={{ padding: '16px 18px 20px', display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <h3 style={{ display: 'flex', alignItems: 'center', gap: 8 }}>{w.title}<ArrowRight size={15} strokeWidth={1.5} aria-hidden="true" style={{ color: 'var(--text-muted)' }} /></h3>
                  <p>{w.body}</p>
                </div>
              </Link>
            )
          })}
        </div>
      </div>

      <div style={{ marginTop: 64 }}>
        <div className="mk-idx"><b>02</b><span>BROWSE BY SECTION</span></div>
        <div className="mk-cells mk-cells-3" style={{ marginTop: 24 }}>
          {SECTIONS.map((s, i) => (
            <Link key={s.slug} to={`/docs/${s.slug}`} className="mk-cell">
              <p className="mk-label">{String(i + 1).padStart(2, '0')}</p>
              <h3 style={{ display: 'flex', alignItems: 'center', gap: 8 }}>{s.title}<ArrowRight size={15} strokeWidth={1.5} aria-hidden="true" style={{ color: 'var(--text-muted)' }} /></h3>
              <p>{s.body}</p>
            </Link>
          ))}
        </div>
      </div>
    </div>
  )
}
