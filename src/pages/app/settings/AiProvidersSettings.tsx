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
  Bot, Check, ShieldCheck, Sparkles, Trash2, AlertTriangle, ArrowUpRight, Loader2,
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
import Button from '../../../components/ui/Button.jsx'
import Tag from '../../../components/ui/Tag.jsx'

// ---------------------------------------------------------------------------
// Provider models list (read-only overview)
// ---------------------------------------------------------------------------

function ModelsRow({ provider }) {
  const n = provider.models.length
  return (
    <div className="py-5 first:pt-0 last:pb-0">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div className="flex items-center gap-3 min-w-0">
          <ProviderIcon provider={provider.id} size={22} />
          <div className="min-w-0">
            <p className="text-sm font-semibold text-fg truncate leading-tight">{provider.display_name}</p>
            <p className="font-mono text-[11px] uppercase tracking-[0.06em] text-subtle tabular-nums mt-0.5">
              {n} model{n === 1 ? '' : 's'}
            </p>
          </div>
        </div>
        {provider.configured ? (
          <Tag variant="success" dot>Configured</Tag>
        ) : (
          <Tag dot>Not set up</Tag>
        )}
      </div>
      <ul className="flex flex-wrap gap-2 list-none p-0 m-0">
        {provider.models.map((m) => {
          const price = formatModelPrice(m)
          return (
            <li
              key={m.id}
              title={price ?? undefined}
              className="inline-flex flex-col gap-0.5 px-2.5 py-1.5 border border-border text-[13px] text-fg"
              style={{ borderRadius: 2 }}
            >
              <span className="inline-flex items-center gap-1.5 leading-tight">
                {m.display_name}
                {m.default && (
                  <span className="font-mono text-[11px] uppercase tracking-[0.06em] text-accent">default</span>
                )}
              </span>
              {price && (
                <span className="font-mono text-[11px] text-subtle tabular-nums">{price}</span>
              )}
            </li>
          )
        })}
      </ul>
      {!provider.configured && (
        <p className="text-xs text-muted mt-3 max-w-prose leading-relaxed">
          The operator hasn&apos;t configured a platform key for {provider.display_name} yet,
          so it is unavailable for wallet-billed calls
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
        <div className="flex items-center gap-3 sm:w-48 shrink-0">
          <ProviderIcon provider={provider.id} size={18} />
          <span className="text-sm font-medium text-fg">{provider.display_name}</span>
        </div>

        <div className="flex-1 min-w-0 flex flex-col sm:flex-row gap-2">
          <input
            type="password"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            disabled={disabled || saving}
            aria-label={`${provider.display_name} API key`}
            placeholder={disabled ? 'Upgrade to a paid plan to set a key' : configured ? '•••••••• (paste a new key to replace it)' : `Paste your ${provider.display_name} API key`}
            autoComplete="new-password"
            className={`${inputCls} font-mono text-xs flex-1 disabled:opacity-50 disabled:cursor-not-allowed`}
          />
          <div className="flex items-center gap-1.5 shrink-0">
            <Button
              size="sm"
              onClick={handleSave}
              disabled={disabled || !value.trim()}
              loading={saving}
            >
              {!saving && <Check size={13} aria-hidden="true" />}
              Save
            </Button>
            {configured && !disabled && (
              confirmClear ? (
                <Button size="sm" variant="danger" onClick={handleClear} loading={clearing}>
                  {!clearing && <Trash2 size={13} aria-hidden="true" />}
                  Confirm
                </Button>
              ) : (
                <Button
                  size="icon-sm"
                  variant="secondary"
                  onClick={() => setConfirmClear(true)}
                  title="Clear stored key"
                  aria-label={`Clear ${provider.display_name} key`}
                >
                  <Trash2 size={13} aria-hidden="true" />
                </Button>
              )
            )}
          </div>
        </div>

        <div className="sm:w-32 shrink-0 flex sm:justify-end">
          {configured ? <Tag variant="success" dot>Key set</Tag> : <Tag dot>Not set</Tag>}
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
        description="Chat, text-to-SQL and dashboard generation run on your choice of Claude, GPT or Gemini models. See what is available below, and optionally bring your own provider key."
      />

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-muted py-6 justify-center">
          <Loader2 size={16} className="animate-spin" /> Loading AI providers…
        </div>
      ) : providers.length === 0 ? (
        <SettingsCard>
          <div className="flex flex-col items-center justify-center text-center py-8 px-6 gap-2">
            <Bot size={24} strokeWidth={1.5} className="text-subtle" aria-hidden="true" />
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
            <div className="divide-y divide-border">
              {withByoFlag.map((p) => <ModelsRow key={p.id} provider={p} />)}
            </div>
          </SettingsCard>

          <SettingsCard
            title="Bring your own key"
            description="Use your own provider API key instead of Nubi's platform key. Calls made with your key bill directly through that vendor's account and are never charged from your Nubi usage wallet."
          >
            {!byo.can_byo ? (
              <div className="flex flex-col sm:flex-row sm:items-center gap-4 border border-border px-4 py-4" style={{ borderRadius: 2 }}>
                <div
                  className="flex items-center justify-center w-10 h-10 border border-border text-fg shrink-0"
                  style={{ borderRadius: 2 }}
                >
                  <Sparkles size={18} strokeWidth={1.75} aria-hidden="true" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-fg">Bringing your own key is a paid-plan feature</p>
                  <p className="text-xs text-muted mt-0.5 leading-relaxed">
                    Upgrade to Starter or above to connect your own Anthropic, OpenAI, or Gemini key.
                    Your calls to that provider skip Nubi&apos;s usage wallet entirely.
                  </p>
                </div>
                {billingEnabled ? (
                  <Button asChild size="sm">
                    <Link to="/billing">View plans <ArrowUpRight size={13} aria-hidden="true" /></Link>
                  </Button>
                ) : (
                  <span className="text-xs text-muted shrink-0">Ask your administrator to upgrade.</span>
                )}
              </div>
            ) : !canManageKeys ? (
              <div className="nubi-notice nubi-notice-warning text-xs">
                <AlertTriangle size={14} className="shrink-0 mt-0.5" aria-hidden="true" />
                Only an org admin or owner can set or clear a provider key. Ask one of them to configure this.
              </div>
            ) : (
              <div className="nubi-notice nubi-notice-info mb-4 text-xs">
                <ShieldCheck size={14} className="shrink-0 mt-0.5" aria-hidden="true" />
                <p className="leading-relaxed">
                  Keys are encrypted at rest and never shown again after saving. This list only ever
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
