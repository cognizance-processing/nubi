/**
 * BridgesSettings — create and manage Bridge v2 agents (Settings → Bridges).
 *
 * A *bridge* is a lightweight agent (`nubi bridge start`) you run inside your
 * VPC / on-prem network so Nubi can reach databases it can't connect to
 * directly. This page lets an owner/admin:
 *   - create a bridge (just a name),
 *   - mint a bridge token (shown ONCE) with the exact install snippet,
 *   - rotate / revoke tokens,
 *   - delete a bridge.
 *
 * Mirrors backend/app/routes/bridges.py + app/auth/bridge_tokens.py. Token
 * management is owner/admin-only on the backend; viewers see a read-only hint.
 *
 * react-hooks/set-state-in-effect: effects here only schedule a load via the
 * `setTimeout(load, 0)` deferral pattern — no setState in the effect body.
 */

import { useEffect, useState, useCallback } from 'react'
import {
  Loader2,
  Network,
  Plus,
  Trash2,
  Copy,
  Check,
  KeyRound,
  RotateCw,
  Ban,
  AlertTriangle,
  Terminal,
} from 'lucide-react'
import { useOrg, useCanWrite } from '../../../contexts/OrgContext.jsx'
import {
  listBridges,
  createBridge,
  deleteBridge,
  listBridgeTokens,
  mintBridgeToken,
  rotateBridgeToken,
  revokeBridgeToken,
} from '../../../lib/bridges.js'
import {
  SettingsPageHeader,
  SettingsCard,
  PrimaryButton,
  DangerZone,
  DangerRow,
  DangerButton,
  inputCls,
} from './SettingsUI.jsx'

const MANAGE_ROLES = ['owner', 'admin']

// ---------------------------------------------------------------------------
// Small presentational helpers
// ---------------------------------------------------------------------------

function StatusDot({ status }) {
  const online = status === 'online'
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted">
      <span
        className={`w-2 h-2 rounded-full ${online ? 'bg-emerald-500' : 'bg-muted/40'}`}
        aria-hidden
      />
      {online ? 'Online' : 'Offline'}
    </span>
  )
}

function fmtDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

