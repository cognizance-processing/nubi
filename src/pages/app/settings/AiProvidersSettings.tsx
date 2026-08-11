/**
 * AiProvidersSettings — Settings → AI providers.
 *
 * Two things live here:
 *
 *   1. A read-only overview of every AI provider Nubi is configured to serve
 *      (GET /ai/providers) — logo, models, and whether the OPERATOR has a
 *      vendor key set up for it ("configured").
 *
 *   2. "Bring your own key" (BYO): on a PAID tier, an org admin/owner can
 *      store the ORG's own provider API key so that org's calls to that
 *      vendor use its own key and are billed directly by the vendor — never
 *      charged from the Nubi usage wallet. Free-tier orgs see the same
 *      section as a disabled upgrade hint (`byo.can_byo` from the backend
 *      gates both this UI and the backend routes).
 *
 * Backend contract (see backend/app/routes/ai.py + ai_keys_routes.py):
 *   GET    /ai/providers   { providers: [...], byo: { can_byo, providers } }
 *   POST   /ai/keys        { org_id, provider, api_key } → { configured: true }
 *   DELETE /ai/keys        { org_id, provider }          → { configured: false }
 *
 * Keys are WRITE-ONLY — the value is never echoed back, only a boolean
 * "configured" flag. The input is always rendered blank; saving replaces
 * whatever was stored (there is no partial-update / "leave blank to keep"
 * semantics here, unlike IntegrationsSettings, since /ai/keys always requires
 * a full api_key on POST).
 *
 * Styling mirrors the other settings sections (SettingsUI building blocks).
 */

import { useCallback, useEffect, useState } from 'react'
import {
  Bot, Check, Loader2, Lock, ShieldCheck, Sparkles, Trash2, AlertTriangle, ArrowUpRight,
} from 'lucide-react'
import { Link } from 'react-router-dom'

import { useOrg } from '../../../contexts/OrgContext.jsx'
import { useFeature } from '../../../lib/features.js'
import { fetchAiProviders, setAiKey, clearAiKey, formatModelPrice } from '../../../lib/aiApi.js'
import ProviderIcon from '../../../components/ai/ProviderIcon.jsx'
import {
  SettingsPageHeader,
  SettingsCard,
  ErrorText,
  inputCls,
} from './SettingsUI.jsx'
import { toast } from '../../../components/ui/Toast.jsx'

// ---------------------------------------------------------------------------
// Provider models list (read-only overview)
// ---------------------------------------------------------------------------

