/**
 * SecretsPanel.jsx — the org-scoped SECRETS manager, sized for the Flows RHS rail.
 *
 * A compact, rail-friendly version of the old full-page Secrets CRUD:
 *   - lists the org's secret NAMES (+ created date) — values are NEVER shown,
 *   - an inline add form (name + write-only value with show/hide) — a modal is
 *     too heavy for the narrow rail, so the form lives inline,
 *   - an inline per-row delete confirm that warns the secret can no longer be
 *     referenced by flow tasks once removed.
 *
 * Security posture (unchanged from SecretsPage):
 *   Values are write-only — the API never returns them after save. Secrets are
 *   referenced by NAME in task config as `{{ secrets.NAME }}` (SQL) /
 *   `secrets["NAME"]` (python) and resolved server-side at run time. This
 *   component never renders a secret value.
 *
 * Robust when the secrets API is unavailable: `listSecrets` returns [] on
 * failure, so the panel shows an empty state and never crashes. Writes surface
 * their error inline.
 *
 * Props:
 *   readOnly  {boolean=}  disables all edit affordances (add / delete).
 */

import { useState, useCallback, useEffect, useRef } from 'react'
import { KeyRound, Trash2, Plus, X, Eye, EyeOff, ShieldCheck, Check, RefreshCw } from 'lucide-react'
import { listSecrets, createSecret, deleteSecret } from '../lib/secrets.js'
import { toast } from '../components/ui/Toast.jsx'

// Mirror VariablesPanel's control styling so the two rail panels match.
const inputCls = [
  'w-full h-8 text-sm border border-border rounded-lg px-2.5',
  'bg-surface text-fg placeholder:text-muted/50',
  'focus:outline-none focus:ring-2 focus:ring-ring/60 focus:border-ring/40',
  'hover:border-border/80 transition-colors',
].join(' ')

