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
import { Plug, Plus, Loader2, Trash2, Copy, Check, AlertTriangle, KeyRound, Terminal, Globe, Link2, Bot, Info, ExternalLink } from 'lucide-react'
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
  PrimaryButton,
  ErrorText,
  inputCls,
} from './SettingsUI.jsx'
import { toast } from '../../../components/ui/Toast.jsx'

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
        <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-muted">
          {Icon && <Icon size={12} />} {label}
        </span>
        <button
          type="button"
          onClick={copy}
          className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs text-muted hover:text-primary border border-border hover:border-primary/40 transition-colors"
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre
        className={[
          'rounded-lg bg-bg border border-border px-3 py-2 text-[11px] text-fg overflow-x-auto whitespace-pre-wrap break-all',
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
    <div className="rounded-xl border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-4 space-y-4">
      <div className="flex items-start gap-2">
        <AlertTriangle size={15} className="text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
        <p className="text-xs text-amber-800 dark:text-amber-300">
          Copy this key now — it is shown <strong>only once</strong> and cannot be
          retrieved again. It authenticates as you, so store it somewhere safe.
        </p>
      </div>

      <div className="flex items-center gap-2 rounded-lg bg-bg border border-border px-3 py-2">
        <code className="flex-1 min-w-0 text-xs text-fg font-mono break-all">{rawKey}</code>
      </div>

      <div className="space-y-3 border-t border-amber-200 dark:border-amber-900 pt-3">
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
        <button
          type="button"
          onClick={onDismiss}
          className="px-3 py-1.5 rounded-lg text-xs font-medium text-muted hover:text-fg border border-border hover:bg-surface-2 transition-colors"
        >
          I&apos;ve saved it — dismiss
        </button>
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

const SETUP_TABS = [
  { id: 'claude', label: 'Claude', icon: Bot },
  { id: 'chatgpt', label: 'ChatGPT', icon: Globe },
  { id: 'code', label: 'Claude Code', icon: Terminal },
] as const

function Step({ n, children }) {
  return (
    <li className="flex gap-2.5 text-xs text-fg">
      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[11px] font-semibold text-primary">
        {n}
      </span>
      <span className="pt-0.5 leading-relaxed">{children}</span>
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

        <div role="tablist" aria-label="Choose your app" className="flex gap-1 rounded-lg bg-surface-2 p-1">
          {SETUP_TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
              className={[
                'flex-1 inline-flex items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium transition-colors',
                tab === t.id ? 'bg-bg text-fg shadow-sm' : 'text-muted hover:text-fg',
              ].join(' ')}
            >
              <t.icon size={13} aria-hidden="true" /> {t.label}
            </button>
          ))}
        </div>

        {tab === 'claude' && (
          <ol className="space-y-2.5">
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
          <ol className="space-y-2.5">
            <Step n={1}>
              In ChatGPT, open <strong>Settings → Apps &amp; Connectors</strong>, turn on <strong>Developer mode</strong>{' '}
              under Advanced, then choose <strong>Create</strong>.
            </Step>
            <Step n={2}>Name it <strong>Nubi</strong>, paste the server URL above and choose <strong>OAuth</strong>.</Step>
            <Step n={3}>Nubi opens — pick the organisation and click <strong>Approve</strong>.</Step>
          </ol>
        )}

        {tab === 'code' && (
          <ol className="space-y-2.5">
            <Step n={1}>
              Run this in your terminal:
              <div className="mt-2">
                <CopyBlock label="Command" icon={Terminal} text={claudeCodeOAuthCommand(url)} />
              </div>
            </Step>
            <Step n={2}>
              In Claude Code, type <code className="rounded bg-surface-2 px-1 font-mono">/mcp</code>, pick{' '}
              <strong>nubi</strong> and choose <strong>Authenticate</strong>.
            </Step>
            <Step n={3}>Your browser opens Nubi. Click <strong>Approve</strong>, then go back to Claude Code.</Step>
          </ol>
        )}

        {!webReady && tab !== 'code' && (
          <p className="flex gap-2 text-xs text-muted">
            <Info size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
            Claude and ChatGPT connect from the internet, so they need Nubi&apos;s public HTTPS address. This
            environment uses a local one — use Claude Code here instead.
          </p>
        )}

        <div className="border-t border-border pt-3">
          <p className="text-xs font-medium text-fg mb-2">Connected apps</p>
          <p className="text-[11px] text-muted -mt-1 mb-2">Disconnecting signs the app out at once.</p>
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
                  <Globe size={14} className="text-muted shrink-0" />
                  <div className="min-w-0 flex-1">
                    <p className="text-xs text-fg truncate">
                      {a.client_name}
                      {a.redirect_host ? <span className="text-muted"> · {a.redirect_host}</span> : null}
                    </p>
                    <p className="text-[11px] text-muted">
                      Connected {fmtDate(a.created_at)}
                      {a.last_used_at ? ` · last used ${fmtDate(a.last_used_at)}` : ' · never used'}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => disconnect(a)}
                    disabled={busy === a.id}
                    title="Disconnect"
                    aria-label="Disconnect app"
                    className="w-7 h-7 flex items-center justify-center rounded-lg text-muted hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors disabled:opacity-30 shrink-0"
                  >
                    {busy === a.id ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                  </button>
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
        description="Connect your own AI — Claude or ChatGPT in the browser, Claude Desktop or Claude Code — to this workspace. Nubi exposes a Model Context Protocol (MCP) server so the AI can browse your schema, run governed queries, and build dashboards on your behalf, staying inside your row-level-security boundary the whole time."
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
            <PrimaryButton type="submit" busy={creating} disabled={creating || !name.trim()} className="shrink-0">
              {!creating && <Plus size={14} />}
              Generate key
            </PrimaryButton>
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
            <KeyRound size={22} className="mx-auto text-muted/40 mb-2" />
            <p className="text-sm text-muted">No connection keys yet — generate one above.</p>
          </div>
        ) : (
          <ul className="divide-y divide-border -my-1.5">
            {keys.map((k) => {
              const rowBusy = busyRow === k.id
              return (
                <li key={k.id} className="flex items-center gap-3 py-2.5">
                  <KeyRound size={14} className="text-muted shrink-0" />
                  <div className="min-w-0 flex-1">
                    <p className="text-xs text-fg truncate font-mono">
                      {k.name} · ••••{k.last_four ?? '????'}
                    </p>
                    <p className="text-[11px] text-muted">
                      Created {fmtDate(k.created_at)}
                      {k.last_used_at ? ` · last used ${fmtDate(k.last_used_at)}` : ' · never used'}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleRevoke(k)}
                    disabled={rowBusy}
                    title="Revoke key"
                    aria-label="Revoke key"
                    className="w-7 h-7 flex items-center justify-center rounded-lg text-muted hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors disabled:opacity-30 shrink-0"
                  >
                    {rowBusy ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </SettingsCard>
    </div>
  )
}
