/**
 * shared/OptionsQueryBuilder.tsx — build a dropdown's list of choices from a
 * connector's own catalog, without leaving the inspector.
 *
 * A `select` / `multiselect` / `list` filter needs an "options query": one
 * returning `value, label` per choice. `QueryPicker` can only PICK one, so a
 * filter on a column nobody had already built a lookup for was a dead end
 * inside the editor — leave, open the query workspace, write
 * `SELECT DISTINCT col AS value, col AS label FROM t`, come back, find it in
 * the picker, bind it. Five context switches for one line of SQL, and only if
 * you already knew about the value/label contract.
 *
 * This is that line of SQL, as three dropdowns. The server generates it in the
 * target connector's own dialect, RUNS it, and returns the values it produced,
 * which are shown before anything is saved — so "did I pick the right column?"
 * is answered by looking at the actual choices rather than by saving and
 * checking the board.
 *
 * Props:
 *   datastoreId  string|null   — connector to preselect (null = demo dataset)
 *   suggestion   {table, column, schema}|null — a starting point, e.g. the
 *                column a sibling widget's query already filters on
 *   onCreated    (row)=>void   — { id, name, sql, sample, count, created }
 *   onCancel     ()=>void
 */

import { useEffect, useMemo, useState } from 'react'
import { Loader2, Database, Table2, Columns3, AlertTriangle, Check } from 'lucide-react'
import { selectCls, FieldLabel } from './inspectorPrimitives.jsx'
import {
  listConnectors, listDataTables, fetchDataColumns, createOptionsQuery,
} from '../../lib/api.js'

/** The built-in dataset every org starts with; addressed as "no datastore". */
const DEMO_OPTION = { id: '', name: 'Demo dataset (built-in)' }

/** The virtual demo connector's id — already covered by DEMO_OPTION. */
const DEMO_SENTINEL = '__demo__' 

/** Column types that cannot sensibly back a dropdown of choices. */
const UNPICKABLE_TYPES = /^(json|jsonb|array|struct|map|record|geography|geometry|bytea|blob|binary)/i

function isPickable(col) {
  const t = String(col?.type ?? '')
  return !UNPICKABLE_TYPES.test(t.trim())
}

