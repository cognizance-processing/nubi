/**
 * PublicPalette — ⌘K / Ctrl K for the public site.
 *
 * Jump to a page, an account action, or search the documentation. The docs
 * index is loaded the first time the palette opens, so marketing pages never
 * pay for the markdown. Opened from anywhere with `openPublicSearch()`.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { Search, CornerDownLeft } from 'lucide-react'
import { useTheme } from '../../contexts/ThemeContext.jsx'

const OPEN_EVENT = 'nubi:public-search'

export function openPublicSearch() {
  window.dispatchEvent(new CustomEvent(OPEN_EVENT))
}

export const isMacLike = () => typeof navigator !== 'undefined' && /Mac|iPhone|iPad/i.test(navigator.platform || navigator.userAgent)

type Item = { id: string; group: string; label: string; hint?: string; snippet?: string; run: () => void }
type DocRow = { slug: string; title: string; group: string; content: string }

const STYLE = `
.mk-pal-back { position: fixed; inset: 0; z-index: 90; background: color-mix(in srgb, var(--ink) 45%, transparent); display: flex; justify-content: center; align-items: flex-start; padding: 12vh 16px 16px; animation: mk-pal-fade 120ms ease-out; }
.mk-pal { width: min(640px, 100%); max-height: min(560px, 76vh); display: flex; flex-direction: column; background: var(--surface); color: var(--text); border: 1px solid var(--ink); border-radius: 4px; box-shadow: var(--shadow-pop, var(--shadow-lg)); overflow: hidden; }
.mk-pal-in { display: flex; align-items: center; gap: 10px; padding: 0 14px; height: 52px; border-bottom: 1px solid var(--border); color: var(--text-muted); }
.mk-pal-in input { flex: 1; min-width: 0; height: 100%; background: transparent; border: 0; outline: none; font: 400 16px var(--font-ui); color: var(--text); }
.mk-pal-in input::placeholder { color: var(--text-muted); }
.mk-pal-list { list-style: none; margin: 0; padding: 6px 0 8px; overflow-y: auto; }
.mk-pal-grp { padding: 10px 14px 4px; font: 500 11px/1 var(--font-mono); letter-spacing: .08em; text-transform: uppercase; color: var(--text-muted); }
.mk-pal-item { display: flex; align-items: baseline; gap: 12px; padding: 8px 14px; cursor: pointer; border-left: 2px solid transparent; }
.mk-pal-item[aria-selected="true"] { background: var(--surface-2); border-left-color: var(--ink); }
.mk-pal-l { font-size: 14px; font-weight: 500; flex: none; max-width: 60%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mk-pal-s { font-size: 13px; color: var(--text-muted); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mk-pal-h { font: 11px/1 var(--font-mono); color: var(--text-muted); flex: none; margin-left: auto; padding-left: 8px; }
.mk-pal-item mark { background: none; color: var(--accent); font-weight: 600; }
.mk-pal-foot { display: flex; gap: 16px; padding: 9px 14px; border-top: 1px solid var(--border); font: 11px/1 var(--font-mono); color: var(--text-muted); }
.mk-pal-foot kbd, .mk-kbd { font: 11px/1 var(--font-mono); padding: 2px 5px; border: 1px solid var(--border); border-radius: 2px; background: var(--surface); color: var(--text-muted); }
.mk-pal-empty { padding: 28px 14px; text-align: center; font-size: 14px; color: var(--text-muted); }
@keyframes mk-pal-fade { from { opacity: 0 } to { opacity: 1 } }
@media (prefers-reduced-motion: reduce) { .mk-pal-back { animation: none; } }
@media (max-width: 639px) { .mk-pal-foot { display: none; } .mk-pal-s { display: none; } .mk-pal-back { padding-top: 8vh; } }
`

function norm(s: string) {
  return s.replace(/[#*`>[\]|]/g, ' ').replace(/\s+/g, ' ').trim()
}

function snippet(text: string, q: string) {
  const i = text.toLowerCase().indexOf(q.toLowerCase())
  if (i < 0) return text.slice(0, 120)
  const start = Math.max(0, i - 40)
  return (start > 0 ? '…' : '') + text.slice(start, i + 90)
}

function Mark({ text, q }: { text: string; q: string }) {
  const i = q ? text.toLowerCase().indexOf(q.toLowerCase()) : -1
  if (i < 0) return <>{text}</>
  return <>{text.slice(0, i)}<mark>{text.slice(i, i + q.length)}</mark>{text.slice(i + q.length)}</>
}

export default function PublicPalette() {
  const navigate = useNavigate()
  const { theme, toggleTheme } = useTheme()
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)
  const [docs, setDocs] = useState<DocRow[] | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const lastFocus = useRef<Element | null>(null)

  const show = useCallback(() => {
    lastFocus.current = document.activeElement
    setQ(''); setSel(0); setOpen(true)
  }, [])
  const hide = useCallback(() => {
    setOpen(false)
    const el = lastFocus.current as HTMLElement | null
    if (el && typeof el.focus === 'function') el.focus()
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen(o => { if (!o) { lastFocus.current = document.activeElement; setQ(''); setSel(0) } return !o })
      }
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener(OPEN_EVENT, show)
    return () => { window.removeEventListener('keydown', onKey); window.removeEventListener(OPEN_EVENT, show) }
  }, [show])

  useEffect(() => {
    if (!open) return
    requestAnimationFrame(() => inputRef.current?.focus())
    if (docs === null) {
      import('../../docs/registry.js')
        .then(m => setDocs((m.getDocs() as DocRow[]).map(d => ({ slug: d.slug, title: d.title, group: d.group, content: norm((d.content ?? '').replace(/^#\s+.*\n/, '')) }))))
        .catch(() => setDocs([]))
    }
  }, [open, docs])

  const go = useCallback((to: string) => () => {
    if (to.includes('#') && !to.startsWith('/docs')) {
      const [path, hash] = to.split('#')
      navigate(path || '/')
      setTimeout(() => document.getElementById(hash)?.scrollIntoView({ behavior: 'smooth' }), 80)
    } else navigate(to)
  }, [navigate])

  const base: Item[] = useMemo(() => [
    { id: 'p-product', group: 'Go to', label: 'Product', hint: '/', run: go('/') },
    { id: 'p-live', group: 'Go to', label: 'Live demo', hint: '/#live', run: go('/#live') },
    { id: 'p-docs', group: 'Go to', label: 'Docs', hint: '/docs', run: go('/docs') },
    { id: 'p-pricing', group: 'Go to', label: 'Pricing', hint: '/pricing', run: go('/pricing') },
    { id: 'p-compare', group: 'Go to', label: 'Compare', hint: '/compare', run: go('/compare') },
    { id: 'p-reporting', group: 'Go to', label: 'Reporting', hint: '/reporting', run: go('/reporting') },
    { id: 'a-start', group: 'Account', label: 'Start free', hint: '/register', run: go('/register') },
    { id: 'a-signin', group: 'Account', label: 'Sign in', hint: '/login', run: go('/login') },
    { id: 'a-theme', group: 'Actions', label: theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode', run: toggleTheme },
    { id: 'a-gh', group: 'Actions', label: 'Open Nubi on GitHub', hint: 'github.com', run: () => window.open('https://github.com/nu-bi/nubi', '_blank', 'noopener') },
  ], [go, theme, toggleTheme])

  const term = q.trim()
  const items: Item[] = useMemo(() => {
    const docItems = (rows: (DocRow & { s?: string })[]): Item[] => rows.map(d => ({
      id: 'd-' + d.slug, group: 'Docs', label: d.title, hint: d.group, snippet: d.s, run: go('/docs/' + d.slug),
    }))
    if (!term) {
      const featured = ['quickstart', 'embedding', 'ai-and-mcp', 'self-host', 'api-reference']
      const rows = featured.map(s => docs?.find(d => d.slug === s)).filter(Boolean) as DocRow[]
      return [...base.filter(i => i.group !== 'Actions'), ...docItems(rows), ...base.filter(i => i.group === 'Actions')]
    }
    const t = term.toLowerCase()
    const pages = base.filter(i => (i.label + ' ' + i.group).toLowerCase().includes(t))
    const hits = (docs ?? [])
      .map(d => {
        const inTitle = d.title.toLowerCase().includes(t)
        const inBody = d.content.toLowerCase().includes(t)
        return { d, score: (inTitle ? 10 : 0) + (inBody ? 1 : 0) }
      })
      .filter(x => x.score > 0)
      .sort((a, b) => b.score - a.score || a.d.title.length - b.d.title.length)
      .slice(0, 8)
      .map(x => ({ ...x.d, s: snippet(x.d.content, term) }))
    return [...pages, ...docItems(hits)]
  }, [term, base, docs, go])

  useEffect(() => { setSel(0) }, [term])
  useEffect(() => { listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' }) }, [sel])

  if (!open) return null

  const choose = (it?: Item) => { if (!it) return; setOpen(false); it.run() }
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); hide() }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setSel(s => Math.min(s + 1, items.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setSel(s => Math.max(s - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); choose(items[sel]) }
    else if (e.key === 'Tab') e.preventDefault()
  }

  let lastGroup = ''
  return createPortal(
    <div className="mk-pal-back" onMouseDown={e => { if (e.target === e.currentTarget) hide() }}>
      <style href="nubi-public-palette" precedence="default">{STYLE}</style>
      <div className="mk-pal" role="dialog" aria-modal="true" aria-label="Search Nubi">
        <div className="mk-pal-in">
          <Search size={16} strokeWidth={1.75} aria-hidden="true" />
          <input
            ref={inputRef}
            value={q}
            onChange={e => setQ(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Search docs or jump to a page"
            role="combobox"
            aria-expanded="true"
            aria-controls="mk-pal-list"
            aria-activedescendant={items[sel] ? `mk-pal-${items[sel].id}` : undefined}
            aria-label="Search docs or jump to a page"
            autoComplete="off"
            spellCheck={false}
          />
          <kbd className="mk-kbd">esc</kbd>
        </div>
        <ul id="mk-pal-list" ref={listRef} className="mk-pal-list" role="listbox" aria-label="Results">
          {items.map((it, i) => {
            const head = it.group !== lastGroup
            lastGroup = it.group
            return (
              <li key={it.id} role="presentation">
                {head && <div className="mk-pal-grp" aria-hidden="true">{it.group}</div>}
                <div
                  id={`mk-pal-${it.id}`}
                  role="option"
                  aria-selected={i === sel}
                  className="mk-pal-item"
                  onMouseMove={() => setSel(i)}
                  onClick={() => choose(it)}
                >
                  <span className="mk-pal-l"><Mark text={it.label} q={term} /></span>
                  {it.snippet && <span className="mk-pal-s"><Mark text={it.snippet} q={term} /></span>}
                  {it.hint && <span className="mk-pal-h">{it.hint}</span>}
                </div>
              </li>
            )
          })}
        </ul>
        {items.length === 0 && <div className="mk-pal-empty">{docs === null ? 'Loading the docs index…' : <>Nothing matches <b>{term}</b>. Try a shorter word.</>}</div>}
        <div aria-live="polite" className="mk-sr">{items.length} {items.length === 1 ? 'result' : 'results'}</div>
        <div className="mk-pal-foot" aria-hidden="true">
          <span><kbd>↑</kbd> <kbd>↓</kbd> move</span>
          <span><kbd><CornerDownLeft size={10} style={{ display: 'inline' }} /></kbd> open</span>
          <span>esc close</span>
        </div>
      </div>
    </div>,
    document.body,
  )
}
