import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import {
  Menu, X, ChevronRight, BookOpen, FileText, Search,
  Home, Layers, Cpu, Wrench, Zap, Database, Code, Bot,
  ArrowLeft, ArrowRight, Hash, ChevronDown, Command,
  ExternalLink, ThumbsUp, ThumbsDown, Copy, Check,
  Cloud, Lock, Settings, Rocket,
} from 'lucide-react'
import { DOC_GROUPS, getDocs, getDoc } from '../docs/registry.js'
import MarkdownRenderer from '../components/MarkdownRenderer.jsx'

// ── Scoped styles ─────────────────────────────────────────────────────────────

const DocsStyles = () => (
  <style>{`
    /* Custom scrollbars */
    .docs-sidebar-scroll {
      scrollbar-width: thin;
      scrollbar-color: var(--border) transparent;
    }
    .docs-sidebar-scroll::-webkit-scrollbar { width: 4px; }
    .docs-sidebar-scroll::-webkit-scrollbar-track { background: transparent; }
    .docs-sidebar-scroll::-webkit-scrollbar-thumb {
      background: var(--border);
      border-radius: 4px;
    }
    .docs-toc-scroll { scrollbar-width: none; }
    .docs-toc-scroll::-webkit-scrollbar { display: none; }

    /* Active nav indicator */
    @keyframes docs-bar-in {
      from { transform: scaleY(0) translateY(4px); opacity: 0; }
      to   { transform: scaleY(1) translateY(0); opacity: 1; }
    }
    .docs-active-bar { animation: docs-bar-in 0.2s cubic-bezier(0.34, 1.56, 0.64, 1); }

    /* Mobile sidebar slide-in */
    @keyframes docs-slide-in {
      from { transform: translateX(-100%); }
      to   { transform: translateX(0); }
    }
    .docs-sidebar-mobile-enter {
      animation: docs-slide-in 0.25s cubic-bezier(0.32, 0.72, 0, 1);
    }

    /* Overlay fade */
    @keyframes docs-fade-in { from { opacity: 0; } to { opacity: 1; } }
    .docs-overlay-enter { animation: docs-fade-in 0.2s ease-out; }

    /* Content fade-up */
    @keyframes docs-content-in {
      from { opacity: 0; transform: translateY(6px); }
      to   { opacity: 1; transform: translateY(0); }
    }
    .docs-content-enter {
      animation: docs-content-in 0.28s cubic-bezier(0.16, 1, 0.3, 1);
    }

    /* Search modal backdrop */
    @keyframes docs-modal-backdrop { from { opacity: 0; } to { opacity: 1; } }
    .docs-search-backdrop { animation: docs-modal-backdrop 0.15s ease-out; }

    /* Search modal card */
    @keyframes docs-modal-card {
      from { opacity: 0; transform: scale(0.97) translateY(-6px); }
      to   { opacity: 1; transform: scale(1) translateY(0); }
    }
    .docs-search-card { animation: docs-modal-card 0.18s cubic-bezier(0.16, 1, 0.3, 1); }

    /* Pager hover lift */
    .docs-pager-btn {
      transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
    }
    .docs-pager-btn:hover {
      transform: translateY(-2px);
      box-shadow: 0 6px 24px rgba(23, 179, 163, 0.10);
      border-color: var(--accent);
    }

    /* Group separator */
    .docs-group + .docs-group { margin-top: 4px; }

    /* Version badge gradient */
    .docs-version-badge {
      background: linear-gradient(135deg, rgba(27,35,99,0.06), rgba(23,179,163,0.08));
    }
    .dark .docs-version-badge {
      background: linear-gradient(135deg, rgba(27,35,99,0.5), rgba(23,179,163,0.2));
    }

    /* Sidebar gradient header */
    .docs-sidebar-header {
      background: linear-gradient(180deg, var(--surface) 0%, var(--surface) 80%, transparent 100%);
    }

    /* Search result active state */
    .docs-search-result-active {
      background: var(--surface-2);
    }

    /* TOC active indicator */
    .docs-toc-active {
      background: linear-gradient(90deg, rgba(23,179,163,0.08), transparent);
    }

    /* Feedback button */
    .docs-feedback-btn {
      transition: background 0.15s, border-color 0.15s, color 0.15s, transform 0.15s;
    }
    .docs-feedback-btn:hover { transform: scale(1.04); }
    .docs-feedback-btn.active-yes {
      background: rgba(23,179,163,0.12);
      border-color: var(--accent);
      color: var(--accent);
    }
    .dark .docs-feedback-btn.active-yes {
      background: rgba(45,212,191,0.15);
    }
    .docs-feedback-btn.active-no {
      background: rgba(239,68,68,0.08);
      border-color: rgba(239,68,68,0.4);
      color: rgb(239,68,68);
    }

    /* Mobile search bar */
    .docs-mobile-search { background: var(--surface-2); }

    /* Heading anchor */
    .docs-heading-anchor {
      opacity: 0;
      transition: opacity 0.12s;
    }
    h2:hover .docs-heading-anchor,
    h3:hover .docs-heading-anchor { opacity: 0.5; }

    /* Code block copy button */
    .docs-copy-btn {
      transition: opacity 0.15s, background 0.15s;
    }

    /* Smooth scroll for the whole page */
    html { scroll-behavior: smooth; }

    /* ── Reading column ── */
    .docs-prose {
      font-size: 15.5px;
      line-height: 1.75;
      color: var(--text);
    }
    .docs-prose > *:first-child { margin-top: 0; }
  `}</style>
)

