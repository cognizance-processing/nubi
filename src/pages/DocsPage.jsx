import { useState, useEffect } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { Menu, X, ChevronRight, BookOpen, FileText } from 'lucide-react'
import { DOC_GROUPS, getDoc, FIRST_DOC } from '../docs/registry.js'
import MarkdownRenderer from '../components/MarkdownRenderer.jsx'

// ── Inline SVG illustrations ──────────────────────────────────────────────

function DocsHeroIllustration() {
  return (
    <svg
      viewBox="0 0 240 160"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className="w-full h-full"
      aria-hidden="true"
    >
      {/* Background glow */}
      <ellipse cx="120" cy="80" rx="90" ry="60" fill="url(#heroGlow)" opacity="0.35" />

      {/* Stack of document pages */}
      <rect x="55" y="50" width="80" height="100" rx="6" fill="#16223b" stroke="#21304a" strokeWidth="1.5" />
      <rect x="60" y="44" width="80" height="100" rx="6" fill="#111a2e" stroke="#21304a" strokeWidth="1.5" />
      <rect x="65" y="38" width="80" height="100" rx="6" fill="#16223b" stroke="#2456a6" strokeWidth="1.5" />

      {/* Text lines on top page */}
      <rect x="78" y="55" width="55" height="4" rx="2" fill="#17b3a3" />
      <rect x="78" y="65" width="50" height="3" rx="1.5" fill="#2dd4bf" opacity="0.5" />
      <rect x="78" y="73" width="45" height="3" rx="1.5" fill="#2dd4bf" opacity="0.4" />
      <rect x="78" y="81" width="52" height="3" rx="1.5" fill="#2dd4bf" opacity="0.4" />

      {/* Divider */}
      <rect x="78" y="91" width="55" height="1" rx="0.5" fill="#21304a" />

      {/* Table rows */}
      <rect x="78" y="98" width="20" height="3" rx="1.5" fill="#4d8de0" opacity="0.8" />
      <rect x="103" y="98" width="28" height="3" rx="1.5" fill="#4d8de0" opacity="0.5" />
      <rect x="78" y="106" width="15" height="3" rx="1.5" fill="#4d8de0" opacity="0.8" />
      <rect x="103" y="106" width="22" height="3" rx="1.5" fill="#4d8de0" opacity="0.5" />

      {/* Code block */}
      <rect x="78" y="115" width="55" height="16" rx="3" fill="#0a1020" />
      <rect x="82" y="119" width="30" height="2.5" rx="1.2" fill="#4d8de0" />
      <rect x="82" y="124" width="20" height="2.5" rx="1.2" fill="#2dd4bf" />

      {/* Small decorative arrow */}
      <path d="M155 80 L168 80" stroke="#17b3a3" strokeWidth="2" strokeLinecap="round" />
      <path d="M163 75 L168 80 L163 85" stroke="#17b3a3" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />

      {/* Data chip */}
      <rect x="172" y="68" width="40" height="24" rx="6" fill="url(#chipGrad)" />
      <rect x="178" y="74" width="15" height="2.5" rx="1.2" fill="white" opacity="0.9" />
      <rect x="178" y="80" width="22" height="2.5" rx="1.2" fill="white" opacity="0.6" />
      <rect x="178" y="86" width="12" height="2.5" rx="1.2" fill="white" opacity="0.5" />

      <defs>
        <radialGradient id="heroGlow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#17b3a3" />
          <stop offset="100%" stopColor="#2456a6" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="chipGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#2456a6" />
          <stop offset="100%" stopColor="#17b3a3" />
        </linearGradient>
      </defs>
    </svg>
  )
}

