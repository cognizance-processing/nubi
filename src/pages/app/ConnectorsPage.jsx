/**
 * ConnectorsPage — list + manage data-source connectors.
 *
 * Layout:
 *   Header (title + "Add connector" CTA)
 *   Connector cards list (or empty state)
 *   Slide-over panel: type picker → type-specific form (Add / Edit)
 *   Delete confirm dialog
 *   Test-result toast/inline panel
 *
 * API calls use src/lib/api.js helpers:
 *   GET    /connectors
 *   POST   /connectors          { name, type, config, secret }
 *   PUT    /connectors/{id}     { name?, config?, secret? }
 *   DELETE /connectors/{id}
 *   POST   /connectors/{id}/test
 *
 * Secrets (password / service_account_json / token) are NEVER displayed after save.
 * A visible security note in the form reinforces this.
 *
 * Only this file and connectorForms.jsx are owned by this wave.
 */

import { useEffect, useState, useCallback, useRef } from 'react'
import { Link } from 'react-router-dom'
import {
  Plus,
  Plug,
  Pencil,
  Trash2,
  Zap,
  CheckCircle,
  XCircle,
  ChevronRight,
  X,
  Loader2,
  ShieldCheck,
  AlertTriangle,
  RefreshCw,
  Table2,
} from 'lucide-react'
import * as api from '../../lib/api.js'
import { useProject } from '../../contexts/ProjectContext.jsx'
import {
  CONNECTOR_TYPES,
  getTypeInfo,
  PostgresForm,
  BigQueryForm,
  HttpJsonForm,
  DuckDbForm,
} from './connectorForms.jsx'

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

const listConnectors    = ()       => api.get('/connectors')
const createConnector   = (body)   => api.post('/connectors', body)
const updateConnector   = (id, b)  => api.put(`/connectors/${id}`, b)
const deleteConnector   = (id)     => api.del(`/connectors/${id}`)
const testConnector     = (id)     => api.post(`/connectors/${id}/test`)

// ---------------------------------------------------------------------------
// Type badge + icon
// ---------------------------------------------------------------------------

const TYPE_BADGE_COLORS = {
  postgres:  'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  bigquery:  'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300',
  http_json: 'bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300',
  duckdb:    'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
}

