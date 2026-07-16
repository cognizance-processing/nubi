/**
 * shared/WidgetHtmlEditor.jsx — Monaco-based code editor for a widget's custom
 * HTML template (widget.html), with a live sanitized preview and an
 * expand-to-fullscreen editing surface.
 *
 * The template supports the same {{tokens}} the runtime resolves (see
 * widgetHtml.js): {{value}}, {{col:NAME}}, {{row.N.NAME}}, {{prop:NAME}}.
 * Data tokens render blank in the preview (no live query here); prop tokens
 * resolve against widget.props so the structure/styling is faithful.
 *
 * Props:
 *   widget    object    — the spec widget (reads widget.html + widget.props)
 *   onChange  (w)=>void — full widget update callback
 */

import { useState, useRef, useMemo, useCallback, useEffect } from 'react'
import { createPortal } from 'react-dom'
import Editor from '@monaco-editor/react'
import { Code2, Maximize2, X, Eye, Smile } from 'lucide-react'
import { renderWidgetHtml } from '../../dashboards/widgetHtml.js'
import { useTheme } from '../../contexts/ThemeContext.jsx'

const MONACO_OPTIONS = {
  fontSize: 12,
  minimap: { enabled: false },
  lineNumbers: 'on',
  scrollBeyondLastLine: false,
  padding: { top: 10, bottom: 10 },
  wordWrap: 'on',
  folding: true,
  automaticLayout: true,
  tabSize: 2,
  quickSuggestions: false,
  renderLineHighlight: 'line',
  scrollbar: { vertical: 'auto', horizontal: 'auto', verticalScrollbarSize: 8, horizontalScrollbarSize: 8 },
}

// Insertable data tokens (label → snippet). Clicking inserts at the cursor.
const TOKENS = [
  { label: '{{value}}', snip: '{{value}}', desc: 'headline value' },
  { label: '{{col:NAME}}', snip: '{{col:NAME}}', desc: 'first-row column' },
  { label: '{{row.0.NAME}}', snip: '{{row.0.NAME}}', desc: 'a specific row' },
  { label: '{{prop:label}}', snip: '{{prop:label}}', desc: 'a widget prop' },
]

const STARTER = '<div style="padding:16px">\n  <h2 style="font-weight:700">{{prop:label}}</h2>\n  <p style="font-size:28px">{{value}}</p>\n</div>'

// Curated emoji set for the picker toggle, grouped by category. Kept inline so
// the editor pulls in no extra dependency and works offline. Selecting one
// inserts the character at the cursor (same path as the token buttons).
const EMOJI_GROUPS = [
  { name: 'Smileys', icon: '😀', emojis: ['😀', '😁', '😂', '🤣', '😊', '😍', '😎', '🤩', '😉', '🙂', '😇', '🤔', '🤨', '😐', '😴', '😢', '😭', '😤', '😱', '🤯', '🥳', '😬', '🙃', '😅'] },
  { name: 'Gestures', icon: '👍', emojis: ['👍', '👎', '👌', '🤙', '👏', '🙌', '🙏', '💪', '✌️', '🤞', '👋', '🤝', '👉', '👈', '👆', '👇', '☝️', '✋', '🖐️', '🤚'] },
  { name: 'Objects', icon: '💡', emojis: ['💡', '🔔', '📌', '📎', '🔗', '🔒', '🔑', '⚙️', '🛠️', '🧰', '📦', '📁', '📄', '📊', '📈', '📉', '🗓️', '⏰', '💾', '🖥️', '📱', '🔍', '🏷️', '📢'] },
  { name: 'Symbols', icon: '✅', emojis: ['✅', '❌', '⚠️', '❗', '❓', '➕', '➖', '⭐', '🌟', '💯', '🔥', '⚡', '❤️', '🧡', '💚', '💙', '💜', '🟢', '🟡', '🔴', '🔵', '⬆️', '⬇️', '➡️'] },
  { name: 'Business', icon: '💰', emojis: ['💰', '💵', '💳', '🏦', '📆', '🎯', '🚀', '🏆', '🥇', '📣', '🤑', '💸', '🧾', '📬', '🕒', '🌍', '🏢', '👥', '🤖', '✉️'] },
  { name: 'Nature', icon: '🌱', emojis: ['🌱', '🌿', '🍀', '🌸', '🌻', '🌈', '☀️', '🌙', '⭐', '☁️', '❄️', '💧', '🌊', '🔆', '🌤️', '🍃', '🌷', '🌵', '🐢', '🦋'] },
  { name: 'Food', icon: '☕', emojis: ['☕', '🍵', '🍎', '🍕', '🍔', '🍟', '🍩', '🍪', '🎂', '🍰', '🍫', '🍬', '🥤', '🍺', '🍷', '🥂', '🍇', '🍊', '🥑', '🌮'] },
]

