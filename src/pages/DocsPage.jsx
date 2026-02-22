import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import {
  Menu, X, ChevronRight, ChevronDown, Hash, Copy, Check,
  Search, Command, Rocket, MessageSquare, Database,
  BarChart2, Sparkles, Shield, FileText, Code2,
  Home, Layers, BookOpen, ArrowLeft, ArrowRight, Terminal, Zap,
} from 'lucide-react'

/* ═══════════════════════════════════════════════════════════
   Navigation structure
   ═══════════════════════════════════════════════════════════ */

const nav = [
  {
    section: 'Getting Started',
    icon: Rocket,
    items: [
      { label: 'Introduction', href: '/docs', icon: Home },
      { label: 'Quick Start', href: '/docs/getting-started', icon: Sparkles },
      { label: 'Environment Setup', href: '/docs/environment', icon: Terminal },
    ],
  },
  {
    section: 'Core Features',
    icon: Layers,
    items: [
      { label: 'Data Sources', href: '/docs/data-sources', icon: Database },
      { label: 'Writing Queries', href: '/docs/queries', icon: MessageSquare },
      { label: 'Boards & Dashboards', href: '/docs/boards', icon: BarChart2 },
    ],
  },
  {
    section: 'Advanced',
    icon: BookOpen,
    items: [
      { label: 'Data Stitching', href: '/docs/data-stitching', icon: Layers },
      { label: 'LLM Assistant', href: '/docs/llm-assistant', icon: Sparkles },
      { label: 'Python & SQL', href: '/docs/code-editor', icon: Code2 },
    ],
  },
  {
    section: 'Legal',
    icon: Shield,
    items: [
      { label: 'Privacy Policy', href: '/docs/privacy', icon: Shield },
      { label: 'Terms of Service', href: '/docs/terms', icon: FileText },
      { label: 'POPIA Compliance', href: '/docs/popia', icon: Shield },
    ],
  },
]

const allPages = nav.flatMap(g => g.items)
const flatPages = allPages.map(p => p.href)

/* ═══════════════════════════════════════════════════════════
   Search
   ═══════════════════════════════════════════════════════════ */

const searchIndex = allPages.map(p => ({
  ...p,
  keywords: p.label.toLowerCase(),
}))

function SearchModal({ open, onClose }) {
  const [query, setQuery] = useState('')
  const inputRef = useRef(null)
  const navigate = useNavigate()

  useEffect(() => {
    if (open) { setQuery(''); setTimeout(() => inputRef.current?.focus(), 50) }
  }, [open])

  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape' && open) onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [open, onClose])

  const results = useMemo(() => {
    if (!query.trim()) return searchIndex
    const q = query.toLowerCase()
    return searchIndex.filter(d => d.keywords.includes(q) || d.label.toLowerCase().includes(q))
  }, [query])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-[100] flex items-start justify-center pt-[12vh] sm:pt-[15vh] px-4" onClick={onClose}>
      <div className="absolute inset-0 bg-slate-950/80 backdrop-blur-sm" />
      <div className="relative w-full max-w-xl rounded-2xl border border-white/[0.08] bg-slate-900/95 backdrop-blur-xl shadow-2xl shadow-black/40 overflow-hidden" onClick={e => e.stopPropagation()}>
        <div className="flex items-center gap-3 border-b border-white/[0.06] px-4 py-3.5">
          <Search className="h-5 w-5 text-slate-500 shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search documentation..."
            className="flex-1 bg-transparent text-sm text-white placeholder:text-slate-500 outline-none"
          />
          <kbd className="hidden sm:flex items-center gap-0.5 rounded-md border border-white/[0.08] bg-slate-800/60 px-2 py-0.5 text-[10px] text-slate-500 font-mono">ESC</kbd>
        </div>
        <div className="max-h-[50vh] overflow-y-auto overscroll-contain py-1.5 scrollbar-none">
          {results.length === 0 ? (
            <div className="px-4 py-12 text-center">
              <Search className="h-8 w-8 text-slate-700 mx-auto mb-3" />
              <p className="text-sm text-slate-500">No results for &ldquo;{query}&rdquo;</p>
            </div>
          ) : (
            results.map(doc => {
              const Icon = doc.icon
              return (
                <button
                  key={doc.href}
                  onClick={() => { onClose(); navigate(doc.href) }}
                  className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-white/[0.04] transition-colors group"
                >
                  <div className="w-8 h-8 rounded-lg bg-white/[0.04] flex items-center justify-center shrink-0 group-hover:bg-indigo-500/10 transition-colors">
                    <Icon className="h-4 w-4 text-slate-500 group-hover:text-indigo-400 transition-colors" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-slate-200 group-hover:text-white truncate">{doc.label}</p>
                  </div>
                  <ChevronRight className="h-3.5 w-3.5 text-slate-700 group-hover:text-slate-400 shrink-0 transition-colors" />
                </button>
              )
            })
          )}
        </div>
      </div>
    </div>
  )
}

/* ═══════════════════════════════════════════════════════════
   Sidebar nav
   ═══════════════════════════════════════════════════════════ */

