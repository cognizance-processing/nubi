/**
 * DashboardCodeView.jsx — VS Code-style "code / files" view for a dashboard.
 *
 * A full-pane view (mirrors src/flows/FlowCodeView.jsx) that projects the
 * dashboard as an editable file tree:
 *   • dashboard.json — the DashboardSpec (the same `config.spec` the CLI writes
 *                      to `dashboards/<slug>.json`, see docs/files-as-code.md §A).
 *                      A Monaco JSON editor; valid edits write straight back to
 *                      the in-memory spec via onSpecChange.
 *
 * Round-trip: edits are parsed on every change. VALID JSON is applied to the
 * spec immediately (same path the canvas + Code panel use). INVALID JSON is
 * surfaced inline as a parse-error banner and the spec is left UNTOUCHED — the
 * editor never corrupts the spec on a half-typed document.
 *
 * The file shape matches the on-disk format so the in-app view is consistent
 * with `nubi pull`: one `dashboard.json` per board, `config.spec` = DashboardSpec.
 *
 * Props:
 *   spec         {object}    — current DashboardSpec
 *   onSpecChange {Function}  — called with the parsed spec after a valid edit
 *   board        {string|null} — saved board id (display only)
 */

import { useState, useCallback, useMemo } from 'react'
import Editor from '@monaco-editor/react'
import {
  FileJson,
  Copy,
  Check,
  AlertCircle,
  CheckCircle2,
  FolderTree,
} from 'lucide-react'
import { useTheme } from '../contexts/ThemeContext.jsx'
import { validateDashboardSpec } from '../dashboards/validateSpec.js'

const DASHBOARD_JSON_ID = '__dashboard_json__'

const MONACO_OPTIONS = {
  fontSize: 12,
  minimap: { enabled: false },
  lineNumbers: 'on',
  scrollBeyondLastLine: false,
  padding: { top: 12, bottom: 12 },
  wordWrap: 'on',
  folding: true,
  renderLineHighlight: 'line',
  scrollbar: { vertical: 'auto', horizontal: 'auto' },
  automaticLayout: true,
  tabSize: 2,
  insertSpaces: true,
  cursorSmoothCaretAnimation: 'on',
  smoothScrolling: true,
}

/** Slugify a title the same way portability.slug_for_envelope does. */
function slugify(s) {
  return (
    (s || 'dashboard')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/(^-|-$)/g, '') || 'dashboard'
  )
}

/** Parse + structurally validate an edited JSON document. */
function parseSpec(text) {
  if (!text || !text.trim()) {
    return { ok: false, error: 'dashboard.json is empty — write a DashboardSpec first.' }
  }
  let doc
  try {
    doc = JSON.parse(text)
  } catch (err) {
    return { ok: false, error: err?.message ?? 'Invalid JSON.' }
  }
  if (!doc || typeof doc !== 'object' || Array.isArray(doc)) {
    return { ok: false, error: 'The document must be a JSON object (a DashboardSpec).' }
  }
  const issues = validateDashboardSpec(doc)
  if (issues.length) {
    return { ok: false, error: `Spec validation failed:\n${issues.map(i => `• ${i}`).join('\n')}`, spec: doc }
  }
  return { ok: true, spec: doc }
}

