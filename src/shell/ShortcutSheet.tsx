/**
 * ShortcutSheet — the "?" keyboard reference.
 *
 * Opened by pressing `?` anywhere outside a text field, from the account menu, or from Spotlight
 * (`?` in an empty search box). Content is generated from NAV_CHORDS so it can't drift from the
 * real shortcuts. Mounted once by AppShell.
 */

import { useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { NAV_CHORDS } from './shellLogic.js'
import { SPOTLIGHT_KEY } from './nav.js'
import './shell.css'

const OPEN_EVENT = 'nubi:shortcuts'

/** Open the sheet from anywhere. */
export function openShortcuts() {
  window.dispatchEvent(new Event(OPEN_EVENT))
}

type Row = { keys: string[]; label: string; sep?: 'then' | 'plus' }
const GROUPS: { title: string; rows: Row[] }[] = [
  {
    title: 'General',
    rows: [
      { keys: [SPOTLIGHT_KEY], label: 'Search everything' },
      { keys: ['?'], label: 'Show this sheet' },
      { keys: ['['], label: 'Collapse or expand the sidebar' },
      { keys: ['Esc'], label: 'Close a dialog, menu or panel' },
    ],
  },
  {
    title: 'Go to',
    rows: Object.entries(NAV_CHORDS).map(([k, v]) => ({ keys: ['g', k], label: v.label, sep: 'then' as const })),
  },
  {
    title: 'In search',
    rows: [
      { keys: ['d'], label: 'Only dashboards (type d then a space)' },
      { keys: ['q'], label: 'Only queries (q then a space)' },
      { keys: ['c'], label: 'Only connectors (c then a space)' },
      { keys: ['>'], label: 'Only actions' },
      { keys: ['↑', '↓'], label: 'Move through results' },
      { keys: ['↵'], label: 'Open the selected result' },
    ],
  },
]

export default function ShortcutSheet() {
  const [open, setOpen] = useState(false)
  const closeRef = useRef<HTMLButtonElement>(null)
  const lastFocus = useRef<Element | null>(null)

  useEffect(() => {
    const onOpen = () => { lastFocus.current = document.activeElement; setOpen(true) }
    window.addEventListener(OPEN_EVENT, onOpen)
    return () => window.removeEventListener(OPEN_EVENT, onOpen)
  }, [])

  useEffect(() => {
    if (!open) return
    closeRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); close() }
      else if (e.key === 'Tab') { e.preventDefault(); closeRef.current?.focus() } // single focus stop
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  function close() {
    setOpen(false)
    const el = lastFocus.current as HTMLElement | null
    if (el && typeof el.focus === 'function') el.focus()
  }

  if (!open) return null

  return (
    <div className="sh-spot-overlay" onMouseDown={e => { if (e.target === e.currentTarget) close() }}>
      <div role="dialog" aria-modal="true" aria-labelledby="shortcuts-title" className="sh-pop sh-spot" style={{ maxWidth: 560 }}>
        <div className="flex items-center justify-between h-12 px-4 border-b border-border">
          <h2 id="shortcuts-title" className="text-[15px] font-semibold text-fg m-0">Keyboard shortcuts</h2>
          <button ref={closeRef} type="button" onClick={close} aria-label="Close shortcuts" className="sh-icon-btn">
            <X size={16} strokeWidth={1.5} aria-hidden="true" />
          </button>
        </div>
        <div className="max-h-[min(520px,70vh)] overflow-y-auto px-4 pb-4">
          {GROUPS.map(g => (
            <section key={g.title} aria-label={g.title}>
              <h3 className="t-label pt-4 pb-1.5 m-0 border-b-2 border-[var(--ink)]">{g.title}</h3>
              <ul className="m-0 p-0 list-none">
                {g.rows.map(r => (
                  <li key={r.label + r.keys.join('')} className="flex items-center gap-3 min-h-9 border-b border-border text-[13.5px] text-fg">
                    <span className="flex-1">{r.label}</span>
                    <span className="inline-flex items-center gap-1" aria-label={r.keys.join(r.sep === 'then' ? ' then ' : ' ')}>
                      {r.keys.map((k, i) => (
                        <span key={i} className="inline-flex items-center gap-1" aria-hidden="true">
                          {i > 0 && r.sep === 'then' && <span className="text-subtle text-xs">then</span>}
                          <kbd className="nubi-kbd">{k}</kbd>
                        </span>
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </div>
    </div>
  )
}
