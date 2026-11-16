/**
 * BridgesSettings — create and manage Bridge v2 agents (Settings → Bridges).
 *
 * A *bridge* is a lightweight agent (`python -m app.bridges.agent` — see
 * docs/bridges.md) you run inside your VPC / on-prem network, or on your own
 * machine, so Nubi can reach databases it can't connect to directly. This
 * page lets an owner/admin:
 *   - create a bridge (just a name),
 *   - mint a bridge token (shown ONCE) with the exact install snippet,
 *   - rotate / revoke tokens,
 *   - delete a bridge.
 *
 * NOT `nubi bridge start` — that CLI command serves a different, file-ingest-
 * only control channel and explicitly discards the binary tunnel frames a
 * database connector's queries need (see agentInstallSnippet in lib/bridges.js).
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
import Button from '../../../components/ui/Button.jsx'
import Tag from '../../../components/ui/Tag.jsx'
import EmptyState from '../../../components/ui/EmptyState.jsx'
import Skeleton from '../../../components/ui/Skeleton.jsx'
import { BridgeTopology } from '../../../components/illustrations'
import {
  listBridges,
  createBridge,
  deleteBridge,
  listBridgeTokens,
  mintBridgeToken,
  rotateBridgeToken,
  revokeBridgeToken,
  agentInstallSnippet,
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
import { toast } from '../../../components/ui/Toast.jsx'
import ConnectionStatusBadge from '../../../components/ui/ConnectionStatusBadge.jsx'

const MANAGE_ROLES = ['owner', 'admin']

// ---------------------------------------------------------------------------
// Small presentational helpers
// ---------------------------------------------------------------------------

// Thin adapter over the shared status badge (Connectors + MCP servers use
// the same one) — kept as StatusDot so existing call sites in this file are
// unchanged; the visual language now lives in one place, not three.
function StatusDot({ status }) {
  return <ConnectionStatusBadge state={status === 'online' ? 'online' : 'offline'} />
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
  if (t.revoked_at) return { label: 'Revoked', variant: 'danger' as const }
  if (t.grace_until) return { label: 'Rotated', variant: 'warning' as const }
  return { label: 'Active', variant: 'success' as const }
}

/** One-time raw-token reveal box with copy + install snippet. */
function TokenReveal({ token, bridgeId, onDismiss }) {
  const [copied, setCopied] = useState(null)
  const installSnippet = agentInstallSnippet(bridgeId, token)

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
    <div className="nubi-notice nubi-notice-warning space-y-3" style={{ display: 'block' }} role="alert">
      <p className="m-0 text-xs">
        <b>Copy this token now.</b> It is shown only once and cannot be
        retrieved again. Store it somewhere safe, such as a secret manager.
      </p>

      {/* Raw token */}
      <div className="flex items-center gap-2 border border-border bg-surface px-3 py-2" style={{ borderRadius: 2 }}>
        <code className="min-w-0 flex-1 break-all font-mono text-xs text-fg">{token}</code>
        <Button variant="secondary" size="xs" onClick={() => copy(token, 'token')} className="shrink-0">
          {copied === 'token' ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
          {copied === 'token' ? 'Copied' : 'Copy'}
        </Button>
      </div>

      {/* Install snippet */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-muted">
            <Terminal size={12} /> Run the agent
          </span>
          <Button variant="secondary" size="xs" onClick={() => copy(installSnippet, 'snippet')}>
            {copied === 'snippet' ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
            {copied === 'snippet' ? 'Copied' : 'Copy'}
          </Button>
        </div>
        <pre className="m-0 overflow-x-auto whitespace-pre border border-border bg-surface px-3 py-2 font-mono text-[11px] text-fg" style={{ borderRadius: 2 }}>
{installSnippet}
        </pre>
      </div>

      <div className="flex justify-end">
        <Button variant="secondary" size="sm" onClick={onDismiss}>
          I&apos;ve saved it. Dismiss
        </Button>
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
      toast.success('Bridge token generated.')
    } catch (e) {
      setErr(e?.message ?? 'Failed to generate token.')
      toast.error(e?.message ?? 'Failed to generate token.')
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
      toast.success('Bridge token rotated.')
    } catch (e) {
      setErr(e?.message ?? 'Failed to rotate token.')
      toast.error(e?.message ?? 'Failed to rotate token.')
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
      toast.success('Bridge token revoked.')
    } catch (e) {
      setErr(e?.message ?? 'Failed to revoke token.')
      toast.error(e?.message ?? 'Failed to revoke token.')
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
        <p className="nubi-notice nubi-notice-error m-0 text-xs" role="alert">
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
                <Tag variant={st.variant} dot className="shrink-0">{st.label}</Tag>
                {!isRevoked && (
                  <>
                    <Button variant="ghost" size="icon-sm" onClick={() => handleRotate(t)} disabled={rowBusy} title="Rotate token" aria-label="Rotate token">
                      {rowBusy ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : <RotateCw size={14} aria-hidden="true" />}
                    </Button>
                    <Button variant="ghost" size="icon-sm" onClick={() => handleRevoke(t)} disabled={rowBusy} title="Revoke token" aria-label="Revoke token">
                      <Ban size={14} aria-hidden="true" />
                    </Button>
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
      toast.success('Bridge deleted.')
    } catch (e) {
      setErr(e?.message ?? 'Failed to delete bridge.')
      toast.error(e?.message ?? 'Failed to delete bridge.')
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
              <span className="text-xs text-danger-fg">{err}</span>
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

  // Re-fetch on org switch, not just on mount — otherwise flipping the org
  // switcher leaves the PREVIOUS org's bridges on screen until a hard reload
  // (the backend now correctly scopes GET /bridges to X-Org-Id; this page
  // has to actually ask again when that header's target changes).
  useEffect(() => {
    const t = setTimeout(load, 0)
    return () => clearTimeout(t)
  }, [load, activeOrg?.id])

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
      toast.success('Bridge created.')
    } catch (e2) {
      setErr(e2?.message ?? 'Failed to create bridge.')
      toast.error(e2?.message ?? 'Failed to create bridge.')
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
        description="Run a bridge agent inside your VPC, on-prem network, or on your own machine so Nubi can reach databases it can't connect to directly."
      />

      {err && (
        <p className="nubi-notice nubi-notice-error text-xs text-danger-fg">
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
          <div className="space-y-2" aria-hidden="true">
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="h-3 w-2/3" />
          </div>
        </SettingsCard>
      ) : bridges.length === 0 ? (
        <SettingsCard>
          <EmptyState
            compact
            art={<BridgeTopology />}
            title="No bridges yet"
            description="A bridge is a small agent you run inside your network. It lets Nubi query databases that are not reachable from the internet, with no inbound ports."
          />
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
