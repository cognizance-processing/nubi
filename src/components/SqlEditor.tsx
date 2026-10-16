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
 *
 * Imperative handle (via ref)
 * ---------------------------
 *   insertSnippetAtCursor(text) — inserts `text` at the current cursor or
 *     selection (same mechanism the Templates menu uses). If `text` contains
 *     one or more literal `<column>` placeholders, every occurrence becomes
 *     a multi-cursor selection afterward, so typing a real column name once
 *     replaces all of them — used by the "Add filter parameter" affordance
 *     in the query editor to drop a guarded {% if %} snippet without the
 *     presenter hand-typing Jinja.
 */

import { useRef, useCallback, useState, useEffect, useContext, forwardRef, useImperativeHandle } from 'react'
import './app/monacoSetup'
import Editor from '@monaco-editor/react'
import { FileCode2, ChevronDown, HelpCircle, Braces, Database, Sparkles } from 'lucide-react'
import Button from './ui/Button.jsx'
import Tag from './ui/Tag.jsx'
import { Select } from './ui/Input.jsx'

import { ThemeContext } from '../contexts/ThemeContext.jsx'
import { validateSql, fetchSchema, completeSql } from '../lib/api.js'

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

/**
 * Table names already referenced in FROM/JOIN clauses (bare name or the part
 * after the last `.` for `schema.table`), lowercased. Used to rank columns
 * belonging to tables already in the query above columns from unrelated
 * tables — a lightweight, no-backend-change stand-in for real join-graph
 * awareness (the schema catalog has no FK metadata to do this properly).
 */
function referencedTables(sql) {
  const re = /\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_.]*)/gi
  const out = new Set()
  let m
  while ((m = re.exec(sql)) !== null) {
    const bare = m[1].split('.').pop()
    if (bare) out.add(bare.toLowerCase())
  }
  return out
}

/** Render a `{tables: {name: [cols]}}` schema as compact text for the LLM prompt. */
function schemaToPromptText(schema: { tables?: Record<string, string[]> } | null | undefined) {
  const tables: Record<string, string[]> = schema?.tables ?? {}
  return Object.entries(tables)
    .slice(0, 40)
    .map(([t, cols]) => `${t}(${(cols || []).slice(0, 30).join(', ')})`)
    .join('\n')
}

// ---------------------------------------------------------------------------
// Templating help popover
// ---------------------------------------------------------------------------

