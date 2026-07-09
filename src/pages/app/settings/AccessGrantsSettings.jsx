/**
 * AccessGrantsSettings — Settings → Access grants
 *
 * Manage dimension-level access grants for this organisation.
 * Endpoints:
 *   GET    /access-grants              — list grants
 *   POST   /access-grants             — create { subject_type, subject_id, dimension, value }
 *   DELETE /access-grants/{id}        — delete
 *
 * Writes are gated on useCanWrite().
 */

import { useCallback, useEffect, useState } from 'react'
import { Key, Plus, Loader2, Trash2, X, Check, AlertTriangle } from 'lucide-react'
import { get, post, del } from '../../../lib/api.js'
import { useCanWrite } from '../../../contexts/OrgContext.jsx'
import {
  SettingsPageHeader,
  SettingsCard,
  ErrorText,
  inputCls,
} from './SettingsUI.jsx'
import { toast } from '../../../components/ui/Toast.jsx'

// ---------------------------------------------------------------------------
// Exported pure validator (tested in accessGrants.test.mjs)
// ---------------------------------------------------------------------------

const VALID_SUBJECT_TYPES = ['user', 'group', 'role']

export function validateGrant(grant) {
  if (!VALID_SUBJECT_TYPES.includes(grant?.subject_type)) {
    return { ok: false, error: `subject_type must be one of: ${VALID_SUBJECT_TYPES.join(', ')}.` }
  }
  if (!grant?.subject_id?.trim()) {
    return { ok: false, error: 'subject_id is required.' }
  }
  if (!grant?.dimension?.trim()) {
    return { ok: false, error: 'dimension is required.' }
  }
  if (!grant?.value?.trim()) {
    return { ok: false, error: 'value is required.' }
  }
  return { ok: true, error: null }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const LABEL_CLS = 'block text-xs font-medium text-muted mb-1'

const DEFAULT_FORM = { subject_type: 'user', subject_id: '', dimension: '', value: '' }

// ---------------------------------------------------------------------------
// AddGrantForm — inline create form
// ---------------------------------------------------------------------------

function AddGrantForm({ onSaved, onCancel }) {
  const [form, setForm] = useState(DEFAULT_FORM)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const setField = (key, v) => setForm((prev) => ({ ...prev, [key]: v }))

  const handleSave = useCallback(async () => {
    const { ok, error: validErr } = validateGrant(form)
    if (!ok) { setError(validErr); return }

    setSaving(true)
    setError(null)
    try {
      const created = await post('/access-grants', {
        subject_type: form.subject_type,
        subject_id: form.subject_id.trim(),
        dimension: form.dimension.trim(),
        value: form.value.trim(),
      })
      toast('Access grant added.')
      onSaved?.(created)
    } catch (err) {
      setError(err?.message || 'Failed to add access grant.')
    } finally {
      setSaving(false)
    }
  }, [form, onSaved])

  return (
    <div className="rounded-2xl border border-border bg-surface p-4 space-y-3">
      <div>
        <label className={LABEL_CLS} htmlFor="ag-subject-type">Subject type</label>
        <select
          id="ag-subject-type"
          value={form.subject_type}
          onChange={(e) => setField('subject_type', e.target.value)}
          className={inputCls}
        >
          <option value="user">user</option>
          <option value="group">group</option>
          <option value="role">role</option>
        </select>
      </div>
      <div>
        <label className={LABEL_CLS} htmlFor="ag-subject-id">Subject ID</label>
        <input
          id="ag-subject-id"
          type="text"
          value={form.subject_id}
          onChange={(e) => setField('subject_id', e.target.value)}
          placeholder="e.g. user_abc123 or role_admin"
          className={inputCls}
          autoFocus
        />
      </div>
      <div>
        <label className={LABEL_CLS} htmlFor="ag-dimension">Dimension</label>
        <input
          id="ag-dimension"
          type="text"
          value={form.dimension}
          onChange={(e) => setField('dimension', e.target.value)}
          placeholder="e.g. country"
          className={inputCls}
        />
      </div>
      <div>
        <label className={LABEL_CLS} htmlFor="ag-value">Value</label>
        <input
          id="ag-value"
          type="text"
          value={form.value}
          onChange={(e) => setField('value', e.target.value)}
          placeholder="e.g. ZA"
          className={inputCls}
        />
      </div>

      {error && (
        <div className="flex items-start gap-2 text-xs text-red-600 dark:text-red-400">
          <AlertTriangle size={14} className="shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      <div className="flex items-center gap-2 pt-1">
        <button
          onClick={handleSave}
          disabled={saving}
          className="inline-flex items-center gap-1.5 h-9 px-4 text-sm font-semibold rounded-lg bg-primary text-primary-fg hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
          Add grant
        </button>
        <button
          onClick={onCancel}
          className="h-9 px-3 text-sm rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// GrantRow — single grant with delete action
// ---------------------------------------------------------------------------

// Render a grant value that may arrive as a scalar, an array, or an object
// (e.g. multi-value grants) without ever falling back to "[object Object]".
function formatGrantValue(v) {
  if (v == null) return '∅'
  if (Array.isArray(v)) return v.map(formatGrantValue).join(', ')
  if (typeof v === 'object') return v.value ?? v.label ?? v.name ?? v.id ?? JSON.stringify(v)
  return String(v)
}

function GrantRow({ grant, canWrite, onDeleted }) {
  const [busy, setBusy] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const handleDelete = useCallback(async () => {
    setBusy(true)
    try {
      await del(`/access-grants/${grant.id}`)
      toast('Access grant removed.')
      onDeleted?.(grant.id)
    } catch {
      toast.error('Failed to delete access grant.')
      setBusy(false)
      setConfirmDelete(false)
    }
  }, [grant, onDeleted])

  return (
    <div className="rounded-2xl border border-border bg-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 min-w-0">
          <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-primary/10 text-primary shrink-0">
            <Key size={16} />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <p className="text-sm font-semibold text-fg truncate">
                {grant.subject_type}:<span className="font-mono">{grant.subject_id}</span>
              </p>
              <span className="inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded bg-surface-2 text-muted border border-border">
                {grant.subject_type}
              </span>
            </div>
            <p className="text-xs text-muted mt-1 font-mono truncate">
              {grant.dimension} = <span className="text-fg font-semibold">{formatGrantValue(grant.value)}</span>
            </p>
          </div>
        </div>

        {canWrite && (
          <div className="flex items-center gap-1.5 shrink-0">
            {confirmDelete ? (
              <div className="flex items-center gap-1.5">
                <button
                  onClick={handleDelete}
                  disabled={busy}
                  aria-label="Confirm delete"
                  className="inline-flex items-center gap-1 h-8 px-2.5 rounded-lg bg-red-600 text-white text-xs font-semibold hover:bg-red-700 disabled:opacity-50 transition-colors"
                >
                  {busy ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                  Confirm
                </button>
                <button
                  onClick={() => setConfirmDelete(false)}
                  aria-label="Cancel delete"
                  className="h-8 w-8 flex items-center justify-center rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors"
                >
                  <X size={14} />
                </button>
              </div>
            ) : (
              <button
                onClick={() => setConfirmDelete(true)}
                title="Delete"
                aria-label="Delete access grant"
                className="inline-flex items-center justify-center h-8 w-8 rounded-lg border border-border text-muted hover:text-red-600 hover:border-red-200 hover:bg-red-50 dark:hover:text-red-400 dark:hover:border-red-900/40 dark:hover:bg-red-900/10 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <Trash2 size={13} />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// AccessGrantsSettings — main section
// ---------------------------------------------------------------------------

export default function AccessGrantsSettings() {
  const canWrite = useCanWrite()

  const [grants, setGrants] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [showForm, setShowForm] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await get('/access-grants')
      setGrants(Array.isArray(data) ? data : (data?.items ?? []))
    } catch (err) {
      setError(err?.message || 'Failed to load access grants.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const t = setTimeout(load, 0)
    return () => clearTimeout(t)
  }, [load])

  const handleSaved = useCallback((created) => {
    if (created) {
      setGrants((prev) => [created, ...prev])
    }
    setShowForm(false)
  }, [])

  const handleDeleted = useCallback((id) => {
    setGrants((prev) => prev.filter((g) => g.id !== id))
  }, [])

  return (
    <div className="space-y-6">
      <SettingsPageHeader
        title="Access grants"
        description="Grant subjects (users, groups, or roles) access to specific dimension values. Grants are enforced at query time to restrict which rows each subject can see."
      />

      <SettingsCard
        title="Grants"
        description="Dimension-level row access grants for this organisation."
      >
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted py-6 justify-center">
            <Loader2 size={16} className="animate-spin" /> Loading grants…
          </div>
        ) : error ? (
          <div className="py-2 flex items-center gap-3">
            <ErrorText>{error}</ErrorText>
            <button onClick={load} className="text-xs text-muted hover:text-fg underline shrink-0">
              Retry
            </button>
          </div>
        ) : grants.length === 0 ? (
          <div className="flex flex-col items-center justify-center text-center py-8 px-6 gap-2">
            <Key size={26} className="text-muted/40" />
            <p className="text-sm font-medium text-fg">No access grants yet</p>
            <p className="text-xs text-muted max-w-sm">
              Add a grant below to restrict dimension-level row access for a user, group, or role.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {grants.map((g) => (
              <GrantRow
                key={g.id}
                grant={g}
                canWrite={canWrite}
                onDeleted={handleDeleted}
              />
            ))}
          </div>
        )}
      </SettingsCard>

      {canWrite && (
        <SettingsCard title="Add a grant" description="Grant a subject access to a dimension value.">
          {showForm ? (
            <AddGrantForm onSaved={handleSaved} onCancel={() => setShowForm(false)} />
          ) : (
            <button
              onClick={() => setShowForm(true)}
              className="inline-flex items-center gap-2 h-9 px-4 text-sm font-medium rounded-xl border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors"
            >
              <Plus size={15} />
              Add grant
            </button>
          )}
        </SettingsCard>
      )}
    </div>
  )
}
