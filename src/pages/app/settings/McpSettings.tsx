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
import { Link } from 'react-router-dom'
import { Plus, Trash2, X, Check, AlertTriangle, Zap, ArrowUpRight, Loader2 } from 'lucide-react'
import { get, post, put, del } from '../../../lib/api.js'
import { useCanWrite } from '../../../contexts/OrgContext.jsx'
import {
  SettingsPageHeader,
  SettingsCard,
  ErrorText,
  inputCls,
} from './SettingsUI.jsx'
import { toast } from '../../../components/ui/Toast.jsx'
import ConnectionStatusBadge from '../../../components/ui/ConnectionStatusBadge.jsx'
import Button from '../../../components/ui/Button.jsx'
import Tag from '../../../components/ui/Tag.jsx'
import ToolLogo from '../../../components/brand/ToolLogo'
import OAuthConsentFlow from '../../../components/illustrations/OAuthConsentFlow'

// last_test_ok is a tri-state (true/false/null) -> the shared status vocabulary.
function mcpStatusState(server) {
  if (server.last_test_ok === true) return 'online'
  if (server.last_test_ok === false) return 'offline'
  return 'unknown'
}

function mcpStatusDetail(server) {
  if (server.last_test_ok === true) {
    return `${server.last_test_tool_count ?? 0} tool(s) available`
  }
  if (server.last_test_ok === false) {
    return server.last_test_error || 'Last test failed'
  }
  return 'Not tested yet'
}

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
    const body: Record<string, any> = { name, url, transport: form.transport, enabled: form.enabled }
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
    <div className="border border-border bg-surface p-4 space-y-3" style={{ borderRadius: 2 }}>
      <div className="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-3">
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
        <div className="sm:w-36">
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
        <div className="nubi-notice nubi-notice-error text-xs" role="alert">
          <AlertTriangle size={14} className="shrink-0 mt-0.5" aria-hidden="true" />
          <span>{error}</span>
        </div>
      )}

      <div className="flex items-center gap-2 pt-1">
        <Button onClick={handleSave} loading={saving}>
          {!saving && <Check size={14} aria-hidden="true" />}
          Add server
        </Button>
        <Button variant="secondary" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Server row
// ---------------------------------------------------------------------------

