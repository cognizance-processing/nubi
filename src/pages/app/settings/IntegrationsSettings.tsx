/**
 * IntegrationsSettings — Settings → Integrations.
 *
 * Connect the org's notify channel. Nubi is embedded BI, not a chat-ops
 * platform, so Email is the one supported kind — the embedding host owns any
 * Slack/Teams/etc. notifications it wants. A connected integration powers
 * outbound alerts (watches, flow runs, shares). This page is the full surface:
 *
 *   - lists connected integrations (kind, name, enabled, configured).
 *   - "Connect" picker → per-kind form (the right secret/non-secret fields).
 *   - edit / enable-disable / delete.
 *   - "Send test" → POST /integrations/{id}/test, showing the result inline.
 *
 * SECRET HANDLING (the contract's hard rule): secret fields are WRITE-ONLY.
 * List/get responses never return them — only the non-secret `config` plus
 * `configured: bool`. So secret inputs are rendered as password fields, are
 * NEVER pre-filled (even when editing a configured integration), and are only
 * sent when the user types a value. On edit, an empty secret means "leave the
 * stored secret unchanged"; the form shows a "secret saved — leave blank to
 * keep" affordance so the user knows it's configured.
 *
 * Styling mirrors the other settings sections (SettingsUI building blocks,
 * bg-surface / border-border / text-fg / text-muted tokens). Writes are gated
 * on useCanWrite() like the rest of the org-scoped settings.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Plug,
  Plus,
  Loader2,
  Trash2,
  Pencil,
  X,
  Check,
  Send,
  CheckCircle2,
  AlertTriangle,
  Mail,
} from 'lucide-react'

import { useCanWrite } from '../../../contexts/OrgContext.jsx'
import {
  listIntegrations,
  createIntegration,
  updateIntegration,
  deleteIntegration,
  testIntegration,
} from '../../../lib/integrationsApi.js'
import {
  SettingsPageHeader,
  SettingsCard,
  ErrorText,
  inputCls,
} from './SettingsUI.jsx'
import { toast } from '../../../components/ui/Toast.jsx'
import Button from '../../../components/ui/Button.jsx'
import Modal from '../../../components/ui/Modal.jsx'
import Tag from '../../../components/ui/Tag.jsx'

// ---------------------------------------------------------------------------
// Per-kind field schema (drives both the form and the list summary).
//
// Each field: { key, label, secret?, placeholder?, hint?, type? }.
// `secret: true` fields are write-only password inputs, never pre-filled.
// The non-secret fields map straight onto `config`; secret fields are merged
// into the same `config` object on write (the backend splits + encrypts them).
// ---------------------------------------------------------------------------

const KINDS = {
  email: {
    label: 'Email',
    Icon: Mail,
    blurb: 'Email alerts to one or more recipients (uses the app SMTP).',
    fields: [
      { key: 'recipients', label: 'Recipients', placeholder: 'ops@acme.com, alerts@acme.com', list: true },
    ],
  },
}

const KIND_ORDER = ['email']

function kindMeta(kind) {
  return KINDS[kind] ?? { label: kind, Icon: Plug, blurb: '', fields: [] }
}

// ---------------------------------------------------------------------------
// Connect / edit modal
// ---------------------------------------------------------------------------

const FIELD_CLS = inputCls
const LABEL_CLS = 'block text-xs font-medium text-muted mb-1'

function buildConfig(kind, values) {
  const meta = kindMeta(kind)
  const config = {}
  for (const f of meta.fields) {
    const raw = values[f.key]
    if (f.secret) {
      // Only send secrets the user actually typed (write-only; blank = keep).
      if (raw && raw.trim()) config[f.key] = raw.trim()
      continue
    }
    if (f.list) {
      const arr = String(raw ?? '')
        .split(/[,\n]/)
        .map((s) => s.trim())
        .filter(Boolean)
      if (arr.length) config[f.key] = arr
      continue
    }
    if (raw != null && String(raw).trim() !== '') config[f.key] = String(raw).trim()
  }
  return config
}

function initialValues(kind, integration) {
  const meta = kindMeta(kind)
  const cfg = integration?.config ?? {}
  const values = {}
  for (const f of meta.fields) {
    if (f.secret) {
      values[f.key] = '' // NEVER pre-fill secrets.
    } else if (f.list) {
      values[f.key] = Array.isArray(cfg[f.key]) ? cfg[f.key].join(', ') : cfg[f.key] ?? ''
    } else {
      values[f.key] = cfg[f.key] ?? ''
    }
  }
  return values
}

function IntegrationModal({ open, kind, initial, onClose, onSaved }) {
  const editing = !!initial
  const meta = kindMeta(kind)
  const [name, setName] = useState('')
  const [values, setValues] = useState({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  // Re-seed each time the modal opens (create vs edit).
  useEffect(() => {
    if (!open) return
    setName(initial?.name ?? meta.label)
    setValues(initialValues(kind, initial))
    setError(null)
  }, [open, kind, initial, meta.label])

  const setField = (key, v) => setValues((prev) => ({ ...prev, [key]: v }))

  const handleSave = useCallback(async () => {
    const trimmedName = name.trim()
    if (!trimmedName) {
      setError('Give this integration a name.')
      return
    }
    const config = buildConfig(kind, values)
    setSaving(true)
    setError(null)
    try {
      const saved = editing
        ? await updateIntegration(initial.id, { name: trimmedName, config })
        : await createIntegration({ kind, name: trimmedName, config, enabled: true })
      toast.success(editing ? 'Integration updated.' : 'Integration connected.')
      onSaved?.(saved)
      onClose?.()
    } catch (err) {
      setError(err?.message || 'Save failed.')
    } finally {
      setSaving(false)
    }
  }, [name, kind, values, editing, initial, onSaved, onClose])

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title={editing ? `Edit ${meta.label}` : `Connect ${meta.label}`}
      description={meta.blurb}
    >
      <Modal.Body>
        <div className="space-y-4">
          <div>
            <label className={LABEL_CLS} htmlFor="integration-name">Name</label>
            <input
              id="integration-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={meta.label}
              className={FIELD_CLS}
              autoFocus
            />
          </div>

          {meta.fields.map((f) => {
            const configured = editing && f.secret && initial?.configured
            const fieldId = `integration-field-${f.key}`
            return (
              <div key={f.key}>
                <label className={LABEL_CLS} htmlFor={fieldId}>{f.label}</label>
                <input
                  id={fieldId}
                  type={f.secret ? 'password' : 'text'}
                  value={values[f.key] ?? ''}
                  onChange={(e) => setField(f.key, e.target.value)}
                  placeholder={configured ? '•••••••• (leave blank to keep)' : f.placeholder}
                  autoComplete={f.secret ? 'new-password' : 'off'}
                  className={[FIELD_CLS, f.secret || f.key === 'webhook_url' ? 'font-mono text-xs' : ''].join(' ')}
                />
                {f.secret && (
                  <p className="text-[11px] text-muted mt-1">
                    {configured
                      ? 'A secret is already stored. Leave blank to keep it, or type a new value to replace it.'
                      : 'Stored encrypted; never shown again after saving.'}
                  </p>
                )}
                {f.list && (
                  <p className="text-[11px] text-muted mt-1">Comma- or newline-separated.</p>
                )}
              </div>
            )
          })}

          {error && (
            <div className="nubi-notice nubi-notice-error text-xs" role="alert">
              <AlertTriangle size={14} className="shrink-0 mt-0.5" aria-hidden="true" />
              <span>{error}</span>
            </div>
          )}
        </div>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button onClick={handleSave} loading={saving}>
          {!saving && <Check size={14} aria-hidden="true" />}
          {editing ? 'Save changes' : 'Connect'}
        </Button>
      </Modal.Footer>
    </Modal>
  )
}

// ---------------------------------------------------------------------------
// One connected integration row
// ---------------------------------------------------------------------------

function IntegrationRow({ integration, canWrite, onEdit, onChanged }) {
  const meta = kindMeta(integration.kind)
  const { Icon } = meta
  const [busy, setBusy] = useState(null) // 'toggle' | 'test' | 'delete' | null
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [testResult, setTestResult] = useState(null)

  const enabled = integration.enabled !== false

  const handleToggle = useCallback(async () => {
    setBusy('toggle')
    try {
      const saved = await updateIntegration(integration.id, { enabled: !enabled })
      onChanged?.(saved ?? { ...integration, enabled: !enabled })
      toast.success(enabled ? 'Integration disabled.' : 'Integration enabled.')
    } catch (err) {
      toast.error(err?.message ?? 'Failed to update integration.')
    } finally {
      setBusy(null)
    }
  }, [integration, enabled, onChanged])

  const handleTest = useCallback(async () => {
    setBusy('test')
    setTestResult(null)
    try {
      const res = await testIntegration(integration.id)
      const ok = res?.ok !== false && !res?.error
      setTestResult({
        ok,
        text: ok ? res?.detail || 'Test message sent.' : res?.error || res?.detail || 'Test failed.',
      })
    } catch (err) {
      setTestResult({ ok: false, text: err?.message || 'Test failed.' })
    } finally {
      setBusy(null)
    }
  }, [integration.id])

  const handleDelete = useCallback(async () => {
    setBusy('delete')
    const ok = await deleteIntegration(integration.id)
    setBusy(null)
    if (ok) {
      toast.success('Integration deleted.')
      onChanged?.(null, integration.id)
    } else {
      toast.error('Failed to delete integration.')
      setConfirmDelete(false)
    }
  }, [integration, onChanged])

  // Compact non-secret summary line.
  const summary = useMemo(() => {
    const cfg = integration.config ?? {}
    const parts = []
    for (const f of meta.fields) {
      if (f.secret) continue
      const v = cfg[f.key]
      if (v == null || v === '') continue
      parts.push(Array.isArray(v) ? v.join(', ') : String(v))
    }
    return parts.join(' · ')
  }, [integration.config, meta.fields])

  return (
    <div className="border border-border bg-surface p-4" style={{ borderRadius: 2 }}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 min-w-0">
          <div className="flex items-center justify-center w-10 h-10 border border-border text-fg shrink-0" style={{ borderRadius: 2 }}>
            <Icon size={18} strokeWidth={1.75} aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <p className="text-sm font-semibold text-fg truncate">{integration.name || meta.label}</p>
              <Tag caps={false}>{meta.label}</Tag>
              {!enabled && <Tag dot>Disabled</Tag>}
              {integration.configured === false && <Tag variant="warning" dot>Not configured</Tag>}
              {enabled && integration.configured !== false && <Tag variant="success" dot>Active</Tag>}
            </div>
            {summary && <p className="text-xs text-muted mt-1 truncate">{summary}</p>}
          </div>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          <Button size="sm" variant="secondary" onClick={handleTest} disabled={busy !== null} loading={busy === 'test'} title="Send a test message">
            {busy !== 'test' && <Send size={12} aria-hidden="true" />}
            Test
          </Button>

          {canWrite && (
            <>
              <Button
                size="sm"
                variant="secondary"
                onClick={handleToggle}
                disabled={busy !== null}
                loading={busy === 'toggle'}
                aria-label={enabled ? 'Disable integration' : 'Enable integration'}
              >
                {enabled ? 'Disable' : 'Enable'}
              </Button>
              <Button
                size="icon-sm"
                variant="secondary"
                onClick={() => onEdit?.(integration)}
                title="Edit"
                aria-label="Edit integration"
              >
                <Pencil size={13} aria-hidden="true" />
              </Button>
              {confirmDelete ? (
                <div className="flex items-center gap-1.5">
                  <Button size="sm" variant="danger" onClick={handleDelete} loading={busy === 'delete'} aria-label="Confirm delete">
                    {busy !== 'delete' && <Check size={12} aria-hidden="true" />}
                    Confirm
                  </Button>
                  <Button size="icon-sm" variant="secondary" onClick={() => setConfirmDelete(false)} aria-label="Cancel delete">
                    <X size={14} aria-hidden="true" />
                  </Button>
                </div>
              ) : (
                <Button
                  size="icon-sm"
                  variant="secondary"
                  onClick={() => setConfirmDelete(true)}
                  title="Delete"
                  aria-label="Delete integration"
                >
                  <Trash2 size={13} aria-hidden="true" />
                </Button>
              )}
            </>
          )}
        </div>
      </div>

      {testResult && (
        <div className={`nubi-notice ${testResult.ok ? 'nubi-notice-success' : 'nubi-notice-error'} mt-3 text-xs`} role="status">
          {testResult.ok ? (
            <CheckCircle2 size={14} className="shrink-0 mt-0.5" />
          ) : (
            <AlertTriangle size={14} className="shrink-0 mt-0.5" />
          )}
          <span className="min-w-0">{testResult.text}</span>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Connect-a-kind picker
// ---------------------------------------------------------------------------

function ConnectPicker({ onPick }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
      {KIND_ORDER.map((kind) => {
        const meta = kindMeta(kind)
        const { Icon } = meta
        return (
          <button
            key={kind}
            type="button"
            onClick={() => onPick(kind)}
            className="group flex items-center gap-3 px-4 py-3 border border-border bg-surface text-left hover:border-ink transition-colors"
          >
            <div className="flex items-center justify-center w-10 h-10 border border-border text-fg shrink-0" style={{ borderRadius: 2 }}>
              <Icon size={18} strokeWidth={1.75} aria-hidden="true" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-fg">{meta.label}</p>
              <p className="text-xs text-muted truncate">{meta.blurb}</p>
            </div>
            <Plus size={15} className="text-subtle group-hover:text-fg shrink-0 transition-colors" aria-hidden="true" />
          </button>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// IntegrationsSettings — the section
// ---------------------------------------------------------------------------

export default function IntegrationsSettings() {
  const canWrite = useCanWrite()

  const [integrations, setIntegrations] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [modalKind, setModalKind] = useState(null)
  const [editing, setEditing] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    const data = await listIntegrations()
    setIntegrations(Array.isArray(data) ? data : [])
    setLoading(false)
  }, [])

  // Defer the initial load (no synchronous setState in the effect body).
  useEffect(() => {
    const t = setTimeout(load, 0)
    return () => clearTimeout(t)
  }, [load])

  const openConnect = useCallback((kind) => {
    setEditing(null)
    setModalKind(kind)
  }, [])

  const openEdit = useCallback((integration) => {
    setEditing(integration)
    setModalKind(integration.kind)
  }, [])

  const closeModal = useCallback(() => {
    setModalKind(null)
    setEditing(null)
  }, [])

  const handleSaved = useCallback((saved) => {
    if (!saved) {
      load()
      return
    }
    setIntegrations((prev) => {
      const idx = prev.findIndex((i) => i.id === saved.id)
      if (idx >= 0) {
        const next = [...prev]
        next[idx] = saved
        return next
      }
      return [saved, ...prev]
    })
  }, [load])

  const handleChanged = useCallback((saved, deletedId) => {
    if (deletedId) {
      setIntegrations((prev) => prev.filter((i) => i.id !== deletedId))
      return
    }
    if (saved) {
      setIntegrations((prev) => prev.map((i) => (i.id === saved.id ? saved : i)))
    }
  }, [])

  return (
    <div className="space-y-6">
      <SettingsPageHeader
        title="Integrations"
        description="Connect an email channel. A connected integration powers outbound alerts (watches, flow runs, shares)."
      />

      {/* Connected list */}
      <SettingsCard
        title="Connected"
        description="Channels this organisation can send alerts to."
      >
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted py-6 justify-center">
            <Loader2 size={16} className="animate-spin" /> Loading integrations…
          </div>
        ) : error ? (
          <div className="py-2">
            <ErrorText>{error}</ErrorText>
          </div>
        ) : integrations.length === 0 ? (
          <div className="flex flex-col items-center justify-center text-center py-8 px-6 gap-2">
            <Plug size={24} strokeWidth={1.5} className="text-subtle" aria-hidden="true" />
            <p className="text-sm font-medium text-fg">No integrations yet</p>
            <p className="text-xs text-muted max-w-sm">
              Connect a channel below to start receiving alerts where your team already works.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {integrations.map((it) => (
              <IntegrationRow
                key={it.id}
                integration={it}
                canWrite={canWrite}
                onEdit={openEdit}
                onChanged={handleChanged}
              />
            ))}
          </div>
        )}
      </SettingsCard>

      {/* Connect a new one */}
      {canWrite && (
        <SettingsCard title="Connect a channel" description="Pick a channel to connect.">
          <ConnectPicker onPick={openConnect} />
        </SettingsCard>
      )}

      <IntegrationModal
        open={!!modalKind}
        kind={modalKind}
        initial={editing}
        onClose={closeModal}
        onSaved={handleSaved}
      />
    </div>
  )
}