export default function SecretsPanel({ readOnly = false }) {
  const [secrets, setSecrets] = useState(null) // null = not loaded yet
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  // Add-form draft.
  const [adding, setAdding] = useState(false)
  const [draftName, setDraftName] = useState('')
  const [draftValue, setDraftValue] = useState('')
  const [showValue, setShowValue] = useState(false)
  const nameRef = useRef(null)

  // Inline delete confirm — the name pending confirmation (or null).
  const [confirmDelete, setConfirmDelete] = useState(null)

  const refresh = useCallback(() => {
    setLoading(true)
    listSecrets()
      .then(rows => setSecrets(rows ?? []))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { refresh() }, [refresh])

  // Focus the name field when the add form opens.
  useEffect(() => {
    if (adding) setTimeout(() => nameRef.current?.focus(), 60)
  }, [adding])

  const resetDraft = () => {
    setDraftName('')
    setDraftValue('')
    setShowValue(false)
    setError(null)
  }

  const openAdd = () => { resetDraft(); setAdding(true) }
  const closeAdd = () => { resetDraft(); setAdding(false) }

  const save = () => {
    const name = draftName.trim()
    if (!name) { setError('Name is required.'); return }
    if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(name)) {
      setError('Start with a letter; letters, digits, _ and - only.')
      return
    }
    if (!draftValue) { setError('Value is required.'); return }
    setBusy(true)
    setError(null)
    createSecret(name, draftValue)
      .then(() => {
        toast.success(`Secret "${name}" saved`)
        closeAdd()
        refresh()
      })
      .catch(err => setError(err?.message ?? 'Failed to save secret.'))
      .finally(() => setBusy(false))
  }

  const remove = (name) => {
    setBusy(true)
    setError(null)
    deleteSecret(name)
      .then(() => {
        toast.success(`Secret "${name}" deleted`)
        setConfirmDelete(null)
        refresh()
      })
      .catch(err => setError(err?.message ?? 'Failed to delete secret.'))
      .finally(() => setBusy(false))
  }

  const fmtDate = (iso) => {
    if (!iso) return null
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return null
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
  }

  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2">
        <p className="text-[10px] text-muted/70 flex-1">
          Encrypted credentials referenced in cells as{' '}
          <code className="font-mono bg-surface-2 px-0.5 rounded">{'{{ secrets.NAME }}'}</code>.
          Values are write-only — never shown after save.
        </p>
        <button
          type="button"
          onClick={refresh}
          disabled={loading}
          title="Refresh secrets"
          aria-label="Refresh secrets"
          className="shrink-0 w-6 h-6 flex items-center justify-center rounded text-muted/60 hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-50"
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Existing secrets */}
      {secrets === null ? (
        <p className="text-xs text-muted">Loading…</p>
      ) : secrets.length === 0 ? (
        <p className="text-xs text-muted/70 rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-3 text-center">
          No secrets yet{readOnly ? '.' : ' — add one below to reference it from your cells.'}
        </p>
      ) : (
        <div className="space-y-1.5">
          {secrets.map(s => (
            <div
              key={s.name}
              className="rounded-lg border border-border bg-surface-2/20 px-2.5 py-1.5"
            >
              <div className="flex items-center gap-2">
                <KeyRound size={12} className="shrink-0 text-primary" />
                <div className="min-w-0 flex-1">
                  <div className="font-mono text-xs text-fg truncate">{s.name}</div>
                  {fmtDate(s.created_at) && (
                    <p className="text-[10px] text-muted/70">Added {fmtDate(s.created_at)}</p>
                  )}
                </div>
                {/* Value placeholder — never a real value. */}
                <span className="shrink-0 text-[11px] text-muted/50 font-mono tracking-widest select-none" aria-hidden="true">
                  ••••
                </span>
                {!readOnly && confirmDelete !== s.name && (
                  <button
                    type="button"
                    onClick={() => { setError(null); setConfirmDelete(s.name) }}
                    disabled={busy}
                    title="Delete secret"
                    aria-label={`Delete ${s.name}`}
                    className="shrink-0 w-6 h-6 flex items-center justify-center rounded text-muted/60 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors disabled:opacity-50"
                  >
                    <Trash2 size={11} />
                  </button>
                )}
              </div>

              {/* Inline delete confirm */}
              {!readOnly && confirmDelete === s.name && (
                <div className="mt-1.5 pt-1.5 border-t border-border/60 space-y-1.5">
                  <p className="text-[10px] text-muted/80 leading-snug">
                    Delete <span className="font-mono text-fg">{s.name}</span>? It can no longer be
                    referenced by flow tasks. This cannot be undone.
                  </p>
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => remove(s.name)}
                      disabled={busy}
                      className="flex items-center gap-1 px-2 py-1 text-[10px] font-medium rounded border border-red-500/30 bg-red-50 text-red-600 hover:bg-red-100 dark:bg-red-900/20 dark:text-red-400 dark:hover:bg-red-900/30 transition-colors disabled:opacity-50"
                    >
                      <Trash2 size={10} />
                      Delete
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmDelete(null)}
                      disabled={busy}
                      className="px-2 py-1 text-[10px] font-medium rounded border border-border bg-surface text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-50"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Add form */}
      {!readOnly && (
        adding ? (
          <div className="rounded-lg border border-border bg-surface-2/20 p-3 space-y-2">
            <div className="flex items-center justify-between">
              <p className="text-[10px] font-semibold text-muted/70 uppercase tracking-wider">
                Add secret
              </p>
              <button
                type="button"
                onClick={closeAdd}
                title="Cancel"
                aria-label="Cancel"
                className="w-5 h-5 flex items-center justify-center rounded text-muted/60 hover:text-fg hover:bg-surface-2 transition-colors"
              >
                <X size={12} />
              </button>
            </div>

            <input
              ref={nameRef}
              type="text"
              className={[inputCls, 'font-mono'].join(' ')}
              value={draftName}
              placeholder="S3_ACCESS_KEY"
              onChange={e => setDraftName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); document.getElementById('secret-panel-value')?.focus() } }}
            />

            <div className="relative">
              <input
                id="secret-panel-value"
                type={showValue ? 'text' : 'password'}
                className={[inputCls, 'pr-8'].join(' ')}
                value={draftValue}
                placeholder="Paste value"
                onChange={e => setDraftValue(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); save() } }}
                autoComplete="off"
                data-1p-ignore
              />
              <button
                type="button"
                onClick={() => setShowValue(v => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted/60 hover:text-fg transition-colors"
                aria-label={showValue ? 'Hide value' : 'Show value'}
              >
                {showValue ? <EyeOff size={13} /> : <Eye size={13} />}
              </button>
            </div>

            <div className="flex items-start gap-1.5 text-[10px] text-muted/70">
              <ShieldCheck size={11} className="shrink-0 text-accent mt-0.5" />
              <span>Encrypted at rest and never returned after save — store it securely.</span>
            </div>

            {error && <p className="text-[10px] text-red-500">{error}</p>}

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={save}
                disabled={busy || !draftName.trim() || !draftValue}
                className="flex items-center gap-1 px-2.5 py-1.5 text-[11px] font-medium rounded-lg bg-primary text-primary-fg hover:opacity-90 transition-opacity disabled:opacity-50"
              >
                <Check size={11} />
                Save secret
              </button>
              <button
                type="button"
                onClick={closeAdd}
                disabled={busy}
                className="px-2.5 py-1.5 text-[11px] font-medium rounded-lg border border-border bg-surface text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            onClick={openAdd}
            className="w-full flex items-center justify-center gap-1.5 px-2.5 py-2 text-[11px] font-medium rounded-lg border border-dashed border-border text-muted hover:text-fg hover:bg-surface-2 hover:border-border/80 transition-colors"
          >
            <Plus size={12} />
            New secret
          </button>
        )
      )}

      {/* Delete errors surface here when the form is closed. */}
      {!adding && error && <p className="text-[10px] text-red-500">{error}</p>}
    </div>
  )
}
