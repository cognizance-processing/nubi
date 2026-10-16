/**
 * connectorForms.jsx — schema-driven form fields for connectors.
 *
 * The connector catalog (src/data/connectors.js) is the single source of truth
 * for which connectors exist and what fields each one needs. <DynamicForm>
 * renders those field schemas; this file no longer hand-codes one component per
 * connector type, so adding a new connector is a data-only change.
 *
 * <DynamicForm> receives:
 *   { type, config, secret, onChange }
 *     type    — connector id (e.g. 'postgres')
 *     config  — non-secret fields (object), controlled
 *     secret  — secret fields (object), controlled
 *     onChange(group, key, value) — group: 'config' | 'secret'
 */

import { useEffect, useState } from 'react'
import { getTypeInfo } from '../../data/connectors.js'
import { listBridges } from '../../lib/bridges.js'
import { useOrg } from '../../contexts/OrgContext.jsx'
import BridgeQuickConnect from '../../components/app/BridgeQuickConnect.jsx'
import Input, { Select, Textarea } from '../../components/ui/Input.jsx'
import '../../components/app/appPatterns.css'

const MANAGE_ROLES = ['owner', 'admin']

// ---------------------------------------------------------------------------
// Shared field primitives
// ---------------------------------------------------------------------------

function Label({ htmlFor = undefined, children, optional = false, required = false }) {
  return (
    <label htmlFor={htmlFor} className="nubi-label">
      {children}
      {required && <span className="text-danger ml-0.5" aria-hidden="true">*</span>}
      {optional && <span className="ml-1.5 font-normal text-subtle">Optional</span>}
    </label>
  )
}

function SecretNote() {
  return (
    <p className="nubi-hint flex items-center gap-1.5">
      <span className="nubi-dot nubi-dot-live" aria-hidden="true" />
      Encrypted at rest with AES-256-GCM. Never shown after save.
    </p>
  )
}

function HelpNote({ children }) {
  return <p className="nubi-hint">{children}</p>
}

// ---------------------------------------------------------------------------
// Per-type field renderers
// ---------------------------------------------------------------------------

function normaliseOptions(options) {
  return (options ?? []).map((o) =>
    typeof o === 'object' ? o : { value: o, label: o },
  )
}

function Segmented({ field, value, onChange }) {
  const opts = field.options ?? []
  return (
    <div className="flex gap-2" role="radiogroup" aria-label={field.label}>
      {opts.map((opt) => {
        const active = value === opt.value
        return (
          <button
            key={String(opt.value)}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(opt.value)}
            className="flex-1 px-3 py-2.5 text-left text-sm transition-colors"
            style={{
              borderRadius: 2,
              border: `1px solid ${active ? 'var(--accent)' : 'var(--border-control)'}`,
              boxShadow: active ? 'inset 0 0 0 1px var(--accent)' : 'none',
              background: active ? 'var(--accent-soft)' : 'var(--surface)',
              color: 'var(--text)',
            }}
          >
            <div className="font-semibold">{opt.label}</div>
            {opt.desc && <div className="text-xs mt-0.5 text-muted">{opt.desc}</div>}
          </button>
        )
      })}
    </div>
  )
}

function SaJson({ field, value, onChange }) {
  function handleFile(e) {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (ev) => onChange(ev.target.result)
    reader.readAsText(file)
  }
  return (
    <div className="space-y-2">
      <label className="nubi-btn nubi-btn-secondary nubi-btn-sm cursor-pointer w-full justify-center">
        <input type="file" accept=".json,application/json" onChange={handleFile} className="sr-only" />
        Upload .json key file
      </label>
      <p className="text-xs text-subtle text-center">or paste the key below</p>
      <Textarea
        id={`f-${field.key}`}
        placeholder={'{\n  "type": "service_account",\n  ...\n}'}
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        rows={6}
        className="font-mono"
        spellCheck={false}
      />
      <SecretNote />
    </div>
  )
}

/**
 * BridgeSelect — live dropdown of the org's Bridge v2 agents plus a
 * "connect a local/private database" quick-start (BridgeQuickConnect). See
 * the 'bridge_select' field type doc in src/data/connectors.js.
 */
