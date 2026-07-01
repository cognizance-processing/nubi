/**
 * ActionWidget.jsx — Governed action/approval widget for the SpecRenderer.
 *
 * Renders recommendation rows with Approve / Reject / Edit controls that
 * POST to the write-back routes (dry-run preview first, then commit on
 * approve). Shows signal-coloured status indicators.
 *
 * Widget spec shape
 * -----------------
 * {
 *   type: 'action',
 *   query_id: string,          // optional: load recommendation rows from a query
 *   props: {
 *     label?: string,          // widget heading
 *     target: {                // REQUIRED: write-back target
 *       connector_id: string,
 *       object: string,
 *     },
 *     mode?: 'append'|'overwrite'|'merge',
 *     approval_required?: boolean,
 *     flow_id?: string,        // optional: flow to re-trigger on success
 *     columns?: string[],      // visible columns in the row display
 *     idempotency_prefix?: string,  // prefix for auto-generated idempotency keys
 *     rows?: object[],         // static rows (alternative to query_id)
 *   },
 *   params?: object,
 * }
 *
 * Route contract (A2 write-back)
 * ------------------------------
 *   POST /api/flows/writeback/preview   { idempotency_key, rows, target, mode, dry_run:false }
 *     → { rows, row_count, target_object, mode, dry_run:true }
 *   POST /api/flows/writeback           { idempotency_key, rows, target, mode, approval_required }
 *     → write-back record { id, state, rows, ... }
 *   POST /api/flows/writeback/{id}/approval  { action:'approve'|'reject'|'edit', rows_override? }
 *     → updated write-back record
 *
 * RBAC / security contract
 * ------------------------
 * - Viewing the widget (rendering recommendation rows) is FREE — no server call.
 * - Only the write-back action (approve/reject) calls the server.
 * - The Bearer token is taken from window.__NUBI_TOKEN__ or the Authorization
 *   header that the embedding shell sets.  RLS is enforced on the server; the
 *   widget only forwards the token — it NEVER constructs or modifies it.
 * - 'viewer' role requests are rejected by the server with 403; the widget
 *   surfaces the error message gracefully without retrying.
 */

import { useState, useEffect, useMemo, useRef } from 'react'
import { Loader2 } from 'lucide-react'
import { runArrowQueryById } from '../../lib/wasmRuntime.js'
import { useResolvedParams } from '../VariableStore.jsx'
import { useRefreshEpoch } from '../RefreshContext.jsx'
import { applySignal } from '../../viz/signals.js'
import Button from '../../components/ui/Button.jsx'
import Badge from '../../components/ui/Badge.jsx'
import Skeleton from '../../components/ui/Skeleton.jsx'
import EmptyState from '../../components/ui/EmptyState.jsx'

// ---------------------------------------------------------------------------
// Status colours — signal-coloured per state
// ---------------------------------------------------------------------------

const STATUS_CONFIG = {
  pending_approval: { label: 'Pending',  variant: 'warning' },
  committed:        { label: 'Approved', variant: 'success' },
  rejected:         { label: 'Rejected', variant: 'danger'  },
  failed:           { label: 'Failed',   variant: 'danger'  },
  preview:          { label: 'Preview',  variant: 'primary' },
  idle:             { label: '',         variant: 'default' },
}

function StatusBadge({ state }) {
  const cfg = STATUS_CONFIG[state] ?? STATUS_CONFIG.idle
  if (!cfg.label) return null
  return <Badge variant={cfg.variant} size="sm">{cfg.label}</Badge>
}

// ---------------------------------------------------------------------------
// Auth helpers
// ---------------------------------------------------------------------------

/**
 * Retrieve the auth token from the global injected variable (set by the
 * embedding shell or DashboardViewPage) or from sessionStorage / localStorage.
 * The widget NEVER reads or derives org/user claims from the token — it forwards
 * it opaquely to the server which enforces RLS.
 */
function getAuthToken() {
  if (typeof window !== 'undefined') {
    if (window.__NUBI_TOKEN__) return window.__NUBI_TOKEN__
    const ss = typeof sessionStorage !== 'undefined' && sessionStorage.getItem('nubi_token')
    if (ss) return ss
    const ls = typeof localStorage !== 'undefined' && localStorage.getItem('nubi_token')
    if (ls) return ls
  }
  return null
}

