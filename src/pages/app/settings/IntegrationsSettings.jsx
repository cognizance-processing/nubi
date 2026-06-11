/**
 * IntegrationsSettings — Settings → Integrations.
 *
 * Connect the org's notify channels (Slack / WhatsApp / Google Chat / Teams /
 * Email). One connected integration powers BOTH inbound chat and outbound
 * alerts (watches, flow runs, shares). This page is the full surface:
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
  Slack,
  MessageCircle,
  MessagesSquare,
  Mail,
  Hash,
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

// ---------------------------------------------------------------------------
// Per-kind field schema (drives both the form and the list summary).
//
// Each field: { key, label, secret?, placeholder?, hint?, type? }.
// `secret: true` fields are write-only password inputs, never pre-filled.
// The non-secret fields map straight onto `config`; secret fields are merged
// into the same `config` object on write (the backend splits + encrypts them).
// ---------------------------------------------------------------------------

const KINDS = {
  slack: {
    label: 'Slack',
    Icon: Slack,
    blurb: 'Post alerts to a Slack channel via an incoming webhook.',
    fields: [
      { key: 'channel', label: 'Channel', placeholder: '#alerts' },
      { key: 'webhook_url', label: 'Incoming webhook URL', secret: true, placeholder: 'https://hooks.slack.com/services/…' },
    ],
  },
  whatsapp: {
    label: 'WhatsApp',
    Icon: MessageCircle,
    blurb: 'Send alerts through the WhatsApp Business Cloud API.',
    fields: [
      { key: 'phone_number_id', label: 'Phone number ID', placeholder: '123456789012345' },
      { key: 'to', label: 'Recipient number', placeholder: '+27…' },
      { key: 'access_token', label: 'Access token', secret: true, placeholder: 'EAAB…' },
    ],
  },
  google_chat: {
    label: 'Google Chat',
    Icon: Hash,
    blurb: 'Post to a Google Chat space via an incoming webhook.',
    fields: [
      { key: 'space', label: 'Space label', placeholder: 'Alerts' },
      { key: 'webhook_url', label: 'Webhook URL', secret: true, placeholder: 'https://chat.googleapis.com/v1/spaces/…' },
    ],
  },
  teams: {
    label: 'Microsoft Teams',
    Icon: MessagesSquare,
    blurb: 'Post to a Teams channel via an incoming webhook connector.',
    fields: [
      { key: 'name', label: 'Connector name', placeholder: 'Alerts' },
      { key: 'webhook_url', label: 'Webhook URL', secret: true, placeholder: 'https://outlook.office.com/webhook/…' },
    ],
  },
  email: {
    label: 'Email',
    Icon: Mail,
    blurb: 'Email alerts to one or more recipients (uses the app SMTP).',
    fields: [
      { key: 'recipients', label: 'Recipients', placeholder: 'ops@acme.com, alerts@acme.com', list: true },
    ],
  },
}

const KIND_ORDER = ['slack', 'whatsapp', 'google_chat', 'teams', 'email']

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

  // Close on Escape.
  useEffect(() => {
    if (!open) return undefined
    const onKey = (e) => {
      if (e.key === 'Escape') onClose?.()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

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
      onSaved?.(saved)
      onClose?.()
    } catch (err) {
      setError(err?.message || 'Save failed.')
    } finally {
      setSaving(false)
    }
  }, [name, kind, values, editing, initial, onSaved, onClose])

  if (!open) return null

  const { Icon } = meta

  return (
    <div className="fixed inset-0 z-[70] flex items-end sm:items-center justify-center p-0 sm:p-4">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full sm:max-w-lg max-h-[92dvh] flex flex-col bg-surface border border-border rounded-t-2xl sm:rounded-2xl shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="shrink-0 flex items-center justify-between px-4 py-3 border-b border-border">
          <div className="flex items-center gap-2">
            <Icon size={16} className="text-primary" />
            <h2 className="text-sm font-semibold text-fg">
              {editing ? `Edit ${meta.label}` : `Connect ${meta.label}`}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="h-8 w-8 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          {meta.blurb && <p className="text-xs text-muted">{meta.blurb}</p>}

          <div>
            <label className={LABEL_CLS}>Name</label>
            <input
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
            return (
              <div key={f.key}>
                <label className={LABEL_CLS}>{f.label}</label>
                <input
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
            <div className="flex items-start gap-2 text-xs text-red-600 dark:text-red-400">
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="shrink-0 flex items-center justify-end gap-2 px-4 py-3 border-t border-border">
          <button
            onClick={onClose}
            className="h-9 px-3 text-sm rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center gap-1.5 h-9 px-4 text-sm font-semibold rounded-lg bg-primary text-primary-fg hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
            {editing ? 'Save changes' : 'Connect'}
          </button>
        </div>
      </div>
    </div>
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
    } catch {
      /* leave state unchanged on failure */
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
    if (ok) onChanged?.(null, integration.id)
    else setConfirmDelete(false)
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
    <div className="rounded-2xl border border-border bg-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 min-w-0">
          <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-primary/10 text-primary shrink-0">
            <Icon size={16} />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <p className="text-sm font-semibold text-fg truncate">{integration.name || meta.label}</p>
              <span className="inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded bg-surface-2 text-muted border border-border">
                {meta.label}
              </span>
              {!enabled && (
                <span className="inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded bg-surface-2 text-muted border border-border">
                  Disabled
                </span>
              )}
              {integration.configured === false && (
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium rounded bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20">
                  <AlertTriangle size={10} /> Not configured
                </span>
              )}
            </div>
            {summary && <p className="text-xs text-muted mt-1 truncate">{summary}</p>}
          </div>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          <button
            onClick={handleTest}
            disabled={busy !== null}
            title="Send a test message"
            className="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-lg text-xs font-medium border border-border text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-50 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {busy === 'test' ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />}
            Test
          </button>

          {canWrite && (
            <>
              <button
                onClick={handleToggle}
                disabled={busy !== null}
                title={enabled ? 'Disable' : 'Enable'}
                className="inline-flex items-center h-8 px-2.5 rounded-lg text-xs font-medium border border-border text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-50 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
              >
                {busy === 'toggle' ? <Loader2 size={12} className="animate-spin" /> : enabled ? 'Disable' : 'Enable'}
              </button>
              <button
                onClick={() => onEdit?.(integration)}
                title="Edit"
                className="inline-flex items-center justify-center h-8 w-8 rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <Pencil size={13} />
              </button>
              {confirmDelete ? (
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={handleDelete}
                    disabled={busy === 'delete'}
                    className="inline-flex items-center gap-1 h-8 px-2.5 rounded-lg bg-red-600 text-white text-xs font-semibold hover:bg-red-700 disabled:opacity-50 transition-colors"
                  >
                    {busy === 'delete' ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                    Confirm
                  </button>
                  <button
                    onClick={() => setConfirmDelete(false)}
                    className="h-8 w-8 flex items-center justify-center rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors"
                  >
                    <X size={14} />
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setConfirmDelete(true)}
                  title="Delete"
                  className="inline-flex items-center justify-center h-8 w-8 rounded-lg border border-border text-muted hover:text-red-600 hover:border-red-200 hover:bg-red-50 dark:hover:text-red-400 dark:hover:border-red-900/40 dark:hover:bg-red-900/10 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  <Trash2 size={13} />
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {testResult && (
        <div
          className={[
            'mt-3 flex items-start gap-2 rounded-lg border px-3 py-2 text-xs',
            testResult.ok
              ? 'border-emerald-500/20 bg-emerald-500/5 text-emerald-700 dark:text-emerald-400'
              : 'border-red-500/20 bg-red-500/5 text-red-700 dark:text-red-400',
          ].join(' ')}
        >
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
            className="group flex items-center gap-3 px-4 py-3 rounded-2xl border border-border bg-surface text-left hover:border-primary/40 hover:bg-surface-2/50 transition-colors"
          >
            <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-primary/10 text-primary shrink-0">
              <Icon size={16} />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-fg">{meta.label}</p>
              <p className="text-xs text-muted truncate">{meta.blurb}</p>
            </div>
            <Plus size={15} className="text-muted/50 group-hover:text-primary shrink-0 transition-colors" />
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
        description="Connect Slack, WhatsApp, Google Chat, Teams or Email. A connected integration powers both inbound chat and outbound alerts (watches, flow runs, shares)."
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
            <Plug size={26} className="text-muted/40" />
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