function SidebarNav({ onClose, onOpenSearch }) {
  const location = useLocation()
  const [collapsed, setCollapsed] = useState({})

  const toggle = (section) => setCollapsed(p => ({ ...p, [section]: !p[section] }))

  return (
    <div className="flex flex-col h-full">
      <div className="px-3 pt-4 pb-2">
        <button
          onClick={onOpenSearch}
          className="w-full flex items-center gap-2.5 rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2 text-xs text-slate-500 transition hover:border-white/[0.1] hover:bg-white/[0.04] hover:text-slate-400"
        >
          <Search className="h-3.5 w-3.5 shrink-0" />
          <span className="flex-1 text-left">Search...</span>
          <kbd className="hidden sm:flex items-center gap-0.5 rounded border border-white/[0.06] bg-slate-800/50 px-1 py-0.5 text-[9px] font-mono text-slate-600">
            <Command className="h-2 w-2" />K
          </kbd>
        </button>
      </div>

      <nav className="flex-1 overflow-y-auto overscroll-contain px-3 pb-6 scrollbar-none">
        {nav.map(({ section, icon: SectionIcon, items }, gi) => {
          const isCollapsed = collapsed[section]
          return (
            <div key={section} className={gi > 0 ? 'mt-4' : 'mt-2'}>
              <button
                onClick={() => toggle(section)}
                className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-md hover:bg-white/[0.03] transition group"
              >
                <SectionIcon className="h-3.5 w-3.5 text-slate-600 group-hover:text-slate-500 transition-colors shrink-0" />
                <span className="flex-1 text-left text-[11px] font-semibold uppercase tracking-widest text-slate-500/80 group-hover:text-slate-400 transition-colors">
                  {section}
                </span>
                <ChevronDown className={`h-3 w-3 text-slate-600 transition-transform duration-200 ${isCollapsed ? '-rotate-90' : ''}`} />
              </button>
              {!isCollapsed && (
                <ul className="mt-1 space-y-0.5">
                  {items.map(({ label, href, icon: ItemIcon }) => {
                    const active = location.pathname === href
                    return (
                      <li key={href}>
                        <NavLink
                          to={href}
                          onClick={onClose}
                          className={`group relative flex items-center gap-2.5 rounded-lg px-3 py-[7px] text-[13px] transition-all duration-150 ${
                            active
                              ? 'bg-indigo-500/10 text-indigo-400 font-medium'
                              : 'text-slate-400 hover:bg-white/[0.04] hover:text-slate-200'
                          }`}
                        >
                          {active && (
                            <span className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-4 rounded-full bg-indigo-500" />
                          )}
                          <ItemIcon className={`h-3.5 w-3.5 shrink-0 transition-colors ${active ? 'text-indigo-400' : 'text-slate-600 group-hover:text-slate-400'}`} />
                          {label}
                        </NavLink>
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>
          )
        })}
      </nav>

      <div className="border-t border-white/[0.06] px-3 py-3">
        <Link to="/" className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs text-slate-500 hover:text-slate-300 hover:bg-white/[0.04] transition">
          <ArrowLeft className="h-3 w-3" />
          Back to Nubi
        </Link>
      </div>
    </div>
  )
}

/* ═══════════════════════════════════════════════════════════
   Table of Contents (right sidebar)
   ═══════════════════════════════════════════════════════════ */

function useScrollSpy(contentRef, pathname) {
  const [headings, setHeadings] = useState([])
  const [activeId, setActiveId] = useState('')

  useEffect(() => {
    if (!contentRef.current) return
    const timer = setTimeout(() => {
      const els = contentRef.current?.querySelectorAll('h2[id]')
      if (!els) return
      const items = Array.from(els).map(el => ({ id: el.id, text: el.textContent }))
      setHeadings(items)
      if (items.length) setActiveId(items[0].id)
    }, 50)
    return () => clearTimeout(timer)
  }, [contentRef, pathname])

  useEffect(() => {
    if (!headings.length) return
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter(e => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
        if (visible.length) setActiveId(visible[0].target.id)
      },
      { rootMargin: '-80px 0px -60% 0px', threshold: 0 }
    )
    headings.forEach(({ id }) => { const el = document.getElementById(id); if (el) observer.observe(el) })
    return () => observer.disconnect()
  }, [headings])

  return { headings, activeId }
}

function TableOfContents({ headings, activeId }) {
  if (!headings.length) return null
  return (
    <div className="hidden xl:block fixed top-14 right-0 w-56 h-[calc(100vh-3.5rem)] pt-10 pr-6 pl-2 overflow-y-auto scrollbar-none">
      <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500/80 mb-3 px-3">On this page</p>
      <nav className="relative">
        <div className="absolute left-0 top-0 bottom-0 w-px bg-white/[0.06]" />
        <ul className="space-y-0.5">
          {headings.map(({ id, text }) => {
            const active = activeId === id
            return (
              <li key={id}>
                <a
                  href={`#${id}`}
                  onClick={e => { e.preventDefault(); document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }}
                  className={`relative block pl-3 py-1.5 text-[12.5px] leading-snug transition-colors duration-150 border-l ${
                    active ? 'border-indigo-500 text-indigo-400 font-medium' : 'border-transparent text-slate-500 hover:text-slate-300 hover:border-slate-600'
                  }`}
                >
                  {text}
                </a>
              </li>
            )
          })}
        </ul>
      </nav>
    </div>
  )
}

/* ═══════════════════════════════════════════════════════════
   Doc page components
   ═══════════════════════════════════════════════════════════ */

function DocHeading({ title, description, badge }) {
  return (
    <div className="mb-10 pb-8 border-b border-white/[0.06] relative">
      <div className="absolute -top-12 -left-12 w-64 h-64 bg-indigo-600/[0.04] rounded-full blur-3xl pointer-events-none" />
      {badge && (
        <span className="relative inline-block mb-4 rounded-full bg-indigo-500/10 px-3 py-1 text-[11px] font-semibold text-indigo-400 tracking-wide uppercase border border-indigo-500/15">
          {badge}
        </span>
      )}
      <h1 className="relative text-3xl sm:text-4xl font-bold text-white mb-3 tracking-tight leading-tight">{title}</h1>
      {description && (
        <p className="relative text-base sm:text-lg text-slate-400 leading-relaxed max-w-2xl">{description}</p>
      )}
    </div>
  )
}

function DocSection({ title, children, id }) {
  return (
    <section className="mb-12 scroll-mt-20" id={id}>
      <h2 className="group text-xl font-semibold text-white mb-4 flex items-center gap-2">
        {id && (
          <a href={`#${id}`} className="opacity-0 group-hover:opacity-100 transition-opacity duration-150 text-slate-600 hover:text-indigo-400" aria-label={`Link to ${title}`}>
            <Hash className="h-4 w-4" />
          </a>
        )}
        {title}
      </h2>
      <div className="text-slate-400 text-[15px] leading-relaxed space-y-3">{children}</div>
    </section>
  )
}

function CodeBlock({ children, title }) {
  const [copied, setCopied] = useState(false)
  const codeRef = useRef(null)

  const handleCopy = useCallback(() => {
    const text = codeRef.current?.textContent || ''
    navigator.clipboard.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000) })
  }, [])

  return (
    <div className="rounded-xl overflow-hidden border border-white/[0.07] my-5 group/code">
      {title && (
        <div className="bg-slate-800/80 border-b border-white/[0.06] px-4 py-2.5 text-xs font-mono text-slate-400 flex items-center justify-between">
          <span>{title}</span>
        </div>
      )}
      <div className="relative">
        <pre className="bg-slate-900/70 p-4 overflow-x-auto text-sm font-mono text-slate-300 leading-relaxed">
          <code ref={codeRef}>{children}</code>
        </pre>
        <button
          onClick={handleCopy}
          className="absolute top-2.5 right-2.5 p-1.5 rounded-md bg-slate-800/80 border border-white/[0.08] text-slate-500 hover:text-white hover:bg-slate-700/80 transition opacity-0 group-hover/code:opacity-100"
        >
          {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
        </button>
      </div>
    </div>
  )
}

function EnvTable({ rows }) {
  return (
    <div className="my-4 overflow-x-auto rounded-xl border border-white/[0.06]">
      <table className="w-full text-left text-sm">
        <thead><tr className="border-b border-white/[0.06]"><th className="px-4 py-3 font-medium text-white">Variable</th><th className="px-4 py-3 font-medium text-white">Description</th></tr></thead>
        <tbody>
          {rows.map(([k, v], i) => (
            <tr key={i} className="border-b border-white/[0.04] last:border-0">
              <td className="px-4 py-2.5"><code className="bg-white/[0.06] px-1.5 py-0.5 rounded text-indigo-300 text-xs">{k}</code></td>
              <td className="px-4 py-2.5 text-slate-400">{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function BulletList({ items }) {
  return (
    <ul className="space-y-2 my-3">
      {items.map((item, i) => (
        <li key={i} className="flex items-start gap-2.5">
          <span className="w-1.5 h-1.5 rounded-full bg-indigo-500/50 mt-2 shrink-0" />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  )
}

function NumberedList({ items }) {
  return (
    <ol className="space-y-2 my-3">
      {items.map((item, i) => (
        <li key={i} className="flex items-start gap-3">
          <span className="w-6 h-6 rounded-full bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center text-xs font-bold text-indigo-400 shrink-0 mt-0.5">{i + 1}</span>
          <span className="pt-0.5">{item}</span>
        </li>
      ))}
    </ol>
  )
}

function DocNav({ prev, next }) {
  return (
    <div className="mt-16 pt-8 border-t border-white/[0.06] grid grid-cols-2 gap-4">
      {prev ? (
        <Link to={prev.href} className="group flex items-center gap-3 rounded-xl border border-white/[0.07] bg-slate-900/30 px-5 py-4 hover:border-indigo-500/20 hover:bg-slate-900/60 transition-all duration-150">
          <ArrowLeft className="h-4 w-4 text-slate-600 group-hover:text-indigo-400 transition-colors shrink-0" />
          <div className="min-w-0">
            <p className="text-[11px] text-slate-600 uppercase tracking-wider font-medium">Previous</p>
            <p className="text-sm font-medium text-slate-300 group-hover:text-white transition-colors truncate">{prev.label}</p>
          </div>
        </Link>
      ) : <span />}
      {next ? (
        <Link to={next.href} className="group flex items-center justify-end gap-3 rounded-xl border border-white/[0.07] bg-slate-900/30 px-5 py-4 hover:border-indigo-500/20 hover:bg-slate-900/60 transition-all duration-150 text-right">
          <div className="min-w-0">
            <p className="text-[11px] text-slate-600 uppercase tracking-wider font-medium">Next</p>
            <p className="text-sm font-medium text-slate-300 group-hover:text-white transition-colors truncate">{next.label}</p>
          </div>
          <ArrowRight className="h-4 w-4 text-slate-600 group-hover:text-indigo-400 transition-colors shrink-0" />
        </Link>
      ) : <span />}
    </div>
  )
}

/* ═══════════════════════════════════════════════════════════
   Page content
   ═══════════════════════════════════════════════════════════ */

const COMPANY = 'Cognizance Processing (Pty) Ltd'
const LAST_UPDATED = 'February 22, 2026'

function PageIntro({ onOpenSearch }) {
  return (
    <div className="-mt-4">
      {/* Hero */}
      <div className="relative text-center mb-14">
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[300px] bg-indigo-600/[0.06] rounded-full blur-[100px] pointer-events-none" />
        <div className="relative">
          <span className="inline-block mb-5 rounded-full bg-indigo-500/10 px-4 py-1.5 text-[11px] font-semibold text-indigo-400 tracking-wide uppercase border border-indigo-500/15">
            Documentation
          </span>
          <h1 className="text-4xl sm:text-5xl font-bold text-white mb-4 tracking-tight leading-[1.15]">
            Build with <span className="bg-gradient-to-r from-indigo-400 via-blue-400 to-purple-400 bg-clip-text text-transparent">Nubi</span>
          </h1>
          <p className="text-lg text-slate-400 leading-relaxed max-w-lg mx-auto mb-8">
            Everything you need to connect your data, ask questions in plain English, and build visual dashboards.
          </p>
          <button
            onClick={onOpenSearch}
            className="inline-flex items-center gap-3 rounded-xl border border-white/[0.1] bg-white/[0.03] pl-4 pr-3 py-2.5 text-sm text-slate-500 transition hover:border-white/[0.15] hover:bg-white/[0.06] hover:text-slate-400 mx-auto"
          >
            <Search className="h-4 w-4 shrink-0" />
            <span className="min-w-[180px] text-left">Search documentation...</span>
            <kbd className="hidden sm:flex items-center gap-0.5 rounded-md border border-white/[0.08] bg-slate-800/60 px-2 py-1 text-[10px] font-mono text-slate-500">
              <Command className="h-2.5 w-2.5" />K
            </kbd>
          </button>
        </div>
      </div>

      {/* Quick start cards */}
      <div className="mb-14">
        <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-widest mb-4" id="quick-links">Get started</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {[
            { icon: Rocket, label: 'Quick Start', desc: 'Install and run Nubi in under 2 minutes', href: '/docs/getting-started', color: 'from-indigo-500/20 to-blue-500/20' },
            { icon: Database, label: 'Data Sources', desc: 'Connect BigQuery, PostgreSQL, CSV & more', href: '/docs/data-sources', color: 'from-emerald-500/20 to-teal-500/20' },
            { icon: MessageSquare, label: 'Writing Queries', desc: 'Natural language, SQL, or Python', href: '/docs/queries', color: 'from-purple-500/20 to-pink-500/20' },
            { icon: BarChart2, label: 'Dashboards', desc: 'Pin widgets, build visual boards', href: '/docs/boards', color: 'from-amber-500/20 to-orange-500/20' },
          ].map(({ icon: Icon, label, desc, href, color }) => (
            <Link key={href} to={href} className="group relative flex items-start gap-4 rounded-xl border border-white/[0.07] bg-white/[0.015] p-5 hover:border-white/[0.12] hover:bg-white/[0.03] transition-all duration-200 overflow-hidden">
              <div className={`absolute inset-0 bg-gradient-to-br ${color} opacity-0 group-hover:opacity-100 transition-opacity duration-300`} />
              <div className="relative w-10 h-10 rounded-xl bg-white/[0.05] border border-white/[0.07] flex items-center justify-center shrink-0 group-hover:border-white/[0.12] transition">
                <Icon className="h-5 w-5 text-slate-400 group-hover:text-white transition-colors" />
              </div>
              <div className="relative min-w-0">
                <p className="text-sm font-semibold text-white mb-1 flex items-center gap-1.5">
                  {label}
                  <ChevronRight className="h-3.5 w-3.5 text-slate-600 group-hover:text-indigo-400 group-hover:translate-x-0.5 transition-all" />
                </p>
                <p className="text-[13px] text-slate-500 leading-relaxed group-hover:text-slate-400 transition-colors">{desc}</p>
              </div>
            </Link>
          ))}
        </div>
      </div>

      {/* Features overview */}
      <div className="mb-14">
        <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-widest mb-4" id="what-is-nubi">What is Nubi?</h2>
        <div className="rounded-xl border border-white/[0.07] bg-white/[0.015] p-6 mb-6">
          <p className="text-[15px] text-slate-400 leading-relaxed">
            Nubi is an open-source, LLM-first business intelligence platform. Connect your databases, ask questions in plain English, and get visual insights in seconds. No SQL required — but it's there when you want it.
          </p>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {[
            { icon: Sparkles, title: 'AI-Powered', desc: 'Natural language to SQL with Google Gemini' },
            { icon: Layers, title: 'Data Stitching', desc: 'Chain SQL & Python into multi-step pipelines' },
            { icon: Shield, title: 'Self-Hosted', desc: 'Your data stays on your infrastructure' },
          ].map(({ icon: Icon, title, desc }) => (
            <div key={title} className="rounded-xl border border-white/[0.06] bg-white/[0.01] p-4 text-center">
              <div className="w-9 h-9 rounded-lg bg-indigo-500/10 flex items-center justify-center mx-auto mb-3">
                <Icon className="h-4 w-4 text-indigo-400" />
              </div>
              <p className="text-sm font-medium text-white mb-1">{title}</p>
              <p className="text-xs text-slate-500 leading-relaxed">{desc}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Explore sections */}
      <div>
        <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-widest mb-4" id="explore">Explore the docs</h2>
        <div className="space-y-2">
          {nav.filter(g => g.section !== 'Legal').map(({ section, icon: SectionIcon, items }) => (
            <div key={section} className="rounded-xl border border-white/[0.06] bg-white/[0.01] overflow-hidden">
              <div className="flex items-center gap-2.5 px-5 py-3 border-b border-white/[0.04]">
                <SectionIcon className="h-4 w-4 text-slate-500" />
                <span className="text-sm font-semibold text-white">{section}</span>
              </div>
              <div className="divide-y divide-white/[0.04]">
                {items.map(({ label, href, icon: Icon }) => (
                  <Link key={href} to={href} className="group flex items-center gap-3 px-5 py-3 hover:bg-white/[0.03] transition-colors">
                    <Icon className="h-3.5 w-3.5 text-slate-600 group-hover:text-indigo-400 transition-colors shrink-0" />
                    <span className="text-sm text-slate-400 group-hover:text-white transition-colors">{label}</span>
                    <ChevronRight className="h-3 w-3 text-slate-700 group-hover:text-slate-400 ml-auto transition-colors" />
                  </Link>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function PageGettingStarted() {
  return (
    <>
      <DocHeading title="Quick Start" description="Get your first Nubi instance running in under 2 minutes." badge="Getting Started" />
      <DocSection title="Prerequisites" id="prerequisites">
        <BulletList items={[
          <><strong className="text-white">Node.js</strong> 18+ and npm</>,
          <><strong className="text-white">Python</strong> 3.10+</>,
          <><strong className="text-white">PostgreSQL</strong> (or a Supabase account)</>,
          <>A <strong className="text-white">Google Cloud</strong> account (for BigQuery and Gemini API access)</>,
        ]} />
      </DocSection>
      <DocSection title="Installation" id="installation">
        <CodeBlock title="terminal">{`# Clone the repository
git clone https://github.com/yourusername/nubi.git
cd nubi

# Install frontend dependencies
npm install

# Install backend dependencies
cd backend
pip install -r requirements.txt

# Start the development servers
npm run dev          # Frontend on :5173
python -m app.main   # Backend on :8000`}</CodeBlock>
      </DocSection>
      <DocSection title="First steps" id="first-steps">
        <NumberedList items={[
          'Open the app at http://localhost:5173 and create an account.',
          'Navigate to Datastores and add your first database connection.',
          'Create a Board and add a query — try asking a question in plain English.',
          'Pin the result as a widget on your dashboard.',
        ]} />
      </DocSection>
    </>
  )
}

function PageEnvironment() {
  return (
    <>
      <DocHeading title="Environment Setup" description="Configure environment variables for your Nubi deployment." badge="Setup" />
      <DocSection title="Environment variables" id="env-vars">
        <p>Create a <code className="bg-white/[0.06] px-1.5 py-0.5 rounded text-indigo-300 text-xs">.env</code> file in the project root:</p>
        <EnvTable rows={[
          ['VITE_SUPABASE_URL', 'Your Supabase project URL'],
          ['VITE_SUPABASE_ANON_KEY', 'Supabase anonymous key'],
          ['GOOGLE_APPLICATION_CREDENTIALS', 'Path to GCP service account JSON'],
          ['GEMINI_API_KEY', 'Google Gemini API key'],
        ]} />
      </DocSection>
    </>
  )
}

function PageDataSources() {
  return (
    <>
      <DocHeading title="Data Sources" description="Connect BigQuery, PostgreSQL, Supabase, or upload CSVs." badge="Data" />
      <DocSection title="Supported connectors" id="connectors">
        <BulletList items={[
          <><strong className="text-white">BigQuery</strong> — Connect with a GCP service account. Nubi reads your schema and lets you query with natural language or SQL.</>,
          <><strong className="text-white">PostgreSQL</strong> — Direct connection via connection string. Supports SSL.</>,
          <><strong className="text-white">CSV Upload</strong> — Upload CSV files directly. Nubi creates a temporary table and infers column types.</>,
          <><strong className="text-white">Supabase</strong> — Native integration with Supabase PostgreSQL databases.</>,
          <><strong className="text-white">Coming soon</strong> — MySQL, SQLite, Snowflake, and DuckDB are on the roadmap.</>,
        ]} />
      </DocSection>
      <DocSection title="Adding a datastore" id="adding">
        <NumberedList items={[
          'Navigate to Datastores from the sidebar.',
          'Click Add Store and choose your connector type.',
          'Provide connection credentials (these are encrypted at rest).',
          'Nubi will introspect your schema automatically.',
          'You can now reference this datastore in any board or query.',
        ]} />
      </DocSection>
    </>
  )
}

function PageQueries() {
  return (
    <>
      <DocHeading title="Writing Queries" description="Use natural language, SQL, or Python to explore your data." badge="Core" />
      <DocSection title="Natural language" id="natural-language">
        <p>Type a question in the query editor and Nubi translates it into SQL using the connected LLM. The generated query is shown for transparency and can be edited before execution.</p>
        <BulletList items={['"What were our top 10 products by revenue last month?"', '"Show me the daily active users trend for the past 90 days"', '"Compare Q3 vs Q4 sales by region"']} />
      </DocSection>
      <DocSection title="SQL mode" id="sql-mode">
        <p>Write SQL directly when you need precise control. Nubi provides schema-aware autocomplete, syntax highlighting, query history, and template variables with <code className="bg-white/[0.06] px-1.5 py-0.5 rounded text-indigo-300 text-xs">{'{{variable_name}}'}</code> syntax.</p>
      </DocSection>
      <DocSection title="Python mode" id="python-mode">
        <p>Use Python for advanced transformations. Your code runs in a sandboxed environment with access to <code className="bg-white/[0.06] px-1.5 py-0.5 rounded text-indigo-300 text-xs">pandas</code>, <code className="bg-white/[0.06] px-1.5 py-0.5 rounded text-indigo-300 text-xs">numpy</code>, the <code className="bg-white/[0.06] px-1.5 py-0.5 rounded text-indigo-300 text-xs">result</code> variable from the previous step, and a <code className="bg-white/[0.06] px-1.5 py-0.5 rounded text-indigo-300 text-xs">context</code> dict for template variables.</p>
      </DocSection>
    </>
  )
}

function PageBoards() {
  return (
    <>
      <DocHeading title="Boards & Dashboards" description="Pin queries as widgets — tables, charts, KPI cards." badge="Visuals" />
      <DocSection title="Creating boards" id="creating-boards">
        <NumberedList items={[
          'Go to the Portal and click New Board.',
          'Give it a name and optional description.',
          'Add queries by clicking Add Query inside the board.',
          'Each query can produce a table, chart, or KPI widget.',
        ]} />
      </DocSection>
      <DocSection title="Visualization types" id="vis-types">
        <BulletList items={[
          <><strong className="text-white">Table</strong> — Sortable, filterable data grid</>,
          <><strong className="text-white">Bar Chart</strong> — Vertical or horizontal</>,
          <><strong className="text-white">Line Chart</strong> — Time series and trends</>,
          <><strong className="text-white">Pie Chart</strong> — Proportional breakdowns</>,
          <><strong className="text-white">KPI Card</strong> — Single metric with optional comparison</>,
        ]} />
      </DocSection>
    </>
  )
}

function PageDataStitching() {
  return (
    <>
      <DocHeading title="Data Stitching" description="Chain SQL and Python steps into multi-step workflows." badge="Workflows" />
      <DocSection title="Overview" id="overview">
        <p>Data stitching lets you chain multiple queries together into a pipeline. The output of one step feeds into the next, allowing complex multi-step analysis. Each step can be SQL, Python, or a natural language question.</p>
      </DocSection>
      <DocSection title="Template variables" id="template-vars">
        <p>Use <code className="bg-white/[0.06] px-1.5 py-0.5 rounded text-indigo-300 text-xs">{'{{step_name.column}}'}</code> to reference values from previous steps. Nubi resolves these at execution time.</p>
        <CodeBlock title="example.sql">{`-- Step 1: Get top region
SELECT region FROM sales GROUP BY region ORDER BY SUM(revenue) DESC LIMIT 1

-- Step 2: Drill into that region
SELECT * FROM sales WHERE region = '{{step1.region}}'`}</CodeBlock>
      </DocSection>
    </>
  )
}

function PageLLM() {
  return (
    <>
      <DocHeading title="LLM Assistant" description="Conversational analytics powered by Google Gemini." badge="AI" />
      <DocSection title="Chat interface" id="chat">
        <p>Every query has an integrated chat assistant. Use it to ask follow-up questions, request query modifications, get explanations of results, or explore related insights.</p>
      </DocSection>
      <DocSection title="Context awareness" id="context">
        <p>The assistant understands your database schema, the current query and results, previous conversation messages, and template variables from data stitching.</p>
      </DocSection>
    </>
  )
}

function PageCodeEditor() {
  return (
    <>
      <DocHeading title="Python & SQL Editor" description="Full code editors with autocomplete and syntax highlighting." badge="Developer" />
      <DocSection title="Editor features" id="editor-features">
        <BulletList items={[
          'Syntax highlighting for SQL and Python',
          'Tab indentation and keyboard shortcuts (Cmd+S to save, Cmd+Enter to run)',
          'Line numbers and scroll sync',
          'Template variable support',
        ]} />
      </DocSection>
    </>
  )
}

function LegalSection({ title, children }) {
  return (
    <div className="mb-8">
      <h3 className="text-[15px] font-semibold text-white mb-3">{title}</h3>
      <div className="text-slate-400 text-[15px] leading-relaxed space-y-3">{children}</div>
    </div>
  )
}

function PagePrivacy() {
  return (
    <>
      <DocHeading title="Privacy Policy" description={`How ${COMPANY} handles your data.`} />
      <p className="text-xs text-slate-500 -mt-6 mb-10">Last updated: {LAST_UPDATED}</p>
      <DocSection title="Introduction" id="intro"><p>This Privacy Policy explains how {COMPANY} trading as Nubi ("we", "us") collects, uses, and protects your information. Nubi is designed as a self-hosted platform — your data stays on your infrastructure by default.</p></DocSection>
      <DocSection title="Information we collect" id="info-collect"><BulletList items={[<><strong className="text-white">Account info</strong> — email address and authentication credentials (via Supabase Auth).</>,<><strong className="text-white">Usage data</strong> — anonymized analytics to improve the product. Does not include query contents or database records.</>,<><strong className="text-white">Database metadata</strong> — table names, column names, data types to enable natural language queries. Stored locally.</>,<><strong className="text-white">Query history</strong> — stored within your deployment for history and caching. In self-hosted mode, never leaves your servers.</>]} /></DocSection>
      <DocSection title="How we use your information" id="how-use"><BulletList items={['Provide and maintain the Service','Authenticate users and manage sessions','Enable natural language to SQL translation','Improve the product through anonymized analytics','We do not sell your personal information to third parties.']} /></DocSection>
      <DocSection title="Third-party services" id="third-party"><BulletList items={[<><strong className="text-white">Google Gemini API</strong> — questions and schema context are sent for processing. No raw database data is sent.</>,<><strong className="text-white">Supabase</strong> — authentication and metadata storage.</>,<><strong className="text-white">Database providers</strong> — direct connections from your deployment. Credentials encrypted at rest.</>]} /></DocSection>
      <DocSection title="Data security" id="security"><BulletList items={['All data in transit encrypted via TLS/HTTPS','Database credentials encrypted at rest','Self-hosted deployments keep all data within your infrastructure','Authentication via industry-standard protocols','Principle of least privilege for all data access']} /></DocSection>
      <DocSection title="Your rights" id="rights"><BulletList items={['Access personal information we hold about you','Request correction of inaccurate information','Request deletion of your account and data','Export your data in a portable format','Opt out of non-essential data collection']} /><p>To exercise these rights, contact us at <strong className="text-white">privacy@cognizanceprocessing.co.za</strong>.</p></DocSection>
      <DocSection title="Contact" id="contact-privacy"><p>Questions about this policy? Email <strong className="text-white">privacy@cognizanceprocessing.co.za</strong> or write to {COMPANY}, South Africa.</p></DocSection>
    </>
  )
}

function PageTerms() {
  return (
    <>
      <DocHeading title="Terms of Service" description={`Terms governing your use of Nubi, operated by ${COMPANY}.`} />
      <p className="text-xs text-slate-500 -mt-6 mb-10">Last updated: {LAST_UPDATED}</p>
      <DocSection title="Acceptance of terms" id="acceptance"><p>By accessing or using Nubi ("the Service"), you agree to be bound by these Terms. These terms apply to all users, including visitors, registered users, and contributors. If you do not agree, you may not use the Service.</p></DocSection>
      <DocSection title="Description of service" id="description"><p>Nubi is an open-source, LLM-first business intelligence platform that allows users to query databases using natural language, SQL, and Python. The Service is provided under the Apache 2.0 license for self-hosted deployments.</p></DocSection>
      <DocSection title="User accounts" id="accounts"><p>You are responsible for maintaining the confidentiality of your account credentials and for all activities under your account. Notify us immediately of any unauthorized use. We reserve the right to suspend or terminate accounts that violate these terms.</p></DocSection>
      <DocSection title="Your data" id="your-data"><p>You retain full ownership of all data processed through Nubi. We do not claim intellectual property rights over your data. When self-hosting, your data never leaves your infrastructure. We do not sell, share, or use your data for advertising.</p></DocSection>
      <DocSection title="Acceptable use" id="acceptable-use"><BulletList items={['Do not violate any applicable law or regulation','Do not infringe upon the rights of others','Do not transmit malicious code or compromise system security','Do not interfere with or disrupt the Service','Do not attempt unauthorized access to other users\' accounts or data']} /></DocSection>
      <DocSection title="Intellectual property" id="ip"><p>Nubi's source code is licensed under Apache 2.0. The Nubi name, logo, and branding are trademarks of {COMPANY} and may not be used without permission.</p></DocSection>
      <DocSection title="Disclaimers" id="disclaimers"><p>The Service is provided "as is" without warranties of any kind. AI-generated queries should be reviewed before use for critical business decisions. We are not liable for inaccuracies in LLM-generated outputs.</p></DocSection>
      <DocSection title="Limitation of liability" id="liability"><p>To the maximum extent permitted by South African law, {COMPANY} shall not be liable for any indirect, incidental, special, consequential, or punitive damages arising from your use of the Service.</p></DocSection>
      <DocSection title="Governing law" id="governing-law"><p>These terms are governed by the laws of the Republic of South Africa. Any disputes shall be resolved in the courts of the Republic of South Africa.</p></DocSection>
      <DocSection title="Contact" id="contact-terms"><p>Questions? Email <strong className="text-white">legal@cognizanceprocessing.co.za</strong> or write to {COMPANY}, South Africa.</p></DocSection>
    </>
  )
}

function PagePOPIA() {
  return (
    <>
      <DocHeading title="POPIA Compliance" description={`How ${COMPANY} complies with the Protection of Personal Information Act (POPIA).`} />
      <p className="text-xs text-slate-500 -mt-6 mb-10">Last updated: {LAST_UPDATED}</p>
      <DocSection title="About POPIA" id="about-popia">
        <p>The Protection of Personal Information Act, 2013 (POPIA) is South Africa's data protection legislation. It regulates how personal information is collected, stored, processed, and shared. As a South African company, {COMPANY} is committed to full compliance with POPIA.</p>
      </DocSection>
      <DocSection title="Responsible party" id="responsible-party">
        <p>The responsible party in terms of POPIA is:</p>
        <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 my-4 text-sm space-y-1">
          <p className="text-white font-medium">{COMPANY}</p>
          <p>Republic of South Africa</p>
          <p>Email: <strong className="text-white">info@cognizanceprocessing.co.za</strong></p>
        </div>
      </DocSection>
      <DocSection title="Lawful basis for processing" id="lawful-basis">
        <p>We process personal information on the following lawful grounds under POPIA Section 11:</p>
        <BulletList items={[
          <><strong className="text-white">Consent</strong> — you consent to processing when you create an account and use the Service.</>,
          <><strong className="text-white">Contract</strong> — processing is necessary to perform our contractual obligations to you.</>,
          <><strong className="text-white">Legitimate interest</strong> — we process anonymized usage data to improve the Service, balanced against your rights.</>,
          <><strong className="text-white">Legal obligation</strong> — we may process information to comply with South African law.</>,
        ]} />
      </DocSection>
      <DocSection title="Categories of personal information" id="categories">
        <p>In accordance with POPIA Section 1, we may process the following categories:</p>
        <BulletList items={[
          <><strong className="text-white">Identifiers</strong> — email address, account ID.</>,
          <><strong className="text-white">Electronic communications</strong> — session tokens, IP addresses (for security).</>,
          <><strong className="text-white">Technical data</strong> — browser type, device information (anonymized analytics only).</>,
        ]} />
        <p>We do <strong className="text-white">not</strong> process special personal information (race, health, biometrics, etc.) as defined in POPIA Section 26.</p>
      </DocSection>
      <DocSection title="Your rights under POPIA" id="popia-rights">
        <p>As a data subject under POPIA, you have the right to:</p>
        <BulletList items={[
          <><strong className="text-white">Access</strong> (Section 23) — request confirmation of and access to your personal information.</>,
          <><strong className="text-white">Correction</strong> (Section 24) — request correction or deletion of inaccurate information.</>,
          <><strong className="text-white">Deletion</strong> (Section 24) — request destruction of your personal information.</>,
          <><strong className="text-white">Object</strong> (Section 11(3)) — object to processing of your personal information.</>,
          <><strong className="text-white">Complain</strong> (Section 74) — lodge a complaint with the Information Regulator.</>,
        ]} />
      </DocSection>
      <DocSection title="Cross-border transfers" id="cross-border">
        <p>When you use the LLM features, schema metadata (not raw data) may be sent to Google's Gemini API servers, which may be located outside South Africa. This transfer is conducted in accordance with POPIA Section 72 requirements. No database records or personal information from your data sources are transmitted.</p>
      </DocSection>
      <DocSection title="Data retention" id="retention">
        <p>We retain personal information only for as long as necessary to fulfil the purposes for which it was collected, in accordance with POPIA Section 14. Account information is retained while your account is active. You may request deletion at any time.</p>
      </DocSection>
      <DocSection title="Security measures" id="popia-security">
        <p>In accordance with POPIA Section 19, we implement appropriate technical and organisational measures:</p>
        <BulletList items={[
          'Encryption of data in transit (TLS) and at rest',
          'Access controls and authentication',
          'Regular security assessments',
          'Self-hosted deployments keep data within your own infrastructure',
          'Incident response procedures for data breaches (Section 22)',
        ]} />
      </DocSection>
      <DocSection title="Information Regulator" id="regulator">
        <p>If you are not satisfied with our handling of your personal information, you may lodge a complaint with the South African Information Regulator:</p>
        <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 my-4 text-sm space-y-1">
          <p className="text-white font-medium">The Information Regulator (South Africa)</p>
          <p>JD House, 27 Stiemens Street, Braamfontein, Johannesburg, 2001</p>
          <p>Email: <strong className="text-white">enquiries@inforegulator.org.za</strong></p>
          <p>Website: <strong className="text-white">https://inforegulator.org.za</strong></p>
        </div>
      </DocSection>
      <DocSection title="Contact us" id="contact-popia">
        <p>For any POPIA-related queries or to exercise your rights, contact our Information Officer:</p>
        <p>Email: <strong className="text-white">info@cognizanceprocessing.co.za</strong></p>
      </DocSection>
    </>
  )
}

/* ═══════════════════════════════════════════════════════════
   Route → content map
   ═══════════════════════════════════════════════════════════ */

const routeContent = {
  '/docs': PageIntro,
  '/docs/getting-started': PageGettingStarted,
  '/docs/environment': PageEnvironment,
  '/docs/data-sources': PageDataSources,
  '/docs/queries': PageQueries,
  '/docs/boards': PageBoards,
  '/docs/data-stitching': PageDataStitching,
  '/docs/llm-assistant': PageLLM,
  '/docs/code-editor': PageCodeEditor,
  '/docs/privacy': PagePrivacy,
  '/docs/terms': PageTerms,
  '/docs/popia': PagePOPIA,
}

/* ═══════════════════════════════════════════════════════════
   Main layout
   ═══════════════════════════════════════════════════════════ */

export default function DocsPage() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const location = useLocation()
  const contentRef = useRef(null)
  const { headings, activeId } = useScrollSpy(contentRef, location.pathname)

  useEffect(() => {
    setSidebarOpen(false)
    window.scrollTo(0, 0)
  }, [location.pathname])

  useEffect(() => {
    const h = (e) => { if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); setSearchOpen(s => !s) } }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])

  const ContentComponent = routeContent[location.pathname] || PageIntro
  const currentIdx = flatPages.indexOf(location.pathname)
  const prev = currentIdx > 0 ? allPages[currentIdx - 1] : null
  const next = currentIdx < flatPages.length - 1 ? allPages[currentIdx + 1] : null

  return (
    <div className="min-h-screen bg-slate-950 text-white antialiased">
      {/* Top bar */}
      <header className="fixed inset-x-0 top-0 z-50 h-14 border-b border-white/[0.07] bg-slate-950/85 backdrop-blur-xl">
        <div className="flex items-center gap-4 px-4 sm:px-6 w-full max-w-[1440px] mx-auto h-full">
          <button onClick={() => setSidebarOpen(!sidebarOpen)} className="lg:hidden rounded-lg p-1.5 text-slate-400 hover:bg-white/5 hover:text-white transition" aria-label="Toggle sidebar">
            {sidebarOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>

          <Link to="/" className="flex items-center gap-2.5">
            <img src="/nubi.png" alt="Nubi" className="h-6 w-6 rounded" />
            <span className="text-sm font-bold text-white tracking-tight">Nubi</span>
          </Link>

          <span className="text-slate-700">/</span>
          <Link to="/docs" className="text-[13px] text-slate-400 font-medium hover:text-white transition">Docs</Link>

          <button
            onClick={() => setSearchOpen(true)}
            className="hidden sm:flex items-center gap-2.5 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-sm text-slate-500 transition hover:border-white/[0.12] hover:bg-white/[0.05] hover:text-slate-400 ml-2"
          >
            <Search className="h-3.5 w-3.5" />
            <span className="hidden md:inline">Search...</span>
            <kbd className="hidden md:flex items-center gap-0.5 rounded border border-white/[0.08] bg-slate-800/60 px-1.5 py-0.5 text-[10px] font-mono text-slate-500"><Command className="h-2.5 w-2.5" />K</kbd>
          </button>

          <div className="ml-auto flex items-center gap-3">
            <button onClick={() => setSearchOpen(true)} className="sm:hidden rounded-lg p-1.5 text-slate-400 hover:bg-white/5 hover:text-white transition" aria-label="Search"><Search className="h-5 w-5" /></button>
            <Link to="/" className="hidden sm:block text-[13px] text-slate-400 hover:text-white transition">Home</Link>
            <Link to="/login" className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-1.5 text-xs font-semibold text-white shadow-lg shadow-indigo-600/20 transition hover:bg-indigo-500">
              <Sparkles className="h-3 w-3" />
              Get started
            </Link>
          </div>
        </div>
      </header>

      {/* Mobile sidebar overlay */}
      {sidebarOpen && <div className="fixed inset-0 z-30 bg-black/60 backdrop-blur-sm lg:hidden" onClick={() => setSidebarOpen(false)} />}

      {/* Sidebar */}
      <aside className={`fixed top-14 left-0 z-40 h-[calc(100vh-3.5rem)] w-64 border-r border-white/[0.06] bg-slate-950/95 backdrop-blur-xl overflow-hidden transition-transform duration-200 lg:translate-x-0 ${sidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}`}>
        <SidebarNav onClose={() => setSidebarOpen(false)} onOpenSearch={() => { setSidebarOpen(false); setSearchOpen(true) }} />
      </aside>

      {/* Main content */}
      <div className="pt-14 lg:pl-64 xl:pr-56">
        <main className="mx-auto max-w-[780px] px-6 py-10 lg:px-10 lg:py-14" ref={contentRef}>
          <ContentComponent onOpenSearch={() => setSearchOpen(true)} />
          {location.pathname !== '/docs' && <DocNav prev={prev} next={next} />}
        </main>
      </div>

      <TableOfContents headings={headings} activeId={activeId} />

      <SearchModal open={searchOpen} onClose={() => setSearchOpen(false)} />
    </div>
  )
}
