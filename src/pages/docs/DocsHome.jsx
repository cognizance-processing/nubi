/**
 * DocsHome — the illustrated documentation landing page (slug "home").
 *
 * Replaces the plain-markdown index with a guided home: a hero + prominent
 * search, illustrated cards for the core product workflows, and quick links
 * into the three doc sections (Using Nubi · Nubi Cloud · Open-source project).
 * Uses the same brand illustrations as the landing/gallery so docs feel of a
 * piece with the product.
 */
import { Link } from 'react-router-dom'
import { Search, ArrowRight, Rocket, Cloud, Github } from 'lucide-react'
import { DOC_ILLUSTRATIONS } from '../../components/illustrations/docMap.js'

// Core product workflows — each links to the deep section doc.
const WORKFLOWS = [
  {
    slug: 'queries-and-params', illo: 'QueryWorkspace', title: 'Queries & parameters',
    body: 'Write SQL, add typed {{params}}, reuse a query library, and let AI text-to-SQL ground on your real schema.',
  },
  {
    slug: 'dashboards', illo: 'DashboardCanvas', title: 'Dashboards & widgets',
    body: 'Compose KPIs, charts, tables, and filters on a grid. Nine widget types, variables, and a code view.',
  },
  {
    slug: 'flows', illo: 'FlowOrchestration', title: 'Flows',
    body: 'Cell-based pipelines — SQL, Python, and Note cells in a notebook or canvas view, with scheduling.',
  },
  {
    slug: 'ai-and-mcp', illo: 'LlmDashboards', title: 'AI, Chat & MCP',
    body: 'Grounded text-to-SQL, an agentic chat loop, and an MCP server so agents can author dashboards.',
  },
  {
    slug: 'embedding', illo: 'EmbedAuth', title: 'Embedding & security',
    body: 'Drop in <nubi-dashboard>, mint per-viewer JWTs, and enforce row-level security as signed claims.',
  },
  {
    slug: 'connectors', illo: 'ConnectorSdk', title: 'Connectors',
    body: 'Point at any Postgres-compatible warehouse or wrap your own source with the Python connector SDK.',
  },
]

const SECTIONS = [
  { icon: Rocket, slug: 'getting-started', title: 'Using Nubi', body: 'How to use the product — for self-host and Cloud alike.' },
  { icon: Cloud, slug: 'cloud', title: 'Nubi Cloud', body: 'The thin managed layer: plans, usage wallet, billing.' },
  { icon: Github, slug: 'self-host', title: 'Open-source project', body: 'Self-host, architecture, security internals, building on Nubi.' },
]

function WorkflowCard({ slug, illo, title, body }) {
  const Illo = DOC_ILLUSTRATIONS[illo]
  return (
    <Link
      to={`/docs/${slug}`}
      className="group flex flex-col rounded-2xl border border-border bg-surface overflow-hidden hover:border-brand-teal/40 hover:shadow-md transition-all"
    >
      <div className="bg-surface-2 px-5 pt-5 pb-2 border-b border-border">
        {Illo ? <Illo className="w-full h-auto max-h-40 mx-auto" /> : null}
      </div>
      <div className="p-5 flex flex-col gap-1.5 flex-1">
        <h3 className="font-display text-base font-bold text-fg flex items-center gap-1.5">
          {title}
          <ArrowRight size={15} className="text-muted group-hover:text-brand-teal group-hover:translate-x-0.5 transition-all" />
        </h3>
        <p className="text-sm leading-relaxed text-muted">{body}</p>
      </div>
    </Link>
  )
}

export default function DocsHome({ onOpenSearch }) {
  return (
    <div className="max-w-5xl mx-auto">
      {/* Hero */}
      <div className="text-center pt-2 pb-8">
        <p className="text-xs font-semibold tracking-widest uppercase text-brand-teal mb-3">Documentation</p>
        <h1 className="font-display text-3xl sm:text-4xl lg:text-5xl font-bold text-fg">
          Build with <span className="text-brand-blue">Nubi</span>
        </h1>
        <p className="mt-4 text-sm sm:text-base text-muted max-w-2xl mx-auto leading-relaxed">
          Embedded-first BI where the kernel runs in the browser. Connect a warehouse, write a query,
          compose a dashboard, and embed it with per-viewer security — start here.
        </p>

        {/* Prominent search */}
        <button
          onClick={onOpenSearch}
          className="mt-7 w-full max-w-xl mx-auto flex items-center gap-3 px-4 h-12 rounded-xl border border-border bg-surface hover:bg-surface-2 hover:border-brand-teal/40 transition-colors text-left"
        >
          <Search size={18} className="text-muted shrink-0" />
          <span className="text-sm text-muted flex-1">Search the docs…</span>
          <kbd className="hidden sm:inline-flex items-center px-2 h-6 text-[11px] font-mono rounded-md border border-border bg-surface-2 text-muted">⌘K</kbd>
        </button>
      </div>

      {/* Core workflows */}
      <div className="mt-4">
        <h2 className="font-display text-lg font-bold text-fg mb-4">Core workflows</h2>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {WORKFLOWS.map((w) => <WorkflowCard key={w.slug} {...w} />)}
        </div>
      </div>

      {/* Sections */}
      <div className="mt-12">
        <h2 className="font-display text-lg font-bold text-fg mb-4">Browse by section</h2>
        <div className="grid sm:grid-cols-3 gap-4">
          {SECTIONS.map(({ icon: Icon, slug, title, body }) => (
            <Link
              key={slug}
              to={`/docs/${slug}`}
              className="group flex flex-col gap-2 rounded-2xl border border-border bg-surface p-5 hover:border-brand-teal/40 hover:shadow-md transition-all"
            >
              <Icon size={20} className="text-brand-teal" />
              <h3 className="font-display text-base font-bold text-fg flex items-center gap-1.5">
                {title}
                <ArrowRight size={15} className="text-muted group-hover:text-brand-teal group-hover:translate-x-0.5 transition-all" />
              </h3>
              <p className="text-sm leading-relaxed text-muted">{body}</p>
            </Link>
          ))}
        </div>
      </div>
    </div>
  )
}