// ── Group + icon metadata ─────────────────────────────────────────────────────

const GROUP_META = {
  'Home':                  { icon: Home,     color: 'text-brand-teal', label: 'Start here' },
  // Using Nubi
  'Get started':           { icon: Zap,      color: 'text-amber-500',  label: 'New here?' },
  'Work with data':        { icon: Database, color: 'text-brand-blue', label: 'Build' },
  'Automate & build':      { icon: Wrench,   color: 'text-accent',     label: 'Automate' },
  'Your account':          { icon: Settings, color: 'text-muted',      label: 'Manage' },
  // Nubi Cloud
  'Cloud & billing':       { icon: Cloud,    color: 'text-brand-teal', label: 'Managed' },
  // Open-source project
  'Self-host':             { icon: Rocket,   color: 'text-brand-blue', label: 'Run it' },
  'Security & internals':  { icon: Lock,     color: 'text-amber-500',  label: 'Internals' },
  'Build on Nubi':         { icon: Code,     color: 'text-accent',     label: 'Extend' },
}

function docIcon(slug) {
  if (slug === 'home')                    return Home
  if (slug.includes('getting-started'))   return Zap
  if (slug.includes('connector'))         return Layers
  if (slug.includes('queries'))           return Database
  if (slug.includes('cache-key'))         return Code
  if (slug.includes('security'))          return Cpu
  if (slug.includes('conformance'))       return Code
  if (slug.includes('dashboard'))         return BookOpen
  if (slug.includes('embed'))             return Layers
  if (slug.includes('ai'))                return Bot
  if (slug.includes('sdk'))               return Wrench
  if (slug.includes('export'))            return ArrowRight
  if (slug.includes('git'))               return Code
  return FileText
}

// ── Search index ──────────────────────────────────────────────────────────────

