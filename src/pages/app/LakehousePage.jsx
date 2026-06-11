/**
 * LakehousePage — manage the built-in MANAGED LAKEHOUSE for the active project.
 *
 * Route:  /lakehouse
 *
 * Users can store data in a Nubi-managed lakehouse — an isolated, secure
 * object-storage prefix that needs no bucket to provision or manage, billed by
 * usage — OR bring their own bucket (the existing object-storage connector on
 * the Connectors page). This page owns the managed option:
 *
 *   configured:false   → explain the managed lake needs central storage
 *                        (admin/cloud) and that BYO bucket is available now.
 *                        Link to the Connectors flow. No scary error.
 *   provisioned:false  → "Provision managed lakehouse" CTA + a "seed demo data"
 *                        checkbox; explains isolation / security / billed-by-use.
 *   provisioned:true   → storage used (bytes → human), demo-seeded status with a
 *                        "Seed demo data" action, the managed datastore link, and
 *                        a destructive "Disconnect / delete" with a confirm step.
 *
 * Styling mirrors UsagePage / ConnectorsPage: the page toolbar is portaled into
 * the single AppShell topbar (useUi().topbarSlot); on-brand Tailwind tokens
 * (bg-surface, border-border, text-fg/text-muted, bg-primary) throughout. Hooks
 * follow react-hooks/set-state-in-effect (the initial load is deferred via a
 * setTimeout(…, 0), never a synchronous setState in the effect body).
 */