function authHeaders() {
  const token = getAuthToken()
  return token
    ? { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
    : { 'Content-Type': 'application/json' }
}

// ---------------------------------------------------------------------------
// API helpers — all network calls go through here
// ---------------------------------------------------------------------------

const API_BASE = '/api'

async function fetchJson(url, options = {}) {
  const res = await fetch(url, { ...options, headers: { ...authHeaders(), ...(options.headers ?? {}) } })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const msg = body?.detail || body?.message || `HTTP ${res.status}`
    throw new Error(msg)
  }
  return body
}

/**
 * POST /flows/writeback/preview — dry-run, returns diff without committing.
 */
async function previewWriteback({ idempotency_key, rows, target, mode }) {
  return fetchJson(`${API_BASE}/flows/writeback/preview`, {
    method: 'POST',
    body: JSON.stringify({ idempotency_key, rows, target, mode, dry_run: false }),
  })
}

/**
 * POST /flows/writeback — submit the write-back (creates a record).
 */
async function submitWriteback({ idempotency_key, rows, target, mode, approval_required, meta }) {
  return fetchJson(`${API_BASE}/flows/writeback`, {
    method: 'POST',
    body: JSON.stringify({
      idempotency_key,
      rows,
      target,
      mode: mode ?? 'append',
      approval_required: approval_required ?? false,
      meta: meta ?? {},
    }),
  })
}

/**
 * POST /flows/writeback/{id}/approval — approve / reject / edit a pending record.
 */
async function approveWriteback(wb_id, { action, rows_override }) {
  return fetchJson(`${API_BASE}/flows/writeback/${wb_id}/approval`, {
    method: 'POST',
    body: JSON.stringify({ action, rows_override: rows_override ?? null }),
  })
}

/**
 * POST /api/flows/{flow_id}/trigger — re-trigger a flow on success.
 * Non-fatal: failures are silently logged.
 */
async function triggerFlow(flow_id) {
  try {
    return await fetchJson(`${API_BASE}/flows/${flow_id}/trigger`, { method: 'POST', body: '{}' })
  } catch (err) {
    console.warn('[ActionWidget] flow re-trigger failed:', err.message)
    return null
  }
}

// ---------------------------------------------------------------------------
// Row display helpers
// ---------------------------------------------------------------------------

function displayValue(val) {
  if (val == null) return <span className="text-muted/50">—</span>
  if (typeof val === 'boolean') return val ? 'Yes' : 'No'
  if (typeof val === 'object') return JSON.stringify(val)
  return String(val)
}

function pickColumns(row, columns) {
  if (!columns || columns.length === 0) return Object.keys(row)
  return columns.filter(c => c in row)
}

// ---------------------------------------------------------------------------
// Editable row state for the edit modal
// ---------------------------------------------------------------------------

function EditModal({ row, columns, onConfirm, onCancel }) {
  const cols = pickColumns(row, columns)
  const [draft, setDraft] = useState(() => ({ ...row }))
  const dialogRef = useRef(null)

  // Escape to close
  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onCancel() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onCancel])

  // Focus the dialog panel on open
  useEffect(() => {
    const t = setTimeout(() => dialogRef.current?.focus(), 50)
    return () => clearTimeout(t)
  }, [])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-label="Edit row before approving"
    >
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onCancel} />
      <div ref={dialogRef} tabIndex={-1} className="relative bg-surface border border-border rounded-2xl shadow-2xl w-full max-w-md mx-4 p-5 flex flex-col gap-4 outline-none">
        <div className="flex items-center justify-between">
          <h4 className="text-sm font-semibold text-fg">Edit before approving</h4>
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg w-7 h-7 flex items-center justify-center text-muted hover:text-fg hover:bg-surface-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <div className="space-y-3 max-h-72 overflow-y-auto">
          {cols.map(col => (
            <div key={col} className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted uppercase tracking-wide">{col}</label>
              <input
                className="w-full px-2.5 py-1.5 text-sm bg-bg border border-border rounded-lg text-fg focus:outline-none focus:ring-2 focus:ring-ring/40 transition-colors"
                value={draft[col] == null ? '' : String(draft[col])}
                onChange={e => setDraft(prev => ({ ...prev, [col]: e.target.value }))}
              />
            </div>
          ))}
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onCancel}>Cancel</Button>
          <Button variant="accent" size="sm" onClick={() => onConfirm(draft)}>Approve with edits</Button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// DryRunPreview — shows the diff before the user commits
