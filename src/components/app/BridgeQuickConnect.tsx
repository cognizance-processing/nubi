/**
 * BridgeQuickConnect — "connect a local/private database" in one step, inline
 * inside the connector form's bridge picker (connectorForms.jsx → BridgeSelect).
 *
 * Bridges already work the same whether the agent runs on a laptop or inside a
 * VPC — it's just an outbound-only WebSocket tunnel, so there's nothing
 * "on-prem" about it architecturally. The friction was UX: standing one up
 * meant leaving the connector form, going to Settings → Bridges, creating a
 * bridge, minting a token, copying the bridge UUID, and coming back — and the
 * connector form had no field to paste that UUID into anyway. This component
 * collapses that into: name it, and it creates the bridge, mints its token
 * (if the caller can), and shows the exact install/run command.
 *
 * Minting a token is owner/admin-only on the backend (app/routes/bridges.py —
 * "a privileged, audit-worthy action"), same as the existing Settings →
 * Bridges page. A writer who isn't an owner/admin can still create the bridge
 * record and point this connector at it; the token step is left for an
 * owner/admin to finish in Settings → Bridges.
 */

import { useState } from 'react'
import { Loader2, Terminal, Copy, Check, AlertTriangle } from 'lucide-react'
import { createBridge, mintBridgeToken, agentInstallSnippet } from '../../lib/bridges.js'
import { toast } from '../ui/Toast.jsx'
import Button from '../ui/Button.jsx'

const inputCls = `
  w-full rounded-xl border border-border bg-bg
  px-3 py-2 text-sm text-fg placeholder:text-muted
  focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent
  transition-colors
`

interface Props {
  defaultName?: string
  canManage: boolean
  onConnected: (bridgeId: string) => void
  onCancel: () => void
}

interface Result {
  bridgeId: string
  token?: string
  pending?: boolean
}

export default function BridgeQuickConnect({ defaultName, canManage, onConnected, onCancel }: Props) {
  const [name, setName] = useState(defaultName || 'local-bridge')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [result, setResult] = useState<Result | null>(null)
  const [copied, setCopied] = useState(false)

  async function handleCreate() {
    const trimmed = name.trim()
    if (!trimmed) return
    setErr(null)
    setBusy(true)
    try {
      const bridge = await createBridge(trimmed)
      if (canManage) {
        const minted = await mintBridgeToken(bridge.id, trimmed)
        setResult({ bridgeId: bridge.id, token: minted.token })
      } else {
        setResult({ bridgeId: bridge.id, pending: true })
        toast.success('Bridge created — ask an owner/admin to generate its token.')
      }
    } catch (e2: any) {
      setErr(e2?.message ?? 'Failed to create bridge.')
    } finally {
      setBusy(false)
    }
  }

  function copy(text: string) {
    try {
      navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      /* clipboard blocked */
    }
  }

  if (result) {
    const snippet = result.token ? agentInstallSnippet(result.bridgeId, result.token) : null

    return (
      <div className="rounded-xl border border-border bg-surface-2 p-3 space-y-3">
        {result.pending ? (
          <div className="flex items-start gap-2 text-xs text-muted">
            <AlertTriangle size={14} className="text-amber-500 shrink-0 mt-0.5" />
            <span>
              Bridge <strong className="text-fg">{name.trim()}</strong> was created, but minting its
              token needs an org owner or admin. Ask one to open Settings → Bridges and generate a
              token — this connector is already pointed at the bridge.
            </span>
          </div>
        ) : (
          <>
            <div className="flex items-start gap-2">
              <AlertTriangle size={14} className="text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
              <p className="text-xs text-amber-800 dark:text-amber-300">
                Copy this token now — it&apos;s shown <strong>only once</strong>. Run the command
                below wherever the database is reachable from (your laptop, a server inside your
                VPC — anywhere with outbound internet access) using the Nubi backend&apos;s Python
                environment; see <span className="font-mono">docs/bridges.md</span>.
              </p>
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-muted">
                <Terminal size={12} /> Run the agent
              </span>
              <button
                type="button"
                onClick={() => copy(snippet as string)}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs text-muted hover:text-primary border border-border hover:border-primary/40 transition-colors"
              >
                {copied ? <Check size={12} /> : <Copy size={12} />}
                {copied ? 'Copied' : 'Copy'}
              </button>
            </div>
            <pre className="rounded-lg bg-bg border border-border px-3 py-2 text-[11px] text-fg font-mono overflow-x-auto whitespace-pre">
              {snippet}
            </pre>
          </>
        )}

        <div className="flex justify-end">
          <Button type="button" size="sm" onClick={() => onConnected(result.bridgeId)}>
            Use this bridge
          </Button>
        </div>
      </div>
    )
  }

  return (
    // A plain <div>, NOT <form>: this renders inside the connector form's own
    // <form onSubmit=…> (ConnectorForm → DynamicForm → BridgeSelect), and
    // browsers cannot represent a nested <form> — the outer parser drops it,
    // silently detaching this panel's inputs/submit from React's intended
    // structure. See the 'Enter' key handler below for the submit affordance
    // a real <form> would otherwise give for free.
    <div className="rounded-xl border border-border bg-surface-2 p-3 space-y-2.5">
      <p className="text-xs text-muted">
        Runs a small agent that dials <em>out</em> to Nubi — no inbound ports to open. Works the
        same whether the database is on your own machine or inside a VPC.
      </p>
      <div className="flex flex-col sm:flex-row gap-2">
        <input
          type="text"
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              if (!busy && name.trim()) handleCreate()
            }
          }}
          placeholder="e.g. my-laptop"
          className={inputCls}
          aria-label="Bridge name"
        />
        <Button type="button" size="sm" loading={busy} disabled={busy || !name.trim()} onClick={handleCreate} className="shrink-0">
          Create &amp; connect
        </Button>
      </div>
      {err && <p className="text-xs text-red-600 dark:text-red-400">{err}</p>}
      <button
        type="button"
        onClick={onCancel}
        className="text-[11px] text-muted hover:text-fg underline underline-offset-2"
      >
        Cancel
      </button>
    </div>
  )
}