function ArrowDataIllustration() {
  // Brand palette for the nodes
  const nodeColors = [
    { fill: '#2456a6', stroke: '#1b2363' },  // Warehouse — brand-blue
    { fill: '#16223b', stroke: '#21304a' },  // Edge — surface-2
    { fill: '#16223b', stroke: '#21304a' },  // Browser — surface-2
    { fill: '#17b3a3', stroke: '#0f9e90' },  // Kernel — brand-teal
  ]
  const barColors = ['rgba(255,255,255,0.8)', '#4d8de0', '#4d8de0', 'rgba(255,255,255,0.8)']

  return (
    <svg
      viewBox="0 0 200 80"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className="w-full h-full"
      aria-hidden="true"
    >
      {[0, 1, 2, 3].map((i) => (
        <g key={i}>
          <rect
            x={10 + i * 48}
            y="20"
            width="36"
            height="40"
            rx="5"
            fill={nodeColors[i].fill}
            stroke={nodeColors[i].stroke}
            strokeWidth="1.2"
          />
          {[0, 1, 2].map(j => (
            <rect
              key={j}
              x={14 + i * 48 + j * 10}
              y="28"
              width="6"
              height={10 + j * 6}
              rx="2"
              fill={barColors[i]}
            />
          ))}
        </g>
      ))}

      {/* Arrows between nodes */}
      {[0, 1, 2].map(i => (
        <g key={i}>
          <path
            d={`M ${46 + i * 48} 40 L ${52 + i * 48} 40`}
            stroke="#17b3a3"
            strokeWidth="1.5"
            strokeDasharray="3 2"
          />
          <path
            d={`M ${49 + i * 48} 37 L ${52 + i * 48} 40 L ${49 + i * 48} 43`}
            stroke="#17b3a3"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </g>
      ))}

      {/* Labels */}
      <text x="28" y="70" textAnchor="middle" fontSize="6" fill="#4d8de0" fontFamily="monospace">Warehouse</text>
      <text x="76" y="70" textAnchor="middle" fontSize="6" fill="#93a4bd" fontFamily="monospace">Edge</text>
      <text x="124" y="70" textAnchor="middle" fontSize="6" fill="#93a4bd" fontFamily="monospace">Browser</text>
      <text x="172" y="70" textAnchor="middle" fontSize="6" fill="#2dd4bf" fontFamily="monospace">Kernel</text>
    </svg>
  )
}

// ── Sidebar nav item ──────────────────────────────────────────────────────

function NavItem({ doc, isActive, onClick }) {
  return (
    <Link
      to={`/docs/${doc.slug}`}
      onClick={onClick}
      className={`
        group relative flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-all duration-150
        ${isActive
          ? 'bg-surface-2 text-brand-teal font-medium shadow-sm'
          : 'text-muted hover:bg-surface-2 hover:text-fg'
        }
      `}
    >
      {/* Brand-gradient accent bar on active item */}
      {isActive && (
        <span className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 rounded-full bg-brand-gradient" />
      )}
      <FileText
        size={14}
        className={`shrink-0 ${isActive ? 'text-brand-teal' : 'text-muted group-hover:text-accent'}`}
      />
      <span className="truncate">{doc.title}</span>
      {isActive && <ChevronRight size={12} className="ml-auto text-brand-teal shrink-0" />}
    </Link>
  )
}

// ── Sidebar ───────────────────────────────────────────────────────────────

function Sidebar({ activeSlug, onNavClick }) {
  return (
    <nav className="flex flex-col gap-6 py-4">
      {DOC_GROUPS.map(group => (
        <div key={group.name}>
          <p className="px-3 mb-2 text-xs font-semibold text-muted uppercase tracking-widest font-display">
            {group.name}
          </p>
          <div className="flex flex-col gap-0.5">
            {group.docs.map(doc => (
              <NavItem
                key={doc.slug}
                doc={doc}
                isActive={doc.slug === activeSlug}
                onClick={onNavClick}
              />
            ))}
          </div>
        </div>
      ))}
    </nav>
  )
}

// ── Docs index / welcome screen ───────────────────────────────────────────

