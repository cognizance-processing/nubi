/**
 * SqlEditor — comprehensive Monaco-based SQL editor.
 *
 * Features
 * --------
 * - Syntax highlighting via Monaco's built-in `sql` language.
 * - Dialect selector (bigquery | duckdb | postgres | mysql) driving both
 *   accurate backend validation and a small dialect hint.
 * - Schema-aware autocomplete: a Monaco completion provider that suggests SQL
 *   keywords + known tables/columns. Tables/columns are sourced from
 *   GET /query/schema (with the registry as a fallback signal). This is static
 *   schema/keyword completion — NOT an LLM.
 * - Accurate syntax validation: on edit (debounced) POST /query/validate is
 *   called and parse errors are rendered as Monaco markers (squiggles) with
 *   messages + positions.
 * - Templates dropdown: inserts starter queries, plus a short templating help
 *   popover explaining {{param}} → named params.
 *
 * Props
 * -----
 *   value        {string}   — controlled SQL content
 *   onChange     {function} — called with new value string on every edit
 *   readOnly     {boolean}  — when true, editor is non-editable (default false)
 *   height       {string}   — CSS height string (default "200px")
 *   onRun        {function} — optional; called on Ctrl/Cmd+Enter
 *   toolbar      {boolean}  — when true, render the dialect + templates toolbar
 *                             (default true). Set false for a bare editor.
 *   dialect      {string}   — controlled dialect (optional); defaults to 'duckdb'
 *   onDialectChange {function} — optional; called when the dialect changes
 *   dialectHint  {string}   — optional; subtle source note for the dialect
 *                             (e.g. "from Postgres prod") shown by the selector
 *   schema       {object}   — optional pre-fetched { tables: { name: [cols] } }.
 *                             When omitted the editor fetches it once via api.
 *
 * Theme: follows the app's ThemeContext (dark → vs-dark, light → light),
 * degrading to 'light' when ThemeContext is unavailable.
 */

import { useRef, useCallback, useState, useEffect } from 'react'
import Editor from '@monaco-editor/react'
import { FileCode2, ChevronDown, HelpCircle, Braces, Database } from 'lucide-react'

import { useTheme } from '../contexts/ThemeContext.jsx'
import { validateSql, fetchSchema } from '../lib/api.js'

// ---------------------------------------------------------------------------
// Static data: dialects, keywords, query templates
// ---------------------------------------------------------------------------

export const SQL_DIALECTS = [
  { value: 'duckdb', label: 'DuckDB' },
  { value: 'bigquery', label: 'BigQuery' },
  { value: 'postgres', label: 'Postgres' },
  { value: 'mysql', label: 'MySQL' },
]

// A pragmatic keyword list for local completion (covers the common surface).
const SQL_KEYWORDS = [
  'SELECT', 'FROM', 'WHERE', 'GROUP BY', 'ORDER BY', 'HAVING', 'LIMIT',
  'OFFSET', 'JOIN', 'LEFT JOIN', 'RIGHT JOIN', 'INNER JOIN', 'FULL JOIN',
  'ON', 'AS', 'AND', 'OR', 'NOT', 'IN', 'IS NULL', 'IS NOT NULL', 'LIKE',
  'BETWEEN', 'DISTINCT', 'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'CASE', 'WHEN',
  'THEN', 'ELSE', 'END', 'WITH', 'UNION', 'UNION ALL', 'EXCEPT', 'INTERSECT',
  'CAST', 'COALESCE', 'NULLIF', 'EXTRACT', 'DATE_TRUNC', 'NOW', 'CURRENT_DATE',
  'INTERVAL', 'ASC', 'DESC', 'OVER', 'PARTITION BY', 'ROW_NUMBER', 'RANK',
]

