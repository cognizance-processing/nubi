/**
 * QueryCodeView.jsx — VS Code-style "code / files" view for a query.
 *
 * A full-pane view (mirrors src/flows/FlowCodeView.jsx) that projects a query as
 * the 3-file on-disk shape from docs/files-as-code.md §A:
 *   • <slug>.sql        — the raw SQL (authoritative source). Editable; edits
 *                         write straight back to the query via onSqlChange,
 *                         exactly the path the SqlEditor uses (language=sql).
 *   • <slug>.meta.json  — read-only sidecar: { id, name, datastore_id, params,
 *                         output_schema? }. Mirrors `<slug>.meta.json` on disk
 *                         (everything bar the SQL itself).
 *
 * SQL is the only editable file — params are derived from `{{placeholders}}` in
 * the SQL by QueryWorkspace, and id/name/datastore are edited via the toolbar,
 * so the meta sidecar is a faithful read-only projection.
 *
 * Props:
 *   sql          {string}     — current SQL
 *   params       {Array}      — param descriptors
 *   datastoreId  {string|null}— bound connector id
 *   query        {object}     — { id?, name, output_schema? }
 *   onSqlChange  {Function}   — called with the new SQL string on every edit
 */

import { useState, useCallback, useMemo } from 'react'
import Editor from '@monaco-editor/react'
import {
  Database,
  FileJson,
  Copy,
  Check,
  FolderTree,
} from 'lucide-react'
import { useTheme } from '../../contexts/ThemeContext.jsx'

const SQL_ID = '__query_sql__'
const META_ID = '__query_meta__'

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

/** Slugify a name the same way portability.slug_for_envelope does. */
function slugify(s) {
  return (
    (s || 'query')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/(^-|-$)/g, '') || 'query'
  )
}

/** Build the `<slug>.meta.json` sidecar (everything bar the raw SQL). */
function buildMeta({ query, params, datastoreId }) {
  const meta = {
    id: query?.id ?? null,
    name: query?.name ?? 'New query',
    datastore_id: datastoreId || null,
    params: Array.isArray(params)
      ? params.map(p => ({
          name: p.name,
          type: p.type ?? 'text',
          default: p.default ?? null,
          required: p.required ?? false,
          ...(p.options_query_id ? { options_query_id: p.options_query_id } : {}),
        }))
      : [],
  }
  if (query?.output_schema) meta.output_schema = query.output_schema
  return meta
}

function FileRow({ Icon, name, active, dirty, readOnly, onSelect }) {
  return (
    <button
      onClick={onSelect}
      className={[
        'group w-full flex items-center gap-2 px-2 py-1 text-left text-xs rounded-md transition-colors',
        active ? 'bg-primary/10 text-fg' : 'text-muted hover:text-fg hover:bg-surface-2',
      ].join(' ')}
      title={name}
    >
      <Icon size={13} className={active ? 'text-primary shrink-0' : 'text-muted/70 shrink-0'} />
      <span className="truncate flex-1">{name}</span>
      {dirty && <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" title="Unsaved edits" />}
      {readOnly && <span className="text-[9px] text-muted/60 shrink-0">ro</span>}
    </button>
  )
}

export default function QueryCodeView({ sql, params, datastoreId, query, onSqlChange }) {
  const { theme } = useTheme()
  const monacoTheme = theme === 'dark' ? 'vs-dark' : 'light'

  const [selectedId, setSelectedId] = useState(SQL_ID)
  const [copied, setCopied] = useState(false)

  const slug = slugify(query?.name)
  const sqlName = `${slug}.sql`
  const metaName = `${slug}.meta.json`

  const metaJson = useMemo(
    () => JSON.stringify(buildMeta({ query, params, datastoreId }), null, 2),
    [query, params, datastoreId],
  )

  const isSql = selectedId === SQL_ID
  const activeName = isSql ? sqlName : metaName
  const activeSource = isSql ? (sql ?? '') : metaJson

  const handleCopy = useCallback(() => {
    if (!activeSource) return
    navigator.clipboard?.writeText(activeSource).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    })
  }, [activeSource])

  return (
    <div className="flex h-full overflow-hidden bg-bg">

      {/* ── File explorer ─────────────────────────────────────────────────── */}
      <aside className="w-56 shrink-0 flex flex-col border-r border-border bg-surface-2/30 overflow-hidden">
        <div className="shrink-0 flex items-center gap-2 px-3 py-2.5 border-b border-border">
          <FolderTree size={13} className="text-muted" />
          <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">Explorer</span>
        </div>
        <div className="flex-1 overflow-y-auto py-2 px-1.5 space-y-0.5">
          <FileRow
            Icon={Database}
            name={sqlName}
            active={isSql}
            dirty={false}
            readOnly={false}
            onSelect={() => setSelectedId(SQL_ID)}
          />
          <FileRow
            Icon={FileJson}
            name={metaName}
            active={!isSql}
            dirty={false}
            readOnly
            onSelect={() => setSelectedId(META_ID)}
          />
        </div>
        <div className="shrink-0 px-3 py-2 border-t border-border">
          <p className="text-[10px] leading-snug text-muted/70">
            Edit <code className="text-muted">{sqlName}</code> — params, datastore &amp; output schema are in the read-only sidecar.
          </p>
        </div>
      </aside>

      {/* ── Editor pane ───────────────────────────────────────────────────── */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">

        {/* Active-file tab bar */}
        <div className="shrink-0 flex items-center justify-between gap-2 px-3 py-2 border-b border-border bg-surface">
          <div className="flex items-center gap-2 min-w-0">
            {isSql
              ? <Database size={13} className="text-blue-500 shrink-0" />
              : <FileJson size={13} className="text-amber-500 shrink-0" />}
            <span className="text-xs font-medium text-fg truncate">{activeName}</span>
            {!isSql && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-2 border border-border text-muted">read-only</span>
            )}
          </div>

          <div className="flex items-center gap-1 shrink-0">
            {activeSource && (
              <button
                onClick={handleCopy}
                className="flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md border border-border bg-surface-2 hover:bg-surface text-fg transition-colors"
                title="Copy to clipboard"
              >
                {copied ? <Check size={11} className="text-green-500" /> : <Copy size={11} />}
                {copied ? 'Copied!' : 'Copy'}
              </button>
            )}
          </div>
        </div>

        {/* Editor body */}
        <div className="flex-1 min-h-0 overflow-hidden">
          {isSql ? (
            <Editor
              key={SQL_ID}
              language="sql"
              value={sql ?? ''}
              onChange={(val) => onSqlChange?.(val ?? '')}
              theme={monacoTheme}
              options={{ ...MONACO_OPTIONS, readOnly: false, contextmenu: true }}
            />
          ) : (
            <Editor
              key={META_ID}
              language="json"
              value={metaJson}
              theme={monacoTheme}
              options={{ ...MONACO_OPTIONS, readOnly: true, contextmenu: false }}
            />
          )}
        </div>
      </div>
    </div>
  )
}
