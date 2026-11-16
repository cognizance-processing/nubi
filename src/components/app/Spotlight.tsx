/**
 * Spotlight — the app-wide ⌘K / Ctrl K palette.
 *
 * One search box for everything in the workspace: recents, actions, pages, settings and the
 * active project's dashboards, queries and connectors (fetched the first time the palette opens,
 * then cached for the session).
 *
 * Craft:
 *   - Recents first (ids in localStorage), then Actions, Go to, Dashboards. Searching ranks across
 *     everything; recents get a small boost.
 *   - Scope prefixes: `d ` dashboards, `q ` queries, `c ` connectors, `>` actions. The prefix
 *     becomes a chip in the input; Backspace on an empty box removes it.
 *   - Right-aligned kbd column (g-chords, `[`, `?`); the whole footer and kbd column hide <640px.
 *   - `?` in an empty box opens the shortcut sheet.
 *   - No results: EmptySearch art + "Ask AI" (hands the query to the chat composer).
 *   - ARIA combobox: aria-activedescendant on the input, listbox + grouped options, and a polite
 *     live region announcing the result count.
 *   - Opens with a 120ms fade only; --shadow-pop instead of a hard offset.
 *
 * Opened by ⌘K / Ctrl K anywhere inside the shell, or `openSpotlight()` (a window event, so no
 * provider plumbing): the sidebar field, the Home search bar and the mobile search icon use it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Search, Plug, FileCode2, LayoutDashboard, Settings, Sparkles, SunMoon, PanelLeft, Plus,
  CornerDownLeft, Building2, BookOpen, Keyboard, Clock, X,
} from 'lucide-react'
import * as api from '../../lib/api.js'
import { useUi } from '../../contexts/UiContext.jsx'
import { useTheme } from '../../contexts/ThemeContext.jsx'
import { useOrg } from '../../contexts/OrgContext.jsx'
import {
  parseSpotlightQuery, pushRecent, scoreText, resultCountLabel, markKeyboardNav,
  type SpotlightScope,
} from '../../shell/shellLogic.js'
import { ALL_PAGES } from '../../shell/nav.js'
import { openShortcuts } from '../../shell/ShortcutSheet.jsx'
import Button from '../ui/Button.jsx'
import Tag from '../ui/Tag.jsx'
import { EmptySearch } from '../illustrations/EmptyFigures.jsx'
import '../../shell/shell.css'

const OPEN_EVENT = 'nubi:spotlight'
const RECENTS_KEY = 'nubi:spotlight:recents'

/** Open the palette from anywhere (sidebar field, Home search bar, …). */
export function openSpotlight(query = '') {
  window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: { query } }))
}

type Item = {
  id: string
  group: string
  label: string
  /** Mono meta shown before the kbd column ("Dashboard", "postgres"…). */
  hint?: string
  Icon: any
  /** Return 'keep' to leave the palette open (e.g. drilling into "Switch workspace"). */
  run: () => void | 'keep'
  keywords?: string
  /** Shortcut keys for the right-aligned column. */
  kbd?: string[]
  /** Recents are recorded for stable destinations only. */
  recent?: boolean
}

const SETTINGS: [string, string][] = [
  ['Profile', 'profile'], ['Organization', 'organization'], ['Members', 'members'], ['Integrations', 'integrations'],
  ['AI providers', 'ai-providers'], ['Bridges', 'bridges'], ['Usage', 'usage'], ['Project', 'project'],
  ['Security', 'security'], ['MCP', 'mcp'], ['Connections', 'connections'], ['Access grants', 'access-grants'],
]

const SCOPE_LABEL: Record<SpotlightScope, string> = {
  all: '', dashboards: 'Dashboards', queries: 'Queries', connectors: 'Connectors', actions: 'Actions',
}

function readRecents(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(RECENTS_KEY) || '[]')
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
  } catch { return [] }
}
function writeRecents(ids: string[]) {
  try { localStorage.setItem(RECENTS_KEY, JSON.stringify(ids)) } catch { /* private mode */ }
}