import { useEffect, useState, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { Link, useNavigate } from 'react-router-dom'
import {
  Warehouse,
  RefreshCw,
  Loader2,
  AlertTriangle,
  ShieldCheck,
  Sparkles,
  HardDrive,
  CheckCircle,
  Trash2,
  ExternalLink,
  Boxes,
  Plug,
  Gauge,
} from 'lucide-react'

import { useUi } from '../../contexts/UiContext.jsx'
import { useProject } from '../../contexts/ProjectContext.jsx'
import { useCanWrite } from '../../contexts/OrgContext.jsx'
import {
  lakehouseStatus,
  provisionLakehouse,
  seedDemoData,
  deprovisionLakehouse,
  formatBytes,
} from '../../lib/lakehouse.js'

// ---------------------------------------------------------------------------
// Small presentational helpers
// ---------------------------------------------------------------------------

function Feature({ Icon, title, children }) {
  return (
    <div className="flex items-start gap-3">
      <span className="flex items-center justify-center w-9 h-9 rounded-xl bg-surface-2 border border-border/60 shrink-0">
        <Icon size={16} className="text-primary" strokeWidth={2} />
      </span>
      <div className="min-w-0">
        <p className="text-sm font-medium text-fg">{title}</p>
        <p className="text-xs text-muted leading-relaxed mt-0.5">{children}</p>
      </div>
    </div>
  )
}

function InlineError({ message }) {
  if (!message) return null
  return (
    <div className="flex items-start gap-2 px-3 py-2.5 rounded-xl bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-xs text-red-700 dark:text-red-300">
      <AlertTriangle size={13} className="shrink-0 mt-0.5" strokeWidth={2} />
      <span>{message}</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// configured:false — managed lake needs central storage; BYO available now
// ---------------------------------------------------------------------------

function NotConfiguredState() {
  return (
    <div className="rounded-2xl border border-dashed border-border bg-surface p-6 sm:p-8">
      <div className="flex items-center justify-center w-16 h-16 rounded-2xl bg-brand-gradient shadow-lg mb-5">
        <Warehouse size={28} className="text-white" />
      </div>
      <h2 className="font-display font-semibold text-xl text-fg mb-2">
        Managed lakehouse isn’t available here
      </h2>
      <p className="text-sm text-muted max-w-prose leading-relaxed mb-4">
        The Nubi-managed lakehouse stores your data in isolated, secure storage we
        run for you — no bucket to provision or manage. It needs central storage to
        be configured by your administrator (it’s available on Nubi Cloud and isn’t
        set up on local / self-hosted builds yet).
      </p>
      <p className="text-sm text-muted max-w-prose leading-relaxed mb-6">
        You can still bring your own bucket today — connect an S3, GCS, or MinIO
        bucket of Parquet / DuckDB files from the Connectors page and query it
        right away.
      </p>
      <Link
        to="/connectors"
        className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-primary text-primary-fg text-sm font-semibold hover:opacity-90 transition-opacity focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 shadow-md"
      >
        <Plug size={16} strokeWidth={2.4} />
        Bring your own bucket
      </Link>
    </div>
  )
}

// ---------------------------------------------------------------------------
// configured:true & provisioned:false — provision CTA
// ---------------------------------------------------------------------------

function ProvisionState({ canWrite, busy, error, onProvision }) {
  const [seedDemo, setSeedDemo] = useState(true)

  return (
    <div className="rounded-2xl border border-border bg-surface p-6 sm:p-8 space-y-6">
      <div>
        <div className="flex items-center justify-center w-14 h-14 rounded-2xl bg-brand-gradient shadow-lg mb-4">
          <Warehouse size={24} className="text-white" />
        </div>
        <h2 className="font-display font-semibold text-xl text-fg mb-2">
          Provision a managed lakehouse
        </h2>
        <p className="text-sm text-muted max-w-prose leading-relaxed">
          Spin up storage Nubi runs for you — no bucket to create, no keys to
          rotate. Your data lands in an isolated prefix scoped to this project and
          is billed only for what you store.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <Feature Icon={ShieldCheck} title="Isolated & secure">
          A dedicated storage prefix per project, encrypted at rest. No
          cross-tenant access.
        </Feature>
        <Feature Icon={Boxes} title="Nothing to manage">
          No bucket to provision, no credentials to wire up. It just works.
        </Feature>
        <Feature Icon={Gauge} title="Billed by usage">
          Pay only for the bytes you store. See it on the Usage page.
        </Feature>
      </div>

      {canWrite ? (
        <div className="space-y-4 pt-2">
          <label className="flex items-center gap-2.5 text-sm text-fg cursor-pointer select-none">
            <input
              type="checkbox"
              checked={seedDemo}
              onChange={(e) => setSeedDemo(e.target.checked)}
              disabled={busy}
              className="w-4 h-4 rounded border-border text-primary focus:ring-2 focus:ring-ring"
            />
            <span className="inline-flex items-center gap-1.5">
              <Sparkles size={14} className="text-primary" />
              Seed demo data so I can explore right away
            </span>
          </label>

          <InlineError message={error} />

          <button
            onClick={() => onProvision({ seedDemo })}
            disabled={busy}
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-primary text-primary-fg text-sm font-semibold hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 shadow-md"
          >
            {busy ? <Loader2 size={16} className="animate-spin" /> : <Warehouse size={16} strokeWidth={2.4} />}
            {busy ? 'Provisioning…' : 'Provision managed lakehouse'}
          </button>

          <p className="text-xs text-muted">
            Prefer your own storage?{' '}
            <Link to="/connectors" className="underline underline-offset-2 hover:text-fg">
              Bring your own bucket
            </Link>{' '}
            instead.
          </p>
        </div>
      ) : (
        <p className="text-xs text-muted">
          Read-only — ask an organisation admin to provision the managed lakehouse.
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// provisioned:true — status, demo seeding, datastore link, disconnect
// ---------------------------------------------------------------------------

function ProvisionedState({
  status, canWrite, seeding, seedError, onSeed, onDisconnectClick,
}) {
  return (
    <div className="space-y-5">
      {/* Header card */}
      <div className="rounded-2xl border border-border bg-surface p-5 sm:p-6">
        <div className="flex items-start gap-4">
          <span className="flex items-center justify-center w-12 h-12 rounded-2xl bg-brand-gradient shadow-md shrink-0">
            <Warehouse size={22} className="text-white" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-display font-semibold text-lg text-fg">Managed lakehouse</h2>
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300">
                <CheckCircle size={11} strokeWidth={2.4} /> Provisioned
              </span>
            </div>
            {status.prefix && (
              <p className="text-xs text-muted font-mono truncate mt-1" title={status.prefix}>
                {status.prefix}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Metrics grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {/* Storage used */}
        <div className="rounded-xl border border-border bg-surface p-4">
          <div className="flex items-center gap-2 mb-3">
            <span className="flex items-center justify-center w-7 h-7 rounded-lg bg-surface-2 shrink-0">
              <HardDrive size={15} className="text-muted" strokeWidth={2} />
            </span>
            <span className="text-sm font-medium text-fg">Storage used</span>
          </div>
          <p className="text-2xl font-display font-semibold text-fg tabular-nums">
            {formatBytes(status.usage_bytes)}
          </p>
          <p className="text-xs text-muted mt-1">
            Billed by usage —{' '}
            <Link to="/usage" className="underline underline-offset-2 hover:text-fg">view on Usage</Link>.
          </p>
        </div>

        {/* Demo data */}
        <div className="rounded-xl border border-border bg-surface p-4">
          <div className="flex items-center gap-2 mb-3">
            <span className="flex items-center justify-center w-7 h-7 rounded-lg bg-surface-2 shrink-0">
              <Sparkles size={15} className="text-muted" strokeWidth={2} />
            </span>
            <span className="text-sm font-medium text-fg">Demo data</span>
          </div>
          {status.demo_seeded ? (
            <p className="inline-flex items-center gap-1.5 text-sm text-green-700 dark:text-green-400">
              <CheckCircle size={15} strokeWidth={2.2} /> Seeded
            </p>
          ) : (
            <p className="text-sm text-muted">Not seeded</p>
          )}
          {canWrite && (
            <button
              onClick={onSeed}
              disabled={seeding}
              className="mt-3 inline-flex items-center gap-1.5 h-8 px-3 rounded-lg text-xs font-medium border border-border text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {seeding ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} strokeWidth={2.2} />}
              {seeding ? 'Seeding…' : status.demo_seeded ? 'Re-seed demo data' : 'Seed demo data'}
            </button>
          )}
        </div>
      </div>

      <InlineError message={seedError} />

      {/* Datastore link */}
      {status.datastore_id && (
        <Link
          to={`/connectors/${status.datastore_id}/data`}
          className="flex items-center gap-3 rounded-xl border border-border bg-surface p-4 hover:border-primary/40 hover:bg-surface-2 transition-colors group focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <span className="flex items-center justify-center w-9 h-9 rounded-xl bg-surface-2 border border-border/60 shrink-0">
            <Boxes size={16} className="text-primary" strokeWidth={2} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-fg">Browse lakehouse data</p>
            <p className="text-xs text-muted">Open the managed datastore to view its tables.</p>
          </div>
          <ExternalLink size={15} className="text-muted group-hover:text-primary shrink-0" />
        </Link>
      )}

      {/* Danger zone */}
      {canWrite && (
        <div className="rounded-xl border border-red-200 dark:border-red-900/40 bg-red-50/40 dark:bg-red-900/10 p-4">
          <div className="flex items-start gap-3">
            <span className="flex items-center justify-center w-9 h-9 rounded-xl bg-red-100 dark:bg-red-900/30 shrink-0">
              <Trash2 size={16} className="text-red-600 dark:text-red-400" strokeWidth={2} />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-fg">Disconnect &amp; delete lakehouse</p>
              <p className="text-xs text-muted leading-relaxed mt-0.5">
                Permanently deletes the managed storage and everything in it. This
                cannot be undone.
              </p>
            </div>
            <button
              onClick={onDisconnectClick}
              className="shrink-0 inline-flex items-center gap-1.5 h-8 px-3 rounded-lg text-xs font-semibold text-red-600 dark:text-red-400 border border-red-300 dark:border-red-800 hover:bg-red-100 dark:hover:bg-red-900/30 transition-colors focus:outline-none focus:ring-2 focus:ring-red-500"
            >
              <Trash2 size={12} strokeWidth={2.2} />
              Disconnect
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Disconnect confirm dialog
// ---------------------------------------------------------------------------

function DisconnectDialog({ busy, error, onCancel, onConfirm }) {
  return (
    <div
      className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={onCancel}
    >
      <div
        className="bg-surface rounded-2xl border border-border shadow-2xl p-6 w-full max-w-sm"
        onClick={(e) => e.stopPropagation()}
        role="alertdialog"
        aria-modal="true"
      >
        <div className="flex items-start gap-3 mb-4">
          <div className="w-10 h-10 rounded-xl bg-red-100 dark:bg-red-900/30 flex items-center justify-center shrink-0">
            <Trash2 size={18} className="text-red-600 dark:text-red-400" strokeWidth={2} />
          </div>
          <div>
            <h3 className="font-semibold text-fg text-sm">Delete managed lakehouse?</h3>
            <p className="text-xs text-muted mt-1 leading-relaxed">
              The managed storage and <strong className="text-fg">all data in it</strong>{' '}
              will be permanently deleted. This cannot be undone.
            </p>
          </div>
        </div>

        {error && (
          <div className="mb-3 px-3 py-2 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-xs text-red-700 dark:text-red-300">
            {error}
          </div>
        )}

        <div className="flex gap-2 justify-end">
          <button
            onClick={onCancel}
            disabled={busy}
            className="px-4 py-2 rounded-xl text-sm font-medium text-muted border border-border hover:bg-surface-2 hover:text-fg disabled:opacity-50 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={busy}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-semibold text-white bg-red-600 hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2"
          >
            {busy && <Loader2 size={13} className="animate-spin" />}
            Delete
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// LakehousePage
// ---------------------------------------------------------------------------

export default function LakehousePage() {
  const { topbarSlot } = useUi()
  const { activeProject } = useProject()
  const canWrite = useCanWrite()
  const navigate = useNavigate()
  const projectId = activeProject?.id

  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [provisioning, setProvisioning] = useState(false)
  const [provisionError, setProvisionError] = useState(null)

  const [seeding, setSeeding] = useState(false)
  const [seedError, setSeedError] = useState(null)

  const [confirmOpen, setConfirmOpen] = useState(false)
  const [disconnecting, setDisconnecting] = useState(false)
  const [disconnectError, setDisconnectError] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setStatus(await lakehouseStatus())
    } catch (err) {
      // lakehouseStatus already degrades gracefully, but guard anyway.
      setError(err?.message ?? 'Failed to load lakehouse status.')
    } finally {
      setLoading(false)
    }
  }, [])

  // Defer the initial/refresh load so it isn't a synchronous setState in the
  // effect body (react-hooks/set-state-in-effect). Re-runs on project change.
  useEffect(() => {
    const t = setTimeout(load, 0)
    return () => clearTimeout(t)
  }, [load, projectId])

  async function handleProvision({ seedDemo }) {
    setProvisioning(true)
    setProvisionError(null)
    try {
      setStatus(await provisionLakehouse({ seedDemo }))
    } catch (err) {
      setProvisionError(err?.message ?? 'Provisioning failed. Please try again.')
    } finally {
      setProvisioning(false)
    }
  }

  async function handleSeed() {
    setSeeding(true)
    setSeedError(null)
    try {
      setStatus(await seedDemoData())
    } catch (err) {
      setSeedError(err?.message ?? 'Seeding failed. Please try again.')
    } finally {
      setSeeding(false)
    }
  }

  async function handleDisconnect() {
    setDisconnecting(true)
    setDisconnectError(null)
    try {
      await deprovisionLakehouse()
      setConfirmOpen(false)
      // Reload to reflect the now-deprovisioned state.
      await load()
    } catch (err) {
      setDisconnectError(err?.message ?? 'Disconnect failed. Please try again.')
    } finally {
      setDisconnecting(false)
    }
  }

  return (
    <div className="flex flex-col min-h-full bg-bg">
      {/* Page toolbar — portaled into the single AppShell topbar */}
      {topbarSlot && createPortal(
        <div className="flex items-center gap-2 w-full min-w-0">
          <Warehouse size={15} className="text-muted shrink-0 hidden sm:block" strokeWidth={2.2} />
          <span className="text-sm font-semibold font-display text-fg truncate">Lakehouse</span>
          <div className="flex-1" />
          <button
            onClick={load}
            disabled={loading}
            title="Refresh"
            aria-label="Refresh lakehouse status"
            className="flex items-center justify-center w-8 h-8 rounded-lg shrink-0 border border-border text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} strokeWidth={2} />
          </button>
          <button
            onClick={() => navigate('/connectors')}
            title="Bring your own bucket"
            className="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-lg shrink-0 border border-border text-muted hover:text-fg hover:bg-surface-2 text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <Plug size={13} strokeWidth={2.2} />
            <span className="hidden sm:inline">Bring your own bucket</span>
          </button>
        </div>,
        topbarSlot,
      )}

      {/* Content */}
      <div className="flex-1 px-4 sm:px-6 py-4 max-w-4xl w-full mx-auto">
        {loading && (
          <div className="flex items-center gap-2 text-sm text-muted py-16 justify-center">
            <Loader2 size={16} className="animate-spin" /> Loading lakehouse…
          </div>
        )}

        {!loading && error && (
          <div className="flex flex-col items-center justify-center py-14 gap-3 rounded-xl border border-dashed border-red-200 dark:border-red-900/40">
            <AlertTriangle size={20} className="text-red-500" />
            <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
            <button onClick={load} className="text-xs text-muted hover:text-fg underline">Retry</button>
          </div>
        )}

        {!loading && !error && status && !status.configured && (
          <NotConfiguredState />
        )}

        {!loading && !error && status && status.configured && !status.provisioned && (
          <ProvisionState
            canWrite={canWrite}
            busy={provisioning}
            error={provisionError}
            onProvision={handleProvision}
          />
        )}

        {!loading && !error && status && status.configured && status.provisioned && (
          <ProvisionedState
            status={status}
            canWrite={canWrite}
            seeding={seeding}
            seedError={seedError}
            onSeed={handleSeed}
            onDisconnectClick={() => { setDisconnectError(null); setConfirmOpen(true) }}
          />
        )}
      </div>

      {confirmOpen && (
        <DisconnectDialog
          busy={disconnecting}
          error={disconnectError}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={handleDisconnect}
        />
      )}
    </div>
  )
}
