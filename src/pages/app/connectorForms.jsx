/**
 * connectorForms.jsx — type-specific form fields for connectors.
 *
 * Each exported form component receives:
 *   { config, secret, onChange }
 *     config  — non-secret fields (object), controlled
 *     secret  — secret fields (object), controlled
 *     onChange(type, key, value) — type: 'config' | 'secret'
 *
 * Connector types: postgres | bigquery | http_json | duckdb
 *
 * Icons and type metadata are also exported so the type-picker can use them.
 */

import { Database, Globe, Archive, Cloud } from 'lucide-react'

// ---------------------------------------------------------------------------
// Type metadata (icon, label, description)
// ---------------------------------------------------------------------------

export const CONNECTOR_TYPES = [
  {
    id: 'postgres',
    label: 'PostgreSQL',
    description: 'Connect to a Postgres-compatible database',
    Icon: Database,
    color: '#336791',
    gradient: 'from-blue-600 to-blue-800',
  },
  {
    id: 'bigquery',
    label: 'BigQuery',
    description: 'Google BigQuery data warehouse',
    Icon: Cloud,
    color: '#4285F4',
    gradient: 'from-indigo-500 to-blue-600',
  },
  {
    id: 'http_json',
    label: 'HTTP / JSON',
    description: 'Any REST API returning JSON',
    Icon: Globe,
    color: '#0f9e90',
    gradient: 'from-teal-500 to-emerald-600',
  },
  {
    id: 'duckdb',
    label: 'DuckDB',
    description: 'In-process analytical database',
    Icon: Archive,
    color: '#FFCC00',
    gradient: 'from-yellow-400 to-amber-500',
  },
]

export function getTypeInfo(typeId) {
  return CONNECTOR_TYPES.find(t => t.id === typeId) ?? CONNECTOR_TYPES[0]
}

// ---------------------------------------------------------------------------
// Shared field primitives
// ---------------------------------------------------------------------------

function Label({ htmlFor, children, optional }) {
  return (
    <label
      htmlFor={htmlFor}
      className="block text-xs font-medium text-fg mb-1"
    >
      {children}
      {optional && <span className="ml-1 text-muted font-normal">(optional)</span>}
    </label>
  )
}

function Input({ id, type = 'text', placeholder, value, onChange, autoComplete, required }) {
  return (
    <input
      id={id}
      type={type}
      placeholder={placeholder}
      value={value ?? ''}
      onChange={e => onChange(e.target.value)}
      autoComplete={autoComplete}
      required={required}
      className="
        w-full rounded-lg border border-border bg-surface
        px-3 py-2 text-sm text-fg placeholder:text-muted
        focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent
        transition-colors
      "
    />
  )
}

function Textarea({ id, placeholder, value, onChange, rows = 4, required }) {
  return (
    <textarea
      id={id}
      placeholder={placeholder}
      value={value ?? ''}
      onChange={e => onChange(e.target.value)}
      rows={rows}
      required={required}
      className="
        w-full rounded-lg border border-border bg-surface
        px-3 py-2 text-sm text-fg placeholder:text-muted font-mono
        focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent
        transition-colors resize-y
      "
    />
  )
}

function Select({ id, value, onChange, children }) {
  return (
    <select
      id={id}
      value={value ?? ''}
      onChange={e => onChange(e.target.value)}
      className="
        w-full rounded-lg border border-border bg-surface
        px-3 py-2 text-sm text-fg
        focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent
        transition-colors
      "
    >
      {children}
    </select>
  )
}

function Field({ label, htmlFor, optional, children }) {
  return (
    <div>
      <Label htmlFor={htmlFor} optional={optional}>{label}</Label>
      {children}
    </div>
  )
}

function SecretNote() {
  return (
    <p className="text-[11px] text-muted mt-1 flex items-center gap-1">
      <span className="inline-block w-1.5 h-1.5 rounded-full bg-accent shrink-0" />
      Encrypted at rest with AES-256-GCM — never shown after save
    </p>
  )
}

// ---------------------------------------------------------------------------
// PostgreSQL form
// ---------------------------------------------------------------------------

export function PostgresForm({ config, secret, onChange }) {
  const c = (key, val) => onChange('config', key, val)
  const s = (key, val) => onChange('secret', key, val)

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <Field label="Host" htmlFor="pg-host">
          <Input
            id="pg-host"
            placeholder="localhost"
            value={config.host}
            onChange={v => c('host', v)}
            autoComplete="off"
            required
          />
        </Field>
        <Field label="Port" htmlFor="pg-port">
          <Input
            id="pg-port"
            type="number"
            placeholder="5432"
            value={config.port}
            onChange={v => c('port', v ? parseInt(v, 10) : undefined)}
          />
        </Field>
        <Field label="SSL mode" htmlFor="pg-sslmode">
          <Select
            id="pg-sslmode"
            value={config.sslmode ?? 'prefer'}
            onChange={v => c('sslmode', v)}
          >
            <option value="disable">disable</option>
            <option value="allow">allow</option>
            <option value="prefer">prefer</option>
            <option value="require">require</option>
            <option value="verify-ca">verify-ca</option>
            <option value="verify-full">verify-full</option>
          </Select>
        </Field>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Database" htmlFor="pg-database">
          <Input
            id="pg-database"
            placeholder="mydb"
            value={config.database}
            onChange={v => c('database', v)}
            required
          />
        </Field>
        <Field label="User" htmlFor="pg-user">
          <Input
            id="pg-user"
            placeholder="postgres"
            value={config.user}
            onChange={v => c('user', v)}
            autoComplete="username"
          />
        </Field>
      </div>

      <Field label="Password" htmlFor="pg-password">
        <Input
          id="pg-password"
          type="password"
          placeholder="••••••••"
          value={secret.password}
          onChange={v => s('password', v)}
          autoComplete="current-password"
        />
        <SecretNote />
      </Field>

      <Field label="Network mode" htmlFor="pg-network" optional>
        <Select
          id="pg-network"
          value={config.network_mode ?? ''}
          onChange={v => c('network_mode', v || undefined)}
        >
          <option value="">— none —</option>
          <option value="direct">direct</option>
          <option value="bridge">bridge</option>
        </Select>
        <p className="text-[11px] text-muted mt-1">
          Use "bridge" if Nubi's query runner is in a Docker network adjacent to your DB.
        </p>
      </Field>
    </div>
  )
}