export default function DashboardCodeView({ spec, onSpecChange, board = null }) {
  const { theme } = useTheme()
  const monacoTheme = theme === 'dark' ? 'vs-dark' : 'light'

  const [copied, setCopied] = useState(false)
  // Local draft. { text, base } — `base` is the specJson the draft was authored
  // against, so an EXTERNAL spec change (AI apply, undo) that doesn't match the
  // base discards the stale draft and re-seeds from the live spec. null ⇒ no
  // pending edits (mirror the live spec).
  const [draft, setDraft] = useState(null)
  const [parseError, setParseError] = useState(null)

  // The canonical JSON projection of the live spec.
  const specJson = useMemo(() => {
    try {
      return JSON.stringify(spec ?? {}, null, 2)
    } catch (e) {
      return `// Serialisation failed: ${e.message}`
    }
  }, [spec])

  // Derive (no setState-in-effect, no ref-in-render): the draft is only "live"
  // while its base still matches the current spec. The moment the spec changes
  // out from under it (external apply/undo), the draft is considered stale and
  // we fall back to the live spec text.
  const draftLive = draft !== null && draft.base === specJson
  const editorValue = draftLive ? draft.text : specJson

  const fileName = `${slugify(spec?.title)}.json`

  const handleChange = useCallback((value) => {
    const text = value ?? ''
    const result = parseSpec(text)
    if (!result.ok) {
      // Keep the invalid text on screen (base = current spec) but DON'T push.
      setDraft({ text, base: specJson })
      setParseError(result.error)
      return
    }
    setParseError(null)
    let nextJson
    try { nextJson = JSON.stringify(result.spec) } catch { nextJson = null }
    let curJson
    try { curJson = JSON.stringify(spec) } catch { curJson = null }
    if (nextJson !== null && nextJson === curJson) {
      // No semantic change (e.g. whitespace) — keep the text, skip the push.
      setDraft({ text, base: specJson })
      return
    }
    // Valid + changed: push to the spec. The push re-renders with a new specJson;
    // the draft re-bases to it so the editor keeps showing the user's text.
    const pushedJson = JSON.stringify(result.spec, null, 2)
    setDraft({ text, base: pushedJson })
    onSpecChange?.(result.spec)
  }, [spec, specJson, onSpecChange])

  const handleCopy = useCallback(() => {
    navigator.clipboard?.writeText(editorValue).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    })
  }, [editorValue])

  // "Dirty" = the on-screen text differs from the canonical serialisation
  // (pretty-printed). Reflects pending whitespace / invalid edits.
  const dirty = draftLive && draft.text !== specJson

  return (
    <div className="flex h-full overflow-hidden bg-bg">

      {/* ── File explorer ─────────────────────────────────────────────────── */}
      <aside className="w-56 shrink-0 flex flex-col border-r border-border bg-surface-2/30 overflow-hidden">
        <div className="shrink-0 flex items-center gap-2 px-3 py-2.5 border-b border-border">
          <FolderTree size={13} className="text-muted" />
          <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">Explorer</span>
        </div>
        <div className="flex-1 overflow-y-auto py-2 px-1.5 space-y-0.5">
          <button
            className={[
              'group w-full flex items-center gap-2 px-2 py-1 text-left text-xs rounded-md transition-colors',
              'bg-primary/10 text-fg',
            ].join(' ')}
            title="The DashboardSpec (config.spec) as a JSON file"
          >
            <FileJson size={13} className="text-amber-500 shrink-0" />
            <span className="truncate flex-1">{fileName}</span>
            {dirty && <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" title="Unsaved edits" />}
          </button>
        </div>
        <div className="shrink-0 px-3 py-2 border-t border-border">
          <p className="text-[10px] leading-snug text-muted/70">
            Edit the spec as JSON — valid edits sync to the canvas live. Use the main Save to persist.
          </p>
        </div>
      </aside>

      {/* ── Editor pane ───────────────────────────────────────────────────── */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">

        {/* Active-file tab bar */}
        <div className="shrink-0 flex items-center justify-between gap-2 px-3 py-2 border-b border-border bg-surface">
          <div className="flex items-center gap-2 min-w-0">
            <FileJson size={13} className="text-amber-500 shrink-0" />
            <span className="text-xs font-medium text-fg truncate">{fileName}</span>
            {dirty && (
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" title="Unsaved edits" />
            )}
            {board && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-2 border border-border text-muted font-mono truncate max-w-[120px]" title={board}>
                {board.slice(0, 8)}…
              </span>
            )}
          </div>

          <div className="flex items-center gap-1 shrink-0">
            {!parseError ? (
              <span className="flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
                <CheckCircle2 size={11} /> Valid
              </span>
            ) : (
              <span className="flex items-center gap-1 text-[11px] text-rose-600 dark:text-rose-400">
                <AlertCircle size={11} /> Invalid
              </span>
            )}
            <button
              onClick={handleCopy}
              className="flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md border border-border bg-surface-2 hover:bg-surface text-fg transition-colors"
              title="Copy to clipboard"
            >
              {copied ? <Check size={11} className="text-green-500" /> : <Copy size={11} />}
              {copied ? 'Copied!' : 'Copy'}
            </button>
          </div>
        </div>

        {/* Parse / validation error banner */}
        {parseError && (
          <div className="shrink-0 flex items-start gap-2 mx-3 mt-2 p-2.5 rounded-lg border border-rose-500/20 bg-rose-500/5 text-[11px] text-rose-600 dark:text-rose-400">
            <AlertCircle size={12} className="shrink-0 mt-0.5" />
            <span className="flex-1 min-w-0 break-words font-mono leading-snug whitespace-pre-wrap">{parseError}</span>
          </div>
        )}

        {/* Editor body */}
        <div className="flex-1 min-h-0 overflow-hidden">
          <Editor
            language="json"
            value={editorValue}
            onChange={handleChange}
            theme={monacoTheme}
            options={{ ...MONACO_OPTIONS, readOnly: false, contextmenu: true }}
          />
        </div>
      </div>
    </div>
  )
}