function tokenState(t) {
  if (t.revoked_at) return { label: 'Revoked', cls: 'bg-red-500/10 text-red-600 dark:text-red-400' }
  if (t.grace_until) return { label: 'Rotated', cls: 'bg-amber-500/10 text-amber-600 dark:text-amber-400' }
  return { label: 'Active', cls: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' }
}

/** One-time raw-token reveal box with copy + install snippet. */
function TokenReveal({ token, bridgeId, onDismiss }) {
  const [copied, setCopied] = useState(null)
  const installSnippet = `pip install 'nubi[bridge]'\nnubi bridge start --token ${token} --bridge-id ${bridgeId}`

  function copy(text, key) {
    try {
      navigator.clipboard.writeText(text)
      setCopied(key)
      setTimeout(() => setCopied(null), 2000)
    } catch {
      /* clipboard blocked */
    }
  }

  return (
    <div className="rounded-xl border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-4 space-y-3">
      <div className="flex items-start gap-2">
        <AlertTriangle size={15} className="text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
        <p className="text-xs text-amber-800 dark:text-amber-300">
          Copy this token now — it is shown <strong>only once</strong> and cannot be
          retrieved again. Store it somewhere safe (e.g. a secret manager).
        </p>
      </div>

      {/* Raw token */}
      <div className="flex items-center gap-2 rounded-lg bg-bg border border-border px-3 py-2">
        <code className="flex-1 min-w-0 text-xs text-fg font-mono break-all">{token}</code>
        <button
          type="button"
          onClick={() => copy(token, 'token')}
          className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs text-muted hover:text-primary border border-border hover:border-primary/40 transition-colors shrink-0"
        >
          {copied === 'token' ? <Check size={12} /> : <Copy size={12} />}
          {copied === 'token' ? 'Copied' : 'Copy'}
        </button>
      </div>

      {/* Install snippet */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-muted">
            <Terminal size={12} /> Run the agent
          </span>
          <button
            type="button"
            onClick={() => copy(installSnippet, 'snippet')}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs text-muted hover:text-primary border border-border hover:border-primary/40 transition-colors"
          >
            {copied === 'snippet' ? <Check size={12} /> : <Copy size={12} />}
            {copied === 'snippet' ? 'Copied' : 'Copy'}
          </button>
        </div>
        <pre className="rounded-lg bg-bg border border-border px-3 py-2 text-[11px] text-fg font-mono overflow-x-auto whitespace-pre">
{installSnippet}
        </pre>
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
// Tokens section for a single bridge
// ---------------------------------------------------------------------------

function BridgeTokens({ bridgeId, canManage }) {
  const [tokens, setTokens] = useState([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(null)
  const [busyRow, setBusyRow] = useState(null)
  const [minting, setMinting] = useState(false)
  const [revealed, setRevealed] = useState(null) // raw token shown once

  const load = useCallback(async () => {
    if (!canManage) {
      setTokens([])
      setLoading(false)
      return
    }
    setLoading(true)
    const rows = await listBridgeTokens(bridgeId)
    setTokens(rows)
    setLoading(false)
  }, [bridgeId, canManage])

  useEffect(() => {
    const t = setTimeout(load, 0)
    return () => clearTimeout(t)
  }, [load])

  async function handleMint() {
    setErr(null)
    setMinting(true)
    try {
      const res = await mintBridgeToken(bridgeId)
      setRevealed(res?.token ?? null)
      await load()
    } catch (e) {
      setErr(e?.message ?? 'Failed to generate token.')
    } finally {
      setMinting(false)
    }
  }

  async function handleRotate(t) {
    setErr(null)
    setBusyRow(t.id)
    try {
      const res = await rotateBridgeToken(bridgeId, t.id)
      setRevealed(res?.token ?? null)
      await load()
    } catch (e) {
      setErr(e?.message ?? 'Failed to rotate token.')
    } finally {
      setBusyRow(null)
    }
  }

  async function handleRevoke(t) {
    if (!window.confirm('Revoke this token? The bridge agent using it will lose its connection immediately.')) return
    setErr(null)
    setBusyRow(t.id)
    try {
      await revokeBridgeToken(bridgeId, t.id)
      await load()
    } catch (e) {
      setErr(e?.message ?? 'Failed to revoke token.')
    } finally {
      setBusyRow(null)
    }
  }

  if (!canManage) {
    return (
      <p className="text-xs text-muted">
        Only owners and admins can view or manage bridge tokens.
      </p>
    )
  }

  return (
    <div className="space-y-3">
      {err && (
        <p className="text-xs text-red-600 dark:text-red-400 rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900 px-3 py-2">
          {err}
        </p>
      )}

      {revealed && (
        <TokenReveal token={revealed} bridgeId={bridgeId} onDismiss={() => setRevealed(null)} />
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-xs text-muted py-1">
          <Loader2 size={13} className="animate-spin" /> Loading tokens…
        </div>
      ) : tokens.length === 0 ? (
        <p className="text-xs text-muted">
          No tokens yet. Generate one to authenticate the agent.
        </p>
      ) : (
        <ul className="divide-y divide-border -my-1.5">
          {tokens.map((t) => {
            const st = tokenState(t)
            const rowBusy = busyRow === t.id
            const isRevoked = Boolean(t.revoked_at)
            return (
              <li key={t.id} className="flex items-center gap-3 py-2.5">
                <KeyRound size={14} className="text-muted shrink-0" />
                <div className="min-w-0 flex-1">
                  <p className="text-xs text-fg truncate font-mono">
                    {t.name} · ••••{t.last_four ?? '????'}
                  </p>
                  <p className="text-[11px] text-muted">Created {fmtDate(t.created_at)}</p>
                </div>
                <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-md shrink-0 ${st.cls}`}>
                  {st.label}
                </span>
                {!isRevoked && (
                  <>
                    <button
                      type="button"
                      onClick={() => handleRotate(t)}
                      disabled={rowBusy}
                      title="Rotate token"
                      className="w-7 h-7 flex items-center justify-center rounded-lg text-muted hover:text-primary hover:bg-surface-2 transition-colors disabled:opacity-30 shrink-0"
                    >
                      {rowBusy ? <Loader2 size={13} className="animate-spin" /> : <RotateCw size={13} />}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleRevoke(t)}
                      disabled={rowBusy}
                      title="Revoke token"
                      className="w-7 h-7 flex items-center justify-center rounded-lg text-muted hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors disabled:opacity-30 shrink-0"
                    >
                      <Ban size={13} />
                    </button>
                  </>
                )}
              </li>
            )
          })}
        </ul>
      )}

      <PrimaryButton type="button" busy={minting} disabled={minting} onClick={handleMint} className="!py-1.5 !text-xs">
        {!minting && <KeyRound size={13} />}
        Generate token
      </PrimaryButton>
    </div>
  )
}

// ---------------------------------------------------------------------------
// One bridge card
// ---------------------------------------------------------------------------

function BridgeCard({ bridge, canManage, onDeleted }) {
  const [deleting, setDeleting] = useState(false)
  const [err, setErr] = useState(null)

  async function handleDelete() {
    if (!window.confirm(`Delete bridge "${bridge.name}"? Any agent using it will stop working and connectors pinned to it will fail.`)) return
    setErr(null)
    setDeleting(true)
    try {
      await deleteBridge(bridge.id)
      onDeleted(bridge.id)
    } catch (e) {
      setErr(e?.message ?? 'Failed to delete bridge.')
      setDeleting(false)
    }
  }

  return (
    <SettingsCard>
      <div className="space-y-4">
        {/* Header row */}
        <div className="flex items-center gap-3">
          <Network size={16} className="text-muted shrink-0" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-fg truncate">{bridge.name}</p>
            <p className="text-[11px] text-muted truncate">
              ID {bridge.id} · last seen {fmtDate(bridge.last_seen_at)}
            </p>
          </div>
          <StatusDot status={bridge.status} />
        </div>

        {/* Tokens */}
        <div className="border-t border-border pt-4">
          <p className="text-xs font-semibold text-fg mb-2">Tokens</p>
          <BridgeTokens bridgeId={bridge.id} canManage={canManage} />
        </div>

        {/* Delete */}
        {canManage && (
          <div className="border-t border-border pt-3 flex items-center justify-between gap-3">
            {err ? (
              <span className="text-xs text-red-600 dark:text-red-400">{err}</span>
            ) : (
              <span className="text-[11px] text-muted">Deleting removes the bridge and all its tokens.</span>
            )}
            <DangerButton busy={deleting} disabled={deleting} onClick={handleDelete} className="!py-1.5 !text-xs shrink-0">
              {!deleting && <Trash2 size={13} />}
              Delete
            </DangerButton>
          </div>
        )}
      </div>
    </SettingsCard>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function BridgesSettings() {
  const { activeOrg } = useOrg()
  const canWrite = useCanWrite()
  const canManage = MANAGE_ROLES.includes(activeOrg?.role)

  const [bridges, setBridges] = useState([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(null)

  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    const rows = await listBridges()
    setBridges(rows)
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
      await createBridge(trimmed)
      setName('')
      await load()
    } catch (e2) {
      setErr(e2?.message ?? 'Failed to create bridge.')
    } finally {
      setCreating(false)
    }
  }

  function handleDeleted(id) {
    setBridges((prev) => prev.filter((b) => b.id !== id))
  }

  return (
    <div className="space-y-6">
      <SettingsPageHeader
        title="Bridges"
        description="Run a bridge agent inside your VPC or on-prem network so Nubi can reach databases it can't connect to directly."
      />

      {err && (
        <p className="text-xs text-red-600 dark:text-red-400 rounded-xl bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900 px-3 py-2">
          {err}
        </p>
      )}

      {/* Create — writers only */}
      {canWrite && (
        <SettingsCard
          title="New bridge"
          description="Give the bridge a name. After creating it, generate a token and run the agent with the install snippet shown."
        >
          <form onSubmit={handleCreate} className="flex flex-col sm:flex-row gap-2">
            <input
              type="text"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. prod-vpc-east"
              className={inputCls}
              aria-label="Bridge name"
            />
            <PrimaryButton type="submit" busy={creating} disabled={creating || !name.trim()} className="shrink-0">
              {!creating && <Plus size={14} />}
              Create bridge
            </PrimaryButton>
          </form>
        </SettingsCard>
      )}

      {/* List */}
      {loading ? (
        <SettingsCard>
          <div className="flex items-center gap-2 text-xs text-muted py-2">
            <Loader2 size={13} className="animate-spin" /> Loading bridges…
          </div>
        </SettingsCard>
      ) : bridges.length === 0 ? (
        <SettingsCard>
          <div className="py-8 text-center">
            <Network size={24} className="mx-auto text-muted/40 mb-2" />
            <p className="text-sm text-muted">
              No bridges yet — create one to ingest from inside your VPC.
            </p>
          </div>
        </SettingsCard>
      ) : (
        bridges.map((b) => (
          <BridgeCard key={b.id} bridge={b} canManage={canManage} onDeleted={handleDeleted} />
        ))
      )}

      {!canManage && bridges.length > 0 && (
        <p className="text-xs text-muted">
          Token management (generate / rotate / revoke) is available to owners and admins only.
        </p>
      )}

      {/* Danger note when a viewer can't write at all */}
      {!canWrite && bridges.length === 0 && (
        <DangerZone>
          <DangerRow
            title="Read-only access"
            description="You don't have permission to create bridges. Ask an owner or admin in this organisation."
          >
            <span className="text-xs text-muted">Viewer</span>
          </DangerRow>
        </DangerZone>
      )}
    </div>
  )
}
