/**
 * ConnectionsSettings — Settings → Connections.
 *
 * Lets a user connect their own Claude (Desktop or Code) to this workspace,
 * the same way you'd connect Claude to ClickUp or another external tool —
 * except here Nubi is the one being connected *to* (Nubi as an MCP server,
 * see docs/mcp.md § 3). This is the reverse direction from Settings → MCP
 * servers, which registers external MCP servers Nubi's own AI chat can call.
 *
 * A connection is backed by a long-lived, personal API key (`nubi_ak_…`,
 * app/auth/api_keys.py). It authenticates exactly as the minting user, scoped
 * to the org it was minted for — keys are per-user, not org-managed, so no
 * useCanWrite() gate here (mirrors GitHub/GitLab personal access tokens).
 *
 * Browser-hosted AI apps (claude.ai, ChatGPT) can't take a pasted key, so they
 * connect over OAuth instead: the user adds Nubi's MCP URL as a custom
 * connector and approves it on /oauth/consent. Those show up here as
 * "Connected apps" (src/lib/oauthConnect.ts) and act exactly like a key —
 * same user, same org — except their tokens only work on the MCP endpoint.
 *
 * Endpoints (src/lib/apiKeys.ts):
 *   GET    /auth/api-keys        — list the caller's keys
 *   POST   /auth/api-keys        { name } → { key, api_key }  (raw key ONCE)
 *   DELETE /auth/api-keys/{id}   — revoke
 */

import { useCallback, useEffect, useState } from 'react'
import { Plug, Plus, Loader2, Trash2, Copy, Check, AlertTriangle, KeyRound, Terminal, Globe, Link2, Info, ExternalLink } from 'lucide-react'
import {
  listApiKeys,
  createApiKey,
  revokeApiKey,
  claudeCodeAddCommand,
  claudeDesktopConfigSnippet,
} from '../../../lib/apiKeys.js'
import {
  claudeCodeOAuthCommand,
  connectorUrl,
  isPublicHttps,
  listConnections,
  revokeConnection,
  type Connection,
} from '../../../lib/oauthConnect.js'
import {
  SettingsPageHeader,
  SettingsCard,
  ErrorText,
  inputCls,
} from './SettingsUI.jsx'
import { toast } from '../../../components/ui/Toast.jsx'
import Button from '../../../components/ui/Button.jsx'
import ToolLogo from '../../../components/brand/ToolLogo'

function fmtDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

// ---------------------------------------------------------------------------
// Copy-to-clipboard block
// ---------------------------------------------------------------------------

