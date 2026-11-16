/**
 * DocsPage — /docs and /docs/:slug.
 *
 * Swiss reading experience: a grouped sidebar with mono labels and no icons, a
 * table-of-contents rail that follows the section you are reading, code on the
 * ink surface with copy buttons, ruled callouts, previous and next, and a
 * search affordance (⌘K, or "/") that opens the public palette.
 */

import { useState, useEffect, useRef, useMemo } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { Menu, X, ChevronDown, Search, ArrowLeft, ArrowRight } from 'lucide-react'
import { DOC_GROUPS, getDocs, getDoc } from '../docs/registry.js'
import DocsHome from './docs/DocsHome.jsx'
import MarkdownRenderer from '../components/MarkdownRenderer.jsx'
import MarketingStyles from '../components/marketing/MarketingStyles.jsx'
import { openPublicSearch, isMacLike } from '../components/marketing/PublicPalette.jsx'
import { Reveal } from '../components/marketing/kit.jsx'
import './docs/docs.css'

const GITHUB = 'https://github.com/nu-bi/nubi'

/* ── Headings for the TOC ─────────────────────────────────────────────────── */

type Heading = { text: string; id: string; level: number }

function extractHeadings(content: string): Heading[] {
  if (!content) return []
  // Skip fenced code so a "# comment" in a snippet is not a heading.
  const stripped = content.replace(/```[\s\S]*?```/g, '')
  return [...stripped.matchAll(/^(#{2,3})\s+(.+)$/gm)].map(m => {
    const text = m[2].trim().replace(/[`*_]/g, '')
    return { text, level: m[1].length, id: text.toLowerCase().replace(/[^\w\s-]/g, '').trim().replace(/\s+/g, '-') }
  })
}

function getPrevNext(slug: string) {
  const all = getDocs()
  const i = all.findIndex(d => d.slug === slug)
  return { prev: i > 0 ? all[i - 1] : null, next: i >= 0 && i < all.length - 1 ? all[i + 1] : null }
}

/* ── Sidebar ──────────────────────────────────────────────────────────────── */

function NavItem({ doc, activeSlug, onNavigate }: { doc: any; activeSlug: string; onNavigate: () => void }) {
  const kids: any[] = doc.children ?? []
  const childActive = kids.some(c => c.slug === activeSlug)
  const [open, setOpen] = useState(doc.slug === activeSlug || childActive)
  useEffect(() => { if (doc.slug === activeSlug || childActive) setOpen(true) }, [activeSlug, childActive, doc.slug])
  return (
    <div>
      <div className="dx-row">
        <Link to={`/docs/${doc.slug}`} className="dx-item" aria-current={doc.slug === activeSlug ? 'page' : undefined} onClick={onNavigate}>
          <span>{doc.slug === 'home' ? 'Overview' : doc.title}</span>
        </Link>
        {kids.length > 0 && (
          <button type="button" className="dx-tog" aria-expanded={open} aria-label={`${open ? 'Collapse' : 'Expand'} ${doc.title}`} onClick={() => setOpen(v => !v)}>
            <ChevronDown size={14} strokeWidth={1.5} aria-hidden="true" />
          </button>
        )}
      </div>
      {kids.length > 0 && open && (
        <div className="dx-kids">
          {kids.map(c => (
            <Link key={c.slug} to={`/docs/${c.slug}`} className="dx-item" aria-current={c.slug === activeSlug ? 'page' : undefined} onClick={onNavigate}>
              <span>{c.title}</span>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}

function Sidebar({ activeSlug, onNavigate }: { activeSlug: string; onNavigate: () => void }) {
  const [closed, setClosed] = useState<Record<string, boolean>>({})
  const mod = isMacLike() ? '⌘K' : 'Ctrl K'

  // Make sure the group holding the active page is open.
  useEffect(() => {
    for (const g of DOC_GROUPS) {
      if (g.docs.some((d: any) => d.slug === activeSlug || (d.children ?? []).some((c: any) => c.slug === activeSlug))) {
        setClosed(prev => (prev[g.name] ? { ...prev, [g.name]: false } : prev))
      }
    }
  }, [activeSlug])

  return (
    <>
      <Link to="/docs/home" className="dx-brand" onClick={onNavigate} aria-label="Documentation home">Docs</Link>
      <button type="button" className="dx-search" onClick={() => { onNavigate(); openPublicSearch() }} aria-label={`Search the docs (${mod})`}>
        <Search size={14} strokeWidth={1.75} aria-hidden="true" />
        <span>Search the docs</span>
        <kbd className="mk-kbd">{mod}</kbd>
      </button>

      <nav className="dx-nav" aria-label="Documentation">
        {DOC_GROUPS.map((g: any, gi: number) => {
          const prevSection = gi > 0 ? DOC_GROUPS[gi - 1].section : null
          const showSection = g.section && g.section !== prevSection
          const isClosed = !!closed[g.name]
          return (
            <div key={g.name}>
              {showSection && <p className="dx-sec">{g.section}</p>}
              {g.name !== 'Home' && (
                <button type="button" className="dx-grp" aria-expanded={!isClosed} onClick={() => setClosed(p => ({ ...p, [g.name]: !p[g.name] }))}>
                  <span>{g.name}</span>
                  <ChevronDown size={13} strokeWidth={1.5} aria-hidden="true" />
                </button>
              )}
              {(g.name === 'Home' || !isClosed) && g.docs.map((d: any) => (
                <NavItem key={d.slug} doc={d} activeSlug={activeSlug} onNavigate={onNavigate} />
              ))}
            </div>
          )
        })}
      </nav>

      <div className="dx-foot">
        <Link to="/">Back to site</Link>
        <a href={GITHUB} target="_blank" rel="noopener noreferrer">GitHub</a>
      </div>
    </>
  )
}

/* ── Table of contents ────────────────────────────────────────────────────── */

function Toc({ headings }: { headings: Heading[] }) {
  const [active, setActive] = useState('')

  useEffect(() => {
    if (headings.length === 0) return
    const els = headings.map(h => document.getElementById(h.id)).filter(Boolean) as HTMLElement[]
    if (els.length === 0) return
    // The active section is the last heading that has crossed the line under the sticky bars.
    const update = () => {
      let cur = els[0].id
      for (const el of els) { if (el.getBoundingClientRect().top <= 140) cur = el.id; else break }
      setActive(cur)
    }
    update()
    window.addEventListener('scroll', update, { passive: true })
    return () => window.removeEventListener('scroll', update)
  }, [headings])

  if (headings.length < 2) return null
  return (
    <aside className="dx-toc" aria-label="On this page">
      <h2>On this page</h2>
      <ul>
        {headings.map(h => (
          <li key={h.id}>
            <a href={`#${h.id}`} className={h.level === 3 ? 'l3' : undefined} aria-current={active === h.id ? 'location' : undefined}>{h.text}</a>
          </li>
        ))}
      </ul>
      <button type="button" onClick={() => window.scrollTo({ top: 0 })}>Back to top</button>
    </aside>
  )
}

/* ── Pieces ───────────────────────────────────────────────────────────────── */

function Breadcrumb({ doc }: { doc: any }) {
  if (!doc) return null
  return (
    <nav aria-label="Breadcrumb" className="dx-crumb">
      <Link to="/docs/home">Docs</Link>
      {doc.group !== 'Home' && (<><i>/</i><span>{doc.group}</span><i>/</i><b aria-current="page">{doc.title}</b></>)}
    </nav>
  )
}

function Pager({ prev, next }: { prev: any; next: any }) {
  if (!prev && !next) return null
  return (
    <nav className="dx-pager" aria-label="Previous and next page">
      {prev ? (
        <Link to={`/docs/${prev.slug}`} rel="prev"><small><ArrowLeft size={12} strokeWidth={1.75} aria-hidden="true" />Previous</small><b>{prev.title}</b></Link>
      ) : <span style={{ background: 'var(--surface)' }} />}
      {next && (
        <Link to={`/docs/${next.slug}`} rel="next" className="next"><small>Next<ArrowRight size={12} strokeWidth={1.75} aria-hidden="true" /></small><b>{next.title}</b></Link>
      )}
    </nav>
  )
}

function EditLinks({ doc }: { doc: any }) {
  if (!doc?.path) return null
  const issue = `${GITHUB}/issues/new?labels=docs&title=${encodeURIComponent(`[Docs] ${doc.title}`)}`
  return (
    <p className="dx-edit">
      <a href={`${GITHUB}/edit/main${doc.path}`} target="_blank" rel="noopener noreferrer">Edit this page on GitHub</a>
      <a href={issue} target="_blank" rel="noopener noreferrer">Report a problem</a>
    </p>
  )
}

function DocNotFound({ slug }: { slug: string }) {
  return (
    <div className="dx-col">
      <p className="mk-idx"><b>404</b><span>DOCS</span></p>
      <h1 className="mk-h1" style={{ fontSize: 'clamp(36px,5vw,56px)', marginTop: 28 }}>No page called <em>{slug}.</em></h1>
      <p className="mk-lead" style={{ marginTop: 16 }}>It may have moved or been renamed. Search the docs, or start from the home page.</p>
      <div className="mk-ctas" style={{ marginTop: 28 }}>
        <button type="button" className="nubi-btn nubi-btn-primary nubi-btn-lg" onClick={openPublicSearch}>Search the docs</button>
        <Link to="/docs/home" className="nubi-btn nubi-btn-secondary nubi-btn-lg">Docs home</Link>
      </div>
    </div>
  )
}

function DocSkeleton() {
  return (
    <div className="dx-col" aria-hidden="true">
      <div style={{ height: 44, width: '55%', background: 'var(--surface-2)', marginBottom: 28 }} />
      {[100, 94, 88, 70].map((w, i) => <div key={i} style={{ height: 14, width: `${w}%`, background: 'var(--surface-2)', marginBottom: 12 }} />)}
    </div>
  )
}

/* ── Page ─────────────────────────────────────────────────────────────────── */

export default function DocsPage() {
  const { slug } = useParams()
  const navigate = useNavigate()
  const [navOpen, setNavOpen] = useState(false)
  const menuBtn = useRef<HTMLButtonElement>(null)

  const activeSlug = slug ?? 'home'
  const doc = getDoc(activeSlug)
  const isUnknown = !!slug && !doc

  useEffect(() => { if (!slug) navigate('/docs/home', { replace: true }) }, [slug, navigate])

  // New page: back to the top, drawer closed; honour a #hash if there is one.
  useEffect(() => {
    setNavOpen(false)
    const hash = window.location.hash.slice(1)
    if (hash) requestAnimationFrame(() => document.getElementById(hash)?.scrollIntoView())
    else window.scrollTo({ top: 0, left: 0, behavior: 'instant' as ScrollBehavior })
  }, [activeSlug])

  // Esc closes the drawer; "/" opens search (⌘K is bound by the palette itself).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && navOpen) { setNavOpen(false); menuBtn.current?.focus() }
      const t = e.target as HTMLElement | null
      const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)
      if (e.key === '/' && !typing && !e.metaKey && !e.ctrlKey) { e.preventDefault(); openPublicSearch() }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [navOpen])

  // The drawer is a phone-and-tablet pattern: close it when the screen widens.
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 1024px)')
    const on = () => { if (mq.matches) setNavOpen(false) }
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])

  const headings = useMemo(() => (doc ? extractHeadings(doc.content) : []), [doc])
  const { prev, next } = getPrevNext(activeSlug)
  const showToc = !!doc && !isUnknown && activeSlug !== 'home' && headings.length >= 2

  return (
    <div className="mk dx">
      <MarketingStyles />

      <div className="dx-mbar">
        <button
          ref={menuBtn}
          type="button"
          className="dx-contents"
          onClick={() => setNavOpen(v => !v)}
          aria-expanded={navOpen}
          aria-controls="dx-side"
        >
          {navOpen ? <X size={16} strokeWidth={1.75} aria-hidden="true" /> : <Menu size={16} strokeWidth={1.75} aria-hidden="true" />}
          <span>{navOpen ? 'Close' : 'Contents'}</span>
        </button>
        <p className="dx-crumb-m" aria-hidden="true">
          DOCS{doc && doc.group !== 'Home' ? <> / <b>{doc.title}</b></> : null}
        </p>
        <button
          type="button"
          onClick={openPublicSearch}
          aria-label="Search the docs"
          style={{ width: 36, height: 36, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: 'none', border: 0, color: 'var(--text-muted)', cursor: 'pointer' }}
        >
          <Search size={17} strokeWidth={1.75} aria-hidden="true" />
        </button>
      </div>

      {navOpen && <div className="dx-scrim" onClick={() => setNavOpen(false)} aria-hidden="true" />}

      <div className="dx-body">
        <aside id="dx-side" className="dx-side" data-open={navOpen} aria-label="Documentation navigation">
          <Sidebar activeSlug={activeSlug} onNavigate={() => setNavOpen(false)} />
        </aside>

        <div className="dx-main" key={activeSlug}>
          {isUnknown ? (
            <DocNotFound slug={slug!} />
          ) : activeSlug === 'home' ? (
            <DocsHome onOpenSearch={openPublicSearch} />
          ) : doc ? (
            <div className="dx-col docs-prose">
              <Breadcrumb doc={doc} />
              <MarkdownRenderer content={doc.content} allowRawHtml />
              <Reveal><Pager prev={prev} next={next} /></Reveal>
              <EditLinks doc={doc} />
            </div>
          ) : (
            <DocSkeleton />
          )}
        </div>

        {showToc && <Toc headings={headings} />}
      </div>
    </div>
  )
}
