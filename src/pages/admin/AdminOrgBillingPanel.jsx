/**
 * AdminOrgBillingPanel — superadmin billing & limits controls for one org.
 *
 * Rendered inside AdminOrgDetailPage. Talks to the EE billing admin endpoints
 * (GET/PUT /ee/billing/admin/orgs/{id}). On an OSS build (no EE mounted) the
 * GET 404s → getAdminOrgBilling returns null → we render a muted note instead
 * of the editor.
 *
 * Capabilities:
 *   - Disable self-serve billing for the org (manually-managed enterprise).
 *   - Override the base tier the limits derive from (tier_override).
 *   - Override individual resource limits: Default / Unlimited / a custom number.
 */

import { useEffect, useMemo, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { getAdminOrgBilling, updateAdminOrgBilling } from '../../lib/admin.js'
import { AdminCard, FieldRow, Toggle, SaveButton, SavedBadge, ErrorText, inputCls } from './AdminUI.jsx'
import { useAsyncLoad } from '../../hooks/useAsyncLoad.js'

// Overridable limit fields — MUST match app.ee.billing.quota.OVERRIDE_KEYS.
const LIMIT_FIELDS = [
  { key: 'max_seats', label: 'Editor seats' },
  { key: 'max_viewer_seats', label: 'Viewer seats' },
  { key: 'max_connectors', label: 'Connectors' },
  { key: 'max_query_rows', label: 'Query rows' },
  { key: 'max_dashboards', label: 'Dashboards' },
  { key: 'max_flows', label: 'Flows' },
  { key: 'max_embedded_sessions_per_month', label: 'Embedded sessions / mo' },
  { key: 'max_agent_runs_per_month', label: 'Agent runs / mo' },
  { key: 'max_ai_calls_per_month', label: 'AI calls / mo' },
]

function fmtLimit(v) {
  if (v === null || v === undefined) return 'unlimited'
  return typeof v === 'number' ? v.toLocaleString() : String(v)
}

/** Build the initial per-field editor state from the loaded overrides. */
function initFields(overrides) {
  const state = {}
  for (const { key } of LIMIT_FIELDS) {
    if (overrides && Object.prototype.hasOwnProperty.call(overrides, key)) {
      const val = overrides[key]
      state[key] = { mode: val === null ? 'unlimited' : 'custom', value: val === null ? '' : String(val) }
    } else {
      state[key] = { mode: 'default', value: '' }
    }
  }
  return state
}

export default function AdminOrgBillingPanel({ orgId }) {
  const { data, loading, reload } = useAsyncLoad(() => getAdminOrgBilling(orgId), [orgId])

  const [billingDisabled, setBillingDisabled] = useState(false)
  const [tierOverride, setTierOverride] = useState('')
  const [note, setNote] = useState('')
  const [fields, setFields] = useState({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(null)

  // Seed editor state whenever fresh data arrives.
  useEffect(() => {
    if (!data) return
    setBillingDisabled(!!data.billing_disabled)
    setTierOverride(data.tier_override || '')
    setNote(data.note || '')
    setFields(initFields(data.limit_overrides))
    setSaved(false)
    setError(null)
  }, [data])

  const defaults = data?.tier_defaults || {}
  const effective = data?.effective_limits || {}

  const setFieldMode = (key, mode) =>
    setFields((f) => ({ ...f, [key]: { ...f[key], mode } }))
  const setFieldValue = (key, value) =>
    setFields((f) => ({ ...f, [key]: { ...f[key], value } }))

  const dirtyOverrides = useMemo(() => {
    const out = {}
    for (const { key, float } of LIMIT_FIELDS) {
      const fs = fields[key]
      if (!fs || fs.mode === 'default') continue
      if (fs.mode === 'unlimited') {
        out[key] = null
      } else {
        const n = float ? parseFloat(fs.value) : parseInt(fs.value, 10)
        if (!Number.isNaN(n) && n >= 0) out[key] = n
      }
    }
    return out
  }, [fields])

  async function handleSave() {
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      await updateAdminOrgBilling(orgId, {
        billing_disabled: billingDisabled,
        tier_override: tierOverride || null,
        limit_overrides: dirtyOverrides,
        note: note.trim() || null,
      })
      setSaved(true)
      reload()
    } catch (cause) {
      setError(cause.message || 'Failed to save billing settings.')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <AdminCard title="Billing & limits">
        <div className="flex items-center gap-2 px-5 py-6 text-sm text-muted">
          <Loader2 size={16} className="animate-spin" /> Loading billing…
        </div>
      </AdminCard>
    )
  }

  // EE billing not present on this deployment (endpoint 404 → null).
  if (!data) {
    return (
      <AdminCard title="Billing & limits">
        <p className="px-5 py-6 text-sm text-muted">
          Billing management is not enabled on this deployment.
        </p>
      </AdminCard>
    )
  }

  const subLabel = data.subscription
    ? `${data.subscription.tier} · ${data.subscription.status}`
    : 'none (self-serve free / unbilled)'

  return (
    <AdminCard title="Billing & limits">
      <div className="px-5 py-4 space-y-1 divide-y divide-border">
        {/* ── Summary row ─────────────────────────────────────────────── */}
        <div className="flex flex-wrap gap-x-8 gap-y-2 text-sm pb-4">
          <div className="text-muted">
            Subscription <span className="text-fg">{subLabel}</span>
          </div>
          <div className="text-muted">
            Effective base tier <span className="text-fg">{data.base_tier}</span>
          </div>
        </div>

        {/* ── Disable billing ─────────────────────────────────────────── */}
        <FieldRow label="Self-serve billing">
          <Toggle
            id="billing-disabled"
            checked={billingDisabled}
            onChange={setBillingDisabled}
            label={billingDisabled ? 'Disabled' : 'Enabled'}
            description="For manually-managed / enterprise accounts. Disabling blocks Paystack checkout and
              turns off overage billing — the limits below are then enforced as hard caps (set a
              field to “Unlimited” to remove a cap)."
          />
        </FieldRow>

        {/* ── Tier override ───────────────────────────────────────────── */}
        <FieldRow
          label="Base tier override"
          htmlFor="tier-override"
          description="Limits derive from this tier before per-field overrides apply."
        >
          <select
            id="tier-override"
            value={tierOverride}
            onChange={(e) => setTierOverride(e.target.value)}
            className={inputCls}
          >
            <option value="">— use subscription tier —</option>
            {(data.available_tiers || []).map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </FieldRow>

        {/* ── Per-field limit overrides ───────────────────────────────── */}
        <div className="py-4">
          <p className="text-sm font-medium text-fg mb-2">Resource limits</p>
          <div className="rounded-xl border border-border overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-surface-2/40">
                  <th className="px-3 py-2 text-left text-xs font-medium text-muted uppercase tracking-wider">Limit</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-muted uppercase tracking-wider">Mode</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-muted uppercase tracking-wider">Value</th>
                  <th className="px-3 py-2 text-right text-xs font-medium text-muted uppercase tracking-wider">Effective</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {LIMIT_FIELDS.map(({ key, label, float }) => {
                  const fs = fields[key] || { mode: 'default', value: '' }
                  return (
                    <tr key={key} className="hover:bg-surface-2/40 transition-colors">
                      <td className="px-3 py-2 text-fg">{label}</td>
                      <td className="px-3 py-2">
                        <select
                          value={fs.mode}
                          onChange={(e) => setFieldMode(key, e.target.value)}
                          aria-label={`${label} override mode`}
                          className={inputCls}
                        >
                          <option value="default">Default ({fmtLimit(defaults[key])})</option>
                          <option value="unlimited">Unlimited</option>
                          <option value="custom">Custom…</option>
                        </select>
                      </td>
                      <td className="px-3 py-2">
                        {fs.mode === 'custom' ? (
                          <input
                            type="number"
                            min="0"
                            step={float ? '0.1' : '1'}
                            value={fs.value}
                            onChange={(e) => setFieldValue(key, e.target.value)}
                            placeholder={fmtLimit(defaults[key])}
                            aria-label={`${label} custom value`}
                            className={`${inputCls} w-32 tabular-nums`}
                          />
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-muted">
                        {fmtLimit(effective[key])}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* ── Note ────────────────────────────────────────────────────── */}
        <FieldRow label="Note" htmlFor="billing-note">
          <input
            id="billing-note"
            type="text"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. contract ref — no PII"
            className={`${inputCls} w-full`}
          />
        </FieldRow>

        {/* ── Save row ────────────────────────────────────────────────── */}
        <div className="flex items-center gap-3 pt-4">
          <SaveButton onClick={handleSave} disabled={saving} busy={saving}>
            Save billing settings
          </SaveButton>
          <SavedBadge show={saved} />
          <ErrorText>{error}</ErrorText>
        </div>
      </div>
    </AdminCard>
  )
}
