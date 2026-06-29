/**
 * McpSettings — Settings → MCP servers
 *
 * Manage external MCP server registrations for this organisation.
 * Endpoints:
 *   GET    /mcp/servers         — list servers (no auth tokens in response)
 *   POST   /mcp/servers         — create { name, url, transport?, auth_token?, enabled? }
 *   PUT    /mcp/servers/{id}    — update (same fields, all optional)
 *   DELETE /mcp/servers/{id}    — delete
 *
 * Auth tokens are write-only; never shown after saving (stored encrypted).
 * Writes are gated on useCanWrite().
 */

import { useCallback, useEffect, useState } from 'react'
import { Cpu, Plus, Loader2, Trash2, X, Check, AlertTriangle } from 'lucide-react'
import { get, post, put, del } from '../../../lib/api.js'
import { useCanWrite } from '../../../contexts/OrgContext.jsx'
import {
  SettingsPageHeader,
  SettingsCard,
  ErrorText,
  inputCls,
} from './SettingsUI.jsx'
import { toast } from '../../../components/ui/Toast.jsx'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const LABEL_CLS = 'block text-xs font-medium text-muted mb-1'

const DEFAULT_FORM = { name: '', url: '', transport: 'http', auth_token: '', enabled: true }

// ---------------------------------------------------------------------------
// Add-server inline form
// ---------------------------------------------------------------------------

