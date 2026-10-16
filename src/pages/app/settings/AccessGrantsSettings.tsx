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
import Button from '../../../components/ui/Button.jsx'
import Tag from '../../../components/ui/Tag.jsx'
import EmptyState from '../../../components/ui/EmptyState.jsx'
import Skeleton from '../../../components/ui/Skeleton.jsx'

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

const LABEL_CLS = 'nubi-label'

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
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
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
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
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
      </div>

      {error && (
        <div className="nubi-notice nubi-notice-error text-xs" role="alert">
          <span>{error}</span>
        </div>
      )}

      <div className="flex items-center gap-2 pt-1">
        <Button onClick={handleSave} loading={saving}>
          {!saving && <Check size={14} aria-hidden="true" />}
          Add grant
        </Button>
        <Button variant="secondary" onClick={onCancel}>Cancel</Button>
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
    <div className="ap-row" style={{ flexWrap: 'wrap' }}>
      <span className="flex h-8 w-8 shrink-0 items-center justify-center border border-frame-line bg-surface text-fg" style={{ borderRadius: 2 }} aria-hidden="true">
        <Key size={15} strokeWidth={1.75} />
      </span>
      <div className="ap-row-main">
        <div className="ap-row-title">
          <span className="ap-trunc">
            {grant.subject_type}:<span className="font-mono">{grant.subject_id}</span>
          </span>
          <Tag caps={false}>{grant.subject_type}</Tag>
        </div>
        <div className="ap-row-sub ap-mono" style={{ fontSize: 12 }}>
          {grant.dimension} = <span style={{ color: 'var(--text)', fontWeight: 500 }}>{formatGrantValue(grant.value)}</span>
        </div>
      </div>

      {canWrite && (
        <div className="ap-actions">
          {confirmDelete ? (
            <span className="inline-flex items-center gap-1.5" role="group" aria-label="Confirm delete">
              <span className="text-xs text-muted">Remove this grant?</span>
              <Button variant="danger" size="xs" onClick={handleDelete} loading={busy} aria-label="Confirm delete">Remove</Button>
              <Button variant="ghost" size="xs" onClick={() => setConfirmDelete(false)} aria-label="Cancel delete">Keep</Button>
            </span>
          ) : (
            <Button variant="ghost" size="icon-sm" onClick={() => setConfirmDelete(true)} title="Delete" aria-label="Delete access grant">
              <Trash2 size={14} aria-hidden="true" />
            </Button>
          )}
        </div>
      )}
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
          <div className="space-y-2" aria-hidden="true">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : error ? (
          <div className="py-2 flex items-center gap-3">
            <ErrorText>{error}</ErrorText>
            <Button variant="secondary" size="xs" onClick={load}>Retry</Button>
          </div>
        ) : grants.length === 0 ? (
          <EmptyState
            compact
            icon={<Key size={18} />}
            title="No access grants yet"
            description="A grant limits which rows a user, group or role can see for one dimension, for example country = ZA. Add the first one below."
          />
        ) : (
          <div className="-my-2 border-t border-border">
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
            <Button variant="secondary" onClick={() => setShowForm(true)}>
              <Plus size={14} aria-hidden="true" />
              Add grant
            </Button>
          )}
        </SettingsCard>
      )}
    </div>
  )
}