/** Starter query templates. {{param}} placeholders become named params. */
export const QUERY_TEMPLATES = [
  {
    label: 'Basic SELECT',
    description: 'Simple projection with a row limit',
    sql: 'SELECT *\nFROM demo\nLIMIT 100',
  },
  {
    label: 'GROUP BY aggregate',
    description: 'Counts by a dimension, ordered',
    sql: 'SELECT\n  category,\n  COUNT(*)   AS n,\n  SUM(value) AS total\nFROM demo\nGROUP BY category\nORDER BY n DESC',
  },
  {
    label: 'Single-select filter',
    description: '{{param}} → a single bindable param',
    sql: "SELECT *\nFROM demo\nWHERE category = {{category}}\nORDER BY value DESC\nLIMIT 100",
  },
  {
    label: 'Date-range filter',
    description: '{{from}} / {{to}} → a date-range param',
    sql: "SELECT *\nFROM events\nWHERE created_at >= {{from}}\n  AND created_at <  {{to}}\nORDER BY created_at DESC",
  },
  {
    label: 'Multi-select IN (…)',
    description: '{{items}} → a multi-select list param',
    sql: "SELECT *\nFROM demo\nWHERE category IN ({{items}})\nORDER BY category, value DESC",
  },
  {
    label: 'Date + filter combo',
    description: 'Date range plus a single-select param',
    sql: "SELECT\n  DATE_TRUNC('day', created_at) AS day,\n  COUNT(*) AS events\nFROM events\nWHERE created_at >= {{from}}\n  AND created_at <  {{to}}\n  AND source = {{source}}\nGROUP BY day\nORDER BY day",
  },
  {
    label: 'Window function',
    description: 'Running total via a window function',
    sql: 'SELECT\n  id,\n  value,\n  SUM(value) OVER (ORDER BY id) AS running_total\nFROM demo\nORDER BY id',
  },
]

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Extract the distinct {{param}} names a template introduces (in order). */
function templateParams(sql) {
  const re = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g
  const out = []
  const seen = new Set()
  let m
  while ((m = re.exec(sql)) !== null) {
    if (!seen.has(m[1])) { seen.add(m[1]); out.push(m[1]) }
  }
  return out
}

// ---------------------------------------------------------------------------
// Templating help popover
// ---------------------------------------------------------------------------

