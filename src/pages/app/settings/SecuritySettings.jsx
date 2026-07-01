/**
 * SecuritySettings — manage JWT issuers used to verify host-signed embed JWTs.
 *
 * When you embed a Nubi dashboard in your own application you generate a
 * short-lived signed JWT that tells the embed endpoint which org/project the
 * viewer belongs to and what dashboards they may see.  This page lets you
 * register the public key (or JWKS endpoint) your backend signs those tokens
 * with so Nubi can verify them.
 *
 * Each issuer entry stores:
 *   name        — a human label ("My App Production")
 *   issuer      — the `iss` claim value in the JWT
 *   jwks_url    — URL of a JWKS endpoint (preferred; Nubi caches and rotates keys)
 *   jwk_pem     — pasted PEM / raw JWK JSON (alternative to jwks_url)
 *   algorithms  — RS256, RS384, RS512, ES256, ES384, ES512 (comma-separated input)
 *   audience    — optional expected `aud` claim
 *   enabled     — can disable without deleting
 *
 * Calls /api/v1/security/jwt-issuers (JwksIssuersBackendAgent).
 */

import { useState } from 'react'
import { useAsyncLoad } from '../../../hooks/useAsyncLoad.js'
import {
  ShieldCheck,
  Plus,
  Pencil,
  Trash2,
  Loader2,
  CheckCircle,
  AlertCircle,
  ChevronDown,
  ChevronUp,
  KeyRound,
} from 'lucide-react'
import {
  listJwtIssuers,
  createJwtIssuer,
  updateJwtIssuer,
  deleteJwtIssuer,
} from '../../../lib/security.js'
import {
  SettingsPageHeader,
  SettingsCard,
  PrimaryButton,
  ErrorText,
  Toggle,
  inputCls,
} from './SettingsUI.jsx'
import { toast } from '../../../components/ui/Toast.jsx'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const ALGORITHM_OPTIONS = ['RS256', 'RS384', 'RS512', 'ES256', 'ES384', 'ES512']

const EMPTY_FORM = {
  name: '',
  issuer: '',
  jwks_url: '',
  jwk_pem: '',
  algorithms: ['RS256'],
  audience: '',
  enabled: true,
}

// ---------------------------------------------------------------------------
// IssuerRow — a single row in the list
// ---------------------------------------------------------------------------