function TypeBadge({ type }) {
  const info = getTypeInfo(type)
  const color = TYPE_BADGE_COLORS[type] ?? 'bg-surface-2 text-muted'
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium ${color}`}>
      <info.Icon size={11} strokeWidth={2} />
      {info.label}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Test result pill
// ---------------------------------------------------------------------------

function TestResultPill({ result }) {
  if (!result) return null
  const ok = result.ok === true
  return (
    <span
      className={`
        inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-xs font-medium
        ${ok
          ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300'
          : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300'
        }
      `}
    >
      {ok
        ? <CheckCircle size={12} strokeWidth={2.2} />
        : <XCircle size={12} strokeWidth={2.2} />
      }
      {ok ? result.checked : result.checked}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Connector card
// ---------------------------------------------------------------------------

function ConnectorCard({ connector, testResult, testingId, onEdit, onDelete, onTest }) {
  const cfg = connector.config ?? {}
  const networkMode = cfg.network_mode
  const isTesting = testingId === connector.id
  const myResult = testResult?.[connector.id]

  return (
    <div className="
      bg-surface rounded-2xl border border-border p-5
      hover:shadow-md hover:border-border/80 transition-all duration-200
      flex flex-col sm:flex-row sm:items-start gap-4
    ">
      {/* Icon */}
      <div className="shrink-0">
        {(() => {
          const info = getTypeInfo(cfg.connector_type)
          return (
            <div
              className="w-11 h-11 rounded-xl flex items-center justify-center"
              style={{ background: `linear-gradient(135deg, ${info.color}55, ${info.color}99)` }}
            >
              <info.Icon size={20} className="text-white drop-shadow-sm" />
            </div>
          )
        })()}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex flex-wrap items-center gap-2 mb-1">
          <h3 className="font-semibold text-fg text-sm truncate">{connector.name}</h3>
          <TypeBadge type={cfg.connector_type} />
          {networkMode && (
            <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-medium bg-surface-2 text-muted">
              {networkMode}
            </span>
          )}
        </div>

        {/* Config summary */}
        <p className="text-xs text-muted truncate">
          {cfg.connector_type === 'postgres' && (
            <>
              {cfg.host && `${cfg.host}`}
              {cfg.port && `:${cfg.port}`}
              {cfg.database && ` / ${cfg.database}`}
              {cfg.user && ` · ${cfg.user}`}
              {cfg.sslmode && ` · ssl:${cfg.sslmode}`}
            </>
          )}
          {cfg.connector_type === 'bigquery' && cfg.project_id && `Project: ${cfg.project_id}`}
          {cfg.connector_type === 'http_json' && cfg.base_url && cfg.base_url}
          {cfg.connector_type === 'duckdb' && (
            cfg.in_memory ? 'in-memory' : (cfg.path || 'file')
          )}
        </p>

        {/* Test result */}
        {myResult && (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <TestResultPill result={myResult} />
            {myResult.layers && (
              <span className="text-[10px] text-muted">
                config:{myResult.layers.config ? '✓' : '✗'}
                {' '}secret:{myResult.layers.secret ? '✓' : '✗'}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 shrink-0 flex-wrap sm:flex-nowrap">
        <Link
          to={`/connectors/${connector.id}/data`}
          title="View data"
          className="
            inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg
            text-xs font-medium border border-border
            text-muted hover:text-fg hover:bg-surface-2
            transition-colors focus:outline-none focus:ring-2 focus:ring-ring
          "
        >
          <Table2 size={12} strokeWidth={2.2} />
          View data
        </Link>

        <button
          onClick={() => onTest(connector.id)}
          disabled={isTesting}
          title="Test connection"
          className="
            inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg
            text-xs font-medium border border-border
            text-muted hover:text-fg hover:bg-surface-2
            disabled:opacity-50 disabled:cursor-not-allowed
            transition-colors focus:outline-none focus:ring-2 focus:ring-ring
          "
        >
          {isTesting
            ? <Loader2 size={12} className="animate-spin" />
            : <Zap size={12} strokeWidth={2.2} />
          }
          {isTesting ? 'Testing…' : 'Test'}
        </button>

        <button
          onClick={() => onEdit(connector)}
          title="Edit connector"
          className="
            inline-flex items-center justify-center w-8 h-8 rounded-lg
            border border-border text-muted
            hover:text-fg hover:bg-surface-2
            transition-colors focus:outline-none focus:ring-2 focus:ring-ring
          "
        >
          <Pencil size={13} strokeWidth={2} />
        </button>

        <button
          onClick={() => onDelete(connector)}
          title="Delete connector"
          className="
            inline-flex items-center justify-center w-8 h-8 rounded-lg
            border border-border text-muted
            hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 hover:border-red-300
            transition-colors focus:outline-none focus:ring-2 focus:ring-ring
          "
        >
          <Trash2 size={13} strokeWidth={2} />
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyState({ onAdd }) {
  return (
    <div className="flex flex-col items-center justify-center py-20 px-6 text-center">
      <div className="
        flex items-center justify-center w-16 h-16 rounded-2xl mb-5
        bg-brand-gradient shadow-lg
      ">
        <Plug size={28} className="text-white" />
      </div>
      <h2 className="font-display font-semibold text-xl text-fg mb-2">
        No connectors yet
      </h2>
      <p className="text-muted text-sm max-w-xs leading-relaxed mb-6">
        Add your first data source to start querying. Postgres, BigQuery, HTTP APIs, and DuckDB are supported.
      </p>
      <button
        onClick={onAdd}
        className="
          inline-flex items-center gap-2 px-5 py-2.5
          bg-primary text-primary-fg
          rounded-xl text-sm font-semibold
          hover:opacity-90 transition-opacity
          focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2
          shadow-md
        "
      >
        <Plus size={16} strokeWidth={2.5} />
        Add your first connector
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Type picker step
// ---------------------------------------------------------------------------

function TypePicker({ onSelect }) {
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted mb-4">
        Choose the type of data source you want to connect:
      </p>
      {CONNECTOR_TYPES.map(({ id, label, description, Icon, color }) => (
        <button
          key={id}
          onClick={() => onSelect(id)}
          className="
            w-full flex items-center gap-4 p-4 rounded-xl border border-border
            text-left hover:border-primary/50 hover:bg-surface-2
            transition-all duration-150 group
            focus:outline-none focus:ring-2 focus:ring-ring
          "
        >
          <div
            className="w-10 h-10 rounded-lg flex items-center justify-center shrink-0"
            style={{ background: `${color}22`, border: `1px solid ${color}44` }}
          >
            <Icon size={20} style={{ color }} strokeWidth={1.8} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-semibold text-fg">{label}</div>
            <div className="text-xs text-muted mt-0.5">{description}</div>
          </div>
          <ChevronRight
            size={16}
            className="text-muted group-hover:text-primary transition-colors shrink-0"
          />
        </button>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Connector form (step 2: fill details)
// ---------------------------------------------------------------------------

const EMPTY_CONFIG = () => ({})
const EMPTY_SECRET = () => ({})

function ConnectorForm({ type, initialConfig, initialName, onBack, onSubmit, isEditing, loading, error }) {
  const [name, setName] = useState(initialName ?? '')
  const [config, setConfig] = useState(initialConfig ?? EMPTY_CONFIG())
  const [secret, setSecret] = useState(EMPTY_SECRET())
  const info = getTypeInfo(type)

  function handleFieldChange(fieldType, key, value) {
    if (fieldType === 'config') {
      setConfig(prev => ({ ...prev, [key]: value }))
    } else {
      setSecret(prev => ({ ...prev, [key]: value }))
    }
  }

  function handleSubmit(e) {
    e.preventDefault()

    // Strip undefined/empty values
    const cleanConfig = Object.fromEntries(
      Object.entries(config).filter(([, v]) => v !== undefined && v !== '')
    )
    const cleanSecret = Object.fromEntries(
      Object.entries(secret).filter(([, v]) => v !== undefined && v !== '')
    )

    onSubmit({ name: name.trim(), type, config: cleanConfig, secret: cleanSecret })
  }

  const formProps = { config, secret, onChange: handleFieldChange }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5">
      {/* Type header */}
      <div className="flex items-center gap-3 pb-4 border-b border-border">
        <div
          className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0"
          style={{ background: `${info.color}22`, border: `1px solid ${info.color}44` }}
        >
          <info.Icon size={18} style={{ color: info.color }} strokeWidth={1.8} />
        </div>
        <div>
          <div className="text-sm font-semibold text-fg">{info.label}</div>
          <div className="text-xs text-muted">{info.description}</div>
        </div>
        {!isEditing && (
          <button
            type="button"
            onClick={onBack}
            className="ml-auto text-xs text-muted hover:text-fg underline underline-offset-2 focus:outline-none"
          >
            Change type
          </button>
        )}
      </div>

      {/* Name */}
      <div>
        <label htmlFor="conn-name" className="block text-xs font-medium text-fg mb-1">
          Connector name
        </label>
        <input
          id="conn-name"
          type="text"
          required
          placeholder={`My ${info.label} connector`}
          value={name}
          onChange={e => setName(e.target.value)}
          className="
            w-full rounded-lg border border-border bg-surface
            px-3 py-2 text-sm text-fg placeholder:text-muted
            focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent
            transition-colors
          "
        />
      </div>

      {/* Type-specific fields */}
      {type === 'postgres'  && <PostgresForm  {...formProps} />}
      {type === 'bigquery'  && <BigQueryForm  {...formProps} />}
      {type === 'http_json' && <HttpJsonForm  {...formProps} />}
      {type === 'duckdb'    && <DuckDbForm    config={config} onChange={handleFieldChange} />}

      {/* Security note */}
      <div className="
        flex items-start gap-2.5 px-3 py-2.5 rounded-xl
        bg-surface-2 border border-border/50
        text-xs text-muted
      ">
        <ShieldCheck size={14} className="shrink-0 text-accent mt-0.5" strokeWidth={2} />
        <span>
          Credentials are <strong className="text-fg">encrypted at rest</strong> with AES-256-GCM and
          are never returned by the API after save.
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
        disabled={loading || !name.trim()}
        className="
          w-full flex items-center justify-center gap-2
          py-2.5 px-4 rounded-xl
          bg-primary text-primary-fg text-sm font-semibold
          hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed
          transition-opacity focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2
        "
      >
        {loading && <Loader2 size={15} className="animate-spin" />}
        {isEditing ? 'Save changes' : 'Add connector'}
      </button>
    </form>
  )
}

// ---------------------------------------------------------------------------
// Slide-over panel
// ---------------------------------------------------------------------------

function SlideOver({ open, onClose, title, children }) {
  // Trap focus + ESC
  const panelRef = useRef(null)

  useEffect(() => {
    if (!open) return
    function onKeyDown(e) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  return (
    <>
      {/* Backdrop */}
      <div
        className={`
          fixed inset-0 z-40 bg-black/40 backdrop-blur-sm
          transition-opacity duration-200
          ${open ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'}
        `}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Panel */}
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`
          fixed inset-y-0 right-0 z-50
          w-full sm:max-w-[480px]
          bg-surface border-l border-border shadow-2xl
          flex flex-col
          transition-transform duration-300 ease-in-out
          ${open ? 'translate-x-0' : 'translate-x-full'}
        `}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border shrink-0">
          <h2 className="font-display font-semibold text-lg text-fg">{title}</h2>
          <button
            onClick={onClose}
            aria-label="Close panel"
            className="
              flex items-center justify-center w-8 h-8 rounded-lg
              text-muted hover:text-fg hover:bg-surface-2
              transition-colors focus:outline-none focus:ring-2 focus:ring-ring
            "
          >
            <X size={16} strokeWidth={2} />
          </button>
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {children}
        </div>
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// Delete confirm dialog
// ---------------------------------------------------------------------------

function DeleteDialog({ connector, loading, error, onCancel, onConfirm }) {
  return (
    <>
      {/* Backdrop */}
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
              <h3 className="font-semibold text-fg text-sm">Delete connector?</h3>
              <p className="text-xs text-muted mt-1 leading-relaxed">
                <strong className="text-fg">{connector?.name}</strong> will be permanently deleted,
                including its encrypted credentials. This cannot be undone.
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
              className="
                px-4 py-2 rounded-xl text-sm font-medium text-muted
                border border-border hover:bg-surface-2 hover:text-fg
                transition-colors focus:outline-none focus:ring-2 focus:ring-ring
              "
            >
              Cancel
            </button>
            <button
              onClick={onConfirm}
              disabled={loading}
              className="
                inline-flex items-center gap-1.5 px-4 py-2 rounded-xl
                text-sm font-semibold text-white bg-red-600 hover:bg-red-700
                disabled:opacity-50 disabled:cursor-not-allowed
                transition-colors focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2
              "
            >
              {loading && <Loader2 size={13} className="animate-spin" />}
              Delete
            </button>
          </div>
        </div>
      </div>
    </>
  )
}

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
      className={`
        fixed bottom-5 left-1/2 -translate-x-1/2 z-[60]
        flex items-center gap-2.5 px-4 py-3 rounded-2xl shadow-xl
        text-sm font-medium max-w-sm w-[calc(100vw-2rem)]
        border transition-all duration-300
        ${isError
          ? 'bg-red-600 text-white border-red-700'
          : 'bg-green-600 text-white border-green-700'
        }
      `}
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
// ConnectorsPage — main component
// ---------------------------------------------------------------------------

export default function ConnectorsPage() {
  // Re-scope the list whenever the active project changes (api.js sends X-Project-Id).
  const { activeProject } = useProject()
  const projectId = activeProject?.id

  // List state
  const [connectors, setConnectors]   = useState([])
  const [listLoading, setListLoading] = useState(true)
  const [listError, setListError]     = useState(null)

  // Slide-over state
  const [slideOpen, setSlideOpen]     = useState(false)
  const [slideStep, setSlideStep]     = useState('type')   // 'type' | 'form'
  const [selectedType, setSelectedType] = useState(null)
  const [editTarget, setEditTarget]   = useState(null)     // connector being edited
  const [formLoading, setFormLoading] = useState(false)
  const [formError, setFormError]     = useState(null)

  // Delete state
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [deleteLoading, setDeleteLoading] = useState(false)
  const [deleteError, setDeleteError]   = useState(null)

  // Test state
  const [testingId, setTestingId]     = useState(null)
  const [testResults, setTestResults] = useState({}) // { [id]: result }

  // Toast
  const [toast, setToast] = useState(null) // { message, type }

  const showToast = useCallback((message, type = 'success') => {
    setToast({ message, type })
  }, [])

  const dismissToast = useCallback(() => setToast(null), [])

  // ---------------------------------------------------------------------------
  // Fetch connectors
  // ---------------------------------------------------------------------------

  const fetchConnectors = useCallback(async () => {
    setListLoading(true)
    setListError(null)
    try {
      const data = await listConnectors()
      setConnectors(Array.isArray(data) ? data : data?.connectors ?? [])
    } catch (err) {
      setListError(err.message ?? 'Failed to load connectors')
    } finally {
      setListLoading(false)
    }
  }, [projectId])

  useEffect(() => { fetchConnectors() }, [fetchConnectors])

  // ---------------------------------------------------------------------------
  // Slide-over open helpers
  // ---------------------------------------------------------------------------

  function openAdd() {
    setEditTarget(null)
    setSelectedType(null)
    setSlideStep('type')
    setFormError(null)
    setSlideOpen(true)
  }

  function openEdit(connector) {
    setEditTarget(connector)
    setSelectedType(connector.config?.connector_type)
    setSlideStep('form')
    setFormError(null)
    setSlideOpen(true)
  }

  function closeSlide() {
    setSlideOpen(false)
    // Reset after animation
    setTimeout(() => {
      setEditTarget(null)
      setSelectedType(null)
      setSlideStep('type')
      setFormError(null)
    }, 320)
  }

  function handleTypePick(typeId) {
    setSelectedType(typeId)
    setSlideStep('form')
  }

  // ---------------------------------------------------------------------------
  // Create / Update connector
  // ---------------------------------------------------------------------------

  async function handleFormSubmit({ name, type, config, secret }) {
    setFormLoading(true)
    setFormError(null)
    try {
      if (editTarget) {
        // PUT — only send changed fields; never re-send type
        const body = { name, config, secret: Object.keys(secret).length ? secret : undefined }
        const updated = await updateConnector(editTarget.id, body)
        setConnectors(prev => prev.map(c => c.id === editTarget.id ? updated : c))
        showToast('Connector updated')
      } else {
        // POST — full body
        const created = await createConnector({ name, type, config, secret })
        setConnectors(prev => [...prev, created])
        showToast('Connector added')
      }
      closeSlide()
    } catch (err) {
      setFormError(err.message ?? 'Something went wrong. Please try again.')
    } finally {
      setFormLoading(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Delete connector
  // ---------------------------------------------------------------------------

  async function handleDeleteConfirm() {
    if (!deleteTarget) return
    setDeleteLoading(true)
    setDeleteError(null)
    try {
      await deleteConnector(deleteTarget.id)
      setConnectors(prev => prev.filter(c => c.id !== deleteTarget.id))
      setDeleteTarget(null)
      showToast('Connector deleted')
    } catch (err) {
      setDeleteError(err.message ?? 'Delete failed. Please try again.')
    } finally {
      setDeleteLoading(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Test connector
  // ---------------------------------------------------------------------------

  async function handleTest(id) {
    setTestingId(id)
    try {
      const result = await testConnector(id)
      setTestResults(prev => ({ ...prev, [id]: result }))
      if (result.ok) {
        showToast('Connection verified successfully')
      } else {
        showToast(`Test failed: ${result.checked}`, 'error')
      }
    } catch (err) {
      setTestResults(prev => ({
        ...prev,
        [id]: { ok: false, checked: err.message ?? 'Test failed', layers: {} },
      }))
      showToast(err.message ?? 'Test failed', 'error')
    } finally {
      setTestingId(null)
    }
  }

  // ---------------------------------------------------------------------------
  // Slide-over title
  // ---------------------------------------------------------------------------

  const slideTitle = editTarget
    ? `Edit — ${editTarget.name}`
    : slideStep === 'type'
    ? 'Add connector'
    : selectedType
    ? `Add ${getTypeInfo(selectedType).label} connector`
    : 'Add connector'

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="flex flex-col min-h-full">
      {/* Page header */}
      <div className="
        flex flex-col sm:flex-row sm:items-center sm:justify-between
        gap-4 px-6 pt-6 pb-4 border-b border-border
        bg-surface
      ">
        <div>
          <h1 className="font-display font-semibold text-2xl text-fg">Connectors</h1>
          <p className="text-sm text-muted mt-0.5">
            Manage your data sources. Credentials are encrypted at rest.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={fetchConnectors}
            disabled={listLoading}
            title="Refresh"
            className="
              flex items-center justify-center w-9 h-9 rounded-xl
              border border-border text-muted
              hover:text-fg hover:bg-surface-2
              disabled:opacity-40
              transition-colors focus:outline-none focus:ring-2 focus:ring-ring
            "
          >
            <RefreshCw size={15} className={listLoading ? 'animate-spin' : ''} strokeWidth={2} />
          </button>

          <button
            onClick={openAdd}
            className="
              inline-flex items-center gap-2 px-4 py-2 rounded-xl
              bg-primary text-primary-fg text-sm font-semibold
              hover:opacity-90 transition-opacity
              focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2
              shadow-sm
            "
          >
            <Plus size={15} strokeWidth={2.5} />
            Add connector
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 px-6 py-6">
        {/* Loading skeleton */}
        {listLoading && (
          <div className="space-y-3">
            {[1, 2].map(i => (
              <div
                key={i}
                className="bg-surface rounded-2xl border border-border h-24 animate-pulse"
              />
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
              <p className="text-sm font-medium text-fg">Failed to load connectors</p>
              <p className="text-xs text-muted mt-1">{listError}</p>
            </div>
            <button
              onClick={fetchConnectors}
              className="
                inline-flex items-center gap-2 px-4 py-2 rounded-xl
                border border-border text-sm text-muted
                hover:text-fg hover:bg-surface-2 transition-colors
                focus:outline-none focus:ring-2 focus:ring-ring
              "
            >
              <RefreshCw size={14} strokeWidth={2} />
              Retry
            </button>
          </div>
        )}

        {/* Empty state */}
        {!listLoading && !listError && connectors.length === 0 && (
          <EmptyState onAdd={openAdd} />
        )}

        {/* Connector list */}
        {!listLoading && !listError && connectors.length > 0 && (
          <div className="space-y-3 max-w-3xl">
            {connectors.map(connector => (
              <ConnectorCard
                key={connector.id}
                connector={connector}
                testResult={testResults}
                testingId={testingId}
                onEdit={openEdit}
                onDelete={setDeleteTarget}
                onTest={handleTest}
              />
            ))}
          </div>
        )}
      </div>

      {/* Slide-over */}
      <SlideOver open={slideOpen} onClose={closeSlide} title={slideTitle}>
        {slideStep === 'type' && !editTarget && (
          <TypePicker onSelect={handleTypePick} />
        )}

        {slideStep === 'form' && selectedType && (
          <ConnectorForm
            type={selectedType}
            isEditing={!!editTarget}
            initialName={editTarget?.name ?? ''}
            initialConfig={editTarget ? { ...editTarget.config } : {}}
            onBack={() => setSlideStep('type')}
            onSubmit={handleFormSubmit}
            loading={formLoading}
            error={formError}
          />
        )}
      </SlideOver>

      {/* Delete confirm */}
      {deleteTarget && (
        <DeleteDialog
          connector={deleteTarget}
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