function ModelsRow({ provider }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-center gap-2.5 min-w-0">
          <ProviderIcon provider={provider.id} size={20} />
          <div className="min-w-0">
            <p className="text-sm font-semibold text-fg truncate">{provider.display_name}</p>
            <p className="text-xs text-muted">{provider.models.length} model{provider.models.length === 1 ? '' : 's'} available</p>
          </div>
        </div>
        {provider.configured ? (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300 text-[10px] font-semibold uppercase tracking-wide shrink-0">
            <Check size={10} /> Configured
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300 text-[10px] font-semibold uppercase tracking-wide shrink-0">
            <Lock size={10} /> Not set up
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {provider.models.map((m) => {
          const price = formatModelPrice(m)
          return (
            <span
              key={m.id}
              title={price ?? undefined}
              className="inline-flex flex-col gap-0.5 px-2 py-1 rounded-lg bg-surface-2 border border-border text-xs text-fg"
            >
              <span className="inline-flex items-center gap-1">
                {m.display_name}
                {m.default && (
                  <span className="text-[9.5px] font-semibold text-muted uppercase tracking-wide">default</span>
                )}
              </span>
              {price && (
                <span className="text-[10px] text-muted tabular-nums">{price}</span>
              )}
            </span>
          )
        })}
      </div>
      {!provider.configured && (
        <p className="text-[11px] text-muted mt-2.5">
          The operator hasn't configured a platform key for {provider.display_name} yet —
          this provider is currently unavailable for wallet-billed calls
          {byoFallbackHint(provider)}
        </p>
      )}
    </div>
  )
}

function byoFallbackHint(provider) {
  return provider.byoConfigured
    ? ', but this org\'s own key is in use.'
    : ' (bring your own key below, if your plan allows it).'
}

// ---------------------------------------------------------------------------
// BYO key row — one provider's bring-your-own-key control
// ---------------------------------------------------------------------------

function ByoKeyRow({ provider, configured, canWrite, canByo, onSave, onClear }) {
  const [value, setValue] = useState('')
  const [saving, setSaving] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [confirmClear, setConfirmClear] = useState(false)
  const [error, setError] = useState(null)

  const disabled = !canWrite || !canByo

  const handleSave = useCallback(async () => {
    const trimmed = value.trim()
    if (!trimmed) {
      setError('Paste an API key first.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await onSave(provider.id, trimmed)
      setValue('')
      toast.success(`${provider.display_name} key saved.`)
    } catch (err) {
      setError(err?.message || 'Could not save key.')
    } finally {
      setSaving(false)
    }
  }, [value, provider, onSave])

  const handleClear = useCallback(async () => {
    setClearing(true)
    setError(null)
    try {
      await onClear(provider.id)
      toast.success(`${provider.display_name} key cleared.`)
    } catch (err) {
      setError(err?.message || 'Could not clear key.')
    } finally {
      setClearing(false)
      setConfirmClear(false)
    }
  }, [provider, onClear])

  return (
    <div className="py-4 first:pt-0 last:pb-0">
      <div className="flex flex-col sm:flex-row sm:items-center gap-3">
        <div className="flex items-center gap-2.5 sm:w-44 shrink-0">
          <ProviderIcon provider={provider.id} size={18} />
          <span className="text-sm font-medium text-fg">{provider.display_name}</span>
        </div>

        <div className="flex-1 min-w-0 flex flex-col sm:flex-row gap-2">
          <input
            type="password"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            disabled={disabled || saving}
            placeholder={disabled ? 'Upgrade to a paid plan to set a key' : configured ? '•••••••• (leave blank, or paste a new key to replace it)' : `Paste your ${provider.display_name} API key`}
            autoComplete="new-password"
            className={`${inputCls} font-mono text-xs flex-1 disabled:opacity-50 disabled:cursor-not-allowed`}
          />
          <div className="flex items-center gap-1.5 shrink-0">
            <button
              onClick={handleSave}
              disabled={disabled || saving || !value.trim()}
              className="inline-flex items-center gap-1.5 h-9 px-3 rounded-xl text-xs font-semibold bg-primary text-primary-fg hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity"
            >
              {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
              Save
            </button>
            {configured && !disabled && (
              confirmClear ? (
                <button
                  onClick={handleClear}
                  disabled={clearing}
                  className="inline-flex items-center gap-1.5 h-9 px-3 rounded-xl text-xs font-semibold bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 transition-colors"
                >
                  {clearing ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                  Confirm
                </button>
              ) : (
                <button
                  onClick={() => setConfirmClear(true)}
                  title="Clear stored key"
                  aria-label={`Clear ${provider.display_name} key`}
                  className="inline-flex items-center justify-center h-9 w-9 rounded-xl border border-border text-muted hover:text-red-600 hover:border-red-200 hover:bg-red-50 dark:hover:text-red-400 dark:hover:border-red-900/40 dark:hover:bg-red-900/10 transition-colors"
                >
                  <Trash2 size={13} />
                </button>
              )
            )}
          </div>
        </div>

        <div className="sm:w-32 shrink-0 flex sm:justify-end">
          {configured ? (
            <span className="inline-flex items-center gap-1 text-xs font-medium text-teal-600 dark:text-teal-400">
              <Check size={12} /> Configured
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-xs text-muted">
              Not set
            </span>
          )}
        </div>
      </div>
      {error && <ErrorText>{error}</ErrorText>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// AiProvidersSettings — the section
// ---------------------------------------------------------------------------

export default function AiProvidersSettings() {
  const { activeOrg } = useOrg()
  const billingEnabled = useFeature('billing')

  const [providers, setProviders] = useState([])
  const [byo, setByo] = useState({ can_byo: false, providers: {} })
  const [loading, setLoading] = useState(true)

  // Match the backend's admin/owner (or superadmin) gate for POST/DELETE
  // /ai/keys — a plain "member" role is not enough (unlike useCanWrite(),
  // which only excludes viewers).
  const canManageKeys = activeOrg?.role === 'owner' || activeOrg?.role === 'admin'

  const load = useCallback(async () => {
    setLoading(true)
    const data = await fetchAiProviders()
    setProviders(data.providers)
    setByo(data.byo)
    setLoading(false)
  }, [])

  // Defer the initial load (no synchronous setState in the effect body).
  useEffect(() => {
    const t = setTimeout(load, 0)
    return () => clearTimeout(t)
  }, [load, activeOrg?.id])

  const handleSave = useCallback(async (providerId, apiKey) => {
    if (!activeOrg?.id) throw new Error('No active organisation.')
    await setAiKey(activeOrg.id, providerId, apiKey)
    setByo((prev) => ({ ...prev, providers: { ...prev.providers, [providerId]: true } }))
  }, [activeOrg])

  const handleClear = useCallback(async (providerId) => {
    if (!activeOrg?.id) throw new Error('No active organisation.')
    await clearAiKey(activeOrg.id, providerId)
    setByo((prev) => ({ ...prev, providers: { ...prev.providers, [providerId]: false } }))
  }, [activeOrg])

  const withByoFlag = providers.map((p) => ({ ...p, byoConfigured: Boolean(byo.providers?.[p.id]) }))

  return (
    <div className="space-y-6">
      <SettingsPageHeader
        title="AI providers"
        description="Nubi's chat, text-to-SQL, and dashboard generation run on your choice of Claude, GPT, or Gemini models. See what's available below, and optionally bring your own provider key."
      />

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-muted py-6 justify-center">
          <Loader2 size={16} className="animate-spin" /> Loading AI providers…
        </div>
      ) : providers.length === 0 ? (
        <SettingsCard>
          <div className="flex flex-col items-center justify-center text-center py-8 px-6 gap-2">
            <Bot size={26} className="text-muted/40" />
            <p className="text-sm font-medium text-fg">No AI providers configured</p>
            <p className="text-xs text-muted max-w-sm">
              This deployment hasn't enabled any LLM providers yet. Contact your administrator.
            </p>
          </div>
        </SettingsCard>
      ) : (
        <>
          <SettingsCard
            title="Providers & models"
            description="Every model advertised here is safe to select in chat — the same allowlist the backend enforces."
          >
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {withByoFlag.map((p) => <ModelsRow key={p.id} provider={p} />)}
            </div>
          </SettingsCard>

          <SettingsCard
            title="Bring your own key"
            description="Use your own provider API key instead of Nubi's platform key. Calls made with your key bill directly through that vendor's account and are never charged from your Nubi usage wallet."
          >
            {!byo.can_byo ? (
              <div className="flex flex-col sm:flex-row sm:items-center gap-4 rounded-xl border border-dashed border-border bg-surface-2/50 px-4 py-5">
                <div className="flex items-center justify-center w-11 h-11 rounded-xl bg-primary/10 text-primary shrink-0">
                  <Sparkles size={18} />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-fg">Bringing your own key is a paid-plan feature</p>
                  <p className="text-xs text-muted mt-0.5 leading-relaxed">
                    Upgrade to Starter or above to connect your own Anthropic, OpenAI, or Gemini key —
                    your calls to that provider skip Nubi's usage wallet entirely.
                  </p>
                </div>
                {billingEnabled ? (
                  <Link
                    to="/billing"
                    className="inline-flex items-center gap-1.5 h-9 px-3.5 rounded-xl text-xs font-semibold bg-primary text-primary-fg hover:opacity-90 transition-opacity shrink-0"
                  >
                    View plans <ArrowUpRight size={13} />
                  </Link>
                ) : (
                  <span className="text-xs text-muted shrink-0">Ask your administrator to upgrade.</span>
                )}
              </div>
            ) : !canManageKeys ? (
              <div className="flex items-start gap-2 rounded-xl border border-border bg-surface-2/50 px-4 py-3 text-xs text-muted">
                <AlertTriangle size={14} className="shrink-0 mt-0.5" />
                Only an org admin or owner can set or clear a provider key. Ask one of them to configure this.
              </div>
            ) : (
              <div className="rounded-xl border border-teal-200 dark:border-teal-800 bg-teal-50 dark:bg-teal-900/15 px-3.5 py-2.5 mb-4 flex items-start gap-2">
                <ShieldCheck size={14} className="text-teal-600 dark:text-teal-400 shrink-0 mt-0.5" />
                <p className="text-xs text-teal-800 dark:text-teal-200 leading-relaxed">
                  Keys are encrypted at rest and never shown again after saving — this list only ever
                  shows whether a key is configured, not its value.
                </p>
              </div>
            )}

            {byo.can_byo && (
              <div className="divide-y divide-border">
                {withByoFlag.map((p) => (
                  <ByoKeyRow
                    key={p.id}
                    provider={p}
                    configured={Boolean(byo.providers?.[p.id])}
                    canWrite={canManageKeys}
                    canByo={byo.can_byo}
                    onSave={handleSave}
                    onClear={handleClear}
                  />
                ))}
              </div>
            )}
          </SettingsCard>
        </>
      )}
    </div>
  )
}