const listFrom = (d: any, key: string): any[] => (Array.isArray(d) ? d : Array.isArray(d?.[key]) ? d[key] : [])
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

function Highlight({ text, terms }: { text: string; terms: string[] }) {
  if (!terms.length) return <>{text}</>
  const re = new RegExp(`(${terms.map(escapeRe).join('|')})`, 'ig')
  return (
    <>
      {text.split(re).map((part, i) =>
        i % 2 === 1 ? <mark key={i}>{part}</mark> : <span key={i}>{part}</span>,
      )}
    </>
  )
}

export default function Spotlight() {
  const navigate = useNavigate()
  const { openChat, toggleSidebar, setChatDraft } = useUi()
  const { theme, toggleTheme } = useTheme()
  const { orgs, activeOrg, setActiveOrg } = useOrg()

  const [open, setOpen] = useState(false)
  const [scope, setScope] = useState<SpotlightScope>('all')
  const [term, setTerm] = useState('')
  const [sel, setSel] = useState(0)
  const [boards, setBoards] = useState<any[] | null>(null)
  const [queries, setQueries] = useState<any[] | null>(null)
  const [connectors, setConnectors] = useState<any[] | null>(null)
  const [recents, setRecents] = useState<string[]>(readRecents)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const lastFocus = useRef<Element | null>(null)
  const openRef = useRef(false)
  openRef.current = open

  const show = useCallback((raw = '') => {
    lastFocus.current = document.activeElement
    const p = parseSpotlightQuery(raw)
    setScope(p.scope); setTerm(p.term); setSel(0); setRecents(readRecents()); setOpen(true)
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
        if (openRef.current) hide(); else show('')
      }
    }
    const onOpen = (e: Event) => show((e as CustomEvent).detail?.query || '')
    window.addEventListener('keydown', onKey)
    window.addEventListener(OPEN_EVENT, onOpen)
    return () => { window.removeEventListener('keydown', onKey); window.removeEventListener(OPEN_EVENT, onOpen) }
  }, [show, hide])

  // Workspace content loads the first time the palette opens.
  useEffect(() => {
    if (!open) return
    requestAnimationFrame(() => inputRef.current?.focus())
    if (boards === null) {
      api.get('/boards').then((d: any) => setBoards(listFrom(d, 'boards'))).catch(() => setBoards([]))
    }
    if (queries === null) api.listRegisteredQueries().then((d: any) => setQueries(Array.isArray(d) ? d : [])).catch(() => setQueries([]))
    if (connectors === null) api.get('/connectors').then((d: any) => setConnectors(listFrom(d, 'connectors'))).catch(() => setConnectors([]))
  }, [open, boards, queries, connectors])

  const go = useCallback((to: string) => () => { navigate(to) }, [navigate])

  const all: Item[] = useMemo(() => {
    const items: Item[] = [
      { id: 'a-new-board', group: 'Actions', label: 'New dashboard', Icon: Plus, run: go('/editor'), keywords: 'create board', recent: true },
      { id: 'a-new-query', group: 'Actions', label: 'New query', Icon: FileCode2, run: go('/queries'), keywords: 'sql write create', recent: true },
      { id: 'a-connect', group: 'Actions', label: 'Connect a data source', Icon: Plug, run: go('/connectors'), keywords: 'add connector database warehouse', recent: true },
      { id: 'a-ai', group: 'Actions', label: 'Ask AI', Icon: Sparkles, run: () => { openChat() }, keywords: 'chat agent assistant' },
      { id: 'a-theme', group: 'Actions', label: 'Toggle theme', Icon: SunMoon, run: toggleTheme, hint: theme === 'dark' ? 'Dark' : 'Light', keywords: 'light dark mode appearance' },
      { id: 'a-docs', group: 'Actions', label: 'Open docs', Icon: BookOpen, run: go('/docs'), keywords: 'documentation help', recent: true },
      { id: 'a-keys', group: 'Actions', label: 'Keyboard shortcuts', Icon: Keyboard, run: () => { openShortcuts() }, kbd: ['?'], keywords: 'keys help cheatsheet' },
      { id: 'a-side', group: 'Actions', label: 'Collapse or expand the sidebar', Icon: PanelLeft, run: toggleSidebar, kbd: ['['], keywords: 'nav' },
    ]
    const others = (orgs || []).filter(o => o.id !== activeOrg?.id)
    if (others.length) {
      items.splice(5, 0, {
        id: 'a-switch', group: 'Actions', label: 'Switch workspace', Icon: Building2, hint: activeOrg?.name,
        keywords: 'org organization account', run: () => { setScope('actions'); setTerm('switch '); setSel(0); return 'keep' },
      })
      for (const o of others) {
        items.push({ id: 'o-' + o.id, group: 'Actions', label: `Switch workspace to ${o.name}`, Icon: Building2, hint: 'Workspace', keywords: 'switch org organization', run: () => { setActiveOrg(o.id) } })
      }
    }
    for (const p of ALL_PAGES) {
      items.push({
        id: 'p-' + p.to, group: 'Go to', label: p.label, Icon: p.Icon, run: go(p.to), recent: true,
        kbd: p.chord ? ['g', p.chord] : undefined,
      })
    }
    for (const [label, slug] of SETTINGS) {
      items.push({ id: 's-' + slug, group: 'Settings', label, Icon: Settings, run: go('/settings/' + slug), hint: 'Settings', keywords: 'settings', recent: true })
    }
    for (const b of boards || []) items.push({ id: 'b-' + b.id, group: 'Dashboards', label: b.name || 'Untitled dashboard', Icon: LayoutDashboard, run: go(`/d/${b.id}`), hint: 'Dashboard', recent: true })
    for (const r of queries || []) items.push({ id: 'q-' + r.id, group: 'Queries', label: r.name || r.id, Icon: FileCode2, run: go(`/queries/${encodeURIComponent(r.id)}`), hint: 'Query', keywords: String(r.id), recent: true })
    for (const c of connectors || []) items.push({ id: 'c-' + c.id, group: 'Connectors', label: c.name || c.id, Icon: Plug, run: go('/connectors'), hint: c.type || c.kind || 'Connector', keywords: `${c.type || ''} ${c.kind || ''}`, recent: true })
    return items
  }, [boards, queries, connectors, orgs, activeOrg, theme, go, openChat, toggleTheme, toggleSidebar, setActiveOrg])

  const byId = useMemo(() => new Map(all.map(i => [i.id, i])), [all])
  const terms = useMemo(() => term.trim().toLowerCase().split(/\s+/).filter(Boolean), [term])

  const grouped = useMemo(() => {
    const recentItems = recents.map(id => byId.get(id)).filter((x): x is Item => Boolean(x))
    const recentSet = new Set(recentItems.map(i => i.id))
    let list: [string, Item[]][] = []

    const inScope = (i: Item) =>
      scope === 'all' ? true
      : scope === 'dashboards' ? i.group === 'Dashboards'
      : scope === 'queries' ? i.group === 'Queries'
      : scope === 'connectors' ? i.group === 'Connectors'
      : i.group === 'Actions'

    if (!terms.length && scope === 'all') {
      const sections: [string, Item[]][] = [
        ['Recent', recentItems.slice(0, 5)],
        ['Actions', all.filter(i => i.group === 'Actions' && !recentSet.has(i.id) && !i.id.startsWith('o-'))],
        ['Go to', all.filter(i => i.group === 'Go to' && !recentSet.has(i.id))],
        ['Dashboards', all.filter(i => i.group === 'Dashboards' && !recentSet.has(i.id)).slice(0, 5)],
      ]
      list = sections.filter(([, xs]) => xs.length)
    } else if (!terms.length) {
      const xs = all.filter(inScope).filter(i => !i.id.startsWith('o-')).slice(0, 30)
      list = xs.length ? [[SCOPE_LABEL[scope], xs]] : []
    } else {
      const ranked = all
        .filter(inScope)
        .map(i => {
          const s = scoreText(`${i.label} ${i.keywords || ''} ${i.group}`, terms)
          return [i, s < 0 ? -1 : s + (recentSet.has(i.id) ? 1 : 0)] as const
        })
        .filter(([, s]) => s >= 0)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 40)
        .map(([i]) => i)
      const order: string[] = []; const by: Record<string, Item[]> = {}
      for (const r of ranked) { if (!by[r.group]) { by[r.group] = []; order.push(r.group) } by[r.group].push(r) }
      list = order.map(g => [g, by[g]] as [string, Item[]])
    }
    return list
  }, [all, byId, recents, scope, terms])

  const flat = useMemo(() => grouped.flatMap(([, xs]) => xs), [grouped])
  // One flat index per rendered row (a recent item can also appear in its own section when searching).
  const indexed = useMemo(() => {
    let n = 0
    return grouped.map(([g, xs]) => [g, xs.map(item => ({ item, idx: n++ }))] as const)
  }, [grouped])

  useEffect(() => { if (sel >= flat.length) setSel(Math.max(0, flat.length - 1)) }, [flat.length, sel])
  useEffect(() => { listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' }) }, [sel])

  if (!open) return null

  const run = (i: Item | undefined) => {
    if (!i) return
    if (i.recent) { const next = pushRecent(readRecents(), i.id); writeRecents(next); setRecents(next) }
    // Picking a destination from the keyboard is not animated by the route transition.
    markKeyboardNav()
    const r = i.run()
    if (r !== 'keep') setOpen(false)
  }

  const onChange = (v: string) => {
    // Typing a scope prefix at the start of an unscoped query converts it into the chip.
    if (scope === 'all') {
      const p = parseSpotlightQuery(v)
      if (p.scope !== 'all') { setScope(p.scope); setTerm(p.term); setSel(0); return }
    }
    setTerm(v); setSel(0)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    const n = Math.max(1, flat.length)
    if (e.key === 'Escape') { e.preventDefault(); hide() }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setSel(s => (s + 1) % n) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setSel(s => (s - 1 + n) % n) }
    else if (e.key === 'Home' && !term) { e.preventDefault(); setSel(0) }
    else if (e.key === 'End' && !term) { e.preventDefault(); setSel(n - 1) }
    else if (e.key === 'Enter') { e.preventDefault(); run(flat[sel]) }
    else if (e.key === 'Tab') { e.preventDefault(); setSel(s => (e.shiftKey ? (s - 1 + n) % n : (s + 1) % n)) }
    else if (e.key === 'Backspace' && !term && scope !== 'all') { e.preventDefault(); setScope('all'); setSel(0) }
    else if (e.key === '?' && !term && scope === 'all') { e.preventDefault(); setOpen(false); openShortcuts() }
  }

  const loading = boards === null || queries === null || connectors === null
  const asking = term.trim()
  const askAi = () => { if (asking) setChatDraft(asking); setOpen(false); openChat() }

  return (
    <div className="sh-spot-overlay" onMouseDown={e => { if (e.target === e.currentTarget) hide() }}>
      <div role="dialog" aria-modal="true" aria-label="Search Nubi" className="sh-pop sh-spot">
        <div className="sh-spot-input-row">
          <Search size={16} strokeWidth={1.5} className="text-subtle shrink-0" aria-hidden="true" />
          {scope !== 'all' && (
            <Tag variant="ink" caps={false} onRemove={() => { setScope('all'); inputRef.current?.focus() }}>
              {SCOPE_LABEL[scope]}
            </Tag>
          )}
          <input
            ref={inputRef}
            value={term}
            onChange={e => onChange(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={scope === 'all' ? 'Search or jump to…' : `Search ${SCOPE_LABEL[scope].toLowerCase()}`}
            role="combobox"
            aria-expanded="true"
            aria-haspopup="listbox"
            aria-autocomplete="list"
            aria-controls="spotlight-list"
            aria-label="Search"
            aria-activedescendant={flat[sel] ? `sp-o-${sel}` : undefined}
            autoComplete="off"
            spellCheck={false}
            className="sh-spot-input"
          />
          {loading && <span className="font-mono text-[11px] text-subtle" aria-hidden="true">loading</span>}
          <kbd className="nubi-kbd" aria-hidden="true">esc</kbd>
        </div>

        <div className="sr-only" role="status" aria-live="polite">{resultCountLabel(flat.length)}</div>

        {flat.length > 0 ? (
          <ul id="spotlight-list" ref={listRef} role="listbox" aria-label="Results" className="sh-spot-list">
            {indexed.map(([g, rows]) => (
              <li key={g} role="presentation">
                <div className="sh-spot-group-label t-label flex items-center gap-1.5" id={`sp-g-${g}`}>
                  {g === 'Recent' && <Clock size={11} strokeWidth={1.5} aria-hidden="true" />}{g}
                </div>
                <ul role="group" aria-labelledby={`sp-g-${g}`} className="m-0 p-0 list-none">
                  {rows.map(({ item: i, idx }) => {
                    const on = idx === sel
                    return (
                      <li
                        key={`${g}-${i.id}`} id={`sp-o-${idx}`} role="option" aria-selected={on}
                        onMouseMove={() => { if (sel !== idx) setSel(idx) }} onClick={() => run(i)}
                        className="sh-spot-opt"
                      >
                        <i.Icon size={16} strokeWidth={1.5} className="sh-spot-icon" aria-hidden="true" />
                        <span className="sh-spot-label"><Highlight text={i.label} terms={terms} /></span>
                        {i.hint && <span className="sh-spot-meta">{i.hint}</span>}
                        <span className="sh-spot-kbds" aria-hidden="true">
                          {i.kbd?.map((k, n) => <kbd key={n} className="nubi-kbd">{k}</kbd>)}
                          {on && <CornerDownLeft size={13} strokeWidth={1.5} />}
                        </span>
                      </li>
                    )
                  })}
                </ul>
              </li>
            ))}
          </ul>
        ) : (
          <>
            <div id="spotlight-list" role="listbox" aria-label="Results" />
            <div className="flex flex-col items-center text-center px-6 pt-6 pb-7">
              <EmptySearch className="w-[128px] h-auto text-fg" />
              <p className="mt-3 mb-0 text-sm font-semibold text-fg">
                {asking ? <>No results for “{asking}”</> : 'Nothing here yet'}
              </p>
              <p className="mt-1 mb-4 text-[13px] text-muted max-w-[44ch]">
                {scope === 'all'
                  ? 'Try a dashboard name, a query, or a page like “flows”. Or ask the assistant.'
                  : `No ${SCOPE_LABEL[scope].toLowerCase()} match. Remove the filter to search everything.`}
              </p>
              <Button size="sm" onClick={askAi}>
                <Sparkles size={14} strokeWidth={1.5} aria-hidden="true" /> Ask AI{asking ? ' about this' : ''}
              </Button>
            </div>
          </>
        )}

        <div className="sh-spot-foot font-mono text-[11px] text-subtle tabular-nums">
          <span className="inline-flex items-center gap-1.5"><kbd className="nubi-kbd">↑</kbd><kbd className="nubi-kbd">↓</kbd> move</span>
          <span className="inline-flex items-center gap-1.5"><kbd className="nubi-kbd">↵</kbd> open</span>
          <span className="sh-spot-hints inline-flex items-center gap-1.5"><kbd className="nubi-kbd">d</kbd><kbd className="nubi-kbd">q</kbd><kbd className="nubi-kbd">c</kbd><kbd className="nubi-kbd">&gt;</kbd> filter</span>
          <span className="ml-auto">{(boards?.length ?? 0)} dashboards · {(queries?.length ?? 0)} queries</span>
        </div>
      </div>
    </div>
  )
}