function ServerRow({ server, canWrite, onChanged, onDeleted }) {
  const [busy, setBusy] = useState(null) // 'toggle' | 'delete' | 'test' | null
  const [confirmDelete, setConfirmDelete] = useState(false)

  const enabled = server.enabled !== false

  const handleTest = useCallback(async () => {
    setBusy('test')
    try {
      const result = await post(`/mcp/servers/${server.id}/test`, {})
      if (result?.server) onChanged?.(result.server)
      if (result?.ok) {
        toast.success(`Connected — ${result.tool_count ?? 0} tool(s) available`)
      } else {
        toast.error(result?.error ? `Test failed: ${result.error}` : 'Test failed.')
      }
    } catch (err) {
      toast.error(err?.message || 'Failed to test server.')
    } finally {
      setBusy(null)
    }
  }, [server, onChanged])

  const handleToggle = useCallback(async () => {
    setBusy('toggle')
    try {
      const updated = await put(`/mcp/servers/${server.id}`, { enabled: !enabled })
      onChanged?.(updated ?? { ...server, enabled: !enabled })
      toast.success(enabled ? 'Server disabled.' : 'Server enabled.')
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
    <div className="border border-border bg-surface p-4" style={{ borderRadius: 2 }}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 min-w-0">
          <span
            className="flex items-center justify-center w-10 h-10 border border-border bg-surface text-fg shrink-0"
            style={{ borderRadius: 2 }}
          >
            <ToolLogo slug="modelcontextprotocol" size={22} decorative />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <p className="text-sm font-semibold text-fg truncate">{server.name}</p>
              <ConnectionStatusBadge state={mcpStatusState(server)} detail={mcpStatusDetail(server)} />
              {server.transport && <Tag caps={false}>{server.transport}</Tag>}
              {!enabled && <Tag dot>Disabled</Tag>}
            </div>
            {server.url && (
              <p className="text-xs text-muted mt-1 truncate font-mono">{server.url}</p>
            )}
          </div>
        </div>

        {canWrite && (
          <div className="flex items-center gap-1.5 shrink-0">
            <Button size="sm" variant="secondary" onClick={handleTest} disabled={busy !== null} loading={busy === 'test'} aria-label="Test connection">
              {busy !== 'test' && <Zap size={12} strokeWidth={2.2} aria-hidden="true" />}
              {busy === 'test' ? 'Testing…' : 'Test'}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={handleToggle}
              disabled={busy !== null}
              loading={busy === 'toggle'}
              aria-label={enabled ? 'Disable server' : 'Enable server'}
            >
              {enabled ? 'Disable' : 'Enable'}
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
              <Button size="icon-sm" variant="secondary" onClick={() => setConfirmDelete(true)} title="Delete" aria-label="Delete server">
                <Trash2 size={13} aria-hidden="true" />
              </Button>
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

/** Pointer to the reverse direction: AI apps connecting to Nubi. Lives in Settings > Connections. */
function AiAppsCard() {
  return (
    <section
      className="border border-border bg-surface grid gap-6 p-5 sm:p-6 md:grid-cols-[minmax(0,1fr)_minmax(0,340px)] items-center"
      style={{ borderRadius: 2 }}
      aria-labelledby="mcp-ai-apps-title"
    >
      <div className="min-w-0">
        <p className="font-mono text-[11px] uppercase tracking-[0.06em] text-subtle">Nubi as an MCP server</p>
        <h3 id="mcp-ai-apps-title" className="font-display font-semibold text-[15px] text-fg mt-1.5">
          Want Claude or ChatGPT to use Nubi?
        </h3>
        <p className="text-sm text-muted mt-1.5 max-w-prose leading-relaxed">
          This page registers MCP servers that Nubi&apos;s own AI can call. To let an AI app browse your
          schema and build boards, add Nubi to it instead: paste one URL, sign in and approve the
          scopes. The app acts as you, in the organisation you pick.
        </p>
        <ul className="flex flex-wrap items-center gap-x-5 gap-y-2 mt-4 list-none p-0 m-0">
          {[
            { slug: 'claude', label: 'Claude' },
            { slug: 'openai', label: 'ChatGPT' },
            { slug: 'claudecode', label: 'Claude Code' },
          ].map((c) => (
            <li key={c.slug} className="inline-flex items-center gap-2 text-[13px] text-fg">
              <ToolLogo slug={c.slug} mode="brand" size={18} decorative />
              {c.label}
            </li>
          ))}
        </ul>
        <Button asChild size="sm" variant="secondary" className="mt-4">
          <Link to="/settings/connections">
            Set up AI apps <ArrowUpRight size={13} aria-hidden="true" />
          </Link>
        </Button>
      </div>
      <div className="hidden sm:block min-w-0 text-fg">
        <OAuthConsentFlow compact title="An AI app asks, you consent to scopes, it reads your data" />
      </div>
    </section>
  )
}

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

      <AiAppsCard />

      <SettingsCard
        title="Registered servers"
        description="MCP servers available to this organisation."
      >
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted py-6 justify-center">
            <Loader2 size={16} className="animate-spin" /> Loading servers…
          </div>
        ) : error ? (
          <div className="py-2 flex items-center gap-3">
            <ErrorText>{error}</ErrorText>
            <Button size="xs" variant="secondary" onClick={load}>Retry</Button>
          </div>
        ) : servers.length === 0 ? (
          <div className="flex flex-col items-center justify-center text-center py-8 px-6 gap-2">
            <ToolLogo slug="modelcontextprotocol" size={28} className="text-subtle" decorative />
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
            <Button variant="secondary" onClick={() => setShowForm(true)}>
              <Plus size={15} aria-hidden="true" />
              Add server
            </Button>
          )}
        </SettingsCard>
      )}
    </div>
  )
}