function DocsIndex() {
  const navigate = useNavigate()

  return (
    <div className="max-w-2xl mx-auto py-8">
      {/* Hero illustration */}
      <div className="flex items-center gap-8 mb-10">
        <div className="w-40 h-28 shrink-0">
          <DocsHeroIllustration />
        </div>
        <div>
          <h1 className="text-3xl font-bold font-display text-fg mb-2">Nubi Docs</h1>
          <p className="text-muted leading-relaxed">
            Embedded analytics with Arrow-native data transport, content-hashed edge
            caching, and server-side RLS — at near-zero marginal compute.
          </p>
        </div>
      </div>

      {/* Arrow data flow illustration */}
      <div className="mb-10 p-5 bg-surface-2 rounded-2xl border border-border">
        <p className="text-xs font-semibold text-brand-teal uppercase tracking-widest mb-3 font-display">
          Arrow-native data plane
        </p>
        <div className="h-20">
          <ArrowDataIllustration />
        </div>
        <p className="text-xs text-muted text-center mt-1">
          Warehouse → Edge → Browser → Kernel — zero serialisation tax
        </p>
      </div>

      {/* Quick-start cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {DOC_GROUPS.map(group => (
          <div
            key={group.name}
            className="border border-border rounded-xl p-5 bg-surface hover:border-brand-teal hover:shadow-sm transition-all"
          >
            <p className="text-xs font-semibold text-brand-teal uppercase tracking-wide mb-3 font-display">
              {group.name}
            </p>
            <ul className="space-y-2">
              {group.docs.slice(0, 4).map(doc => (
                <li key={doc.slug}>
                  <button
                    onClick={() => navigate(`/docs/${doc.slug}`)}
                    className="text-sm text-fg hover:text-brand-teal flex items-center gap-1.5 transition-colors text-left w-full"
                  >
                    <ChevronRight size={12} className="text-accent shrink-0" />
                    {doc.title}
                  </button>
                </li>
              ))}
              {group.docs.length > 4 && (
                <li className="text-xs text-muted">
                  +{group.docs.length - 4} more
                </li>
              )}
            </ul>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Main DocsPage ─────────────────────────────────────────────────────────

export default function DocsPage() {
  const { slug } = useParams()
  const [sidebarOpen, setSidebarOpen] = useState(false)

  // Resolve the active doc
  const doc = slug ? getDoc(slug) : null

  // Scroll to top when doc changes
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [slug])

  // Close sidebar on outside click (mobile)
  useEffect(() => {
    if (!sidebarOpen) return
    const handler = (e) => {
      if (!e.target.closest('[data-sidebar]') && !e.target.closest('[data-sidebar-toggle]')) {
        setSidebarOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [sidebarOpen])

  return (
    <div className="min-h-screen bg-bg flex flex-col">
      {/* Top bar */}
      <header className="sticky top-0 z-30 bg-surface/90 backdrop-blur border-b border-border shadow-sm">
        <div className="max-w-7xl mx-auto px-4 h-14 flex items-center gap-4">
          {/* Mobile sidebar toggle */}
          <button
            data-sidebar-toggle
            onClick={() => setSidebarOpen(v => !v)}
            className="lg:hidden p-2 -ml-1 rounded-lg text-muted hover:bg-surface-2 transition-colors"
            aria-label="Toggle navigation"
          >
            {sidebarOpen ? <X size={20} /> : <Menu size={20} />}
          </button>

          <Link to="/docs" className="flex items-center gap-2 text-brand-teal hover:text-accent transition-colors">
            <BookOpen size={18} />
            <span className="font-semibold text-sm font-display">Nubi Docs</span>
          </Link>

          {doc && (
            <>
              <ChevronRight size={14} className="text-border hidden sm:block" />
              <span className="text-sm text-muted truncate hidden sm:block">{doc.title}</span>
            </>
          )}
        </div>
      </header>

      <div className="flex-1 flex max-w-7xl mx-auto w-full relative">
        {/* Mobile sidebar overlay */}
        {sidebarOpen && (
          <div
            className="fixed inset-0 z-20 bg-black/40 lg:hidden"
            onClick={() => setSidebarOpen(false)}
          />
        )}

        {/* Left sidebar */}
        <aside
          data-sidebar
          className={`
            fixed lg:sticky top-14 z-20 h-[calc(100vh-3.5rem)] w-64 shrink-0
            bg-surface border-r border-border overflow-y-auto
            transition-transform duration-200 ease-in-out
            ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
            lg:translate-x-0 lg:relative lg:top-0 lg:h-auto lg:max-h-[calc(100vh-3.5rem)] lg:sticky lg:top-14
            px-3
          `}
        >
          <Sidebar
            activeSlug={slug ?? null}
            onNavClick={() => setSidebarOpen(false)}
          />
        </aside>

        {/* Main content */}
        <main className="flex-1 min-w-0 px-6 lg:px-10 py-8 lg:py-10">
          {doc ? (
            <div className="max-w-3xl">
              <MarkdownRenderer content={doc.content} />
            </div>
          ) : (
            <DocsIndex />
          )}
        </main>
      </div>
    </div>
  )
}