// ---------------------------------------------------------------------------

function DryRunPreview({ diff, columns, onConfirm, onCancel }) {
  const cols = diff.rows && diff.rows.length > 0 ? pickColumns(diff.rows[0], columns) : []
  const dialogRef = useRef(null)

  // Escape to close
  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onCancel() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onCancel])

  // Focus the dialog panel on open
  useEffect(() => {
    const t = setTimeout(() => dialogRef.current?.focus(), 50)
    return () => clearTimeout(t)
  }, [])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-label="Dry-run preview"
    >
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onCancel} />
      <div ref={dialogRef} tabIndex={-1} className="relative bg-surface border border-border rounded-2xl shadow-2xl w-full max-w-lg mx-4 p-5 flex flex-col gap-4 outline-none">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h4 className="text-sm font-semibold text-fg">
              Preview — {diff.row_count} row{diff.row_count !== 1 ? 's' : ''} to write
            </h4>
            <StatusBadge state="preview" />
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg w-7 h-7 flex items-center justify-center text-muted hover:text-fg hover:bg-surface-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <p className="text-xs text-muted -mt-2">
          Target: <strong className="text-fg">{diff.target_object}</strong>
          {' · '}Mode: <strong className="text-fg">{diff.mode}</strong>
          {' '}<span className="italic">(no data committed yet)</span>
        </p>
        {diff.rows && diff.rows.length > 0 && (
          <div className="overflow-auto max-h-48 rounded-xl border border-border">
            <table className="w-full text-xs border-collapse">
              <thead className="sticky top-0 bg-surface-2">
                <tr>
                  {cols.map(c => (
                    <th key={c} className="px-2.5 py-1.5 text-left font-semibold text-muted border-b border-border whitespace-nowrap">{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {diff.rows.map((row, i) => (
                  <tr key={i} className="border-b border-border/40 last:border-0 hover:bg-surface-2/50 transition-colors">
                    {cols.map(c => (
                      <td key={c} className="px-2.5 py-1.5 text-fg">{displayValue(row[c])}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onCancel}>Cancel</Button>
          <Button variant="accent" size="sm" onClick={onConfirm}>Confirm &amp; approve</Button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Single action row
// ---------------------------------------------------------------------------

/**
 * Renders one recommendation row with Approve / Reject / Edit actions.
 *
 * State machine per row:
 *   idle → previewing (dry-run modal) → submitting → done (committed/rejected/failed)
 *   idle → editing (edit modal) → previewing → submitting → done
 */
function ActionRow({ row, index, columns, target, mode, approvalRequired, idempotencyPrefix, flowId, signalRules }) {
  const [phase, setPhase] = useState('idle')       // idle|previewing|editing|submitting
  const [wbRecord, setWbRecord] = useState(null)   // write-back record from server
  const [dryRunDiff, setDryRunDiff] = useState(null)
  const [editedRow, setEditedRow] = useState(null)  // row after edit
  const [error, setError] = useState(null)

  const idempotencyKey = `${idempotencyPrefix ?? 'aw'}-${index}-${JSON.stringify(row).slice(0, 40)}`
  const effectiveRow = editedRow ?? row
  const rowCols = pickColumns(effectiveRow, columns)

  // Apply signal rules to the first numeric value in the row
  const firstNumericVal = useMemo(() => {
    for (const col of rowCols) {
      const v = effectiveRow[col]
      if (v != null && !Number.isNaN(Number(v))) return Number(v)
    }
    return null
  }, [effectiveRow, rowCols])
  const signal = applySignal(firstNumericVal, signalRules)

  const isDone = ['committed', 'rejected', 'failed'].includes(wbRecord?.state)

  // -- Approve flow: preview → confirm → submit --
  async function handleApprove() {
    setError(null)
    setPhase('previewing')
    try {
      const diff = await previewWriteback({
        idempotency_key: idempotencyKey,
        rows: [effectiveRow],
        target,
        mode: mode ?? 'append',
      })
      setDryRunDiff(diff)
    } catch (err) {
      setError(err.message)
      setPhase('idle')
    }
  }

  async function handleConfirmPreview() {
    setDryRunDiff(null)
    setPhase('submitting')
    setError(null)
    try {
      const record = await submitWriteback({
        idempotency_key: idempotencyKey,
        rows: editedRow ? [editedRow] : [row],
        target,
        mode: mode ?? 'append',
        approval_required: approvalRequired ?? false,
      })
      setWbRecord(record)
      setPhase('idle')

      // If there's a pending_approval record and we're not the approver, stay in pending.
      // If auto-committed or already committed, re-trigger flow if configured.
      if (record.state === 'committed' && flowId) {
        triggerFlow(flowId)
      }

      // If approval_required=true the record is pending — call the approval route
      if (record.state === 'pending_approval') {
        const approved = await approveWriteback(record.id, { action: 'approve' })
        setWbRecord(approved)
        if (approved.state === 'committed' && flowId) {
          triggerFlow(flowId)
        }
      }
    } catch (err) {
      setError(err.message)
      setPhase('idle')
    }
  }

  // -- Reject --
  async function handleReject() {
    setError(null)
    // Submit first (creates the record in pending state), then reject
    setPhase('submitting')
    try {
      const record = await submitWriteback({
        idempotency_key: idempotencyKey + '-rej',
        rows: [effectiveRow],
        target,
        mode: mode ?? 'append',
        approval_required: true,   // must go through pending to reject
      })
      if (record.state === 'pending_approval') {
        const rejected = await approveWriteback(record.id, { action: 'reject' })
        setWbRecord(rejected)
      } else {
        setWbRecord(record)
      }
      setPhase('idle')
    } catch (err) {
      setError(err.message)
      setPhase('idle')
    }
  }

  // -- Edit modal --
  function handleEdit() {
    setPhase('editing')
    setError(null)
  }

  function handleEditConfirm(draft) {
    setEditedRow(draft)
    setPhase('idle')
    // Kick off the approve flow with the edited row
    handleApproveEdited(draft)
  }

  async function handleApproveEdited(draftRow) {
    setError(null)
    setPhase('previewing')
    try {
      const diff = await previewWriteback({
        idempotency_key: idempotencyKey + '-edit',
        rows: [draftRow],
        target,
        mode: mode ?? 'append',
      })
      setDryRunDiff(diff)
    } catch (err) {
      setError(err.message)
      setPhase('idle')
    }
  }

  const rowSignalBg = signal.matched && signal.color
    ? `${signal.color}10`
    : undefined

  return (
    <>
      {phase === 'editing' && (
        <EditModal
          row={effectiveRow}
          columns={columns}
          onConfirm={handleEditConfirm}
          onCancel={() => setPhase('idle')}
        />
      )}
      {phase === 'previewing' && dryRunDiff && (
        <DryRunPreview
          diff={dryRunDiff}
          columns={columns}
          onConfirm={handleConfirmPreview}
          onCancel={() => { setDryRunDiff(null); setPhase('idle') }}
        />
      )}

      <div
        className="flex flex-col gap-2.5 p-3.5 rounded-xl border border-border transition-colors"
        style={{ background: rowSignalBg ?? undefined }}
        data-testid="action-row"
        data-state={wbRecord?.state ?? 'idle'}
      >
        {/* Row data cells */}
        <div className="flex flex-wrap gap-x-5 gap-y-1.5 text-sm">
          {rowCols.map(col => (
            <div key={col} className="flex flex-col min-w-[5rem]">
              <span className="text-[10px] font-semibold text-muted uppercase tracking-wider leading-none mb-0.5">{col}</span>
              <span
                className="text-fg font-medium tabular-nums text-sm"
                style={signal.matched && signal.color ? { color: signal.color } : undefined}
              >
                {displayValue(effectiveRow[col])}
              </span>
            </div>
          ))}
        </div>

        {/* Action controls + status */}
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2">
            {wbRecord && <StatusBadge state={wbRecord.state} />}
            {phase === 'submitting' && (
              <span className="text-xs text-muted italic">Processing…</span>
            )}
            {phase === 'previewing' && !dryRunDiff && (
              <span className="text-xs text-muted italic inline-flex items-center gap-1">
                <Loader2 size={11} className="animate-spin" aria-hidden="true" />
                Loading preview…
              </span>
            )}
            {error && (
              <span className="text-xs text-danger" role="alert">{error}</span>
            )}
          </div>

          {!isDone && phase === 'idle' && (
            <div className="flex items-center gap-1.5">
              <Button
                variant="ghost"
                size="xs"
                onClick={handleEdit}
                data-testid="action-edit"
              >
                Edit
              </Button>
              <Button
                variant="danger"
                size="xs"
                onClick={handleReject}
                data-testid="action-reject"
              >
                Reject
              </Button>
              <Button
                variant="accent"
                size="xs"
                onClick={handleApprove}
                data-testid="action-approve"
              >
                Approve
              </Button>
            </div>
          )}
        </div>
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// ActionWidget — public export
// ---------------------------------------------------------------------------

/**
 * Renders the full action widget: heading + list of recommendation rows each
 * with their own approve/reject/edit controls.
 *
 * Row source precedence:
 *   1. Live query via widget.query_id (recommended — backed by RLS query)
 *   2. Static widget.props.rows  (useful in Canvas / designer)
 */
export default function ActionWidget({ widget }) {
  const {
    query_id,
    props: wProps = {},
    params: widgetParams,
  } = widget

  const label      = wProps.label ?? 'Actions'
  const target     = wProps.target ?? {}
  const mode       = wProps.mode ?? 'append'
  const approvalRequired = wProps.approval_required ?? false
  const flowId     = wProps.flow_id ?? null
  const columnsRaw = wProps.columns ?? []
  const columns    = Array.isArray(columnsRaw)
    ? columnsRaw
    : columnsRaw ? columnsRaw.split(',').map(c => c.trim()).filter(Boolean) : []
  const staticRows = Array.isArray(wProps.rows) ? wProps.rows : []
  const idempotencyPrefix = wProps.idempotency_prefix ?? `aw-${widget.id ?? '0'}`
  const signalRules = widget.signalRules ?? wProps.signalRules ?? null

  const resolvedParams = useResolvedParams(widgetParams)
  const refreshEpoch   = useRefreshEpoch()

  const [queryRows, setQueryRows] = useState(null)
  const [loading, setLoading]     = useState(false)
  const [fetchError, setFetchError] = useState(null)

  // Fetch recommendation rows from a query if query_id is set.
  // VIEWING is free (no write-back call here).
  useEffect(() => {
    if (!query_id) return
    let cancelled = false

    async function fetchRows() {
      setLoading(true)
      setFetchError(null)
      try {
        const hasParams = Object.keys(resolvedParams).length > 0
        const { table } = await runArrowQueryById(
          query_id,
          hasParams ? { namedParams: resolvedParams } : undefined,
        )
        if (cancelled || !table) return
        const fields = table.schema.fields.map(f => f.name)
        const rows = []
        for (let i = 0; i < table.numRows; i++) {
          const row = {}
          for (const col of fields) {
            const v = table.getChild(col)?.get(i) ?? null
            row[col] = typeof v === 'bigint' ? Number(v) : v
          }
          rows.push(row)
        }
        if (!cancelled) setQueryRows(rows)
      } catch (err) {
        if (!cancelled) setFetchError(err.message ?? 'Query failed.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchRows()
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query_id, JSON.stringify(resolvedParams), refreshEpoch])

  const rows = queryRows ?? staticRows

  return (
    <div className="flex flex-col h-full overflow-hidden" data-testid="action-widget">
      {/* Header */}
      <div className="flex items-center justify-between px-4 pt-3 pb-2 shrink-0">
        <h3 className="text-sm font-semibold text-fg">{label}</h3>
        {rows.length > 0 && (
          <span className="text-xs text-muted">
            {rows.length} recommendation{rows.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      {/* Body */}
      <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-4 space-y-2">
        {loading && (
          <div className="space-y-2 py-2" aria-busy="true" aria-label="Loading recommendations">
            {[1, 2, 3].map(i => (
              <Skeleton key={i} className="h-[4.5rem] w-full rounded-xl" />
            ))}
          </div>
        )}

        {!loading && fetchError && (
          <div
            className="px-3 py-2.5 text-xs rounded-xl border border-warning/20 bg-warning-bg text-warning flex items-center gap-1.5"
            role="alert"
          >
            <span aria-hidden="true">&#9888;</span>
            {fetchError}
          </div>
        )}

        {!loading && !fetchError && rows.length === 0 && (
          <EmptyState
            title="No recommendations"
            description="There are no pending actions to review."
            compact
          />
        )}

        {!loading && rows.map((row, idx) => (
          <ActionRow
            key={idx}
            index={idx}
            row={row}
            columns={columns}
            target={target}
            mode={mode}
            approvalRequired={approvalRequired}
            idempotencyPrefix={idempotencyPrefix}
            flowId={flowId}
            signalRules={signalRules}
          />
        ))}
      </div>
    </div>
  )
}
