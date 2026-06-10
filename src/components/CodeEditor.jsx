/**
 * CodeEditor.jsx — Reusable Monaco editor wrapper with error markers.
 *
 * A thin, composable wrapper around @monaco-editor/react that:
 *   - Picks the right Monaco language mode ('sql', 'python', 'json', 'yaml')
 *   - Follows the app's ThemeContext (dark → vs-dark, light → light)
 *   - Accepts an optional `markers` prop (array of error/warning descriptors)
 *     and applies them as Monaco model markers so they render as VS Code-style
 *     red/yellow squiggles + hover messages.
 *   - Exposes a ref-based imperative handle via `editorRef` prop (optional).
 *
 * Props
 * -----
 *   value       {string}        — controlled content
 *   onChange    {function}      — called with new value string on every edit
 *   language    {'sql'|'python'|'json'|'yaml'}  — Monaco language id
 *   dialect     {string?}       — SQL dialect hint (used by SqlEditor; ignored here)
 *   markers     {Marker[]}      — error/warning markers to apply as squiggles
 *   height      {string}        — CSS height string (default '200px')
 *   readOnly    {boolean}       — when true, editor is non-editable (default false)
 *   onRun       {function?}     — if provided, Ctrl/Cmd+Enter triggers it
 *   onMount     {function?}     — (editor, monaco) called after editor mounts
 *   editorRef   {React.MutableRefObject?} — receives the editor instance
 *   monacoRef   {React.MutableRefObject?} — receives the monaco namespace
 *   markerId    {string}        — owner string for setModelMarkers (default 'nubi')
 *   fontSize    {number}        — Monaco font size (default 13)
 *   lineNumbers {'on'|'off'|'relative'} — default 'on'
 *   wordWrap    {'on'|'off'}    — default 'on'
 *   minimap     {boolean}       — default false
 *   padding     {{top,bottom}}  — default {top:8, bottom:8}
 *
 * Marker shape (each item):
 *   {
 *     line:     number  — 1-based line number
 *     col:      number  — 1-based column (start)
 *     endLine?: number  — defaults to line
 *     endCol?:  number  — defaults to col + 1
 *     message:  string
 *     severity: 'error'|'warning'|'info'|'hint'  — default 'error'
 *   }
 *
 * Theme: follows ThemeContext (dark → vs-dark, light → light). If
 * ThemeContext is absent (e.g. in Storybook), defaults to 'light'.
 */

import { useRef, useCallback, useEffect } from 'react'
import Editor from '@monaco-editor/react'
import { useTheme } from '../contexts/ThemeContext.jsx'

// ---------------------------------------------------------------------------
// severity map
// ---------------------------------------------------------------------------

/** Map a human-readable severity string to the Monaco MarkerSeverity int. */
function severityInt(monaco, s) {
  switch ((s ?? 'error').toLowerCase()) {
    case 'warning': return monaco.MarkerSeverity.Warning
    case 'info':    return monaco.MarkerSeverity.Info
    case 'hint':    return monaco.MarkerSeverity.Hint
    default:        return monaco.MarkerSeverity.Error
  }
}

// ---------------------------------------------------------------------------
// CodeEditor
// ---------------------------------------------------------------------------

export default function CodeEditor({
  value = '',
  onChange,
  language = 'sql',
  dialect,      // informational only — SqlEditor uses it; we ignore it here
  markers = [],
  height = '200px',
  readOnly = false,
  onRun,
  onMount: onMountProp,
  editorRef: editorRefProp,
  monacoRef: monacoRefProp,
  markerId = 'nubi',
  fontSize = 13,
  lineNumbers = 'on',
  wordWrap = 'on',
  minimap = false,
  padding = { top: 8, bottom: 8 },
}) {
  // Theme — soft-fail if ThemeProvider is absent (e.g. tests / Storybook)
  let themeVal = 'light'
  try {
    themeVal = useTheme().theme
  } catch {
    // Outside ThemeProvider
  }
  const monacoTheme = themeVal === 'dark' ? 'vs-dark' : 'light'

  // Internal refs
  const editorRef = useRef(null)
  const monacoRef = useRef(null)

  // Keep latest markers in a ref so the apply-markers effect doesn't need
  // to be added to the editor's dependency list.
  const markersRef = useRef(markers)
  useEffect(() => { markersRef.current = markers }, [markers])

  // ── Apply markers whenever the markers prop changes ─────────────────────
  useEffect(() => {
    const monaco = monacoRef.current
    const editor = editorRef.current
    if (!monaco || !editor) return
    const model = editor.getModel()
    if (!model) return

    const monacoMarkers = (markersRef.current ?? []).map(m => {
      const line = Math.max(1, m.line ?? 1)
      const col = Math.max(1, m.col ?? 1)
      const endLine = Math.max(line, m.endLine ?? line)
      const endCol = Math.max(col + 1, m.endCol ?? col + 1)
      return {
        severity: severityInt(monaco, m.severity),
        message: m.message ?? 'Error',
        startLineNumber: line,
        startColumn: col,
        endLineNumber: endLine,
        endColumn: endCol,
      }
    })

    monaco.editor.setModelMarkers(model, markerId, monacoMarkers)
  }, [markers, markerId])

  // ── Mount handler ───────────────────────────────────────────────────────
  const handleMount = useCallback((editor, monaco) => {
    editorRef.current = editor
    monacoRef.current = monaco

    // Forward refs to parent if requested
    if (editorRefProp) editorRefProp.current = editor
    if (monacoRefProp) monacoRefProp.current = monaco

    // Ctrl/Cmd+Enter → onRun
    if (onRun) {
      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => onRun())
    }

    // Apply any initial markers
    const model = editor.getModel()
    if (model && markersRef.current?.length) {
      const initial = markersRef.current.map(m => {
        const line = Math.max(1, m.line ?? 1)
        const col = Math.max(1, m.col ?? 1)
        return {
          severity: severityInt(monaco, m.severity),
          message: m.message ?? 'Error',
          startLineNumber: line,
          startColumn: col,
          endLineNumber: Math.max(line, m.endLine ?? line),
          endColumn: Math.max(col + 1, m.endCol ?? col + 1),
        }
      })
      monaco.editor.setModelMarkers(model, markerId, initial)
    }

    // Delegate to parent onMount if provided
    onMountProp?.(editor, monaco)
  }, [onRun, onMountProp, editorRefProp, monacoRefProp, markerId])

  const handleChange = useCallback((val) => {
    onChange?.(val ?? '')
  }, [onChange])

  return (
    <div
      className="rounded-lg border border-border overflow-hidden"
      style={{ height }}
    >
      <Editor
        height={height}
        language={language}
        theme={monacoTheme}
        value={value}
        onChange={handleChange}
        onMount={handleMount}
        options={{
          readOnly,
          minimap: { enabled: minimap },
          scrollBeyondLastLine: false,
          fontSize,
          lineNumbers,
          wordWrap,
          tabSize: language === 'python' ? 4 : 2,
          automaticLayout: true,
          padding,
          overviewRulerLanes: language === 'sql' || language === 'python' ? 1 : 0,
          quickSuggestions: !readOnly,
          suggestOnTriggerCharacters: !readOnly,
          scrollbar: { vertical: 'auto', horizontal: 'auto' },
          // Show a glyph in the gutter for error lines (like VS Code)
          glyphMargin: true,
          // Render the problems indicator via the default Monaco 'overviewRuler'
          overviewRulerBorder: false,
        }}
        loading={
          <div className="flex items-center justify-center h-full text-xs text-muted">
            Loading editor…
          </div>
        }
      />
    </div>
  )
}
