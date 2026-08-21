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
 * Endpoints (src/lib/apiKeys.ts):
 *   GET    /auth/api-keys        — list the caller's keys
 *   POST   /auth/api-keys        { name } → { key, api_key }  (raw key ONCE)
 *   DELETE /auth/api-keys/{id}   — revoke
 */

import { useCallback, useEffect, useState } from 'react'
import { Plug, Plus, Loader2, Trash2, Copy, Check, AlertTriangle, KeyRound, Terminal } from 'lucide-react'
import {
  listApiKeys,
  createApiKey,
  revokeApiKey,
  claudeCodeAddCommand,
  claudeDesktopConfigSnippet,
} from '../../../lib/apiKeys.js'
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
        description="Connect your own Claude — Desktop or Code — to this workspace. Nubi exposes a Model Context Protocol (MCP) server so Claude can browse your schema, run governed queries, and build dashboards on your behalf, staying inside your row-level-security boundary the whole time."
      />

      {err && <ErrorText>{err}</ErrorText>}

      <SettingsCard
        title="New connection key"
        description="Give it a name so you can recognise it later (e.g. “Claude Desktop — laptop”). The key acts as you and is scoped to this organisation."
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