function AddServerForm({ onSaved, onCancel }) {
  const [form, setForm] = useState(DEFAULT_FORM)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const set = (key, v) => setForm((prev) => ({ ...prev, [key]: v }))

  const handleSave = useCallback(async () => {
    const name = form.name.trim()
    const url = form.url.trim()
    if (!name) { setError('Name is required.'); return }
    if (!url) { setError('URL is required.'); return }
    const body = { name, url, transport: form.transport, enabled: form.enabled }
    if (form.auth_token.trim()) body.auth_token = form.auth_token.trim()
    setSaving(true)
    setError(null)
    try {
      const created = await post('/mcp/servers', body)
      toast('MCP server added.')
      onSaved?.(created)
    } catch (err) {
      setError(err?.message || 'Failed to add server.')
    } finally {
      setSaving(false)
    }
  }, [form, onSaved])

  return (
    <div className="rounded-2xl border border-border bg-surface p-4 space-y-3">
      <div>
        <label className={LABEL_CLS} htmlFor="mcp-name">Name</label>
        <input
          id="mcp-name"
          type="text"
          value={form.name}
          onChange={(e) => set('name', e.target.value)}
          placeholder="My MCP server"
          className={inputCls}
          autoFocus
        />
      </div>
      <div>
        <label className={LABEL_CLS} htmlFor="mcp-url">URL</label>
        <input
          id="mcp-url"
          type="text"
          value={form.url}
          onChange={(e) => set('url', e.target.value)}
          placeholder="https://mcp.example.com"
          className={inputCls}
        />
      </div>
      <div>
        <label className={LABEL_CLS} htmlFor="mcp-transport">Transport</label>
        <select
          id="mcp-transport"
          value={form.transport}
          onChange={(e) => set('transport', e.target.value)}
          className={inputCls}
        >
          <option value="http">http</option>
          <option value="sse">sse</option>
        </select>
      </div>
      <div>
        <label className={LABEL_CLS} htmlFor="mcp-token">Auth token</label>
        <input
          id="mcp-token"
          type="password"
          value={form.auth_token}
          onChange={(e) => set('auth_token', e.target.value)}
          placeholder="Optional — leave blank for unauthenticated"
          autoComplete="new-password"
          className={[inputCls, 'font-mono text-xs'].join(' ')}
        />
        <p className="text-[11px] text-muted mt-1">Stored encrypted; never shown again after saving.</p>
      </div>
      <div className="flex items-center gap-2">
        <input
          id="mcp-enabled"
          type="checkbox"
          checked={form.enabled}
          onChange={(e) => set('enabled', e.target.checked)}
          className="rounded border-border"
        />
        <label htmlFor="mcp-enabled" className="text-sm text-fg cursor-pointer">Enabled</label>
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
          Add server
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
// Server row
// ---------------------------------------------------------------------------

function ServerRow({ server, canWrite, onChanged, onDeleted }) {
  const [busy, setBusy] = useState(null) // 'toggle' | 'delete' | null
  const [confirmDelete, setConfirmDelete] = useState(false)

  const enabled = server.enabled !== false

  const handleToggle = useCallback(async () => {
    setBusy('toggle')
    try {
      const updated = await put(`/mcp/servers/${server.id}`, { enabled: !enabled })
      onChanged?.(updated ?? { ...server, enabled: !enabled })
    } catch {
      toast.error('Failed to update server.')
    } finally {
      setBusy(null)
    }
  }, [server, enabled, onChanged])

  const handleDelete = useCallback(async () => {
    setBusy('delete')
    try {
      await del(`/mcp/servers/${server.id}`)
      toast('MCP server removed.')
      onDeleted?.(server.id)
    } catch {
      toast.error('Failed to delete server.')
      setBusy(null)
      setConfirmDelete(false)
    }
  }, [server, onDeleted])

  return (
    <div className="rounded-2xl border border-border bg-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 min-w-0">
          <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-primary/10 text-primary shrink-0">
            <Cpu size={16} />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <p className="text-sm font-semibold text-fg truncate">{server.name}</p>
              {server.transport && (
                <span className="inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded bg-surface-2 text-muted border border-border">
                  {server.transport}
                </span>
              )}
              {!enabled && (
                <span className="inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded bg-surface-2 text-muted border border-border">
                  Disabled
                </span>
              )}
            </div>
            {server.url && (
              <p className="text-xs text-muted mt-1 truncate font-mono">{server.url}</p>
            )}
          </div>
        </div>

        {canWrite && (
          <div className="flex items-center gap-1.5 shrink-0">
            <button
              onClick={handleToggle}
              disabled={busy !== null}
              title={enabled ? 'Disable' : 'Enable'}
              className="inline-flex items-center h-8 px-2.5 rounded-lg text-xs font-medium border border-border text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-50 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {busy === 'toggle' ? <Loader2 size={12} className="animate-spin" /> : enabled ? 'Disable' : 'Enable'}
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
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// McpSettings — main section
// ---------------------------------------------------------------------------

export default function McpSettings() {
  const canWrite = useCanWrite()

  const [servers, setServers] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [showForm, setShowForm] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await get('/mcp/servers')
      setServers(Array.isArray(data) ? data : (data?.items ?? []))
    } catch (err) {
      setError(err?.message || 'Failed to load MCP servers.')
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
      setServers((prev) => [created, ...prev])
    }
    setShowForm(false)
  }, [])

  const handleChanged = useCallback((updated) => {
    if (updated) {
      setServers((prev) => prev.map((s) => (s.id === updated.id ? updated : s)))
    }
  }, [])

  const handleDeleted = useCallback((id) => {
    setServers((prev) => prev.filter((s) => s.id !== id))
  }, [])

  return (
    <div className="space-y-6">
      <SettingsPageHeader
        title="MCP servers"
        description="Register external Model Context Protocol (MCP) servers for your organisation. Auth tokens are stored encrypted and never returned after saving."
      />

      <SettingsCard
        title="Registered servers"
        description="MCP servers available to this organisation."
      >
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted py-6 justify-center">
            <Loader2 size={16} className="animate-spin" /> Loading servers…
          </div>
        ) : error ? (
          <div className="py-2">
            <ErrorText>{error}</ErrorText>
          </div>
        ) : servers.length === 0 ? (
          <div className="flex flex-col items-center justify-center text-center py-8 px-6 gap-2">
            <Cpu size={26} className="text-muted/40" />
            <p className="text-sm font-medium text-fg">No MCP servers yet</p>
            <p className="text-xs text-muted max-w-sm">
              Add a server below to connect external MCP endpoints to this organisation.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {servers.map((s) => (
              <ServerRow
                key={s.id}
                server={s}
                canWrite={canWrite}
                onChanged={handleChanged}
                onDeleted={handleDeleted}
              />
            ))}
          </div>
        )}
      </SettingsCard>

      {canWrite && (
        <SettingsCard title="Add a server" description="Connect a new MCP server endpoint.">
          {showForm ? (
            <AddServerForm onSaved={handleSaved} onCancel={() => setShowForm(false)} />
          ) : (
            <button
              onClick={() => setShowForm(true)}
              className="inline-flex items-center gap-2 h-9 px-4 text-sm font-medium rounded-xl border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors"
            >
              <Plus size={15} />
              Add server
            </button>
          )}
        </SettingsCard>
      )}
    </div>
  )
}