function CopyBlock({ label, icon: Icon, text, mono = true }) {
  const [copied, setCopied] = useState(false)

  function copy() {
    try {
      navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      /* clipboard blocked */
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <span className="inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-[0.06em] text-subtle">
          {Icon && <Icon size={12} />} {label}
        </span>
        <Button size="xs" variant="secondary" onClick={copy} aria-label={`Copy ${label}`}>
          {copied ? <Check size={12} aria-hidden="true" /> : <Copy size={12} aria-hidden="true" />}
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
      <pre
        className={[
          'bg-surface-2 border border-border px-3 py-2 text-[12px] text-fg overflow-x-auto whitespace-pre-wrap break-all',
          mono ? 'font-mono' : '',
        ].join(' ')}
      >
        {text}
      </pre>
    </div>
  )
}

// ---------------------------------------------------------------------------
// One-time raw-key reveal + connect snippets
// ---------------------------------------------------------------------------

function KeyReveal({ rawKey, onDismiss }) {
  return (
    <div className="space-y-4">
      <div className="nubi-notice nubi-notice-warning text-xs" role="alert">
        <AlertTriangle size={15} className="shrink-0 mt-0.5" aria-hidden="true" />
        <p>
          Copy this key now. It is shown <strong>only once</strong> and cannot be
          retrieved again. It authenticates as you, so store it somewhere safe.
        </p>
      </div>

      <div className="flex items-center gap-2 bg-surface-2 border border-border px-3 py-2">
        <code className="flex-1 min-w-0 text-xs text-fg font-mono break-all">{rawKey}</code>
      </div>

      <div className="space-y-3 border-t border-border pt-3">
        <p className="text-xs text-fg font-medium">Connect Claude Code</p>
        <CopyBlock label="Run in your terminal" icon={Terminal} text={claudeCodeAddCommand(rawKey)} />

        <p className="text-xs text-fg font-medium pt-1">Connect Claude Desktop</p>
        <CopyBlock
          label="Add to claude_desktop_config.json"
          icon={Plug}
          text={claudeDesktopConfigSnippet(rawKey)}
        />
      </div>

      <div className="flex justify-end">
        <Button variant="secondary" size="sm" onClick={onDismiss}>
          I&apos;ve saved it, dismiss
        </Button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Browser AI apps (OAuth)
// ---------------------------------------------------------------------------

// Client marks come from the shared brand registry (official artwork only).
// ChatGPT's mark is OpenAI's.
const SETUP_TABS = [
  { id: 'claude', label: 'Claude', logo: 'claude' },
  { id: 'chatgpt', label: 'ChatGPT', logo: 'openai' },
  { id: 'code', label: 'Claude Code', logo: 'claudecode' },
] as const

/** Best-effort mark for a connected app, by the client name it registered with. */
function appLogoSlug(name: string | undefined): string | null {
  const n = (name ?? '').toLowerCase()
  if (n.includes('claude code')) return 'claudecode'
  if (n.includes('claude') || n.includes('anthropic')) return 'claude'
  if (n.includes('chatgpt') || n.includes('openai')) return 'openai'
  if (n.includes('cursor') || n.includes('gemini')) return n.includes('gemini') ? 'googlegemini' : null
  return null
}

function Step({ n, children }) {
  return (
    <li className="flex gap-3 text-fg">
      <span
        className="flex h-5 w-5 shrink-0 items-center justify-center border border-ink font-mono text-[11px] font-medium text-fg tabular-nums"
        style={{ borderRadius: 2 }}
        aria-hidden="true"
      >
        {n}
      </span>
      <span className="pt-0.5 text-[13px] leading-relaxed">{children}</span>
    </li>
  )
}

function AppConnections() {
  const [tab, setTab] = useState<(typeof SETUP_TABS)[number]['id']>('claude')
  const [apps, setApps] = useState<Connection[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setApps(await listConnections())
    setLoading(false)
  }, [])

  useEffect(() => {
    const t = setTimeout(load, 0)
    return () => clearTimeout(t)
  }, [load])

  async function disconnect(app: Connection) {
    if (!window.confirm(`Disconnect "${app.client_name}"? It will lose access immediately.`)) return
    setBusy(app.id)
    try {
      await revokeConnection(app.id)
      await load()
      toast.success('App disconnected.')
    } catch (e) {
      toast.error((e as Error)?.message ?? 'Failed to disconnect.')
    } finally {
      setBusy(null)
    }
  }

  const url = connectorUrl()
  const webReady = isPublicHttps(url)

  return (
    <SettingsCard
      title="Connect Claude or ChatGPT"
      description="Paste one URL, sign in to Nubi when asked and approve. No keys to copy — the app acts as you, only in the organisation you pick, and you can disconnect it here at any time."
    >
      <div className="space-y-4">
        <CopyBlock label="Server URL" icon={Link2} text={url} />

        <div role="tablist" aria-label="Choose your app" className="flex overflow-x-auto border-b border-border [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {SETUP_TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              id={`setup-tab-${t.id}`}
              aria-selected={tab === t.id}
              aria-controls="setup-panel"
              onClick={() => setTab(t.id)}
              className={[
                'nubi-focus-inset -mb-px inline-flex shrink-0 items-center gap-2 whitespace-nowrap border-b-2 px-3 sm:px-4 py-2.5 text-[13px] font-medium transition-colors',
                tab === t.id ? 'border-ink text-fg' : 'border-transparent text-muted hover:text-fg',
              ].join(' ')}
            >
              <ToolLogo slug={t.logo} mode={tab === t.id ? 'brand' : 'mono'} size={16} decorative />
              {t.label}
            </button>
          ))}
        </div>

        <div id="setup-panel" role="tabpanel" aria-labelledby={`setup-tab-${tab}`}>
        {tab === 'claude' && (
          <ol className="space-y-3 list-none p-0 m-0">
            <Step n={1}>
              In Claude (web or desktop app), open <strong>Settings → Connectors</strong> and choose{' '}
              <strong>Add custom connector</strong>.{' '}
              <a href="https://claude.ai/settings/connectors" target="_blank" rel="noreferrer noopener"
                className="inline-flex items-center gap-0.5 text-primary hover:opacity-80">
                Open <ExternalLink size={11} aria-hidden="true" />
              </a>
            </Step>
            <Step n={2}>Name it <strong>Nubi</strong> and paste the server URL above.</Step>
            <Step n={3}>Click <strong>Connect</strong>. Nubi opens — pick the organisation and click <strong>Approve</strong>.</Step>
          </ol>
        )}

        {tab === 'chatgpt' && (
          <ol className="space-y-3 list-none p-0 m-0">
            <Step n={1}>
              In ChatGPT, open <strong>Settings → Apps &amp; Connectors</strong>, turn on <strong>Developer mode</strong>{' '}
              under Advanced, then choose <strong>Create</strong>.
            </Step>
            <Step n={2}>Name it <strong>Nubi</strong>, paste the server URL above and choose <strong>OAuth</strong>.</Step>
            <Step n={3}>Nubi opens — pick the organisation and click <strong>Approve</strong>.</Step>
          </ol>
        )}

        {tab === 'code' && (
          <ol className="space-y-3 list-none p-0 m-0">
            <Step n={1}>
              Run this in your terminal:
              <div className="mt-2">
                <CopyBlock label="Command" icon={Terminal} text={claudeCodeOAuthCommand(url)} />
              </div>
            </Step>
            <Step n={2}>
              In Claude Code, type <code className="bg-surface-2 border border-border px-1 font-mono text-[12px]">/mcp</code>, pick{' '}
              <strong>nubi</strong> and choose <strong>Authenticate</strong>.
            </Step>
            <Step n={3}>Your browser opens Nubi. Click <strong>Approve</strong>, then go back to Claude Code.</Step>
          </ol>
        )}

        </div>

        {!webReady && tab !== 'code' && (
          <p className="flex gap-2 text-xs text-muted">
            <Info size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
            Claude and ChatGPT connect from the internet, so they need Nubi&apos;s public HTTPS address. This
            environment uses a local one — use Claude Code here instead.
          </p>
        )}

        <div className="border-t border-border pt-3">
          <p className="font-mono text-[11px] uppercase tracking-[0.06em] text-subtle mb-1">Connected apps</p>
          <p className="text-xs text-muted mb-2">Disconnecting signs the app out at once.</p>
          {loading ? (
            <div className="flex items-center gap-2 text-xs text-muted py-1">
              <Loader2 size={13} className="animate-spin" /> Loading…
            </div>
          ) : apps.length === 0 ? (
            <p className="text-xs text-muted">No apps connected to this organisation yet.</p>
          ) : (
            <ul className="divide-y divide-border -my-1.5">
              {apps.map((a) => (
                <li key={a.id} className="flex items-center gap-3 py-2.5">
                  <span
                    className="flex h-8 w-8 shrink-0 items-center justify-center border border-border bg-surface text-muted"
                    style={{ borderRadius: 2 }}
                  >
                    {appLogoSlug(a.client_name)
                      ? <ToolLogo slug={appLogoSlug(a.client_name) as string} mode="brand" size={18} decorative />
                      : <Globe size={15} strokeWidth={1.75} aria-hidden="true" />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-[13px] text-fg truncate">
                      {a.client_name}
                      {a.redirect_host ? <span className="text-muted"> · {a.redirect_host}</span> : null}
                    </p>
                    <p className="text-[11px] text-muted">
                      Connected {fmtDate(a.created_at)}
                      {a.last_used_at ? ` · last used ${fmtDate(a.last_used_at)}` : ' · never used'}
                    </p>
                  </div>
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    onClick={() => disconnect(a)}
                    loading={busy === a.id}
                    title="Disconnect"
                    aria-label={`Disconnect ${a.client_name}`}
                  >
                    {busy !== a.id && <Trash2 size={13} aria-hidden="true" />}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </SettingsCard>
  )
}

export default function ConnectionsSettings() {
  const [keys, setKeys] = useState([])
  const [loading, setLoading] = useState(true)
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  const [busyRow, setBusyRow] = useState(null)
  const [err, setErr] = useState(null)
  const [revealed, setRevealed] = useState(null) // raw key shown once

  const load = useCallback(async () => {
    setLoading(true)
    const rows = await listApiKeys()
    setKeys(rows)
    setLoading(false)
  }, [])

  useEffect(() => {
    const t = setTimeout(load, 0)
    return () => clearTimeout(t)
  }, [load])

  async function handleCreate(e) {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    setErr(null)
    setCreating(true)
    try {
      const res = await createApiKey(trimmed)
      setRevealed(res?.key ?? null)
      setName('')
      await load()
      toast.success('Connection key generated.')
    } catch (e2) {
      setErr(e2?.message ?? 'Failed to generate key.')
      toast.error(e2?.message ?? 'Failed to generate key.')
    } finally {
      setCreating(false)
    }
  }

  async function handleRevoke(k) {
    if (!window.confirm(`Revoke "${k.name}"? Any Claude connected with this key will lose access immediately.`)) return
    setErr(null)
    setBusyRow(k.id)
    try {
      await revokeApiKey(k.id)
      await load()
      toast.success('Key revoked.')
    } catch (e) {
      setErr(e?.message ?? 'Failed to revoke key.')
      toast.error(e?.message ?? 'Failed to revoke key.')
    } finally {
      setBusyRow(null)
    }
  }

  return (
    <div className="space-y-6">
      <SettingsPageHeader
        title="Connections"
        description="Connect your own AI (Claude or ChatGPT in the browser, Claude Desktop or Claude Code) to this workspace. Nubi exposes a Model Context Protocol (MCP) server so the AI can browse your schema, run governed queries and build dashboards on your behalf, staying inside your row-level-security boundary."
      />

      {err && <ErrorText>{err}</ErrorText>}

      <AppConnections />

      <SettingsCard
        title="API keys — scripts, CI and headless agents"
        description="For tools that can't open a browser to sign in. Generate a key and paste it into the tool's config. Give it a name so you can recognise it later (e.g. “Claude Desktop — laptop”). The key acts as you and is scoped to this organisation."
      >
        {revealed ? (
          <KeyReveal rawKey={revealed} onDismiss={() => setRevealed(null)} />
        ) : (
          <form onSubmit={handleCreate} className="flex flex-col sm:flex-row gap-2">
            <input
              type="text"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Claude Desktop — laptop"
              className={inputCls}
              aria-label="Connection key name"
            />
            <Button type="submit" loading={creating} disabled={!name.trim()} className="shrink-0">
              {!creating && <Plus size={14} aria-hidden="true" />}
              Generate key
            </Button>
          </form>
        )}
      </SettingsCard>

      <SettingsCard title="Your connection keys">
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-muted py-1">
            <Loader2 size={13} className="animate-spin" /> Loading…
          </div>
        ) : keys.length === 0 ? (
          <div className="py-6 text-center">
            <KeyRound size={22} strokeWidth={1.5} className="mx-auto text-subtle mb-2" aria-hidden="true" />
            <p className="text-sm text-muted">No connection keys yet — generate one above.</p>
          </div>
        ) : (
          <ul className="divide-y divide-border -my-1.5">
            {keys.map((k) => {
              const rowBusy = busyRow === k.id
              return (
                <li key={k.id} className="flex items-center gap-3 py-2.5">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center border border-border text-muted" style={{ borderRadius: 2 }}><KeyRound size={15} strokeWidth={1.75} aria-hidden="true" /></span>
                  <div className="min-w-0 flex-1">
                    <p className="text-xs text-fg truncate font-mono">
                      {k.name} · ••••{k.last_four ?? '????'}
                    </p>
                    <p className="text-[11px] text-muted">
                      Created {fmtDate(k.created_at)}
                      {k.last_used_at ? ` · last used ${fmtDate(k.last_used_at)}` : ' · never used'}
                    </p>
                  </div>
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    onClick={() => handleRevoke(k)}
                    loading={rowBusy}
                    title="Revoke key"
                    aria-label={`Revoke key ${k.name}`}
                  >
                    {!rowBusy && <Trash2 size={13} aria-hidden="true" />}
                  </Button>
                </li>
              )
            })}
          </ul>
        )}
      </SettingsCard>
    </div>
  )
}