function TemplatingHelp({ open, onClose }) {
  if (!open) return null
  return (
    <div
      className="absolute right-0 top-full mt-1.5 z-40 w-72 rounded-lg border border-border bg-surface shadow-xl p-3 text-xs text-fg"
      onMouseLeave={onClose}
    >
      <p className="font-semibold mb-1.5 flex items-center gap-1.5">
        <HelpCircle size={12} className="text-primary" />
        Templates &amp; params
      </p>
      <p className="text-muted leading-relaxed">
        A <span className="font-medium text-fg">template</span> drops a ready-made starter
        query at your cursor — pick one, then tweak it.
      </p>
      <p className="text-muted leading-relaxed mt-2">
        Use <code className="px-1 py-0.5 rounded bg-surface-2 font-mono">{'{{name}}'}</code> to
        declare a <span className="font-medium text-fg">named parameter</span>. When the
        query is saved and used on a dashboard, each placeholder becomes a bindable
        param (filters / variables).
      </p>
      <p className="text-muted leading-relaxed mt-2">
        Example:{' '}
        <code className="px-1 py-0.5 rounded bg-surface-2 font-mono">
          WHERE region = {'{{region}}'}
        </code>{' '}
        → a <code className="font-mono">region</code> param. Values are bound safely
        (never string-concatenated).
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Templates dropdown
// ---------------------------------------------------------------------------

function TemplatesMenu({ onInsert }) {
  const [open, setOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    function onDocClick(e) {
      if (ref.current && !ref.current.contains(e.target)) { setOpen(false); setHelpOpen(false) }
    }
    function onKey(e) { if (e.key === 'Escape') { setOpen(false); setHelpOpen(false) } }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        className={[
          'inline-flex items-center gap-1.5 h-7 px-2.5 text-xs font-medium rounded-md border transition-colors',
          open
            ? 'bg-surface-2 border-border text-fg'
            : 'bg-surface border-border text-muted hover:text-fg hover:bg-surface-2',
        ].join(' ')}
        title="Insert a starter query"
      >
        <FileCode2 size={12} className="text-primary/70" />
        Templates
        <ChevronDown size={11} className={open ? 'rotate-180 transition-transform' : 'transition-transform'} />
      </button>

      {open && (
        <div className="absolute left-0 top-full mt-1.5 z-30 w-80 rounded-lg border border-border bg-surface shadow-xl overflow-visible">
          <div className="flex items-center justify-between px-3 py-2 border-b border-border bg-surface-2/60">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-muted">
              Starter queries
            </span>
            <button
              type="button"
              onMouseEnter={() => setHelpOpen(true)}
              onClick={() => setHelpOpen(o => !o)}
              className="inline-flex items-center gap-1 text-[10px] font-medium text-muted hover:text-primary transition-colors"
              title="What are templates & params?"
            >
              <HelpCircle size={12} />
              What&apos;s this?
            </button>
          </div>
          <ul role="menu" className="max-h-80 overflow-y-auto py-1">
            {QUERY_TEMPLATES.map((tpl, i) => {
              const ps = templateParams(tpl.sql)
              return (
                <li key={i} role="none">
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => { onInsert(tpl.sql); setOpen(false); setHelpOpen(false) }}
                    className="group w-full text-left px-3 py-2 hover:bg-primary/5 focus:bg-primary/5 focus:outline-none transition-colors"
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-fg group-hover:text-primary transition-colors">
                        {tpl.label}
                      </span>
                      {ps.length > 0 && (
                        <span className="inline-flex items-center gap-1 text-[9px] font-semibold uppercase tracking-wide text-primary/80 shrink-0">
                          <Braces size={9} />
                          {ps.length} param{ps.length > 1 ? 's' : ''}
                        </span>
                      )}
                    </span>
                    <span className="block text-[11px] text-muted mt-0.5 leading-snug">{tpl.description}</span>
                    {ps.length > 0 && (
                      <span className="flex flex-wrap gap-1 mt-1.5">
                        {ps.map(p => (
                          <code
                            key={p}
                            className="px-1 py-0.5 rounded bg-surface-2 border border-border/60 font-mono text-[10px] text-primary/90"
                          >
                            {`{{${p}}}`}
                          </code>
                        ))}
                      </span>
                    )}
                  </button>
                </li>
              )
            })}
          </ul>
          <div className="px-3 py-2 border-t border-border bg-surface-2/40">
            <p className="text-[10px] text-muted leading-relaxed">
              Inserts at your cursor. Use{' '}
              <code className="px-1 py-0.5 rounded bg-surface font-mono text-[10px]">{'{{name}}'}</code>{' '}
              to declare a bindable param.
            </p>
          </div>
          <TemplatingHelp open={helpOpen} onClose={() => setHelpOpen(false)} />
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// SqlEditor
// ---------------------------------------------------------------------------

export default function SqlEditor({
  value,
  onChange,
  readOnly = false,
  height = '200px',
  onRun,
  toolbar = true,
  dialect: dialectProp,
  onDialectChange,
  dialectHint,
  schema: schemaProp,
}) {
  // Theme — soft-fail if ThemeProvider is not mounted
  let theme = 'light'
  try {
    theme = useTheme().theme
  } catch {
    // Outside ThemeProvider — default to light
  }
  const monacoTheme = theme === 'dark' ? 'vs-dark' : 'light'

  // Dialect — controlled when dialectProp is provided, else internal state.
  const [dialectState, setDialectState] = useState(dialectProp ?? 'duckdb')
  const dialect = dialectProp ?? dialectState
  const setDialect = useCallback((d) => {
    setDialectState(d)
    onDialectChange?.(d)
  }, [onDialectChange])

  // Schema for autocomplete — use prop if given, otherwise fetch once.
  const [schema, setSchema] = useState(schemaProp ?? { tables: {} })
  useEffect(() => {
    if (schemaProp) { setSchema(schemaProp); return }
    let alive = true
    fetchSchema().then(s => { if (alive) setSchema(s) })
    return () => { alive = false }
  }, [schemaProp])

  // Refs to the Monaco editor + monaco namespace (set on mount).
  const editorRef = useRef(null)
  const monacoRef = useRef(null)
  const modelUriRef = useRef(null)

  // Keep latest schema/dialect/value available to async callbacks without
  // re-registering Monaco providers on every change.
  const schemaRef = useRef(schema)
  useEffect(() => { schemaRef.current = schema }, [schema])
  const dialectRef = useRef(dialect)
  useEffect(() => { dialectRef.current = dialect }, [dialect])

  // ── Validation (debounced) → Monaco markers ─────────────────────────────
  const validateTimer = useRef(null)
  const runValidation = useCallback((sql) => {
    const monaco = monacoRef.current
    const editor = editorRef.current
    if (!monaco || !editor) return
    const model = editor.getModel()
    if (!model) return

    validateSql(sql, dialectRef.current).then(({ ok, errors }) => {
      // The model may have been disposed while awaiting.
      if (!editor.getModel() || editor.getModel() !== model) return
      const markers = (ok || !errors) ? [] : errors.map(e => {
        const line = Math.max(1, e.line || 1)
        const col = Math.max(1, e.col || 1)
        return {
          severity: monaco.MarkerSeverity.Error,
          message: e.message || 'Syntax error',
          startLineNumber: line,
          startColumn: col,
          endLineNumber: line,
          endColumn: col + 1,
        }
      })
      monaco.editor.setModelMarkers(model, 'nubi-sql', markers)
    })
  }, [])

  const scheduleValidation = useCallback((sql) => {
    if (validateTimer.current) clearTimeout(validateTimer.current)
    validateTimer.current = setTimeout(() => runValidation(sql), 500)
  }, [runValidation])

  // Re-validate when the dialect changes (errors are dialect-specific).
  useEffect(() => {
    if (editorRef.current?.getModel()) {
      runValidation(editorRef.current.getModel().getValue())
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dialect])

  useEffect(() => () => { if (validateTimer.current) clearTimeout(validateTimer.current) }, [])

  // ── Mount: keybindings + completion provider + initial validation ───────
  const handleMount = useCallback((editor, monaco) => {
    editorRef.current = editor
    monacoRef.current = monaco
    modelUriRef.current = editor.getModel()?.uri?.toString() ?? null

    // Ctrl/Cmd+Enter → run
    if (onRun) {
      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => onRun())
    }

    // Register a schema-aware (static) completion provider ONCE per monaco instance.
    if (!monaco.__nubiSqlCompletionRegistered) {
      monaco.__nubiSqlCompletionRegistered = true
      monaco.languages.registerCompletionItemProvider('sql', {
        triggerCharacters: ['.', ' '],
        provideCompletionItems: (model, position) => {
          const word = model.getWordUntilPosition(position)
          const range = {
            startLineNumber: position.lineNumber,
            endLineNumber: position.lineNumber,
            startColumn: word.startColumn,
            endColumn: word.endColumn,
          }
          const tables = schemaRef.current?.tables ?? {}
          const suggestions = []

          // Keywords
          for (const kw of SQL_KEYWORDS) {
            suggestions.push({
              label: kw,
              kind: monaco.languages.CompletionItemKind.Keyword,
              insertText: kw,
              range,
            })
          }
          // Tables
          for (const t of Object.keys(tables)) {
            suggestions.push({
              label: t,
              kind: monaco.languages.CompletionItemKind.Struct,
              insertText: t,
              detail: 'table',
              range,
            })
          }
          // Columns (deduped, with owning-table detail)
          const seen = new Set()
          for (const [t, cols] of Object.entries(tables)) {
            for (const c of cols ?? []) {
              const key = `${c}`
              if (seen.has(key)) continue
              seen.add(key)
              suggestions.push({
                label: c,
                kind: monaco.languages.CompletionItemKind.Field,
                insertText: c,
                detail: `column · ${t}`,
                range,
              })
            }
          }
          return { suggestions }
        },
      })
    }

    // Initial validation pass.
    runValidation(editor.getModel()?.getValue() ?? '')
  }, [onRun, runValidation])

  const handleChange = useCallback((val) => {
    const next = val ?? ''
    onChange?.(next)
    scheduleValidation(next)
  }, [onChange, scheduleValidation])

  // ── Insert a template at the cursor (replacing selection) ───────────────
  const insertTemplate = useCallback((sql) => {
    const editor = editorRef.current
    if (!editor) { onChange?.(sql); return }
    const selection = editor.getSelection()
    editor.executeEdits('insert-template', [{ range: selection, text: sql, forceMoveMarkers: true }])
    editor.focus()
  }, [onChange])

  return (
    <div className="flex flex-col gap-1.5">
      {toolbar && (
        <div className="flex items-center gap-2 flex-wrap">
          <TemplatesMenu onInsert={insertTemplate} />

          <div className="flex-1" />

          <div className="inline-flex items-center gap-1.5">
            <label
              htmlFor="sql-dialect"
              className="inline-flex items-center gap-1 text-[11px] font-medium text-muted"
            >
              <Database size={11} className="text-primary/70" />
              Dialect
            </label>
            <select
              id="sql-dialect"
              value={dialect}
              onChange={e => setDialect(e.target.value)}
              className="h-7 rounded-md border border-border bg-surface text-fg text-xs px-2 focus:outline-none focus:ring-1 focus:ring-ring cursor-pointer transition-colors hover:bg-surface-2"
              title={dialectHint ? `Auto-detected ${dialectHint}. You can override it here.` : 'SQL dialect for validation and hints'}
            >
              {SQL_DIALECTS.map(d => (
                <option key={d.value} value={d.value}>{d.label}</option>
              ))}
            </select>
            {dialectHint && (
              <span className="hidden md:inline text-[10px] text-muted/70 italic whitespace-nowrap">
                from {dialectHint}
              </span>
            )}
          </div>
        </div>
      )}

      <div className="rounded-lg border border-border overflow-hidden" style={{ height }}>
        <Editor
          height={height}
          language="sql"
          theme={monacoTheme}
          value={value}
          onChange={handleChange}
          onMount={handleMount}
          options={{
            readOnly,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            fontSize: 13,
            lineNumbers: 'on',
            wordWrap: 'on',
            tabSize: 2,
            automaticLayout: true,
            padding: { top: 8, bottom: 8 },
            overviewRulerLanes: 0,
            quickSuggestions: true,
            suggestOnTriggerCharacters: true,
            scrollbar: { vertical: 'auto', horizontal: 'auto' },
          }}
          loading={
            <div className="flex items-center justify-center h-full text-xs text-muted">
              Loading editor…
            </div>
          }
        />
      </div>
    </div>
  )
}