export function OptionsQueryBuilder({
  datastoreId = null,
  suggestion = null,
  onCreated = undefined,
  onCancel = undefined,
}) {
  const [connectors, setConnectors] = useState([])
  const [datastore, setDatastore] = useState(datastoreId ?? '')
  // Once the author picks a connector themselves, a late-arriving suggestion
  // must not yank it back.
  const [datastoreTouched, setDatastoreTouched] = useState(false)
  const [tables, setTables] = useState(null)      // null = not loaded yet
  const [table, setTable] = useState(suggestion?.table ?? '')
  const [columns, setColumns] = useState(null)
  const [column, setColumn] = useState(suggestion?.column ?? '')
  const [loadingTables, setLoadingTables] = useState(false)
  const [loadingColumns, setLoadingColumns] = useState(false)
  const [preview, setPreview] = useState(null)    // server's dry run
  const [previewing, setPreviewing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const schema = useMemo(
    () => tables?.find(t => t.name === table)?.schema ?? null,
    [tables, table],
  )

  // The suggested connector is read from the query registry, which loads
  // asynchronously — so it commonly arrives AFTER this mounts. Without this the
  // builder sits on the demo dataset while the board's data is somewhere else.
  useEffect(() => {
    if (!datastoreTouched && datastoreId && datastoreId !== datastore) {
      setDatastore(datastoreId)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datastoreId])

  /**
   * Connectors to offer, always including the one currently selected.
   *
   * `listConnectors()` deliberately hides some datastores a query can still be
   * bound to — seeded `system: true` rows are presented as the branded demo
   * connector rather than as their own card. A <select> whose value is not
   * among its <option>s silently displays the FIRST option instead, so without
   * this the builder would look like it was targeting the demo dataset while
   * the board's data lived elsewhere, and the query would be created against
   * the wrong connector.
   */
  const connectorOptions = useMemo(() => {
    const rows = [DEMO_OPTION, ...connectors.filter(c => c?.id && c.id !== DEMO_SENTINEL)]
    if (datastore && !rows.some(r => r.id === datastore)) {
      rows.push({ id: datastore, name: `This board’s connector · ${datastore.slice(0, 8)}…` })
    }
    return rows
  }, [connectors, datastore])

  // ── Connectors ───────────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    listConnectors()
      .then(rows => { if (!cancelled) setConnectors(Array.isArray(rows) ? rows : []) })
      .catch(() => { if (!cancelled) setConnectors([]) })
    return () => { cancelled = true }
  }, [])

  // ── Tables for the chosen connector ──────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    setLoadingTables(true)
    setTables(null)
    listDataTables(datastore || null)
      .then(rows => { if (!cancelled) setTables(rows) })
      .catch(err => { if (!cancelled) { setTables([]); setError(err?.message ?? 'Could not list tables.') } })
      .finally(() => { if (!cancelled) setLoadingTables(false) })
    return () => { cancelled = true }
  }, [datastore])

  // Keep a table selected only while it still exists on this connector.
  useEffect(() => {
    if (tables && table && !tables.some(t => t.name === table)) {
      setTable('')
      setColumn('')
    }
  }, [tables, table])

  // ── Columns for the chosen table ─────────────────────────────────────────
  useEffect(() => {
    if (!table) { setColumns(null); return undefined }
    let cancelled = false
    setLoadingColumns(true)
    setColumns(null)
    fetchDataColumns(datastore || null, table, { schema })
      .then(res => {
        if (cancelled) return
        const cols = Array.isArray(res?.columns) ? res.columns : []
        setColumns(cols)
        // Keep an explicit choice; otherwise start on the first usable column.
        setColumn(prev => (cols.some(c => c.name === prev) ? prev : (cols.find(isPickable)?.name ?? '')))
      })
      .catch(err => { if (!cancelled) { setColumns([]); setError(err?.message ?? 'Could not read columns.') } })
      .finally(() => { if (!cancelled) setLoadingColumns(false) })
    return () => { cancelled = true }
  }, [datastore, table, schema])

  // ── Dry run: show the real choices before anything is saved ──────────────
  useEffect(() => {
    if (!table || !column) { setPreview(null); return undefined }
    let cancelled = false
    setPreviewing(true)
    setError(null)
    createOptionsQuery({
      table, column, schema, datastore_id: datastore || null, apply: false,
    })
      .then(res => { if (!cancelled) setPreview(res) })
      .catch(err => { if (!cancelled) { setPreview(null); setError(err?.message ?? 'Could not build the query.') } })
      .finally(() => { if (!cancelled) setPreviewing(false) })
    return () => { cancelled = true }
  }, [datastore, table, column, schema])

  const create = async () => {
    setSaving(true)
    setError(null)
    try {
      const res = await createOptionsQuery({
        table, column, schema, datastore_id: datastore || null, apply: true,
      })
      if (!res?.ok || !res?.id) {
        setError(res?.reason ?? 'The query could not be created.')
        return
      }
      onCreated?.(res)
    } catch (err) {
      setError(err?.message ?? 'The query could not be created.')
    } finally {
      setSaving(false)
    }
  }

  const canCreate = Boolean(table && column && preview?.ok && !previewing && !saving)

  return (
    <div
      className="rounded-lg border border-border bg-surface-2/40 p-2.5 space-y-2.5"
      data-testid="options-query-builder"
    >
      <p className="text-[10px] text-muted leading-relaxed">
        Lists the distinct values of one column, as <span className="font-mono">value, label</span>
        {' '}— the shape a dropdown needs.
      </p>

      <div>
        <FieldLabel className="flex items-center gap-1.5"><Database size={11} /> Connector</FieldLabel>
        <select
          className={selectCls}
          value={datastore}
          aria-label="Connector"
          onChange={e => {
            setDatastoreTouched(true)
            setDatastore(e.target.value)
            setTable('')
            setColumn('')
          }}
        >
          {connectorOptions.map(c => (
            <option key={c.id || '__none__'} value={c.id}>{c.name || c.id}</option>
          ))}
        </select>
      </div>

      <div>
        <FieldLabel className="flex items-center gap-1.5"><Table2 size={11} /> Table</FieldLabel>
        {loadingTables ? (
          <p className="text-[10px] text-muted flex items-center gap-1">
            <Loader2 size={10} className="animate-spin" /> Reading this connector’s catalog…
          </p>
        ) : (tables?.length ?? 0) === 0 ? (
          <p className="text-[10px] text-muted/70">No tables found on this connector.</p>
        ) : (
          <select
            className={selectCls}
            value={table}
            aria-label="Table"
            onChange={e => { setTable(e.target.value); setColumn('') }}
          >
            <option value="" disabled>Choose a table…</option>
            {tables.map(t => (
              <option key={`${t.schema ?? ''}.${t.name}`} value={t.name}>
                {t.schema ? `${t.schema}.${t.name}` : t.name}
              </option>
            ))}
          </select>
        )}
      </div>

      {table && (
        <div>
          <FieldLabel className="flex items-center gap-1.5"><Columns3 size={11} /> Column</FieldLabel>
          {loadingColumns ? (
            <p className="text-[10px] text-muted flex items-center gap-1">
              <Loader2 size={10} className="animate-spin" /> Reading columns…
            </p>
          ) : (columns?.length ?? 0) === 0 ? (
            <p className="text-[10px] text-muted/70">No columns found on this table.</p>
          ) : (
            <select
              className={selectCls}
              value={column}
              aria-label="Column"
              onChange={e => setColumn(e.target.value)}
            >
              <option value="" disabled>Choose a column…</option>
              {columns.filter(isPickable).map(c => (
                <option key={c.name} value={c.name}>
                  {c.name}{c.type ? ` · ${c.type}` : ''}
                </option>
              ))}
            </select>
          )}
        </div>
      )}

      {/* ── What you would actually get ────────────────────────────────────── */}
      {previewing && (
        <p className="text-[10px] text-muted flex items-center gap-1" data-testid="options-preview-loading">
          <Loader2 size={10} className="animate-spin" /> Running it…
        </p>
      )}

      {!previewing && preview?.ok && (
        <div className="space-y-1.5" data-testid="options-preview">
          <div className="rounded-lg border border-border bg-surface px-2 py-1.5">
            <p className="text-[9px] uppercase tracking-wide text-muted/60 mb-1">
              {preview.count} {preview.count === 1 ? 'choice' : 'choices'}
            </p>
            <div className="flex flex-wrap gap-1">
              {(preview.sample ?? []).slice(0, 8).map((v, i) => (
                <span key={i} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-surface-2 text-fg truncate max-w-[140px]">
                  {String(v)}
                </span>
              ))}
              {preview.count > 8 && (
                <span className="text-[10px] text-muted/60 self-center">+{preview.count - 8} more</span>
              )}
            </div>
          </div>
          {(preview.warnings ?? []).map((w, i) => (
            <p key={i} className="text-[10px] text-warning/90 flex items-start gap-1 leading-relaxed">
              <AlertTriangle size={10} className="mt-px flex-none" /> {w}
            </p>
          ))}
          <details className="group">
            <summary className="text-[10px] text-muted/60 hover:text-muted cursor-pointer list-none focus-visible:outline-none focus-visible:underline">
              Show the SQL
            </summary>
            <pre className="mt-1 text-[9px] font-mono text-muted whitespace-pre-wrap break-all bg-surface rounded-lg border border-border p-1.5">
              {preview.sql}
            </pre>
          </details>
        </div>
      )}

      {!previewing && preview && !preview.ok && (
        <p className="text-[10px] text-danger leading-relaxed" data-testid="options-preview-error">
          {preview.reason}
        </p>
      )}
      {error && <p className="text-[10px] text-danger leading-relaxed">{error}</p>}

      <div className="flex items-center gap-1.5 pt-0.5">
        <button
          type="button"
          onClick={create}
          disabled={!canCreate}
          data-testid="options-create-btn"
          className="h-7 px-2.5 text-[11px] font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity inline-flex items-center gap-1"
        >
          {saving ? <Loader2 size={10} className="animate-spin" /> : <Check size={11} />}
          {saving ? 'Creating…' : 'Create & use'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="h-7 px-2.5 text-[11px] text-muted hover:text-fg border border-border rounded-lg bg-surface hover:bg-surface-2 transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}