function IssuerRow({ issuer, onEdit, onDelete, deleting }) {
  return (
    <div className="flex items-start justify-between gap-4 py-3">
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-fg truncate">{issuer.name}</span>
          {issuer.enabled ? (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-semibold bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block" />
              enabled
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-semibold bg-surface-2 text-muted">
              disabled
            </span>
          )}
        </div>
        <p className="text-xs text-muted font-mono truncate">{issuer.issuer}</p>
        <div className="flex items-center gap-3 flex-wrap">
          {issuer.jwks_url && (
            <span className="text-[11px] text-muted">
              JWKS: <span className="font-mono">{issuer.jwks_url}</span>
            </span>
          )}
          {issuer.jwk_pem && !issuer.jwks_url && (
            <span className="text-[11px] text-muted">Inline key (PEM/JWK)</span>
          )}
          {issuer.algorithms?.length > 0 && (
            <span className="text-[11px] text-muted">
              Algorithms:{' '}
              <span className="font-mono">{issuer.algorithms.join(', ')}</span>
            </span>
          )}
          {issuer.audience && (
            <span className="text-[11px] text-muted">
              Audience: <span className="font-mono">{issuer.audience}</span>
            </span>
          )}
        </div>
      </div>

      <div className="flex items-center gap-1.5 shrink-0">
        <button
          type="button"
          onClick={() => onEdit(issuer)}
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-muted hover:text-fg hover:bg-surface-2 border border-border transition-colors"
        >
          <Pencil size={12} />
          Edit
        </button>
        <button
          type="button"
          onClick={() => onDelete(issuer.id)}
          disabled={deleting === issuer.id}
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30 border border-red-200 dark:border-red-800 transition-colors disabled:opacity-40"
        >
          {deleting === issuer.id ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <Trash2 size={12} />
          )}
          Delete
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// IssuerForm — create or edit an issuer
// ---------------------------------------------------------------------------

function IssuerForm({ initial, onSave, onCancel, saving, saveError }) {
  const [form, setForm] = useState(() => ({
    ...EMPTY_FORM,
    ...(initial ?? {}),
    algorithms: initial?.algorithms ?? ['RS256'],
  }))

  const [showPem, setShowPem] = useState(Boolean(initial?.jwk_pem))

  function set(field, value) {
    setForm((prev) => ({ ...prev, [field]: value }))
  }

  function toggleAlgo(algo) {
    set(
      'algorithms',
      form.algorithms.includes(algo)
        ? form.algorithms.filter((a) => a !== algo)
        : [...form.algorithms, algo],
    )
  }

  function handleSubmit(e) {
    e.preventDefault()
    const payload = {
      name: form.name.trim(),
      issuer: form.issuer.trim(),
      algorithms: form.algorithms.length > 0 ? form.algorithms : ['RS256'],
      enabled: form.enabled,
    }
    if (form.jwks_url.trim()) payload.jwks_url = form.jwks_url.trim()
    if (form.jwk_pem.trim()) payload.jwk_pem = form.jwk_pem.trim()
    if (form.audience.trim()) payload.audience = form.audience.trim()
    onSave(payload)
  }

  const isEdit = Boolean(initial?.id)

  return (
    <form onSubmit={handleSubmit} className="space-y-5 pt-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {/* Name */}
        <div className="space-y-1.5">
          <label className="block text-xs font-medium text-muted" htmlFor="issuer-name">
            Name <span className="text-red-500">*</span>
          </label>
          <input
            id="issuer-name"
            type="text"
            required
            value={form.name}
            onChange={(e) => set('name', e.target.value)}
            placeholder="My App Production"
            className={inputCls}
          />
        </div>

        {/* Issuer (iss claim) */}
        <div className="space-y-1.5">
          <label className="block text-xs font-medium text-muted" htmlFor="issuer-iss">
            Issuer (<code className="text-[10px]">iss</code> claim){' '}
            <span className="text-red-500">*</span>
          </label>
          <input
            id="issuer-iss"
            type="text"
            required
            value={form.issuer}
            onChange={(e) => set('issuer', e.target.value)}
            placeholder="https://myapp.example.com"
            className={inputCls + ' font-mono'}
          />
        </div>
      </div>

      {/* JWKS URL */}
      <div className="space-y-1.5">
        <label className="block text-xs font-medium text-muted" htmlFor="issuer-jwks-url">
          JWKS URL{' '}
          <span className="text-muted/60 font-normal">(recommended — keys are refreshed automatically)</span>
        </label>
        <input
          id="issuer-jwks-url"
          type="url"
          value={form.jwks_url}
          onChange={(e) => set('jwks_url', e.target.value)}
          placeholder="https://myapp.example.com/.well-known/jwks.json"
          className={inputCls + ' font-mono'}
        />
      </div>

      {/* Inline PEM / JWK toggle */}
      <div className="space-y-2">
        <button
          type="button"
          onClick={() => setShowPem((v) => !v)}
          className="flex items-center gap-1.5 text-xs font-medium text-muted hover:text-fg transition-colors"
        >
          {showPem ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          {showPem ? 'Hide' : 'Or paste'} inline PEM / JWK
        </button>

        {showPem && (
          <div className="space-y-1.5">
            <label className="block text-xs font-medium text-muted" htmlFor="issuer-pem">
              Public key (PEM or JWK JSON)
            </label>
            <textarea
              id="issuer-pem"
              rows={6}
              value={form.jwk_pem}
              onChange={(e) => set('jwk_pem', e.target.value)}
              placeholder={"-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"}
              className={inputCls + ' text-xs font-mono resize-y'}
            />
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {/* Algorithms */}
        <div className="space-y-2">
          <p className="text-xs font-medium text-muted">
            Algorithms <span className="text-red-500">*</span>
          </p>
          <div className="flex flex-wrap gap-1.5">
            {ALGORITHM_OPTIONS.map((algo) => {
              const active = form.algorithms.includes(algo)
              return (
                <button
                  key={algo}
                  type="button"
                  aria-pressed={active}
                  onClick={() => toggleAlgo(algo)}
                  className={[
                    'px-2.5 py-1 rounded-lg text-xs font-mono font-medium border transition-colors',
                    active
                      ? 'bg-primary/10 border-primary/40 text-primary dark:bg-primary/15'
                      : 'bg-surface-2 border-border text-muted hover:text-fg hover:border-border/80',
                  ].join(' ')}
                >
                  {algo}
                </button>
              )
            })}
          </div>
        </div>

        {/* Audience */}
        <div className="space-y-1.5">
          <label className="block text-xs font-medium text-muted" htmlFor="issuer-audience">
            Audience (<code className="text-[10px]">aud</code> claim){' '}
            <span className="text-muted/60 font-normal">optional</span>
          </label>
          <input
            id="issuer-audience"
            type="text"
            value={form.audience}
            onChange={(e) => set('audience', e.target.value)}
            placeholder="nubi-embed"
            className={inputCls + ' font-mono'}
          />
        </div>
      </div>

      {/* Enabled toggle */}
      <Toggle
        id="issuer-enabled"
        checked={form.enabled}
        onChange={(v) => set('enabled', v)}
        label="Enabled"
      />

      {saveError && (
        <div className="flex items-start gap-2 px-3 py-2.5 rounded-xl bg-red-50 dark:bg-red-950/20 border border-red-200 dark:border-red-800 text-sm text-red-700 dark:text-red-400">
          <AlertCircle size={15} className="shrink-0 mt-0.5" />
          {saveError}
        </div>
      )}

      <div className="flex items-center gap-3 pt-1">
        <PrimaryButton
          type="submit"
          busy={saving}
          disabled={saving || !form.name.trim() || !form.issuer.trim()}
        >
          {isEdit ? 'Update issuer' : 'Add issuer'}
        </PrimaryButton>
        <button
          type="button"
          onClick={onCancel}
          className="px-3 py-2 rounded-xl text-sm text-muted hover:text-fg hover:bg-surface-2 border border-border transition-colors"
        >
          Cancel
        </button>
      </div>
    </form>
  )
}

// ---------------------------------------------------------------------------
// SecuritySettings
// ---------------------------------------------------------------------------

export default function SecuritySettings() {
  const { data: issuersData, loading, error: loadErrorObj, reload: load } = useAsyncLoad(listJwtIssuers, [])
  // Local mirror enables optimistic updates after create/edit/delete without refetching.
  // Seeded from remote data on first successful load; null until then.
  const [localIssuers, setLocalIssuers] = useState(null)

  const issuers = localIssuers ?? (Array.isArray(issuersData) ? issuersData : [])
  const loadError = !loading && loadErrorObj ? (loadErrorObj?.message ?? 'Failed to load JWT issuers.') : null

  // Seed local mirror when remote data first arrives (or after a forced reload)
  if (!loading && issuersData !== null && localIssuers === null) {
    setLocalIssuers(Array.isArray(issuersData) ? issuersData : [])
  }

  // Form state
  const [formMode, setFormMode] = useState(null) // null | 'create' | 'edit'
  const [editTarget, setEditTarget] = useState(null) // JwtIssuer | null
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const [savedId, setSavedId] = useState(null)

  // Delete state
  const [deleting, setDeleting] = useState(null) // issuer id | null

  function openCreate() {
    setEditTarget(null)
    setFormMode('create')
    setSaveError(null)
  }

  function openEdit(issuer) {
    setEditTarget(issuer)
    setFormMode('edit')
    setSaveError(null)
  }

  function closeForm() {
    setFormMode(null)
    setEditTarget(null)
    setSaveError(null)
  }

  async function handleSave(payload) {
    setSaving(true)
    setSaveError(null)
    try {
      if (formMode === 'edit' && editTarget?.id) {
        const updated = await updateJwtIssuer(editTarget.id, payload)
        setLocalIssuers((prev) =>
          (prev ?? issuers).map((iss) => (iss.id === editTarget.id ? { ...iss, ...updated } : iss)),
        )
        setSavedId(editTarget.id)
      } else {
        const created = await createJwtIssuer(payload)
        setLocalIssuers((prev) => [...(prev ?? issuers), created])
        setSavedId(created?.id ?? null)
      }
      closeForm()
      setTimeout(() => setSavedId(null), 3000)
    } catch (err) {
      setSaveError(err?.message ?? 'Failed to save issuer.')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(id) {
    if (!window.confirm('Delete this JWT issuer? Embed tokens signed by it will stop working.')) return
    setDeleting(id)
    try {
      await deleteJwtIssuer(id)
      setLocalIssuers((prev) => (prev ?? issuers).filter((iss) => iss.id !== id))
      toast.success('JWT issuer deleted.')
    } catch (err) {
      toast.error(err?.message ?? 'Failed to delete issuer.')
    } finally {
      setDeleting(null)
    }
  }

  return (
    <div className="space-y-6">
      <SettingsPageHeader
        title="Security"
        description="Org-wide embed trust — JWT issuers used to verify host-signed embed tokens."
      />

      {/* Explainer */}
      <div className="flex items-start gap-3 px-4 py-3.5 rounded-2xl bg-surface border border-border">
        <KeyRound size={16} className="shrink-0 mt-0.5 text-primary" />
        <div className="text-sm text-muted space-y-1">
          <p>
            <span className="text-fg font-medium">Embed authentication</span> — when you embed
            a Nubi dashboard in your own application your backend signs a short-lived JWT
            (RS256 / ES256). Nubi verifies the signature against the public keys registered
            here before granting access.
          </p>
          <p>
            Each issuer entry ties a signing key (via JWKS URL or inline PEM/JWK) to the{' '}
            <code className="text-xs bg-surface-2 px-1 py-0.5 rounded">iss</code> claim value
            your tokens carry. Only enabled issuers are consulted at verification time.
          </p>
        </div>
      </div>

      {/* Issuer list */}
      <SettingsCard
        title="JWT issuers"
        description="Public keys used to verify host-signed embed JWTs."
      >
        {/* Loading */}
        {loading && (
          <div className="flex items-center gap-2 py-6 text-sm text-muted justify-center">
            <Loader2 size={15} className="animate-spin" />
            Loading issuers…
          </div>
        )}

        {/* Load error */}
        {!loading && loadError && (
          <div className="flex items-center justify-between gap-3 py-2">
            <ErrorText>{loadError}</ErrorText>
            <button
              type="button"
              onClick={load}
              className="text-xs text-muted hover:text-fg underline shrink-0"
            >
              Retry
            </button>
          </div>
        )}

        {/* Empty state */}
        {!loading && !loadError && issuers.length === 0 && formMode === null && (
          <div className="flex flex-col items-center justify-center text-center py-8 px-6 gap-2">
            <ShieldCheck size={26} className="text-muted/40" />
            <p className="text-sm font-medium text-fg">No JWT issuers configured</p>
            <p className="text-xs text-muted max-w-sm">
              Add an issuer below to enable host-signed embed authentication.
            </p>
          </div>
        )}

        {/* Issuer rows */}
        {!loading && !loadError && issuers.length > 0 && (
          <div className="-mx-5 sm:-mx-6 -my-5 divide-y divide-border">
            {issuers.map((issuer) => (
              <div key={issuer.id} className="px-5 sm:px-6">
                {/* Saved flash */}
                {savedId === issuer.id && (
                  <div className="flex items-center gap-1.5 py-1.5 text-xs text-success">
                    <CheckCircle size={13} />
                    Saved
                  </div>
                )}

                {/* Edit form for this row */}
                {formMode === 'edit' && editTarget?.id === issuer.id ? (
                  <IssuerForm
                    initial={editTarget}
                    onSave={handleSave}
                    onCancel={closeForm}
                    saving={saving}
                    saveError={saveError}
                  />
                ) : (
                  <IssuerRow
                    issuer={issuer}
                    onEdit={openEdit}
                    onDelete={handleDelete}
                    deleting={deleting}
                  />
                )}
              </div>
            ))}
          </div>
        )}
      </SettingsCard>

      {/* Add issuer */}
      {formMode !== 'edit' && (
        <SettingsCard title="Add an issuer" description="Register a new signing key.">
          {formMode === 'create' ? (
            <IssuerForm
              initial={null}
              onSave={handleSave}
              onCancel={closeForm}
              saving={saving}
              saveError={saveError}
            />
          ) : (
            <PrimaryButton type="button" onClick={openCreate}>
              <Plus size={13} />
              Add issuer
            </PrimaryButton>
          )}
        </SettingsCard>
      )}
    </div>
  )
}
