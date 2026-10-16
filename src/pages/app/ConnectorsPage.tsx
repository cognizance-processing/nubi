/**
 * ConnectorsPage — list + manage data-source connectors.
 *
 * Layout (Swiss, ruled list):
 *   PageShell header (title, description, Add connector)
 *   Toolbar: search + sort, removable engine filter chip
 *   Ruled rows: logo tile, name + summary, engine / dialect / network tags, status, actions
 *   Inline test result under the row it belongs to (2px rule: ok / failed + layers)
 *   Right drawer: type picker -> type-specific form (Add / Edit)
 *   Delete confirm dialog
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
 */

import { useState, useEffect, useRef, useMemo } from 'react'
import { useAsyncLoad } from '../../hooks/useAsyncLoad.js'
import { Link } from 'react-router-dom'
import {
  Plus,
  Pencil,
  Trash2,
  Zap,
  X,
  Search,
  Table2,
  Lock,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react'
import * as api from '../../lib/api.js'
import Button from '../../components/ui/Button.jsx'
import Tag from '../../components/ui/Tag.jsx'
import Input from '../../components/ui/Input.jsx'
import EmptyState from '../../components/ui/EmptyState.jsx'
import ConnectionStatusBadge from '../../components/ui/ConnectionStatusBadge.jsx'
import PageShell, { Toolbar, SearchBar, SortMenu } from '../../components/app/PageShell.jsx'
import { useProject } from '../../contexts/ProjectContext.jsx'
import { useCanWrite } from '../../contexts/OrgContext.jsx'
import {
  getTypeInfo,
  getConnectorsByCategory,
  defaultsFor,
  dialectFor,
} from '../../data/connectors.js'
import { DynamicForm, missingRequired } from './connectorForms.jsx'
import { toast } from '../../components/ui/Toast.jsx'
import ConnectorLogo from '../../components/app/ConnectorLogo.jsx'
import DialectBadge from '../../components/app/DialectBadge.jsx'
import { EmptyConnectors, EmptySearch } from '../../components/illustrations'
import { LoadError, ListSkeleton, FilterBar } from './appShared.jsx'
import '../../components/app/appPatterns.css'

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

const listConnectors    = ()       => api.get('/connectors')
const createConnector   = (body)   => api.post('/connectors', body)
const updateConnector   = (id, b)  => api.put(`/connectors/${id}`, b)
const deleteConnector   = (id)     => api.del(`/connectors/${id}`)
const testConnector     = (id)     => api.post(`/connectors/${id}/test`)

const SORTS = [
  { value: 'name', label: 'Name' },
  { value: 'type', label: 'Engine' },
  { value: 'status', label: 'Status' },
]

// Worst first, so "needs attention" rises to the top when sorting by status.
const STATUS_RANK = { error: 0, offline: 1, unknown: 2, degraded: 2, online: 3, connected: 3 }

// ---------------------------------------------------------------------------
// Inline test result: a 2px rule (ok / failed) under the row it belongs to
// ---------------------------------------------------------------------------

function LayerStep({ name, pass }) {
  return (
    <span className={pass ? 'pass' : 'fail'}>
      {name} <b>{pass ? 'pass' : 'fail'}</b>
    </span>
  )
}

function TestResult({ result, onDismiss }) {
  if (!result) return null
  const ok = result.ok === true
  // The round-trip time is the proof that a real socket was opened; a
  // structural check has no elapsed time worth showing.
  const took = ok && Number.isFinite(result.elapsed_ms) ? ` in ${result.elapsed_ms} ms` : ''
  const layers = result.layers
  return (
    <div className={`ap-result ${ok ? 'is-ok' : 'is-bad'}`} role="status">
      <div className="ap-result-head">
        <span className={`nubi-dot ${ok ? 'nubi-dot-success' : 'nubi-dot-danger'}`} aria-hidden="true" />
        {ok ? `Connection verified${took}` : 'Connection failed'}
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss test result"
          className="ml-auto text-subtle hover:text-fg"
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>
      {/* A driver's failure text is a whole sentence, not a status word. */}
      {(!ok || result.checked) && <div className="ap-result-msg">{result.checked}</div>}
      {layers && (
        <div className="ap-layers" aria-label="Test layers">
          <LayerStep name="config" pass={!!layers.config} />
          <LayerStep name="secret" pass={!!layers.secret} />
          {/* Only present when the probe ran. It is the layer that matters most:
              config + secret can both pass on a connector that cannot reach its
              database at all. */}
          {layers.connection !== undefined && <LayerStep name="connection" pass={!!layers.connection} />}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Connector row
// ---------------------------------------------------------------------------

function ConnectorRow({ connector, testResult, testingId, onEdit, onDelete, onTest, onDismissTest, onFilterType, canWrite }) {
  const cfg = connector.config ?? {}
  const info = getTypeInfo(cfg.connector_type)
  const networkMode = cfg.network_mode
  const isTesting = testingId === connector.id
  const myResult = testResult?.[connector.id]
  // System connectors (e.g. the built-in demo dataset) are not editable:
  // they have no configurable fields. They can still be removed and re-added.
  const isSystem = info.system === true
  const summary = info.summary?.(cfg)

  return (
    <li className="list-none">
      <div className="ap-row">
        <ConnectorLogo info={info} size={24} />
        <div className="ap-row-main">
          <div className="ap-row-title">
            <span className="ap-trunc">{connector.name}</span>
            {connector.status && (
              <ConnectionStatusBadge state={connector.status.state} detail={connector.status.detail} />
            )}
          </div>
          <div className="ap-tags mt-1">
            <Tag
              as="button"
              type="button"
              caps={false}
              title={`Show only ${info.label} connectors`}
              onClick={() => onFilterType(cfg.connector_type)}
            >
              {info.label}
            </Tag>
            {dialectFor(cfg.connector_type)?.toLowerCase() !== info.label.toLowerCase() && (
              <DialectBadge dialect={dialectFor(cfg.connector_type)} />
            )}
            {isSystem && (
              <Tag caps={false}><Lock size={10} aria-hidden="true" /> Built-in</Tag>
            )}
            {networkMode === 'bridge' ? (
              <Tag as={Link} to="/settings/bridges" caps={false} title="Manage bridges">{networkMode}</Tag>
            ) : networkMode ? (
              <Tag caps={false}>{networkMode}</Tag>
            ) : null}
            {/* Config summary (BYO connectors): host:port / database / user */}
            {summary && <span className="ap-meta truncate max-w-full">{summary}</span>}
          </div>
        </div>

        <div className="ap-actions">
          <Button asChild variant="ghost" size="sm">
            <Link to={`/data?connector=${connector.id}`} title="View data">
              <Table2 size={14} aria-hidden="true" /> <span className="hidden sm:inline">View data</span>
              <span className="sm:hidden">Data</span>
            </Link>
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onTest(connector.id)}
            loading={isTesting}
            title="Test connection"
          >
            {!isTesting && <Zap size={14} aria-hidden="true" />}
            {isTesting ? 'Testing' : 'Test'}
          </Button>
          {canWrite && !isSystem && (
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => onEdit(connector)}
              aria-label={`Edit ${connector.name}`}
              title="Edit connector"
            >
              <Pencil size={14} aria-hidden="true" />
            </Button>
          )}
          {canWrite && (
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => onDelete(connector)}
              aria-label={`${isSystem ? 'Remove' : 'Delete'} ${connector.name}`}
              title={isSystem ? 'Remove connector' : 'Delete connector'}
            >
              <Trash2 size={14} aria-hidden="true" />
            </Button>
          )}
        </div>
      </div>
      {myResult && (
        <div className="py-2 border-b border-border">
          <TestResult result={myResult} onDismiss={() => onDismissTest(connector.id)} />
        </div>
      )}
    </li>
  )
}

// ---------------------------------------------------------------------------
// Type picker step
// ---------------------------------------------------------------------------

function TypePicker({ onSelect }) {
  const [query, setQuery] = useState('')

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase()
    const all = getConnectorsByCategory()
    if (!q) return all
    return all
      .map((cat) => ({
        ...cat,
        connectors: cat.connectors.filter(
          (info) =>
            info.label.toLowerCase().includes(q) ||
            info.description.toLowerCase().includes(q),
        ),
      }))
      .filter((cat) => cat.connectors.length > 0)
  }, [query])

  return (
    <div>
      <div className="mb-5">
        <Input
          type="search"
          icon={<Search size={16} />}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search 20+ connector types"
          aria-label="Search connector types"
          autoFocus
        />
      </div>

      {groups.length === 0 && (
        <EmptyState
          compact
          kind="none-match"
          art={<EmptySearch />}
          title={`No connector matches “${query}”`}
          description="Search by engine name, like Postgres or Snowflake, or clear the filter to see every type."
          action={<Button variant="secondary" size="sm" onClick={() => setQuery('')}>Clear search</Button>}
        />
      )}

      {groups.map((cat) => (
        <section key={cat.id} aria-label={cat.label}>
          <h3 className="ap-group-label">{cat.label}</h3>
          <div className="ap-tiles">
            {cat.connectors.map((info) => (
              <button key={info.id} type="button" onClick={() => onSelect(info.id)} className="ap-tile">
                <ConnectorLogo info={info} size={24} />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5 flex-wrap">
                    <span className="ap-tile-name">{info.label}</span>
                    <DialectBadge dialect={dialectFor(info.id)} />
                  </span>
                  <span className="ap-tile-desc block">{info.description}</span>
                </span>
              </button>
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Connector form (step 2: fill details)
// ---------------------------------------------------------------------------

function ConnectorForm({ type, initialConfig, initialName, onBack, onSubmit, isEditing, loading, error }) {
  const [name, setName] = useState(initialName ?? '')
  // Seed new connectors with their schema defaults (ports, ssl mode, etc.) so
  // those values are actually submitted even if the user never touches them.
  const [config, setConfig] = useState(() => ({ ...defaultsFor(type), ...(initialConfig ?? {}) }))
  const [secret, setSecret] = useState({})
  const [touched, setTouched] = useState<Record<string, boolean>>({})
  const info = getTypeInfo(type)

  function handleFieldChange(fieldType, key, value) {
    if (fieldType === 'config') {
      setConfig(prev => ({ ...prev, [key]: value }))
    } else {
      setSecret(prev => ({ ...prev, [key]: value }))
    }
  }

  // Inline validation: a required field shows its error once it was left (or on submit).
  const missing = missingRequired(type, config, secret, isEditing)
  const errors = Object.fromEntries(missing.filter(k => touched[k]).map(k => [k, 'Required']))
  const nameError = touched.__name && !name.trim() ? 'Give this connector a name' : undefined

  function handleSubmit(e) {
    e.preventDefault()
    if (!name.trim() || missing.length) {
      setTouched({ __name: true, ...Object.fromEntries(missing.map(k => [k, true])) })
      // Move focus to the first thing that needs attention.
      requestAnimationFrame(() => {
        const first = !name.trim() ? 'conn-name' : `f-${missing[0]}`
        document.getElementById(first)?.focus()
      })
      return
    }

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
    <form onSubmit={handleSubmit} className="ap-form" noValidate>
      {/* Type header */}
      <div className="ap-form-id">
        <ConnectorLogo info={info} size={24} />
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <div className="text-sm font-semibold text-fg">{info.label}</div>
            <DialectBadge dialect={dialectFor(info.id)} />
          </div>
          <div className="text-xs text-muted">{info.description}</div>
        </div>
        {!isEditing && (
          <button type="button" onClick={onBack} className="ap-link-btn ml-auto shrink-0">
            Change type
          </button>
        )}
      </div>

      {/* Name */}
      <Input
        id="conn-name"
        type="text"
        label="Connector name"
        required
        placeholder={`My ${info.label} connector`}
        value={name}
        onChange={e => setName(e.target.value)}
        onBlur={() => setTouched(t => ({ ...t, __name: true }))}
        error={nameError}
        hint="Shown in queries and pickers. You can rename it later."
      />

      {/* Type-specific fields (schema-driven from the connector catalog) */}
      <DynamicForm
        type={type}
        {...formProps}
        errors={errors}
        onFieldBlur={(k) => setTouched(t => ({ ...t, [k]: true }))}
      />

      {/* Security note */}
      <div className="ap-note">
        <ShieldCheck size={14} className="shrink-0 mt-0.5 text-fg" aria-hidden="true" />
        <span>
          Credentials are <b className="text-fg font-semibold">encrypted at rest</b> with AES-256-GCM and
          are never returned by the API after save.
        </span>
      </div>

      {/* Error */}
      {error && (
        <div className="ap-note is-error" role="alert">
          <span><b>Couldn&rsquo;t save.</b> {error}</span>
        </div>
      )}

      {/* Submit */}
      <Button type="submit" loading={loading} className="w-full">
        {isEditing ? 'Save changes' : 'Add connector'}
      </Button>
    </form>
  )
}

// ---------------------------------------------------------------------------
// Right drawer
// ---------------------------------------------------------------------------

function Drawer({ open, onClose, title, step, children }) {
  const panelRef = useRef<HTMLDivElement>(null)
  const returnFocus = useRef<HTMLElement | null>(null)

  // Esc closes; focus moves in on open and returns to the trigger on close.
  useEffect(() => {
    if (!open) return
    returnFocus.current = document.activeElement as HTMLElement
    const t = setTimeout(() => {
      panelRef.current?.querySelector<HTMLElement>('input, select, textarea, button:not([data-drawer-close])')?.focus()
    }, 60)
    function onKeyDown(e) {
      if (e.key === 'Escape') onClose()
      // Keep Tab inside the drawer while it is open.
      if (e.key === 'Tab' && panelRef.current) {
        const f = panelRef.current.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        )
        if (!f.length) return
        const first = f[0], last = f[f.length - 1]
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      clearTimeout(t)
      window.removeEventListener('keydown', onKeyDown)
      returnFocus.current?.focus?.()
    }
  }, [open, onClose])

  return (
    <>
      {open && <div className="ap-backdrop" onClick={onClose} aria-hidden="true" />}
      <aside
        ref={panelRef}
        role="dialog"
        aria-modal={open}
        aria-label={title}
        aria-hidden={!open}
        data-open={open}
        className="ap-drawer"
      >
        <header className="ap-drawer-head">
          <div className="min-w-0 flex-1">
            <div className="ap-steps" aria-hidden="true">
              <span className={step === 'type' ? 'on' : ''}>01 Type</span>
              <span className="sep" />
              <span className={step === 'form' ? 'on' : ''}>02 Details</span>
            </div>
            <h2 className="ap-drawer-title mt-1 truncate">{title}</h2>
          </div>
          <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close panel" data-drawer-close>
            <X size={16} aria-hidden="true" />
          </Button>
        </header>
        <div className="ap-drawer-body">{open ? children : null}</div>
      </aside>
    </>
  )
}

// ---------------------------------------------------------------------------
// Delete confirm dialog (Swiss: 2px danger rule, plain consequences, no icon tile)
// ---------------------------------------------------------------------------

function DeleteDialog({ connector, loading, error, onCancel, onConfirm }) {
  const isSystem = getTypeInfo(connector?.config?.connector_type).system === true
  const cancelRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    cancelRef.current?.focus()
    function onKeyDown(e) {
      if (e.key === 'Escape' && !loading) onCancel()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [loading, onCancel])

  return (
    <div
      className="fixed inset-0 flex items-center justify-center p-4"
      style={{ zIndex: 'var(--z-modal)' as any, background: 'rgba(11,12,14,.5)' }}
      onClick={loading ? undefined : onCancel}
    >
      <div
        className="ap-confirm nubi-animate-scale-in"
        onClick={e => e.stopPropagation()}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="del-conn-title"
        aria-describedby="del-conn-desc"
      >
        <h3 id="del-conn-title">{isSystem ? 'Remove this connector?' : 'Delete this connector?'}</h3>
        <p id="del-conn-desc">
          {isSystem ? (
            <>
              <b>{connector?.name}</b> will be removed from this workspace. You can add it back
              from Add connector.
            </>
          ) : (
            <>
              <b>{connector?.name}</b> and its encrypted credentials will be deleted. Queries that
              use it will stop running. This cannot be undone.
            </>
          )}
        </p>
        {error && <div className="ap-note is-error mt-3" role="alert"><span>{error}</span></div>}
        <div className="ap-confirm-actions">
          <Button ref={cancelRef} variant="secondary" onClick={onCancel} disabled={loading}>Cancel</Button>
          <Button variant="danger" onClick={onConfirm} loading={loading}>
            {isSystem ? 'Remove connector' : 'Delete connector'}
          </Button>
        </div>
      </div>
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
  const canWrite = useCanWrite()

  // List state
  const { data: connectorsData, loading: listLoading, error: listError, reload: reloadConnectors } = useAsyncLoad(
    () => listConnectors().then(d => Array.isArray(d) ? d : d?.connectors ?? []),
    [projectId]
  )
  const connectors = connectorsData ?? []

  // List controls
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('name')
  const [typeFilter, setTypeFilter] = useState<string | null>(null)

  // Drawer state
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

  // ---------------------------------------------------------------------------
  // Derived list
  // ---------------------------------------------------------------------------

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase()
    const rows = connectors.filter(c => {
      const cfg = c.config ?? {}
      if (typeFilter && cfg.connector_type !== typeFilter) return false
      if (!q) return true
      const info = getTypeInfo(cfg.connector_type)
      return (
        c.name?.toLowerCase().includes(q) ||
        info.label.toLowerCase().includes(q) ||
        (info.summary?.(cfg) ?? '').toLowerCase().includes(q)
      )
    })
    const label = (c) => getTypeInfo(c.config?.connector_type).label
    return [...rows].sort((a, b) => {
      if (sort === 'type') return label(a).localeCompare(label(b)) || a.name.localeCompare(b.name)
      if (sort === 'status') {
        const ra = STATUS_RANK[a.status?.state] ?? 2
        const rb = STATUS_RANK[b.status?.state] ?? 2
        return ra - rb || a.name.localeCompare(b.name)
      }
      return a.name.localeCompare(b.name)
    })
  }, [connectors, search, sort, typeFilter])

  const chips = typeFilter
    ? [{ key: 'type', label: `Engine: ${getTypeInfo(typeFilter).label}`, onRemove: () => setTypeFilter(null) }]
    : []

  // ---------------------------------------------------------------------------
  // Drawer open helpers
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
    // Reset after the panel has slid out
    setTimeout(() => {
      setEditTarget(null)
      setSelectedType(null)
      setSlideStep('type')
      setFormError(null)
    }, 260)
  }

  function handleTypePick(typeId) {
    const info = getTypeInfo(typeId)
    // System connectors (e.g. the built-in demo dataset) have no config — adding
    // one is a single click; submit immediately instead of showing an empty form.
    if (info.system) {
      handleFormSubmit({ name: info.label, type: typeId, config: {}, secret: {} })
      return
    }
    // Demo-seeded connectors also have no user-facing config fields — submit
    // immediately with the seed flag so the backend provisions the parquet.
    if (info.apiSeed === 'demo' && info.fields.length === 0) {
      handleFormSubmit({
        name: info.label,
        type: info.apiType ?? typeId,
        config: {},
        secret: {},
        seed: info.apiSeed,
      })
      return
    }
    setSelectedType(typeId)
    setSlideStep('form')
  }

  // ---------------------------------------------------------------------------
  // Create / Update connector
  // ---------------------------------------------------------------------------

  async function handleFormSubmit({ name, type, config, secret, seed = undefined }: { name: string; type: string; config: any; secret: any; seed?: string }) {
    setFormLoading(true)
    setFormError(null)
    try {
      if (editTarget) {
        // PUT — only send changed fields; never re-send type
        const body = { name, config, secret: Object.keys(secret).length ? secret : undefined }
        await updateConnector(editTarget.id, body)
        reloadConnectors()
        toast.success('Connector updated')
      } else {
        // POST — full body. The picker submits the backend factory type
        // directly. For connectors with an `apiType` override (e.g. GCS →
        // 'duckdb_storage') we send the backend type, not the catalog id.
        const info = getTypeInfo(type)
        const backendType = info.apiType ?? type
        const body: Record<string, any> = { name, type: backendType, config, secret }
        if (seed) body.seed = seed
        await createConnector(body)
        reloadConnectors()
        toast.success('Connector added')
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
      reloadConnectors()
      setDeleteTarget(null)
      toast.success('Connector deleted')
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
        toast.success(
          Number.isFinite(result.elapsed_ms)
            ? `Connected. The database answered in ${result.elapsed_ms} ms`
            : 'Connection verified',
        )
      } else {
        toast.error(`Test failed: ${result.checked}`)
      }
      // Direct-mode connectors persist this result server-side (config.health) —
      // reload so the status badge in the header reflects it, not just this
      // transient result (which disappears on refresh; the badge doesn't).
      reloadConnectors()
    } catch (err) {
      setTestResults(prev => ({
        ...prev,
        [id]: { ok: false, checked: err.message ?? 'Test failed', layers: {} },
      }))
      toast.error(err.message ?? 'Test failed')
    } finally {
      setTestingId(null)
    }
  }

  // ---------------------------------------------------------------------------
  // Drawer title
  // ---------------------------------------------------------------------------

  const slideTitle = editTarget
    ? `Edit ${editTarget.name}`
    : slideStep === 'type'
    ? 'Add a connector'
    : selectedType
    ? `Connect ${getTypeInfo(selectedType).label}`
    : 'Add a connector'

  const showList = !listLoading && !listError && connectors.length > 0

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <PageShell
      eyebrow={listLoading || listError ? 'Data' : `Data · ${connectors.length} ${connectors.length === 1 ? 'connector' : 'connectors'}`}
      title="Connectors"
      description="The databases, warehouses and files your queries read from. Credentials are encrypted at rest and never returned by the API."
      actions={
        <>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={reloadConnectors}
            disabled={listLoading}
            aria-label="Refresh connectors"
            title="Refresh"
          >
            <RefreshCw size={14} className={listLoading ? 'animate-spin' : ''} aria-hidden="true" />
          </Button>
          {canWrite && (
            <Button onClick={openAdd}>
              <Plus size={14} aria-hidden="true" /> Add connector
            </Button>
          )}
        </>
      }
      toolbar={showList ? (
        <Toolbar>
          <SearchBar value={search} onChange={setSearch} placeholder="Search connectors" />
          <SortMenu value={sort} onChange={setSort} options={SORTS} />
        </Toolbar>
      ) : undefined}
    >
      {/* Loading: rows that match the real ones (hidden for the first 300ms) */}
      {listLoading && <ListSkeleton rows={3} />}

      {/* Error state */}
      {!listLoading && listError && (
        <LoadError title="Connectors didn’t load" error={listError} onRetry={reloadConnectors} />
      )}

      {/* Empty state */}
      {!listLoading && !listError && connectors.length === 0 && (
        <EmptyState
          art={<EmptyConnectors />}
          title="No connectors yet"
          description={canWrite
            ? 'Connect Postgres, MySQL, Snowflake, BigQuery or a folder of files. Queries read from a connector, and boards read from queries.'
            : 'Connectors are added by workspace admins. Ask one to connect your first data source.'}
          action={canWrite ? <Button onClick={openAdd}><Plus size={14} aria-hidden="true" /> Add connector</Button> : undefined}
          secondaryAction={<Button asChild variant="ghost"><Link to="/docs/connectors">Read the guide</Link></Button>}
        />
      )}

      {/* Connector list */}
      {showList && (
        <>
          <FilterBar
            count={visible.length}
            noun="connector"
            chips={chips}
            onClear={() => { setTypeFilter(null); setSearch('') }}
            trailing={
              <span className="inline-flex items-center gap-1.5 ap-count">
                <Lock size={11} aria-hidden="true" /> Encrypted at rest
              </span>
            }
          />
          {visible.length === 0 ? (
            <EmptyState
              kind="none-match"
              art={<EmptySearch />}
              title={search ? `No connector matches “${search}”` : 'No connectors with these filters'}
              description="Search matches the name, the engine and the host. Clear the filters to see all connectors."
              action={
                <Button variant="secondary" onClick={() => { setSearch(''); setTypeFilter(null) }}>
                  Clear filters
                </Button>
              }
            />
          ) : (
            <ul className="ap-list m-0 p-0" aria-label="Connectors">
              {visible.map(connector => (
                <ConnectorRow
                  key={connector.id}
                  connector={connector}
                  testResult={testResults}
                  testingId={testingId}
                  onEdit={openEdit}
                  onDelete={setDeleteTarget}
                  onTest={handleTest}
                  onDismissTest={(id) => setTestResults(prev => { const n = { ...prev }; delete n[id]; return n })}
                  onFilterType={setTypeFilter}
                  canWrite={canWrite}
                />
              ))}
            </ul>
          )}
        </>
      )}

      {/* Drawer */}
      <Drawer open={slideOpen} onClose={closeSlide} title={slideTitle} step={slideStep}>
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
      </Drawer>

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
    </PageShell>
  )
}