function BridgeSelect({ value, onChange, invalid = false }) {
  const { activeOrg } = useOrg()
  const canManage = MANAGE_ROLES.includes(activeOrg?.role)
  const [bridges, setBridges] = useState([])
  const [loading, setLoading] = useState(true)
  const [quickConnect, setQuickConnect] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    listBridges().then((rows) => {
      if (cancelled) return
      setBridges(rows)
      setLoading(false)
    })
    return () => {
      cancelled = true
    }
    // Re-fetch if the org switches while this picker is mounted, not just on
    // first mount — same "stale org" gap as BridgesSettings.tsx.
  }, [activeOrg?.id])

  function handleConnected(bridgeId) {
    onChange(bridgeId)
    setQuickConnect(false)
    listBridges().then(setBridges) // pick up the just-created bridge
  }

  if (quickConnect) {
    return (
      <BridgeQuickConnect
        canManage={canManage}
        onConnected={handleConnected}
        onCancel={() => setQuickConnect(false)}
      />
    )
  }

  return (
    <div className="space-y-1.5">
      <Select
        id="f-bridge_id"
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value || undefined)}
        aria-invalid={invalid ? 'true' : undefined}
        error={invalid}
      >
        <option value="">{loading ? 'Loading bridges…' : 'Select a bridge'}</option>
        {bridges.map((b) => (
          <option key={b.id} value={b.id}>
            {b.name} · {b.status === 'online' ? 'online' : 'offline'}
          </option>
        ))}
      </Select>
      {!loading && bridges.length === 0 && (
        <p className="nubi-hint">No bridges yet. Connect one below.</p>
      )}
      <button type="button" onClick={() => setQuickConnect(true)} className="ap-link-btn">
        Connect a local or private database
      </button>
    </div>
  )
}

function FieldControl({ field, value, onChange, invalid = false, onBlur = undefined }) {
  const id = `f-${field.key}`
  const isSecret = (field.group ?? 'config') === 'secret'
  const aria = { 'aria-invalid': invalid ? ('true' as const) : undefined }

  switch (field.type) {
    case 'segmented':
      return <Segmented field={field} value={value} onChange={onChange} />
    case 'sa_json':
      return <SaJson field={field} value={value} onChange={onChange} />
    case 'bridge_select':
      return <BridgeSelect value={value} onChange={onChange} invalid={invalid} />
    case 'select':
      return (
        <Select
          id={id}
          value={value ?? field.default ?? ''}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onBlur}
          error={invalid}
          options={normaliseOptions(field.options).map((o) => ({ value: o.value, label: o.label }))}
        />
      )
    case 'textarea':
      return (
        <Textarea
          id={id}
          placeholder={field.placeholder}
          value={value ?? ''}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onBlur}
          rows={field.rows ?? 3}
          className="font-mono"
          error={invalid}
          spellCheck={false}
        />
      )
    default: {
      // text | number | url | password
      const htmlType =
        field.type === 'password' ? 'password'
        : field.type === 'number' ? 'number'
        : field.type === 'url' ? 'url'
        : 'text'
      return (
        <Input
          id={id}
          type={htmlType}
          placeholder={field.placeholder ?? (field.default != null ? String(field.default) : '')}
          value={value ?? ''}
          onChange={(e) =>
            onChange(field.type === 'number' ? (e.target.value ? Number(e.target.value) : undefined) : e.target.value)
          }
          onBlur={onBlur}
          autoComplete={isSecret ? 'off' : undefined}
          error={invalid}
          className={field.type === 'number' ? 'ap-mono' : undefined}
          {...aria}
        />
      )
    }
  }
}

const WIDTH_CLS = { full: 'sm:col-span-2', half: 'sm:col-span-1', third: 'sm:col-span-1' }

/** Keys of required visible fields that are empty. Pure: used by the form to gate submit. */
export function missingRequired(type, config = {}, secret = {}, editing = false) {
  const info = getTypeInfo(type)
  return (info.fields ?? [])
    .filter((f) => f.required && (!f.showIf || f.showIf(config)))
    .filter((f) => {
      const group = f.group ?? 'config'
      const v = group === 'secret' ? secret[f.key] : config[f.key]
      // A saved secret is never returned: when editing, blank means "keep".
      if (group === 'secret' && editing) return false
      return v === undefined || v === null || String(v).trim() === ''
    })
    .map((f) => f.key)
}

// ---------------------------------------------------------------------------
// DynamicForm — renders all fields for a connector type from its schema
// ---------------------------------------------------------------------------

export function DynamicForm({ type, config = {}, secret = {}, onChange, errors = {}, onFieldBlur = undefined }) {
  const info = getTypeInfo(type)
  const fields = (info.fields ?? []).filter((f) => !f.showIf || f.showIf(config))

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-5">
      {fields.map((field) => {
        const group = field.group ?? 'config'
        const value = group === 'secret' ? secret[field.key] : config[field.key]
        const isSecret = group === 'secret'
        const err = errors[field.key]
        const labelled = field.type !== 'segmented' && field.type !== 'sa_json'
        return (
          <div key={field.key} className={WIDTH_CLS[field.width] ?? 'sm:col-span-2'}>
            <Label htmlFor={labelled ? `f-${field.key}` : undefined} optional={field.optional} required={field.required}>
              {field.label}
            </Label>
            <FieldControl
              field={field}
              value={value}
              invalid={Boolean(err)}
              onBlur={() => onFieldBlur?.(field.key)}
              onChange={(v) => onChange(group, field.key, v)}
            />
            {err && <p role="alert" className="nubi-field-error">{err}</p>}
            {field.help && <HelpNote>{field.help}</HelpNote>}
            {isSecret && field.type !== 'sa_json' && <SecretNote />}
          </div>
        )
      })}
    </div>
  )
}