function buildIndex() {
  return getDocs().map(doc => ({
    slug: doc.slug,
    title: doc.title,
    group: doc.group,
    content: (doc.content ?? '').replace(/[#*`>\-\[\]]/g, '').replace(/\s+/g, ' ').trim(),
  }))
}

function snippet(content, query, len = 140) {
  const lower = content.toLowerCase()
  const idx   = lower.indexOf(query.toLowerCase())
  if (idx === -1) return content.slice(0, len).trim() + '…'
  const start = Math.max(0, idx - 45)
  const end   = Math.min(content.length, idx + len)
  return (start > 0 ? '…' : '') + content.slice(start, end).trim() + '…'
}

function searchDocs(index, query) {
  if (!query.trim()) return []
  const q = query.toLowerCase()
  const scored = index.map(d => {
    let score = 0
    const titleMatch = d.title.toLowerCase().includes(q)
    const bodyMatch  = d.content.toLowerCase().includes(q)
    if (titleMatch) score += 10
    if (bodyMatch)  score += 1
    return { ...d, score, snippet: snippet(d.content, query) }
  }).filter(d => d.score > 0)
  scored.sort((a, b) => b.score - a.score)
  return scored.slice(0, 8)
}

// ── Extract headings for TOC ──────────────────────────────────────────────────

function extractHeadings(content) {
  if (!content) return []
  const matches = [...content.matchAll(/^(#{1,3})\s+(.+)$/gm)]
  return matches.map(m => {
    const level = m[1].length
    const text  = m[2].trim()
    const id    = text.toLowerCase().replace(/[^\w\s-]/g, '').trim().replace(/\s+/g, '-')
    return { text, id, level }
  }).filter(h => h.level === 2 || h.level === 3)
}

// ── Prev/Next navigation ──────────────────────────────────────────────────────

function getPrevNext(activeSlug) {
  const all = getDocs()
  const idx  = all.findIndex(d => d.slug === activeSlug)
  return {
    prev: idx > 0            ? all[idx - 1] : null,
    next: idx < all.length - 1 ? all[idx + 1] : null,
  }
}

// ── Highlight match in text ───────────────────────────────────────────────────

function Highlight({ text, query }) {
  if (!query?.trim()) return <>{text}</>
  const idx = text.toLowerCase().indexOf(query.toLowerCase())
  if (idx === -1) return <>{text}</>
  return (
    <>
      {text.slice(0, idx)}
      <mark className="bg-accent/15 text-accent rounded px-0.5 not-italic font-semibold">
        {text.slice(idx, idx + query.length)}
      </mark>
      {text.slice(idx + query.length)}
    </>
  )
}

// ── Search Modal (Command-palette style) ──────────────────────────────────────

function SearchModal({ onClose }) {
  const [query, setQuery]       = useState('')
  const [results, setResults]   = useState([])
  const [activeIdx, setActiveIdx] = useState(0)
  const inputRef  = useRef(null)
  const listRef   = useRef(null)
  const navigate  = useNavigate()
  const indexRef  = useRef(null)

  const getIndex = useCallback(() => {
    if (!indexRef.current) indexRef.current = buildIndex()
    return indexRef.current
  }, [])

  useEffect(() => { inputRef.current?.focus() }, [])

  useEffect(() => {
    if (query.trim()) {
      const res = searchDocs(getIndex(), query)
      setResults(res)
      setActiveIdx(0)
    } else {
      setResults([])
      setActiveIdx(0)
    }
  }, [query, getIndex])

  // Scroll active result into view
  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-idx="${activeIdx}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [activeIdx])

  function handleKey(e) {
    if (e.key === 'Escape') { onClose(); return }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIdx(i => Math.min(i + 1, results.length - 1))
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIdx(i => Math.max(i - 1, 0))
    }
    if (e.key === 'Enter' && results[activeIdx]) {
      navigate(`/docs/${results[activeIdx].slug}`)
      onClose()
    }
  }

  // Group label for result
  const allDocs = getDocs()
  const recent  = allDocs.slice(0, 5)

  return (
    <div
      className="docs-search-backdrop fixed inset-0 z-50 flex items-start justify-center pt-[12vh] px-4 pb-8 bg-black/50 backdrop-blur-sm"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="docs-search-card w-full max-w-[600px] bg-surface border border-border rounded-2xl shadow-2xl overflow-hidden"
        role="dialog"
        aria-modal="true"
        aria-label="Search documentation"
      >
        {/* Input */}
        <div className="flex items-center gap-3 px-4 py-3.5 border-b border-border">
          <Search size={17} className="text-muted/60 shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Search documentation…"
            className="flex-1 bg-transparent text-fg text-[15px] placeholder:text-muted/40 outline-none"
            aria-label="Search documentation"
          />
          {query ? (
            <button
              onClick={() => setQuery('')}
              className="shrink-0 text-muted/60 hover:text-fg transition-colors"
              aria-label="Clear"
            >
              <X size={14} />
            </button>
          ) : (
            <kbd className="shrink-0 text-[11px] text-muted/50 font-mono bg-surface-2 border border-border rounded px-1.5 py-0.5">
              esc
            </kbd>
          )}
        </div>

        {/* Results */}
        <div className="max-h-[min(420px,60vh)] overflow-y-auto docs-toc-scroll" ref={listRef}>
          {query.trim() && results.length === 0 && (
            <div className="py-12 text-center">
              <Search size={24} className="mx-auto text-muted/20 mb-3" />
              <p className="text-sm text-muted">
                No results for <span className="text-fg font-medium">"{query}"</span>
              </p>
            </div>
          )}

          {results.length > 0 && (
            <ul className="py-1.5">
              {results.map((r, i) => {
                const Icon = docIcon(r.slug)
                return (
                  <li key={r.slug}>
                    <button
                      data-idx={i}
                      onClick={() => { navigate(`/docs/${r.slug}`); onClose() }}
                      onMouseEnter={() => setActiveIdx(i)}
                      className={`
                        w-full text-left px-4 py-3 flex gap-3 transition-colors
                        ${i === activeIdx ? 'docs-search-result-active' : 'hover:bg-surface-2/50'}
                      `}
                    >
                      <span className={`
                        mt-0.5 w-7 h-7 rounded-lg flex items-center justify-center shrink-0
                        ${i === activeIdx ? 'bg-accent/10' : 'bg-surface-2'}
                      `}>
                        <Icon size={13} className={i === activeIdx ? 'text-accent' : 'text-muted/60'} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 mb-0.5">
                          <span className="text-[10.5px] font-semibold text-muted/60 uppercase tracking-[0.08em] font-display">
                            {r.group}
                          </span>
                          <span className="text-border">·</span>
                          <span className={`text-sm font-medium truncate ${i === activeIdx ? 'text-accent' : 'text-fg'}`}>
                            <Highlight text={r.title} query={query} />
                          </span>
                        </div>
                        <p className="text-xs text-muted leading-relaxed line-clamp-1 opacity-80">
                          {r.snippet}
                        </p>
                      </div>
                      {i === activeIdx && (
                        <span className="shrink-0 self-center text-muted/40">
                          <ChevronRight size={14} />
                        </span>
                      )}
                    </button>
                  </li>
                )
              })}
            </ul>
          )}

          {/* Empty state — show recently visited / all docs */}
          {!query.trim() && (
            <div className="py-2">
              <p className="px-4 pt-2 pb-1.5 text-[10.5px] font-bold text-muted/60 uppercase tracking-[0.1em] font-display">
                All Docs
              </p>
              <ul>
                {allDocs.slice(0, 6).map((d, i) => {
                  const Icon = docIcon(d.slug)
                  return (
                    <li key={d.slug}>
                      <button
                        onClick={() => { navigate(`/docs/${d.slug}`); onClose() }}
                        className="w-full text-left px-4 py-2.5 flex items-center gap-3 hover:bg-surface-2/60 transition-colors group"
                      >
                        <span className="w-7 h-7 rounded-lg bg-surface-2 flex items-center justify-center shrink-0 group-hover:bg-accent/10 transition-colors">
                          <Icon size={13} className="text-muted/50 group-hover:text-accent transition-colors" />
                        </span>
                        <div className="min-w-0">
                          <span className="text-sm text-fg group-hover:text-accent transition-colors truncate block">
                            {d.title}
                          </span>
                          <span className="text-[10.5px] text-muted/60">{d.group}</span>
                        </div>
                      </button>
                    </li>
                  )
                })}
              </ul>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-4 py-2.5 border-t border-border/60 flex items-center gap-4 bg-surface-2/30">
          <div className="flex items-center gap-1.5 text-[11px] text-muted/50">
            <kbd className="font-mono bg-surface border border-border rounded px-1 py-0.5 text-[10px]">↑↓</kbd>
            <span>navigate</span>
          </div>
          <div className="flex items-center gap-1.5 text-[11px] text-muted/50">
            <kbd className="font-mono bg-surface border border-border rounded px-1 py-0.5 text-[10px]">↵</kbd>
            <span>select</span>
          </div>
          <div className="flex items-center gap-1.5 text-[11px] text-muted/50">
            <kbd className="font-mono bg-surface border border-border rounded px-1 py-0.5 text-[10px]">esc</kbd>
            <span>close</span>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── NavItem ───────────────────────────────────────────────────────────────────

function NavItem({ doc, isActive, onClick }) {
  const Icon = docIcon(doc.slug)
  return (
    <Link
      to={`/docs/${doc.slug}`}
      onClick={onClick}
      className={`
        group relative flex items-center gap-2.5 px-3 py-[7px] rounded-lg text-[13px] leading-snug
        transition-all duration-100 select-none outline-none
        focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1
        ${isActive
          ? 'bg-surface-2 text-fg font-medium'
          : 'text-muted hover:bg-surface-2/70 hover:text-fg'
        }
      `}
    >
      {isActive && (
        <span
          className="docs-active-bar absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-[20px] rounded-r-full"
          style={{ background: 'linear-gradient(180deg, #2456a6, #17b3a3)' }}
        />
      )}
      <Icon
        size={13}
        className={`shrink-0 transition-colors duration-100 ${
          isActive ? 'text-accent' : 'text-muted/50 group-hover:text-muted'
        }`}
      />
      <span className="truncate">{doc.title}</span>
      {isActive && (
        <span className="ml-auto shrink-0 w-1.5 h-1.5 rounded-full bg-accent/60" />
      )}
    </Link>
  )
}

// ── Sidebar inner ─────────────────────────────────────────────────────────────

function SidebarContent({ activeSlug, onNavClick, onOpenSearch }) {
  const [openGroups, setOpenGroups] = useState(() => {
    const initial = {}
    DOC_GROUPS.forEach(g => { initial[g.name] = true })
    return initial
  })

  // Auto-expand group that contains active doc
  useEffect(() => {
    DOC_GROUPS.forEach(g => {
      if (g.docs.some(d => d.slug === activeSlug)) {
        setOpenGroups(prev => ({ ...prev, [g.name]: true }))
      }
    })
  }, [activeSlug])

  const toggleGroup = (name) =>
    setOpenGroups(prev => ({ ...prev, [name]: !prev[name] }))

  return (
    <nav className="flex flex-col h-full" aria-label="Documentation navigation">
      {/* Brand header */}
      <div className="px-4 pt-5 pb-3 shrink-0">
        <Link
          to="/docs/home"
          onClick={onNavClick}
          className="flex items-center gap-2 mb-4 group select-none"
        >
          <span
            className="flex items-center justify-center w-7 h-7 rounded-lg shrink-0"
            style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
          >
            <BookOpen size={14} className="text-white" />
          </span>
          <span className="font-display font-semibold text-[13px] tracking-tight text-fg">
            Nubi <span className="text-muted font-normal">Docs</span>
          </span>
          <span className="ml-auto docs-version-badge text-[10px] font-mono text-muted px-1.5 py-0.5 rounded border border-border">
            v1
          </span>
        </Link>

        {/* Search trigger */}
        <button
          onClick={onOpenSearch}
          className="
            w-full flex items-center gap-2.5 px-3 py-2 rounded-lg
            bg-surface-2/80 border border-border/60 text-muted/60
            hover:bg-surface-2 hover:border-border hover:text-muted
            transition-all duration-150 text-[13px] text-left
            focus-visible:ring-2 focus-visible:ring-ring
          "
          aria-label="Open search"
        >
          <Search size={13} className="shrink-0" />
          <span className="flex-1 truncate">Search docs…</span>
          <div className="flex items-center gap-0.5 shrink-0">
            <kbd className="text-[10px] font-mono text-muted/40 bg-surface border border-border rounded px-1 py-0.5 hidden sm:block leading-none">
              ⌘K
            </kbd>
          </div>
        </button>
      </div>

      {/* Nav groups */}
      <div className="flex-1 overflow-y-auto docs-sidebar-scroll px-3 pb-6">
        <div className="space-y-0">
          {DOC_GROUPS.map((group, gi) => {
            const meta = GROUP_META[group.name] ?? { icon: FileText, color: 'text-muted', label: '' }
            const GroupIcon = meta.icon
            const isOpen = openGroups[group.name] ?? true
            // Render a section header the first time a new section appears.
            const prevSection = gi > 0 ? DOC_GROUPS[gi - 1].section : null
            const showSectionHeader = group.section && group.section !== prevSection

            return (
              <div key={group.name} className={`docs-group ${gi > 0 ? 'pt-3 mt-2 border-t border-border/50' : 'pt-1'}`}>
                {showSectionHeader && (
                  <p className="px-2 pt-2 pb-1.5 text-[10px] font-bold uppercase tracking-[0.16em] text-fg/40 font-display">
                    {group.section}
                  </p>
                )}
                <button
                  onClick={() => toggleGroup(group.name)}
                  className="w-full flex items-center gap-2 px-2 py-1.5 mb-0.5 rounded-md hover:bg-surface-2/60 transition-colors group/grp"
                >
                  <GroupIcon size={11} className={`${meta.color} shrink-0`} />
                  <span className="flex-1 text-left text-[10.5px] font-bold text-muted/70 uppercase tracking-[0.1em] font-display">
                    {group.name}
                  </span>
                  <ChevronDown
                    size={11}
                    className={`text-muted/30 transition-transform duration-200 ${isOpen ? 'rotate-0' : '-rotate-90'}`}
                  />
                </button>

                {isOpen && (
                  <div className="space-y-0.5 pl-0.5">
                    {group.docs.map(doc => (
                      <NavItem
                        key={doc.slug}
                        doc={doc}
                        isActive={doc.slug === activeSlug}
                        onClick={onNavClick}
                      />
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* Footer */}
      <div className="shrink-0 px-4 py-3 border-t border-border">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5 text-[11px] text-muted/50">
            <span className="w-1.5 h-1.5 rounded-full bg-accent/50 inline-block shrink-0" />
            <span>Nubi Platform</span>
          </div>
          <Link
            to="/"
            className="text-[11px] text-muted/50 hover:text-muted transition-colors flex items-center gap-1"
          >
            <span>Home</span>
            <ExternalLink size={9} />
          </Link>
        </div>
      </div>
    </nav>
  )
}

// ── Table of Contents ─────────────────────────────────────────────────────────

function TableOfContents({ headings }) {
  const [activeId, setActiveId] = useState('')

  useEffect(() => {
    if (headings.length === 0) return
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter(e => e.isIntersecting)
        if (visible.length > 0) {
          const top = visible.reduce((a, b) =>
            a.boundingClientRect.top < b.boundingClientRect.top ? a : b
          )
          setActiveId(top.target.id)
        }
      },
      { rootMargin: '-56px 0px -55% 0px', threshold: 0 }
    )
    headings.forEach(h => {
      const el = document.getElementById(h.id)
      if (el) observer.observe(el)
    })
    return () => observer.disconnect()
  }, [headings])

  if (headings.length < 2) return null

  return (
    <aside className="hidden xl:flex flex-col w-[228px] shrink-0 sticky top-14 h-[calc(100vh-3.5rem)] py-8 pl-2 pr-4">
      <div className="flex-1 overflow-y-auto docs-toc-scroll">
        <div className="flex items-center gap-1.5 mb-3 px-2">
          <Hash size={10} className="text-muted/40" />
          <p className="text-[10.5px] font-bold text-muted/60 uppercase tracking-[0.12em] font-display">
            On this page
          </p>
        </div>
        <ul className="space-y-0.5">
          {headings.map(h => (
            <li key={h.id}>
              <a
                href={`#${h.id}`}
                className={`
                  group flex items-start py-[5px] px-2 rounded-md text-[12.5px] leading-snug
                  border-l-2 transition-all duration-150 no-underline
                  ${h.level === 3 ? 'ml-3 text-[12px]' : ''}
                  ${activeId === h.id
                    ? 'docs-toc-active border-accent text-fg font-medium'
                    : 'border-transparent text-muted/60 hover:text-fg hover:border-border/80 hover:bg-surface-2/40'
                  }
                `}
              >
                <span className="truncate">{h.text}</span>
              </a>
            </li>
          ))}
        </ul>
      </div>

      {/* Back to top */}
      <button
        onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
        className="mt-4 mx-2 flex items-center gap-1.5 text-[11px] text-muted/40 hover:text-muted transition-colors"
      >
        <span className="rotate-90"><ChevronRight size={11} /></span>
        Back to top
      </button>
    </aside>
  )
}

// ── Breadcrumb ────────────────────────────────────────────────────────────────

function DocBreadcrumb({ doc }) {
  if (!doc) return null
  const meta = GROUP_META[doc.group] ?? { icon: FileText, color: 'text-muted' }
  const GroupIcon = meta.icon
  return (
    <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 mb-7 text-[12.5px] text-muted">
      <Link to="/docs/home" className="flex items-center gap-1 hover:text-fg transition-colors">
        <Home size={11} />
        <span>Docs</span>
      </Link>
      {doc.group !== 'Home' && (
        <>
          <ChevronRight size={10} className="text-border" />
          <span className={`flex items-center gap-1 font-medium ${meta.color} opacity-80`}>
            <GroupIcon size={11} />
            <span>{doc.group}</span>
          </span>
          <ChevronRight size={10} className="text-border" />
          <span className="text-fg/70 truncate max-w-[220px] font-medium">{doc.title}</span>
        </>
      )}
    </nav>
  )
}

// ── Prev/Next Pager ───────────────────────────────────────────────────────────

function DocPager({ prev, next }) {
  if (!prev && !next) return null
  return (
    <div className="mt-14 pt-8 border-t border-border grid grid-cols-1 sm:grid-cols-2 gap-3">
      {prev ? (
        <Link
          to={`/docs/${prev.slug}`}
          className="docs-pager-btn group flex items-center gap-3 px-4 py-4 rounded-xl border border-border bg-surface hover:bg-surface-2/40 text-left"
        >
          <ArrowLeft size={16} className="shrink-0 text-muted group-hover:text-accent transition-colors" />
          <div className="min-w-0">
            <p className="text-[10.5px] text-muted/50 uppercase tracking-wide font-display font-bold mb-0.5">
              Previous
            </p>
            <p className="text-sm font-medium text-fg group-hover:text-accent transition-colors truncate">
              {prev.title}
            </p>
          </div>
        </Link>
      ) : <div />}
      {next && (
        <Link
          to={`/docs/${next.slug}`}
          className="docs-pager-btn group flex items-center gap-3 px-4 py-4 rounded-xl border border-border bg-surface hover:bg-surface-2/40 text-right justify-end"
        >
          <div className="min-w-0">
            <p className="text-[10.5px] text-muted/50 uppercase tracking-wide font-display font-bold mb-0.5">
              Next
            </p>
            <p className="text-sm font-medium text-fg group-hover:text-accent transition-colors truncate">
              {next.title}
            </p>
          </div>
          <ArrowRight size={16} className="shrink-0 text-muted group-hover:text-accent transition-colors" />
        </Link>
      )}
    </div>
  )
}

// ── Not Found ─────────────────────────────────────────────────────────────────

function DocNotFound({ slug }) {
  return (
    <div className="max-w-lg py-20 text-center mx-auto">
      <div
        className="w-16 h-16 rounded-2xl mx-auto mb-6 flex items-center justify-center border border-border"
        style={{ background: 'linear-gradient(135deg, rgba(27,35,99,0.06), rgba(23,179,163,0.08))' }}
      >
        <FileText size={26} className="text-muted/40" />
      </div>
      <h1 className="font-display text-2xl font-bold text-fg mb-3">Page not found</h1>
      <p className="text-muted text-sm mb-8 leading-relaxed">
        No documentation page exists for{' '}
        <code className="px-1.5 py-0.5 bg-surface-2 border border-border rounded font-mono text-brand-teal text-[0.875em]">
          {slug}
        </code>.
        It may have moved or been renamed.
      </p>
      <div className="flex items-center justify-center gap-3">
        <Link
          to="/docs/home"
          className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-primary text-white text-sm font-medium hover:opacity-90 transition-opacity"
        >
          <Home size={14} />
          Docs Home
        </Link>
        <Link
          to="/"
          className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl border border-border text-muted text-sm font-medium hover:bg-surface-2 hover:text-fg transition-colors"
        >
          Back to site
        </Link>
      </div>
    </div>
  )
}

// ── Loading skeleton ──────────────────────────────────────────────────────────

function DocSkeleton() {
  return (
    <div className="max-w-[740px] animate-pulse">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 mb-7">
        <div className="h-3 bg-surface-2 rounded w-8" />
        <div className="h-3 bg-surface-2 rounded w-2" />
        <div className="h-3 bg-surface-2 rounded w-20" />
        <div className="h-3 bg-surface-2 rounded w-2" />
        <div className="h-3 bg-surface-2 rounded w-32" />
      </div>
      {/* Title */}
      <div className="h-9 bg-surface-2 rounded-lg mb-4 w-3/5" />
      <div className="h-3 bg-surface-2 rounded mb-8 w-1/4" />
      {/* Body */}
      <div className="space-y-3">
        {[100, 95, 88, 80, 72].map((w, i) => (
          <div key={i} className="h-3.5 bg-surface-2 rounded" style={{ width: `${w}%` }} />
        ))}
      </div>
      {/* Section */}
      <div className="mt-10">
        <div className="h-6 bg-surface-2 rounded w-2/5 mb-5" />
        <div className="space-y-3">
          {[92, 85, 78, 60].map((w, i) => (
            <div key={i} className="h-3.5 bg-surface-2 rounded" style={{ width: `${w}%` }} />
          ))}
        </div>
      </div>
      {/* Code block */}
      <div className="mt-8 h-32 bg-surface-2 rounded-xl border border-border" />
    </div>
  )
}

// ── Feedback footer ───────────────────────────────────────────────────────────

function DocFeedback() {
  const [vote, setVote] = useState(null) // 'yes' | 'no' | null
  return (
    <div className="mt-12 pt-6 border-t border-border/50">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <p className="text-[11.5px] text-muted/50">
          Last reviewed by the Nubi team
        </p>
        {vote ? (
          <div className="flex items-center gap-2 text-[12px] text-muted/60">
            <span className="text-accent">✓</span>
            <span>Thanks for the feedback!</span>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <span className="text-[12px] text-muted/60">Was this page helpful?</span>
            <button
              onClick={() => setVote('yes')}
              className="docs-feedback-btn flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border text-[12px] text-muted/70"
              aria-label="Yes, helpful"
            >
              <ThumbsUp size={12} />
              <span>Yes</span>
            </button>
            <button
              onClick={() => setVote('no')}
              className="docs-feedback-btn flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border text-[12px] text-muted/70"
              aria-label="No, not helpful"
            >
              <ThumbsDown size={12} />
              <span>No</span>
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Mobile top bar ────────────────────────────────────────────────────────────

function MobileBar({ doc, sidebarOpen, onToggle, onOpenSearch }) {
  return (
    <div className="lg:hidden sticky top-14 z-20 bg-surface/95 backdrop-blur-sm border-b border-border px-3 h-12 flex items-center gap-2">
      {/* Menu toggle */}
      <button
        data-sidebar-toggle
        onClick={onToggle}
        className="flex items-center justify-center w-9 h-9 rounded-lg text-muted hover:bg-surface-2 hover:text-fg transition-colors shrink-0"
        aria-label="Toggle navigation"
        aria-expanded={sidebarOpen}
      >
        {sidebarOpen ? <X size={17} /> : <Menu size={17} />}
      </button>

      {/* Title breadcrumb */}
      {doc && (
        <div className="flex items-center gap-1.5 text-[12px] text-muted min-w-0 flex-1">
          <ChevronRight size={11} className="text-border shrink-0" />
          <span className="truncate text-fg/80 font-medium">{doc.title}</span>
        </div>
      )}

      {/* Search button */}
      <button
        onClick={onOpenSearch}
        className="shrink-0 flex items-center justify-center w-9 h-9 rounded-lg text-muted/60 hover:bg-surface-2 hover:text-muted transition-colors"
        aria-label="Search docs"
      >
        <Search size={16} />
      </button>
    </div>
  )
}

// ── Main DocsPage ─────────────────────────────────────────────────────────────

export default function DocsPage() {
  const { slug }   = useParams()
  const navigate   = useNavigate()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [searchOpen, setSearchOpen]   = useState(false)
  const contentKey = useRef(0)

  const activeSlug = slug ?? 'home'
  const doc        = getDoc(activeSlug)
  const isUnknown  = slug && !doc

  // Redirect bare /docs to home
  useEffect(() => {
    if (!slug) navigate('/docs/home', { replace: true })
  }, [slug, navigate])

  // Scroll to top when doc changes
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' })
    contentKey.current++
  }, [activeSlug])

  // Close sidebar on outside click
  useEffect(() => {
    if (!sidebarOpen) return
    const handler = (e) => {
      if (
        !e.target.closest('[data-sidebar]') &&
        !e.target.closest('[data-sidebar-toggle]')
      ) setSidebarOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [sidebarOpen])

  // Global keyboard shortcuts
  useEffect(() => {
    const handler = (e) => {
      // ⌘K / Ctrl+K → open search
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setSearchOpen(true)
      }
      // / → open search (when not in input)
      if (
        e.key === '/' &&
        document.activeElement?.tagName !== 'INPUT' &&
        document.activeElement?.tagName !== 'TEXTAREA'
      ) {
        e.preventDefault()
        setSearchOpen(true)
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [])

  const headings       = doc ? extractHeadings(doc.content) : []
  const { prev, next } = getPrevNext(activeSlug)

  function handleNavClick() {
    setSidebarOpen(false)
  }

  const hasRightRail = doc && headings.length >= 2 && !isUnknown

  return (
    <>
      <DocsStyles />

      {/* Global search modal */}
      {searchOpen && (
        <SearchModal onClose={() => setSearchOpen(false)} />
      )}

      <div className="min-h-[calc(100vh-3.5rem)] bg-bg flex flex-col">

        {/* Mobile bar */}
        <MobileBar
          doc={doc}
          sidebarOpen={sidebarOpen}
          onToggle={() => setSidebarOpen(v => !v)}
          onOpenSearch={() => setSearchOpen(true)}
        />

        {/* Body */}
        <div className="flex flex-1 max-w-[1520px] mx-auto w-full">

          {/* Mobile overlay — starts below navbar+mobilebar so the top bar stays tappable */}
          {sidebarOpen && (
            <div
              className="docs-overlay-enter fixed inset-x-0 bottom-0 z-10 bg-black/40 lg:hidden backdrop-blur-[2px]"
              style={{ top: 'calc(3.5rem + 3rem)' }}
              onClick={() => setSidebarOpen(false)}
            />
          )}

          {/* Left sidebar */}
          <aside
            data-sidebar
            className={`
              fixed lg:sticky lg:top-14 z-20 top-0
              h-screen lg:h-[calc(100vh-3.5rem)]
              w-[272px] shrink-0
              bg-surface border-r border-border
              flex flex-col
              transition-transform duration-[240ms] ease-[cubic-bezier(0.32,0.72,0,1)]
              ${sidebarOpen ? 'translate-x-0 docs-sidebar-mobile-enter' : '-translate-x-full'}
              lg:translate-x-0
            `}
          >
            <SidebarContent
              activeSlug={activeSlug}
              onNavClick={handleNavClick}
              onOpenSearch={() => { setSidebarOpen(false); setSearchOpen(true) }}
            />
          </aside>

          {/* Main reading area */}
          <main className="flex-1 min-w-0 flex" role="main">
            <div className="flex-1 min-w-0">
              {/* Doc content */}
              <div
                key={activeSlug}
                className="px-6 sm:px-8 lg:px-12 xl:px-16 py-10 lg:py-12 docs-content-enter"
              >
                {isUnknown ? (
                  <DocNotFound slug={slug} />
                ) : doc ? (
                  <div className="max-w-[760px] docs-prose">
                    <DocBreadcrumb doc={doc} />
                    <MarkdownRenderer content={doc.content} />
                    <DocPager prev={prev} next={next} />
                    <DocFeedback />
                  </div>
                ) : (
                  <DocSkeleton />
                )}
              </div>
            </div>

            {/* Right rail TOC */}
            {hasRightRail && (
              <TableOfContents headings={headings} />
            )}
          </main>
        </div>
      </div>
    </>
  )
}
