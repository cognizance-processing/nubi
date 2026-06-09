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

import { getTypeInfo } from '../../data/connectors.js'

// ---------------------------------------------------------------------------
// Shared field primitives
// ---------------------------------------------------------------------------

function Label({ htmlFor, children, optional }) {
  return (
    <label htmlFor={htmlFor} className="block text-xs font-medium text-fg mb-1">
      {children}
      {optional && <span className="ml-1 text-muted font-normal">(optional)</span>}
    </label>
  )
}

const inputCls = `
  w-full rounded-lg border border-border bg-surface
  px-3 py-2 text-sm text-fg placeholder:text-muted
  focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent
  transition-colors
`

function SecretNote() {
  return (
    <p className="text-[11px] text-muted mt-1 flex items-center gap-1">
      <span className="inline-block w-1.5 h-1.5 rounded-full bg-accent shrink-0" />
      Encrypted at rest with AES-256-GCM — never shown after save
    </p>
  )
}

function HelpNote({ children }) {
  return <p className="text-[11px] text-muted mt-1 leading-relaxed">{children}</p>
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
    <div className="flex gap-3">
      {opts.map((opt) => {
        const active = value === opt.value
        return (
          <button
            key={String(opt.value)}
            type="button"
            onClick={() => onChange(opt.value)}
            className={`
              flex-1 rounded-xl border px-3 py-3 text-left text-sm
              transition-all duration-150
              focus:outline-none focus:ring-2 focus:ring-ring
              ${active
                ? 'border-primary bg-primary/10 text-primary'
                : 'border-border text-muted hover:text-fg hover:bg-surface-2'}
            `}
          >
            <div className="font-medium">{opt.label}</div>
            {opt.desc && <div className="text-[11px] opacity-70 mt-0.5">{opt.desc}</div>}
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
      <label
        className="
          flex items-center justify-center gap-2 h-9 px-3 rounded-lg
          border border-dashed border-border text-xs text-muted cursor-pointer
          hover:border-primary hover:text-primary transition-colors
        "
      >
        <input type="file" accept=".json,application/json" onChange={handleFile} className="sr-only" />
        Upload .json key file
      </label>
      <p className="text-[11px] text-muted text-center">— or paste below —</p>
      <textarea
        id={`f-${field.key}`}
        placeholder={'{\n  "type": "service_account",\n  ...\n}'}
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        rows={6}
        className={`${inputCls} font-mono resize-y`}
      />
      <SecretNote />
    </div>
  )
}

function FieldControl({ field, value, onChange }) {
  const id = `f-${field.key}`
  const isSecret = (field.group ?? 'config') === 'secret'

  switch (field.type) {
    case 'segmented':
      return <Segmented field={field} value={value} onChange={onChange} />
    case 'sa_json':
      return <SaJson field={field} value={value} onChange={onChange} />
    case 'select':
      return (
        <select
          id={id}
          value={value ?? field.default ?? ''}
          onChange={(e) => onChange(e.target.value)}
          className={inputCls}
        >
          {normaliseOptions(field.options).map((o) => (
            <option key={String(o.value)} value={o.value}>{o.label}</option>
          ))}
        </select>
      )
    case 'textarea':
      return (
        <textarea
          id={id}
          placeholder={field.placeholder}
          value={value ?? ''}
          onChange={(e) => onChange(e.target.value)}
          rows={field.rows ?? 3}
          className={`${inputCls} font-mono resize-y`}
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
        <input
          id={id}
          type={htmlType}
          placeholder={field.placeholder ?? (field.default != null ? String(field.default) : '')}
          value={value ?? ''}
          onChange={(e) =>
            onChange(field.type === 'number' ? (e.target.value ? Number(e.target.value) : undefined) : e.target.value)
          }
          autoComplete={isSecret ? 'off' : undefined}
          className={inputCls}
        />
      )
    }
  }
}

const WIDTH_CLS = { full: 'sm:col-span-2', half: 'sm:col-span-1', third: 'sm:col-span-1' }

// ---------------------------------------------------------------------------
// DynamicForm — renders all fields for a connector type from its schema
// ---------------------------------------------------------------------------

export function DynamicForm({ type, config = {}, secret = {}, onChange }) {
  const info = getTypeInfo(type)
  const fields = (info.fields ?? []).filter((f) => !f.showIf || f.showIf(config))

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      {fields.map((field) => {
        const group = field.group ?? 'config'
        const value = group === 'secret' ? secret[field.key] : config[field.key]
        const isSecret = group === 'secret'
        return (
          <div key={field.key} className={WIDTH_CLS[field.width] ?? 'sm:col-span-2'}>
            {field.type !== 'segmented' && field.type !== 'sa_json' && (
              <Label htmlFor={`f-${field.key}`} optional={field.optional}>{field.label}</Label>
            )}
            {(field.type === 'segmented' || field.type === 'sa_json') && (
              <Label optional={field.optional}>{field.label}</Label>
            )}
            <FieldControl field={field} value={value} onChange={(v) => onChange(group, field.key, v)} />
            {field.help && <HelpNote>{field.help}</HelpNote>}
            {isSecret && field.type !== 'sa_json' && <SecretNote />}
          </div>
        )
      })}
    </div>
  )
}