// ---------------------------------------------------------------------------
// BigQuery form
// ---------------------------------------------------------------------------

export function BigQueryForm({ config, secret, onChange }) {
  const c = (key, val) => onChange('config', key, val)
  const s = (key, val) => onChange('secret', key, val)

  function handleFile(e) {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = ev => s('service_account_json', ev.target.result)
    reader.readAsText(file)
  }

  return (
    <div className="space-y-4">
      <Field label="GCP Project ID" htmlFor="bq-project">
        <Input
          id="bq-project"
          placeholder="my-gcp-project"
          value={config.project_id}
          onChange={v => c('project_id', v)}
          required
        />
      </Field>

      <Field label="Service account JSON" htmlFor="bq-sa">
        <div className="space-y-2">
          <label className="
            flex items-center justify-center gap-2
            h-9 px-3 rounded-lg border border-dashed border-border
            text-xs text-muted cursor-pointer
            hover:border-primary hover:text-primary transition-colors
          ">
            <input
              type="file"
              accept=".json,application/json"
              onChange={handleFile}
              className="sr-only"
            />
            Upload .json key file
          </label>
          <p className="text-[11px] text-muted text-center">— or paste below —</p>
          <Textarea
            id="bq-sa"
            placeholder={'{\n  "type": "service_account",\n  "project_id": "...",\n  ...\n}'}
            value={secret.service_account_json}
            onChange={v => s('service_account_json', v)}
            rows={6}
          />
          <SecretNote />
        </div>
      </Field>
    </div>
  )
}

// ---------------------------------------------------------------------------
// HTTP JSON form
// ---------------------------------------------------------------------------

export function HttpJsonForm({ config, secret, onChange }) {
  const c = (key, val) => onChange('config', key, val)
  const s = (key, val) => onChange('secret', key, val)

  return (
    <div className="space-y-4">
      <Field label="Base URL" htmlFor="http-url">
        <Input
          id="http-url"
          type="url"
          placeholder="https://api.example.com/v1"
          value={config.base_url}
          onChange={v => c('base_url', v)}
          required
        />
      </Field>

      <Field label="Bearer token" htmlFor="http-token" optional>
        <Input
          id="http-token"
          type="password"
          placeholder="eyJhbGciOiJIUzI1NiIsInR5cCI6Ikp..."
          value={secret.token}
          onChange={v => s('token', v || undefined)}
          autoComplete="off"
        />
        <SecretNote />
      </Field>

      <Field label="Extra headers (JSON)" htmlFor="http-headers" optional>
        <Textarea
          id="http-headers"
          placeholder={'{\n  "X-Api-Key": "..."\n}'}
          value={config.headers}
          onChange={v => c('headers', v || undefined)}
          rows={3}
        />
        <p className="text-[11px] text-muted mt-1">
          Non-secret headers only. Put auth tokens in the Bearer token field above.
        </p>
      </Field>
    </div>
  )
}

// ---------------------------------------------------------------------------
// DuckDB form
// ---------------------------------------------------------------------------

export function DuckDbForm({ config, onChange }) {
  const c = (key, val) => onChange('config', key, val)
  const inMemory = config.in_memory === true || config.in_memory === 'true'

  return (
    <div className="space-y-4">
      <Field label="Storage" htmlFor="duck-mode">
        <div className="flex gap-3">
          {[
            { value: true, label: 'In-memory', desc: 'Data lives only for the session' },
            { value: false, label: 'File path', desc: 'Persisted to a local .db file' },
          ].map(opt => (
            <button
              key={String(opt.value)}
              type="button"
              onClick={() => c('in_memory', opt.value)}
              className={`
                flex-1 rounded-xl border px-3 py-3 text-left text-sm
                transition-all duration-150
                focus:outline-none focus:ring-2 focus:ring-ring
                ${inMemory === opt.value
                  ? 'border-primary bg-primary/10 text-primary'
                  : 'border-border text-muted hover:border-border hover:text-fg hover:bg-surface-2'
                }
              `}
            >
              <div className="font-medium">{opt.label}</div>
              <div className="text-[11px] opacity-70 mt-0.5">{opt.desc}</div>
            </button>
          ))}
        </div>
      </Field>

      {!inMemory && (
        <Field label="Database file path" htmlFor="duck-path">
          <Input
            id="duck-path"
            placeholder="/data/analytics.db"
            value={config.path}
            onChange={v => c('path', v)}
            required
          />
          <p className="text-[11px] text-muted mt-1">
            Absolute path on the Nubi server where the .db file lives.
          </p>
        </Field>
      )}
    </div>
  )
}