/**
 * EmojiPicker — a toggle button that opens a categorized emoji grid. Selecting
 * an emoji calls onInsert(char). Closes on outside-click or Escape.
 */
function EmojiPicker({ onInsert }) {
  const [open, setOpen] = useState(false)
  const [group, setGroup] = useState(0)
  const wrapRef = useRef(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e) => { if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false) }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        title="Insert emoji"
        aria-label="Insert emoji"
        aria-expanded={open}
        className={`flex items-center gap-1 px-1.5 h-6 rounded-md border text-[10px] font-medium transition-colors ${
          open ? 'border-primary/60 text-primary bg-primary/5' : 'border-border bg-surface text-muted hover:text-primary hover:border-primary/60'
        }`}
      >
        <Smile size={12} /> Emoji
      </button>

      {open && (
        <div
          className="absolute z-[110] mt-1 left-0 w-[248px] rounded-lg border border-border bg-bg shadow-xl overflow-hidden"
          role="dialog"
          aria-label="Emoji picker"
        >
          <div className="flex items-center gap-0.5 px-1.5 py-1 border-b border-border overflow-x-auto">
            {EMOJI_GROUPS.map((g, i) => (
              <button
                key={g.name}
                type="button"
                onClick={() => setGroup(i)}
                title={g.name}
                aria-label={g.name}
                aria-pressed={group === i}
                className={`shrink-0 w-6 h-6 flex items-center justify-center rounded-md text-sm transition-colors ${
                  group === i ? 'bg-primary/10 ring-1 ring-primary/40' : 'hover:bg-surface-2'
                }`}
              >
                {g.icon}
              </button>
            ))}
          </div>
          <div className="grid grid-cols-8 gap-0.5 p-1.5 max-h-[168px] overflow-y-auto">
            {EMOJI_GROUPS[group].emojis.map((em, i) => (
              <button
                key={`${em}-${i}`}
                type="button"
                onClick={() => onInsert(em)}
                title={`Insert ${em}`}
                className="w-6 h-6 flex items-center justify-center rounded-md text-base leading-none hover:bg-primary/10 transition-colors"
              >
                {em}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/** Live sanitized preview of the template (prop tokens filled, data blank). */
function HtmlPreview({ html, props, dark }) {
  const safe = useMemo(
    () => renderWidgetHtml(html || '', { table: null, props: props ?? {}, extra: {} }),
    [html, props],
  )
  return (
    <div
      className="rounded-lg border border-border overflow-auto"
      style={{ background: dark ? '#0f1826' : '#ffffff', color: dark ? '#dbe6f5' : '#0f172a', minHeight: 80 }}
    >
      {safe
        ? <div dangerouslySetInnerHTML={{ __html: safe }} />
        : <div className="p-4 text-xs text-muted/70">Preview — start typing HTML above.</div>}
    </div>
  )
}

export default function WidgetHtmlEditor({ widget, onChange }) {
  const { theme } = useTheme()
  const dark = theme === 'dark'
  const monacoTheme = dark ? 'vs-dark' : 'light'
  const html = widget.html ?? ''
  const editorRef = useRef(null)
  const [expanded, setExpanded] = useState(false)

  const setHtml = useCallback((val) => {
    onChange({ ...widget, html: val || undefined })
  }, [widget, onChange])

  const insertToken = useCallback((snippet) => {
    const ed = editorRef.current
    if (!ed) { setHtml((html || '') + snippet); return }
    const sel = ed.getSelection()
    ed.executeEdits('insert-token', [{ range: sel, text: snippet, forceMoveMarkers: true }])
    ed.focus()
  }, [html, setHtml])

  const tokenBar = (
    <div className="flex flex-wrap items-center gap-1">
      {TOKENS.map(t => (
        <button
          key={t.label}
          type="button"
          onClick={() => insertToken(t.snip)}
          title={`Insert ${t.desc}`}
          className="px-1.5 h-6 rounded-md border border-border bg-surface font-mono text-[10px] text-muted hover:text-primary hover:border-primary/60 transition-colors"
        >
          {t.label}
        </button>
      ))}
      <span className="mx-0.5 h-4 w-px bg-border" aria-hidden="true" />
      <EmojiPicker onInsert={insertToken} />
    </div>
  )

  const editorPane = (height) => (
    <div className="rounded-lg border border-border overflow-hidden" style={{ height }}>
      <Editor
        language="html"
        theme={monacoTheme}
        value={html}
        onChange={(v) => setHtml(v ?? '')}
        onMount={(ed) => { editorRef.current = ed }}
        options={MONACO_OPTIONS}
      />
    </div>
  )

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-[11px] font-medium text-muted">
          <Code2 size={12} /> Tokens pull live query data
        </span>
        <div className="flex items-center gap-1.5">
          {!html && (
            <button type="button" onClick={() => setHtml(STARTER)}
              className="text-[10px] font-medium text-muted hover:text-primary transition-colors">Insert starter</button>
          )}
          <button type="button" onClick={() => setExpanded(true)} title="Expand editor"
            className="flex items-center gap-1 px-1.5 h-6 rounded-md border border-border bg-surface text-[10px] font-medium text-muted hover:text-primary hover:border-primary/60 transition-colors">
            <Maximize2 size={11} /> Expand
          </button>
        </div>
      </div>

      {tokenBar}
      {editorPane(180)}

      <div className="space-y-1">
        <span className="flex items-center gap-1.5 text-[10px] font-semibold text-muted/70 uppercase tracking-wide">
          <Eye size={11} /> Preview
        </span>
        <HtmlPreview html={html} props={widget.props} dark={dark} />
      </div>

      {html && (
        <button type="button" onClick={() => setHtml('')}
          className="text-xs text-muted hover:text-red-500 transition-colors">Clear custom HTML → use default widget</button>
      )}

      {expanded && createPortal(
        <div className="fixed inset-0 z-[100] flex flex-col bg-black/50 backdrop-blur-sm" role="dialog" aria-modal="true">
          <div className="m-auto w-[min(1100px,94vw)] h-[min(760px,90vh)] flex flex-col rounded-2xl border border-border bg-bg shadow-2xl overflow-hidden">
            <div className="flex items-center justify-between px-4 h-12 border-b border-border shrink-0">
              <span className="flex items-center gap-2 text-sm font-semibold text-fg"><Code2 size={15} className="text-primary" /> Custom HTML</span>
              <button type="button" onClick={() => setExpanded(false)}
                className="w-8 h-8 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors" aria-label="Close">
                <X size={16} />
              </button>
            </div>
            <div className="px-4 py-2 border-b border-border shrink-0">{tokenBar}</div>
            <div className="flex-1 grid grid-cols-2 gap-3 p-3 min-h-0">
              <div className="min-h-0">{editorPane('100%')}</div>
              <div className="min-h-0 flex flex-col">
                <span className="flex items-center gap-1.5 text-[10px] font-semibold text-muted/70 uppercase tracking-wide mb-1">
                  <Eye size={11} /> Preview
                </span>
                <div className="flex-1 min-h-0 overflow-auto rounded-lg border border-border" style={{ background: dark ? '#0f1826' : '#fff', color: dark ? '#dbe6f5' : '#0f172a' }}>
                  <HtmlPreview html={html} props={widget.props} dark={dark} />
                </div>
              </div>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}
