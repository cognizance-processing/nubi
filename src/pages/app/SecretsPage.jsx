/**
 * SecretsPage — manage org-scoped secrets used by flow tasks.
 *
 * Layout:
 *   Header (title + "Add secret" CTA)
 *   Secrets list (name + created date, no values ever shown)
 *   Add secret form (name + value, write-only)
 *   Delete confirm dialog
 *
 * API calls use src/lib/secrets.js:
 *   GET    /secrets            → [{ name, created_at }]
 *   POST   /secrets            { name, value }
 *   DELETE /secrets/{name}
 *
 * Values are NEVER returned by the API. The form is intentionally write-only.
 * Secrets are referenced by name in task config and resolved server-side at run time.
 */

import { useEffect, useState, useCallback, useRef } from 'react'
import {
  Plus,
  KeyRound,
  Trash2,
  X,
  Loader2,
  AlertTriangle,
  RefreshCw,
  CheckCircle,
  XCircle,
  ShieldCheck,
  Eye,
  EyeOff,
  ArrowLeft,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { listSecrets, createSecret, deleteSecret } from '../../lib/secrets.js'
import { useCanWrite } from '../../contexts/OrgContext.jsx'

// ---------------------------------------------------------------------------
// Toast notification
// ---------------------------------------------------------------------------

function Toast({ message, type, onDismiss }) {
  useEffect(() => {
    if (!message) return
    const t = setTimeout(onDismiss, 4000)
    return () => clearTimeout(t)
  }, [message, onDismiss])

  if (!message) return null

  const isError = type === 'error'
  return (
    <div
      className={[
        'fixed bottom-5 left-1/2 -translate-x-1/2 z-[60]',
        'flex items-center gap-2.5 px-4 py-3 rounded-2xl shadow-xl',
        'text-sm font-medium max-w-sm w-[calc(100vw-2rem)]',
        'border transition-all duration-300',
        isError
          ? 'bg-red-600 text-white border-red-700'
          : 'bg-green-600 text-white border-green-700',
      ].join(' ')}
      role="status"
    >
      {isError
        ? <XCircle size={16} strokeWidth={2.5} className="shrink-0" />
        : <CheckCircle size={16} strokeWidth={2.5} className="shrink-0" />
      }
      <span className="flex-1">{message}</span>
      <button onClick={onDismiss} className="shrink-0 opacity-70 hover:opacity-100 transition-opacity">
        <X size={14} strokeWidth={2.5} />
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Delete confirm dialog
// ---------------------------------------------------------------------------

function DeleteDialog({ name, loading, error, onCancel, onConfirm }) {
  return (
    <div
      className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={onCancel}
    >
      <div
        className="bg-surface rounded-2xl border border-border shadow-2xl p-6 w-full max-w-sm"
        onClick={e => e.stopPropagation()}
        role="alertdialog"
        aria-modal="true"
      >
        <div className="flex items-start gap-3 mb-4">
          <div className="w-10 h-10 rounded-xl bg-red-100 dark:bg-red-900/30 flex items-center justify-center shrink-0">
            <Trash2 size={18} className="text-red-600 dark:text-red-400" strokeWidth={2} />
          </div>
          <div>
            <h3 className="font-semibold text-fg text-sm">Delete secret?</h3>
            <p className="text-xs text-muted mt-1 leading-relaxed">
              <span className="font-mono font-medium text-fg">{name}</span> will be permanently
              deleted and can no longer be used by flow tasks. This cannot be undone.
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
            className="px-4 py-2 rounded-xl text-sm font-medium text-muted border border-border hover:bg-surface-2 hover:text-fg transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-semibold text-white bg-red-600 hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2"
          >
            {loading && <Loader2 size={13} className="animate-spin" />}
            Delete
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Add secret slide-over
// ---------------------------------------------------------------------------

function AddSecretPanel({ open, onClose, onCreated }) {
  const [name, setName]       = useState('')
  const [value, setValue]     = useState('')
  const [showValue, setShowValue] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)
  const nameRef = useRef(null)

  // Reset form when panel opens
  useEffect(() => {
    if (open) {
      setName('')
      setValue('')
      setShowValue(false)
      setError(null)
      setTimeout(() => nameRef.current?.focus(), 80)
    }
  }, [open])

  // ESC to close
  useEffect(() => {
    if (!open) return
    function onKey(e) { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  async function handleSubmit(e) {
    e.preventDefault()
    if (!name.trim() || !value) return
    setLoading(true)
    setError(null)
    try {
      const created = await createSecret(name.trim(), value)
      onCreated(created ?? { name: name.trim(), created_at: new Date().toISOString() })
      onClose()
    } catch (err) {
      setError(err.message ?? 'Failed to create secret. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      {/* Backdrop */}
      <div
        className={[
          'fixed inset-0 z-40 bg-black/40 backdrop-blur-sm transition-opacity duration-200',
          open ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none',
        ].join(' ')}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Panel */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Add secret"
        className={[
          'fixed inset-y-0 right-0 z-50',
          'w-full sm:max-w-[420px]',
          'bg-surface border-l border-border shadow-2xl',
          'flex flex-col transition-transform duration-300 ease-in-out',
          open ? 'translate-x-0' : 'translate-x-full',
        ].join(' ')}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border shrink-0">
          <h2 className="font-display font-semibold text-lg text-fg">Add secret</h2>
          <button
            onClick={onClose}
            aria-label="Close panel"
            className="flex items-center justify-center w-8 h-8 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <X size={16} strokeWidth={2} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5">
          <form onSubmit={handleSubmit} className="flex flex-col gap-5">

            {/* Name */}
            <div>
              <label htmlFor="secret-name" className="block text-xs font-medium text-fg mb-1.5">
                Name
              </label>
              <input
                id="secret-name"
                ref={nameRef}
                type="text"
                required
                placeholder="e.g. S3_ACCESS_KEY"
                value={name}
                onChange={e => setName(e.target.value)}
                pattern="[a-zA-Z][a-zA-Z0-9_\-]*"
                title="Must start with a letter; only letters, digits, underscores, and hyphens allowed"
                className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-fg font-mono placeholder:text-muted placeholder:font-sans focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent transition-colors"
              />
              <p className="text-[10px] text-muted mt-1">
                Alphanumeric, underscores, and hyphens only. Referenced in tasks as{' '}
                <code className="font-mono bg-surface-2 px-1 rounded">{'{{secrets.NAME}}'}</code>.
              </p>
            </div>

            {/* Value (write-only) */}
            <div>
              <label htmlFor="secret-value" className="block text-xs font-medium text-fg mb-1.5">
                Value
              </label>
              <div className="relative">
                <input
                  id="secret-value"
                  type={showValue ? 'text' : 'password'}
                  required
                  placeholder="Paste your secret value here"
                  value={value}
                  onChange={e => setValue(e.target.value)}
                  className="w-full rounded-lg border border-border bg-surface px-3 py-2 pr-9 text-sm text-fg placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent transition-colors"
                  autoComplete="off"
                  data-1p-ignore
                />
                <button
                  type="button"
                  onClick={() => setShowValue(v => !v)}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted hover:text-fg transition-colors"
                  aria-label={showValue ? 'Hide value' : 'Show value'}
                >
                  {showValue ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            </div>

            {/* Security note */}
            <div className="flex items-start gap-2.5 px-3 py-2.5 rounded-xl bg-surface-2 border border-border/50 text-xs text-muted">
              <ShieldCheck size={14} className="shrink-0 text-accent mt-0.5" strokeWidth={2} />
              <span>
                Values are <strong className="text-fg">encrypted at rest</strong> with AES-256-GCM
                and are <strong className="text-fg">never returned</strong> by the API after save.
                Store your value securely — it cannot be retrieved later.
              </span>
            </div>

            {/* Error */}
            {error && (
              <div className="flex items-start gap-2 px-3 py-2.5 rounded-xl bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-xs text-red-700 dark:text-red-300">
                <AlertTriangle size={13} className="shrink-0 mt-0.5" strokeWidth={2} />
                <span>{error}</span>
              </div>
            )}

            {/* Submit */}
            <button
              type="submit"
              disabled={loading || !name.trim() || !value}
              className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-xl bg-primary text-primary-fg text-sm font-semibold hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
            >
              {loading && <Loader2 size={15} className="animate-spin" />}
              Save secret
            </button>
          </form>
        </div>
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// Secret row
// ---------------------------------------------------------------------------

function SecretRow({ secret, onDelete, canWrite }) {
  const created = secret.created_at
    ? new Date(secret.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
    : null

  return (
    <div className="flex items-center gap-4 bg-surface rounded-xl border border-border px-4 py-3 hover:shadow-sm hover:border-border/80 transition-all duration-150">
      {/* Icon */}
      <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
        <KeyRound size={16} className="text-primary" strokeWidth={2} />
      </div>

      {/* Name + meta */}
      <div className="flex-1 min-w-0">
        <p className="font-mono font-medium text-sm text-fg truncate">{secret.name}</p>
        {created && (
          <p className="text-[11px] text-muted mt-0.5">Added {created}</p>
        )}
      </div>

      {/* Value placeholder */}
      <p className="text-xs text-muted font-mono hidden sm:block shrink-0 select-none tracking-widest">
        ••••••••
      </p>

      {/* Delete */}
      {canWrite && (
        <button
          onClick={() => onDelete(secret.name)}
          title={`Delete ${secret.name}`}
          className="inline-flex items-center justify-center w-8 h-8 rounded-lg border border-border text-muted hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 hover:border-red-300 transition-colors focus:outline-none focus:ring-2 focus:ring-ring shrink-0"
        >
          <Trash2 size={13} strokeWidth={2} />
        </button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyState({ onAdd, canWrite }) {
  return (
    <div className="flex flex-col items-center justify-center py-20 px-6 text-center">
      <div className="flex items-center justify-center w-16 h-16 rounded-2xl mb-5 bg-primary/10 shadow-sm">
        <KeyRound size={28} className="text-primary" strokeWidth={1.5} />
      </div>
      <h2 className="font-display font-semibold text-xl text-fg mb-2">No secrets yet</h2>
      <p className="text-muted text-sm max-w-xs leading-relaxed mb-6">
        Add secrets to store credentials and API keys. Reference them in flow tasks as{' '}
        <code className="font-mono bg-surface-2 px-1.5 rounded text-xs">{'{{secrets.NAME}}'}</code>.
      </p>
      {canWrite ? (
        <button
          onClick={onAdd}
          className="inline-flex items-center gap-2 px-5 py-2.5 bg-primary text-primary-fg rounded-xl text-sm font-semibold hover:opacity-90 transition-opacity focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 shadow-md"
        >
          <Plus size={16} strokeWidth={2.5} />
          Add your first secret
        </button>
      ) : (
        <p className="text-xs text-muted">Read-only — ask an admin to add a secret.</p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// SecretsPage
// ---------------------------------------------------------------------------

export default function SecretsPage() {
  const canWrite = useCanWrite()
  const [secrets, setSecrets]         = useState([])
  const [listLoading, setListLoading] = useState(true)
  const [listError, setListError]     = useState(null)

  const [panelOpen, setPanelOpen]     = useState(false)

  const [deleteTarget, setDeleteTarget] = useState(null) // secret name to delete
  const [deleteLoading, setDeleteLoading] = useState(false)
  const [deleteError, setDeleteError]   = useState(null)

  const [toast, setToast] = useState(null)

  const showToast = useCallback((message, type = 'success') => setToast({ message, type }), [])
  const dismissToast = useCallback(() => setToast(null), [])

  // ---------------------------------------------------------------------------
  // Fetch secrets
  // ---------------------------------------------------------------------------

  const fetchSecrets = useCallback(async () => {
    setListLoading(true)
    setListError(null)
    try {
      const data = await listSecrets()
      setSecrets(data)
    } catch (err) {
      setListError(err.message ?? 'Failed to load secrets')
    } finally {
      setListLoading(false)
    }
  }, [])

  useEffect(() => { fetchSecrets() }, [fetchSecrets])

  // ---------------------------------------------------------------------------
  // Created callback
  // ---------------------------------------------------------------------------

  const handleCreated = useCallback((secret) => {
    setSecrets(prev => {
      // Upsert by name (in case of overwrite)
      const exists = prev.some(s => s.name === secret.name)
      if (exists) return prev.map(s => s.name === secret.name ? secret : s)
      return [...prev, secret]
    })
    showToast(`Secret "${secret.name}" saved`)
  }, [showToast])

  // ---------------------------------------------------------------------------
  // Delete
  // ---------------------------------------------------------------------------

  async function handleDeleteConfirm() {
    if (!deleteTarget) return
    setDeleteLoading(true)
    setDeleteError(null)
    try {
      await deleteSecret(deleteTarget)
      setSecrets(prev => prev.filter(s => s.name !== deleteTarget))
      setDeleteTarget(null)
      showToast(`Secret "${deleteTarget}" deleted`)
    } catch (err) {
      setDeleteError(err.message ?? 'Delete failed. Please try again.')
    } finally {
      setDeleteLoading(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="flex flex-col min-h-full">
      {/* Page header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 px-6 pt-6 pb-4 border-b border-border bg-surface">
        <div>
          <Link
            to="/flows"
            className="inline-flex items-center gap-1 text-xs text-muted hover:text-fg transition-colors mb-1"
          >
            <ArrowLeft size={13} />
            Flows
          </Link>
          <h1 className="font-display font-semibold text-2xl text-fg">Secrets</h1>
          <p className="text-sm text-muted mt-0.5">
            Encrypted credentials and API keys referenced by flow tasks via{' '}
            <span className="font-mono text-fg">{'{{ secrets.NAME }}'}</span>. Values are write-only.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={fetchSecrets}
            disabled={listLoading}
            title="Refresh"
            className="flex items-center justify-center w-9 h-9 rounded-xl border border-border text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <RefreshCw size={15} className={listLoading ? 'animate-spin' : ''} strokeWidth={2} />
          </button>

          {canWrite && (
            <button
              onClick={() => setPanelOpen(true)}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-primary text-primary-fg text-sm font-semibold hover:opacity-90 transition-opacity focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 shadow-sm"
            >
              <Plus size={15} strokeWidth={2.5} />
              Add secret
            </button>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 px-6 py-6">

        {/* Loading skeleton */}
        {listLoading && (
          <div className="space-y-3 max-w-2xl">
            {[1, 2, 3].map(i => (
              <div key={i} className="bg-surface rounded-xl border border-border h-16 animate-pulse" />
            ))}
          </div>
        )}

        {/* Error state */}
        {!listLoading && listError && (
          <div className="flex flex-col items-center justify-center py-16 gap-4">
            <div className="w-12 h-12 rounded-xl bg-red-100 dark:bg-red-900/30 flex items-center justify-center">
              <AlertTriangle size={22} className="text-red-600 dark:text-red-400" strokeWidth={2} />
            </div>
            <div className="text-center">
              <p className="text-sm font-medium text-fg">Failed to load secrets</p>
              <p className="text-xs text-muted mt-1">{listError}</p>
            </div>
            <button
              onClick={fetchSecrets}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl border border-border text-sm text-muted hover:text-fg hover:bg-surface-2 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <RefreshCw size={14} strokeWidth={2} />
              Retry
            </button>
          </div>
        )}

        {/* Empty state */}
        {!listLoading && !listError && secrets.length === 0 && (
          <EmptyState onAdd={() => setPanelOpen(true)} canWrite={canWrite} />
        )}

        {/* Secrets list */}
        {!listLoading && !listError && secrets.length > 0 && (
          <div className="space-y-2 max-w-2xl">
            {/* Info bar */}
            <div className="flex items-center gap-2 text-xs text-muted mb-4">
              <ShieldCheck size={13} className="text-accent shrink-0" strokeWidth={2} />
              <span>
                {secrets.length} secret{secrets.length === 1 ? '' : 's'} stored.
                Values are AES-256-GCM encrypted and never returned by the API.
              </span>
            </div>

            {secrets.map(secret => (
              <SecretRow
                key={secret.name}
                secret={secret}
                onDelete={setDeleteTarget}
                canWrite={canWrite}
              />
            ))}
          </div>
        )}
      </div>

      {/* Add secret slide-over */}
      <AddSecretPanel
        open={panelOpen}
        onClose={() => setPanelOpen(false)}
        onCreated={handleCreated}
      />

      {/* Delete confirm */}
      {deleteTarget && (
        <DeleteDialog
          name={deleteTarget}
          loading={deleteLoading}
          error={deleteError}
          onCancel={() => { setDeleteTarget(null); setDeleteError(null) }}
          onConfirm={handleDeleteConfirm}
        />
      )}

      {/* Toast */}
      <Toast
        message={toast?.message}
        type={toast?.type}
        onDismiss={dismissToast}
      />
    </div>
  )
}