function TemplatingHelp({ open, onClose }) {
  if (!open) return null
  return (
    <div
      className="nubi-dropdown absolute right-0 top-full mt-1.5 z-40 w-72 p-3 text-xs text-fg"
      onMouseLeave={onClose}
    >
      <p className="font-semibold mb-1.5 flex items-center gap-1.5">
        <HelpCircle size={13} aria-hidden="true" />
        Templates &amp; params
      </p>
      <p className="text-muted leading-relaxed">
        A <span className="font-medium text-fg">template</span> drops a ready-made starter
        query at your cursor — pick one, then tweak it.
      </p>
      <p className="text-muted leading-relaxed mt-2">
        Use <code className="px-1 py-0.5 bg-surface-2 font-mono" style={{ borderRadius: 2 }}>{'{{name}}'}</code> to
        declare a <span className="font-medium text-fg">named parameter</span>. When the
        query is saved and used on a dashboard, each placeholder becomes a bindable
        param (filters / variables).
      </p>
      <p className="text-muted leading-relaxed mt-2">
        Example:{' '}
        <code className="px-1 py-0.5 bg-surface-2 font-mono" style={{ borderRadius: 2 }}>
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
      <Button
        type="button"
        variant="secondary"
        size="xs"
        onClick={() => setOpen(o => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        title="Insert a starter query"
      >
        <FileCode2 size={13} aria-hidden="true" />
        Templates
        <ChevronDown size={12} aria-hidden="true" className={open ? 'rotate-180' : ''} />
      </Button>

      {open && (
        <div className="nubi-dropdown absolute left-0 top-full mt-1.5 z-30 w-80 p-0 overflow-visible">
          <div className="flex items-center justify-between px-3 py-2 border-b border-border">
            <span className="t-label">Starter queries</span>
            <button
              type="button"
              onMouseEnter={() => setHelpOpen(true)}
              onClick={() => setHelpOpen(o => !o)}
              className="inline-flex items-center gap-1 text-[11px] font-medium text-muted hover:text-fg transition-colors"
              title="What are templates & params?"
            >
              <HelpCircle size={12} aria-hidden="true" />
              What&apos;s this?
            </button>
          </div>
          <ul role="menu" className="max-h-80 overflow-y-auto py-1 m-0 list-none">
            {QUERY_TEMPLATES.map((tpl, i) => {
              const ps = templateParams(tpl.sql)
              return (
                <li key={i} role="none">
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => { onInsert(tpl.sql); setOpen(false); setHelpOpen(false) }}
                    className="w-full text-left px-3 py-2 hover:bg-frame-hover focus-visible:bg-frame-hover nubi-focus-inset transition-colors"
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="text-[13px] font-semibold text-fg">{tpl.label}</span>
                      {ps.length > 0 && (
                        <Tag className="shrink-0">
                          <Braces size={10} aria-hidden="true" />
                          {ps.length} param{ps.length > 1 ? 's' : ''}
                        </Tag>
                      )}
                    </span>
                    <span className="block text-xs text-muted mt-0.5 leading-snug">{tpl.description}</span>
                    {ps.length > 0 && (
                      <span className="flex flex-wrap gap-1 mt-1.5">
                        {ps.map(p => (
                          <code key={p} className="px-1 border border-border font-mono text-[11px] text-muted" style={{ borderRadius: 2 }}>
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
          <div className="px-3 py-2 border-t border-border">
            <p className="text-[11px] text-muted leading-relaxed m-0">
              Inserts at your cursor. Use{' '}
              <code className="font-mono text-[11px] text-fg">{'{{name}}'}</code>{' '}
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

// Providers are registered once per page. (Not flagged on the monaco namespace:
// the locally bundled build exposes it as a frozen ES module.)
const registeredProviders = new Set<string>()

function SqlEditor({
  value,
  onChange = undefined,
  readOnly = false,
  height = '200px',
  onRun = undefined,
  toolbar = true,
  dialect: dialectProp = undefined,
  onDialectChange = undefined,
  dialectHint = undefined,
  schema: schemaProp = undefined,
}, ref) {
  // Theme — null-safe read so this renders outside a ThemeProvider without a
  // conditional hook call; degrades to 'light'.
  const theme = useContext(ThemeContext)?.theme ?? 'light'
  const monacoTheme = theme === 'dark' ? 'vs-dark' : 'light'

  // Dialect — controlled when dialectProp is provided, else internal state.
  const [dialectState, setDialectState] = useState(dialectProp ?? 'duckdb')
  const dialect = dialectProp ?? dialectState
  const setDialect = useCallback((d) => {
    setDialectState(d)
    onDialectChange?.(d)
  }, [onDialectChange])

  // Schema for autocomplete — use prop if given, otherwise fetch once.
  const [schema, setSchema] = useState<{ tables: Record<string, string[]> }>(schemaProp ?? { tables: {} })
  useEffect(() => {
    if (schemaProp) { setSchema(schemaProp); return }
    let alive = true
    fetchSchema().then(s => { if (alive) setSchema(s) })
    return () => { alive = false }
  }, [schemaProp])

  // Monaco is loaded from a CDN by @monaco-editor/react. If it has not mounted
  // after 4s (offline, blocked, slow), fall back to a plain textarea so the
  // user can always write and run SQL. If Monaco arrives later it takes over.
  const [monacoReady, setMonacoReady] = useState(false)
  const [loadTimedOut, setLoadTimedOut] = useState(false)
  useEffect(() => {
    if (monacoReady) return
    const t = setTimeout(() => setLoadTimedOut(true), 4000)
    return () => clearTimeout(t)
  }, [monacoReady])
  const showFallback = loadTimedOut && !monacoReady
  const fallbackRef = useRef<HTMLTextAreaElement | null>(null)
  const escapedRef = useRef(false) // Esc then Tab leaves the textarea (keyboard-trap escape hatch)

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
    setMonacoReady(true)

    // Ctrl/Cmd+Enter → run
    if (onRun) {
      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => onRun())
    }

    // Register a schema-aware (static) completion provider ONCE per monaco instance.
    if (!registeredProviders.has('completion')) {
      registeredProviders.add('completion')
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
          // Tables already in this query's FROM/JOIN clauses — their columns
          // are far more likely to be what the user wants next, so rank them
          // first. (No FK/relationship metadata is available from the schema
          // catalog, so this is text-proximity, not a real join graph.)
          const inScope = referencedTables(model.getValue())

          // Keywords
          for (const kw of SQL_KEYWORDS) {
            suggestions.push({
              label: kw,
              kind: monaco.languages.CompletionItemKind.Keyword,
              insertText: kw,
              sortText: `3_${kw}`,
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
              sortText: `1_${t}`,
              range,
            })
          }
          // Columns (deduped, with owning-table detail) — columns of a table
          // already referenced in the query sort ahead of everything else.
          const seen = new Set()
          for (const [t, cols] of Object.entries(tables)) {
            const referenced = inScope.has(t.toLowerCase())
            for (const c of cols ?? []) {
              const key = `${c}`
              if (seen.has(key)) continue
              seen.add(key)
              suggestions.push({
                label: c,
                kind: monaco.languages.CompletionItemKind.Field,
                insertText: c,
                detail: `column · ${t}`,
                sortText: `${referenced ? '0' : '2'}_${c}`,
                range,
              })
            }
          }
          return { suggestions }
        },
      })
    }

    // Register an AI inline-completion ("ghost text") provider ONCE per monaco
    // instance. POST /query/complete has no cost ceiling/quota check on the
    // backend (unlike the chat endpoints), so this only ever fires on an
    // EXPLICIT trigger (Alt+\, or the toolbar button below) — never
    // automatically as the user types, which would call the LLM on every
    // pause with no budget guard.
    if (!registeredProviders.has('inline')) {
      registeredProviders.add('inline')
      monaco.languages.registerInlineCompletionsProvider('sql', {
        provideInlineCompletions: async (model, position, context) => {
          if (context.triggerKind !== monaco.languages.InlineCompletionTriggerKind.Explicit) {
            return { items: [] }
          }
          const value = model.getValue()
          if (!value.trim()) return { items: [] }
          const { suggestion } = await completeSql({
            sql: value,
            cursor: model.getOffsetAt(position),
            schema: schemaToPromptText(schemaRef.current),
          })
          if (!suggestion) return { items: [] }
          return {
            items: [{
              insertText: suggestion,
              range: new monaco.Range(position.lineNumber, position.column, position.lineNumber, position.column),
            }],
          }
        },
        freeInlineCompletions: () => {},
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
  const insertIntoFallback = useCallback((text) => {
    const ta = fallbackRef.current
    if (!ta) { onChange?.(text); return }
    const from = ta.selectionStart ?? ta.value.length
    const to = ta.selectionEnd ?? from
    const next = ta.value.slice(0, from) + text + ta.value.slice(to)
    onChange?.(next)
    requestAnimationFrame(() => {
      ta.focus()
      const ph = text.indexOf('<column>')
      if (ph >= 0) ta.setSelectionRange(from + ph, from + ph + '<column>'.length)
      else ta.setSelectionRange(from + text.length, from + text.length)
    })
  }, [onChange])

  const insertTemplate = useCallback((sql) => {
    const editor = editorRef.current
    if (!editor) { insertIntoFallback(sql); return }
    const selection = editor.getSelection()
    editor.executeEdits('insert-template', [{ range: selection, text: sql, forceMoveMarkers: true }])
    editor.focus()
  }, [onChange, insertIntoFallback])

  // ── Imperative handle: insert a snippet at the cursor, multi-selecting
  //    every `<column>` placeholder it contains ───────────────────────────
  useImperativeHandle(ref, () => ({
    insertSnippetAtCursor(text) {
      const editor = editorRef.current
      const monaco = monacoRef.current
      if (!editor) { insertIntoFallback(text); return }

      const selection = editor.getSelection()
      const startLine = selection.startLineNumber
      const startCol = selection.startColumn
      editor.executeEdits('insert-filter-param', [{ range: selection, text, forceMoveMarkers: true }])
      editor.focus()

      if (!monaco) return
      const model = editor.getModel()
      if (!model) return
      const insertStartOffset = model.getOffsetAt({ lineNumber: startLine, column: startCol })
      const placeholderRe = /<column>/g
      const selections = []
      let m
      while ((m = placeholderRe.exec(text)) !== null) {
        const from = model.getPositionAt(insertStartOffset + m.index)
        const to = model.getPositionAt(insertStartOffset + m.index + m[0].length)
        selections.push(new monaco.Selection(from.lineNumber, from.column, to.lineNumber, to.column))
      }
      if (selections.length > 0) {
        editor.setSelections(selections)
        editor.revealRangeInCenter(selections[0])
      }
    },
  }), [onChange, insertIntoFallback])

  return (
    <div className="flex flex-col gap-2">
      {toolbar && (
        <div className="flex items-center gap-2 flex-wrap">
          <TemplatesMenu onInsert={insertTemplate} />
          <Button
            type="button"
            variant="ghost"
            size="xs"
            onClick={() => {
              editorRef.current?.focus()
              editorRef.current?.trigger('nubi', 'editor.action.inlineSuggest.trigger', {})
            }}
            disabled={showFallback}
            title="Suggest what comes next, from your schema (Alt+\)"
          >
            <Sparkles size={13} aria-hidden="true" />
            AI suggest
            <kbd className="nubi-kbd hidden md:inline-flex">Alt \</kbd>
          </Button>

          <div className="flex-1" />

          <div className="inline-flex items-center gap-2">
            <label htmlFor="sql-dialect" className="inline-flex items-center gap-1.5 t-label">
              <Database size={12} aria-hidden="true" />
              Dialect
            </label>
            <Select
              id="sql-dialect"
              size="xs"
              value={dialect}
              onChange={e => setDialect(e.target.value)}
              options={SQL_DIALECTS}
              title={dialectHint ? `Auto-detected ${dialectHint}. You can override it here.` : 'SQL dialect for validation and hints'}
              className="w-32"
            />
            {dialectHint && (
              <span className="hidden md:inline text-[11px] text-subtle whitespace-nowrap">
                from {dialectHint}
              </span>
            )}
          </div>
        </div>
      )}

      <div className="overflow-hidden border border-frame-line" style={{ height, borderRadius: 2 }}>
        {showFallback && (
          <div className="flex h-full flex-col">
            <p className="m-0 shrink-0 border-b border-border bg-surface-2 px-3 py-1.5 text-xs text-muted" role="status">
              The code editor is taking a while to load. You can keep writing here. Autocomplete and syntax checks switch on when it arrives.
            </p>
            <textarea
              ref={fallbackRef}
              value={value}
              onChange={(e) => onChange?.(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); onRun?.() }
                // Tab inserts two spaces instead of leaving the field (Esc then Tab moves on).
                if (e.key === 'Tab' && !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey && !e.nativeEvent.isComposing) {
                  if (escapedRef.current) { escapedRef.current = false; return }
                  e.preventDefault()
                  const ta = e.currentTarget
                  const from = ta.selectionStart, to = ta.selectionEnd
                  onChange?.(ta.value.slice(0, from) + '  ' + ta.value.slice(to))
                  requestAnimationFrame(() => ta.setSelectionRange(from + 2, from + 2))
                }
                if (e.key === 'Escape') escapedRef.current = true
              }}
              readOnly={readOnly}
              spellCheck={false}
              aria-label="SQL"
              className="min-h-0 w-full flex-1 resize-none bg-surface p-3 text-[13px] leading-relaxed text-fg outline-none"
              style={{ fontFamily: 'var(--font-mono)', tabSize: 2 }}
            />
          </div>
        )}
        <div style={{ height: showFallback ? 0 : height, visibility: showFallback ? 'hidden' : 'visible' }}>
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
              fontFamily: "'Geist Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
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
              <div className="flex h-full items-center justify-center text-xs text-muted" role="status">
                Loading editor
              </div>
            }
          />
        </div>
      </div>
    </div>
  )
}

const ForwardedSqlEditor = forwardRef(SqlEditor)
ForwardedSqlEditor.displayName = 'SqlEditor'
export default ForwardedSqlEditor
