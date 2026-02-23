import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import api, { getSchema } from '../../lib/api'
import { useHeader } from '../../contexts/HeaderContext'
import { useChat } from '../../contexts/ChatContext'
import {
    ArrowLeft, Database, Save, Loader2, CheckCircle2, XCircle,
    Columns, Table, ChevronRight, Wifi, Search, FolderOpen,
    Hash, Key, Shield, Upload
} from 'lucide-react'

const schemaCache = new Map()

function cacheKey(datastoreId, database, table) {
    return `${datastoreId}:${database || ''}:${table || ''}`
}

const DB_META = {
    postgres: { name: 'PostgreSQL', color: 'from-[#336791] to-[#264d6b]', badge: 'bg-[#336791]/15 text-[#5b9bd5]' },
    mysql: { name: 'MySQL', color: 'from-[#4479A1] to-[#336085]', badge: 'bg-[#4479A1]/15 text-[#6db3f0]' },
    bigquery: { name: 'BigQuery', color: 'from-[#4285F4] to-[#2b6de0]', badge: 'bg-[#4285F4]/15 text-[#6ea6ff]' },
    athena: { name: 'AWS Athena', color: 'from-[#FF9900] to-[#cc7a00]', badge: 'bg-[#FF9900]/15 text-[#ffb84d]' },
    mssql: { name: 'Azure SQL', color: 'from-[#0078D4] to-[#005a9e]', badge: 'bg-[#0078D4]/15 text-[#4da6ff]' },
    duckdb: { name: 'DuckDB', color: 'from-[#FFC107] to-[#e6a800]', badge: 'bg-[#FFC107]/15 text-[#ffd54f]' },
}

function Toast({ message, type, onClose }) {
    useEffect(() => {
        const t = setTimeout(onClose, 3500)
        return () => clearTimeout(t)
    }, [onClose])

    return (
        <div className={`fixed bottom-6 right-6 z-50 flex items-center gap-2.5 rounded-xl border px-4 py-3 text-sm font-medium shadow-2xl shadow-black/40 animate-fade-in ${
            type === 'success'
                ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400'
                : 'bg-red-500/10 border-red-500/20 text-red-400'
        }`}>
            {type === 'success' ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
            {message}
        </div>
    )
}

export default function DatastoreDetailPage() {
    const { datastoreId } = useParams()
    const navigate = useNavigate()
    const { setHeaderContent } = useHeader()
    const { setPageContext } = useChat()

    const cached = schemaCache.get(datastoreId)
    const [activeTab, setActiveTab] = useState(cached?.activeTab || 'details')
    const [datastore, setDatastore] = useState(null)
    const [loading, setLoading] = useState(true)
    const [saving, setSaving] = useState(false)
    const [testing, setTesting] = useState(false)
    const [testResult, setTestResult] = useState(null)
    const [toast, setToast] = useState(null)

    const [name, setName] = useState('')
    const [type, setType] = useState('postgres')
    const [connectionString, setConnectionString] = useState('')
    const [projectId, setProjectId] = useState('')
    const [keyfilePath, setKeyfilePath] = useState('')
    const [duckdbFilePath, setDuckdbFilePath] = useState('')
    const [athenaRegion, setAthenaRegion] = useState('')
    const [athenaDatabase, setAthenaDatabase] = useState('')
    const [athenaS3Output, setAthenaS3Output] = useState('')
    const [athenaAccessKey, setAthenaAccessKey] = useState('')
    const [athenaSecretKey, setAthenaSecretKey] = useState('')

    const [schemaData, setSchemaData] = useState(cached?.schemaData || null)
    const [schemaLoading, setSchemaLoading] = useState(false)
    const [breadcrumbs, setBreadcrumbs] = useState(cached?.breadcrumbs || [])
    const [schemaSearch, setSchemaSearch] = useState('')

    const showToast = useCallback((message, type = 'success') => {
        setToast({ message, type })
    }, [])

    useEffect(() => {
        schemaCache.set(datastoreId, { schemaData, breadcrumbs, activeTab })
    }, [datastoreId, schemaData, breadcrumbs, activeTab])

    useEffect(() => { loadDatastore() }, [datastoreId])

    useEffect(() => {
        setPageContext({ type: 'datastore', datastoreId })
        return () => setPageContext({ type: 'general' })
    }, [datastoreId, setPageContext])

    useEffect(() => {
        if (datastore) {
            setName(datastore.name)
            setType(datastore.type)
            setConnectionString(datastore.config?.connection_string || '')
            setProjectId(datastore.config?.project_id || '')
            setKeyfilePath(datastore.config?.keyfile_path || '')
            setDuckdbFilePath(datastore.config?.file_path || '')
            setAthenaRegion(datastore.config?.region || '')
            setAthenaDatabase(datastore.config?.database || '')
            setAthenaS3Output(datastore.config?.s3_output_location || '')
            setAthenaAccessKey(datastore.config?.access_key_id || '')
            setAthenaSecretKey(datastore.config?.secret_access_key || '')
        }
    }, [datastore])

    const meta = DB_META[datastore?.type] || DB_META.postgres

    useEffect(() => {
        setHeaderContent(
            <div className="flex items-center gap-3 flex-1 px-2">
                <button
                    className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.05] transition-all font-medium text-xs"
                    onClick={() => navigate('/datastores')}
                >
                    <ArrowLeft size={14} />
                    <span className="hidden sm:inline">Datastores</span>
                </button>

                <div className="h-4 w-px bg-white/[0.07]" />

                <div className="flex items-center gap-2.5 flex-1 min-w-0">
                    <div className={`w-7 h-7 rounded-lg bg-gradient-to-br ${meta.color} text-white flex items-center justify-center shrink-0`}>
                        <Database size={13} />
                    </div>
                    <div className="min-w-0">
                        <h2 className="text-[13px] font-semibold text-white truncate leading-tight">
                            {datastore?.name || 'Loading...'}
                        </h2>
                        <span className={`text-[9px] font-semibold uppercase tracking-wider ${meta.badge} px-1 py-px rounded`}>
                            {DB_META[datastore?.type]?.name || ''}
                        </span>
                    </div>
                </div>

                <div className="flex bg-slate-800/80 p-0.5 rounded-lg border border-white/[0.06]">
                    {['details', 'schema'].map(tab => (
                        <button
                            key={tab}
                            className={`px-3.5 py-1.5 rounded-md text-xs font-medium transition-all duration-150 ${
                                activeTab === tab
                                    ? 'bg-slate-900 text-indigo-400 shadow-sm'
                                    : 'text-slate-400 hover:text-white'
                            }`}
                            onClick={() => setActiveTab(tab)}
                        >
                            {tab.charAt(0).toUpperCase() + tab.slice(1)}
                        </button>
                    ))}
                </div>

                {activeTab === 'details' && (
                    <button className="btn btn-primary" onClick={saveDatastore} disabled={saving}>
                        {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
                        Save
                    </button>
                )}
            </div>
        )
        return () => setHeaderContent(null)
    }, [activeTab, datastore, saving, meta])

    async function loadDatastore() {
        setLoading(true)
        try {
            const data = await api.datastores.get(datastoreId)
            setDatastore(data)
        } catch (error) {
            console.error('Error loading datastore:', error)
            navigate('/datastores')
        }
        setLoading(false)
    }

    async function saveDatastore() {
        setSaving(true)
        try {
            let config = {}
            if (type === 'postgres' || type === 'mysql' || type === 'mssql') {
                config = { connection_string: connectionString }
            } else if (type === 'bigquery') {
                config = { project_id: projectId, keyfile_path: keyfilePath }
            } else if (type === 'athena') {
                config = { region: athenaRegion, database: athenaDatabase, s3_output_location: athenaS3Output, access_key_id: athenaAccessKey, secret_access_key: athenaSecretKey }
            } else if (type === 'duckdb') {
                config = { file_path: duckdbFilePath }
            }

            await api.datastores.update(datastoreId, { name, type, config })
            setDatastore({ ...datastore, name, type, config })
            showToast('Datastore saved successfully')
        } catch (err) {
            showToast('Failed to save: ' + err.message, 'error')
        } finally {
            setSaving(false)
        }
    }

    async function testConnection() {
        setTesting(true)
        setTestResult(null)
        try {
            const backendUrl = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000'
            const res = await fetch(`${backendUrl}/test-datastore`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ datastore_id: datastoreId })
            })
            const result = await res.json()
            const ok = result.status === 'success'
            setTestResult(ok)
            showToast(ok ? 'Connection verified' : 'Connection failed', ok ? 'success' : 'error')
        } catch (err) {
            setTestResult(false)
            showToast('Connection test failed', 'error')
        } finally {
            setTesting(false)
        }
    }

    async function loadSchema(database = null, table = null) {
        setSchemaSearch('')
        const key = cacheKey(datastoreId, database, table)
        const hit = schemaCache.get(key)
        if (hit?.response) {
            setSchemaData(hit.response)
            const crumbs = [{ label: datastore.name, database: null, table: null }]
            if (database) crumbs.push({ label: database, database, table: null })
            if (table) crumbs.push({ label: table, database, table })
            setBreadcrumbs(crumbs)
            return
        }

        setSchemaLoading(true)
        const { data, error } = await getSchema({ datastore_id: datastoreId, database, table })

        if (error) {
            showToast('Failed to load schema: ' + error.message, 'error')
        } else {
            schemaCache.set(key, { response: data })
            setSchemaData(data)
            const crumbs = [{ label: datastore.name, database: null, table: null }]
            if (database) crumbs.push({ label: database, database, table: null })
            if (table) crumbs.push({ label: table, database, table })
            setBreadcrumbs(crumbs)
        }
        setSchemaLoading(false)
    }

    useEffect(() => {
        if (activeTab === 'schema' && datastore && !schemaData) loadSchema()
    }, [activeTab])

    if (loading) {
        return (
            <div className="flex flex-col items-center justify-center min-h-[60vh] gap-3">
                <div className="spinner" />
                <p className="text-slate-500 text-sm">Loading datastore...</p>
            </div>
        )
    }

    return (
        <div className="flex flex-col h-full bg-slate-950">
            {/* Details Tab */}
            {activeTab === 'details' && (
                <div className="flex-1 overflow-y-auto p-6 lg:p-8">
                    <div className="max-w-2xl mx-auto space-y-6">
                        {/* Connection Details */}
                        <section className="rounded-xl border border-white/[0.07] bg-white/[0.015] overflow-hidden">
                            <div className="px-6 py-4 border-b border-white/[0.06] flex items-center gap-3">
                                <div className={`w-8 h-8 rounded-lg bg-gradient-to-br ${meta.color} text-white flex items-center justify-center`}>
                                    <Database size={14} />
                                </div>
                                <div>
                                    <h3 className="text-sm font-semibold text-white">Connection Details</h3>
                                    <p className="text-[11px] text-slate-500">Configure how Nubi connects to this datastore</p>
                                </div>
                            </div>
                            <div className="p-6 space-y-5">
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                                    <div className="form-group">
                                        <label className="form-label">Display Name</label>
                                        <input
                                            type="text"
                                            value={name}
                                            onChange={(e) => setName(e.target.value)}
                                            className="form-input"
                                            placeholder="e.g. Production Database"
                                        />
                                    </div>
                                    <div className="form-group">
                                        <label className="form-label">Type</label>
                                        <select value={type} onChange={(e) => setType(e.target.value)} className="form-input">
                                            {Object.entries(DB_META).map(([k, v]) => (
                                                <option key={k} value={k}>{v.name}</option>
                                            ))}
                                        </select>
                                    </div>
                                </div>

                                {(type === 'postgres' || type === 'mysql' || type === 'mssql') && (
                                    <div className="form-group">
                                        <label className="form-label flex items-center gap-1.5">
                                            <Key size={12} className="text-slate-500" />
                                            Connection String
                                        </label>
                                        <input
                                            type="text"
                                            value={connectionString}
                                            onChange={(e) => setConnectionString(e.target.value)}
                                            className="form-input font-mono text-xs"
                                            placeholder={
                                                type === 'postgres' ? 'postgresql://user:pass@host:port/db' :
                                                type === 'mysql' ? 'mysql+pymysql://user:pass@host:port/db' :
                                                'mssql+pymssql://user:pass@host:port/db'
                                            }
                                        />
                                        <p className="text-[10px] text-slate-600 mt-1.5">
                                            {type === 'postgres' ? 'Standard PostgreSQL connection URI' :
                                             type === 'mysql' ? 'MySQL connection string using PyMySQL driver' :
                                             'SQL Server connection string using pymssql driver'}
                                        </p>
                                    </div>
                                )}

                                {type === 'bigquery' && (
                                    <div className="space-y-5">
                                        <div className="form-group">
                                            <label className="form-label">Project ID</label>
                                            <input type="text" value={projectId} onChange={(e) => setProjectId(e.target.value)} className="form-input" placeholder="my-gcp-project-123" />
                                        </div>
                                        <div className="form-group">
                                            <label className="form-label flex items-center gap-1.5">
                                                <Shield size={12} className="text-slate-500" />
                                                Service Account Keyfile
                                            </label>
                                            {keyfilePath ? (
                                                <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg bg-emerald-500/5 border border-emerald-500/15">
                                                    <CheckCircle2 size={14} className="text-emerald-400 shrink-0" />
                                                    <span className="text-xs text-emerald-400 font-medium">Keyfile configured</span>
                                                    <span className="text-[10px] text-slate-600 truncate ml-auto font-mono">{keyfilePath.split('/').pop()}</span>
                                                </div>
                                            ) : (
                                                <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg bg-amber-500/5 border border-amber-500/15">
                                                    <Upload size={14} className="text-amber-400 shrink-0" />
                                                    <span className="text-xs text-amber-400">No keyfile -- upload one from the datastores list</span>
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                )}

                                {type === 'athena' && (
                                    <div className="space-y-5">
                                        <div className="grid grid-cols-2 gap-4">
                                            <div className="form-group">
                                                <label className="form-label">AWS Region</label>
                                                <input type="text" value={athenaRegion} onChange={(e) => setAthenaRegion(e.target.value)} className="form-input" placeholder="us-east-1" />
                                            </div>
                                            <div className="form-group">
                                                <label className="form-label">Database</label>
                                                <input type="text" value={athenaDatabase} onChange={(e) => setAthenaDatabase(e.target.value)} className="form-input" placeholder="my_database" />
                                            </div>
                                        </div>
                                        <div className="form-group">
                                            <label className="form-label">S3 Output Location</label>
                                            <input type="text" value={athenaS3Output} onChange={(e) => setAthenaS3Output(e.target.value)} className="form-input font-mono text-xs" placeholder="s3://my-bucket/athena-results/" />
                                        </div>
                                        <div className="grid grid-cols-2 gap-4">
                                            <div className="form-group">
                                                <label className="form-label">Access Key ID</label>
                                                <input type="text" value={athenaAccessKey} onChange={(e) => setAthenaAccessKey(e.target.value)} className="form-input font-mono text-xs" placeholder="AKIA..." />
                                            </div>
                                            <div className="form-group">
                                                <label className="form-label">Secret Access Key</label>
                                                <input type="password" value={athenaSecretKey} onChange={(e) => setAthenaSecretKey(e.target.value)} className="form-input font-mono text-xs" placeholder="••••••••" />
                                            </div>
                                        </div>
                                    </div>
                                )}

                                {type === 'duckdb' && (
                                    <div className="form-group">
                                        <label className="form-label">File Path</label>
                                        {duckdbFilePath ? (
                                            <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg bg-emerald-500/5 border border-emerald-500/15">
                                                <CheckCircle2 size={14} className="text-emerald-400 shrink-0" />
                                                <span className="text-xs text-emerald-400 font-medium">File configured</span>
                                                <span className="text-[10px] text-slate-600 truncate ml-auto font-mono">{duckdbFilePath.split('/').pop()}</span>
                                            </div>
                                        ) : (
                                            <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg bg-amber-500/5 border border-amber-500/15">
                                                <Upload size={14} className="text-amber-400 shrink-0" />
                                                <span className="text-xs text-amber-400">No file -- upload one from the datastores list</span>
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        </section>

                        {/* Test Connection */}
                        <section className="rounded-xl border border-white/[0.07] bg-white/[0.015] overflow-hidden">
                            <div className="px-6 py-4 border-b border-white/[0.06] flex items-center gap-3">
                                <div className="w-8 h-8 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 flex items-center justify-center">
                                    <Wifi size={14} />
                                </div>
                                <div>
                                    <h3 className="text-sm font-semibold text-white">Test Connection</h3>
                                    <p className="text-[11px] text-slate-500">Verify that Nubi can reach your datastore</p>
                                </div>
                            </div>
                            <div className="p-6">
                                <div className="flex items-center gap-4">
                                    <button
                                        onClick={testConnection}
                                        disabled={testing}
                                        className={`btn ${
                                            testing ? 'btn-secondary' :
                                            testResult === true ? 'btn-secondary bg-emerald-500/10 text-emerald-400 border-emerald-500/20 hover:bg-emerald-500/15' :
                                            testResult === false ? 'btn-secondary bg-red-500/10 text-red-400 border-red-500/20 hover:bg-red-500/15' :
                                            'btn-primary'
                                        }`}
                                    >
                                        {testing ? (
                                            <><Loader2 size={15} className="animate-spin" /> Testing connection...</>
                                        ) : testResult === true ? (
                                            <><CheckCircle2 size={15} /> Connected successfully</>
                                        ) : testResult === false ? (
                                            <><XCircle size={15} /> Connection failed</>
                                        ) : (
                                            <><Wifi size={15} /> Test connection</>
                                        )}
                                    </button>

                                    {testResult !== null && (
                                        <button
                                            onClick={() => setTestResult(null)}
                                            className="text-xs text-slate-500 hover:text-slate-300 transition"
                                        >
                                            Reset
                                        </button>
                                    )}
                                </div>

                                {testResult === false && (
                                    <div className="mt-4 rounded-lg bg-red-500/5 border border-red-500/10 p-3">
                                        <p className="text-xs text-red-400/80 leading-relaxed">
                                            Could not connect. Double-check your credentials, ensure the host is reachable, and try again.
                                        </p>
                                    </div>
                                )}
                            </div>
                        </section>
                    </div>
                </div>
            )}

            {/* Schema Tab */}
            {activeTab === 'schema' && (
                <div className="flex-1 overflow-y-auto p-6 lg:p-8">
                    <div className="max-w-5xl mx-auto">
                        {/* Breadcrumbs + Search */}
                        <div className="flex items-center justify-between gap-4 mb-5">
                            <div className="flex items-center gap-1.5 text-sm min-w-0 flex-1">
                                {breadcrumbs.map((crumb, idx) => (
                                    <div key={idx} className="flex items-center gap-1.5 min-w-0">
                                        {idx > 0 && <ChevronRight size={12} className="text-slate-600 shrink-0" />}
                                        <button
                                            onClick={() => loadSchema(crumb.database, crumb.table)}
                                            className={`truncate transition ${
                                                idx === breadcrumbs.length - 1
                                                    ? 'text-white font-medium'
                                                    : 'text-slate-500 hover:text-indigo-400'
                                            }`}
                                        >
                                            {crumb.label}
                                        </button>
                                    </div>
                                ))}
                            </div>

                            {schemaData && (schemaData.type === 'tables' || schemaData.type === 'datasets' || schemaData.type === 'schemas') && (
                                <div className="relative shrink-0">
                                    <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-600" />
                                    <input
                                        type="text"
                                        value={schemaSearch}
                                        onChange={(e) => setSchemaSearch(e.target.value)}
                                        placeholder="Filter..."
                                        className="pl-8 pr-3 py-1.5 w-48 rounded-lg border border-white/[0.07] bg-white/[0.02] text-xs text-slate-200 placeholder:text-slate-600 focus:border-indigo-500/40 focus:outline-none transition"
                                    />
                                </div>
                            )}
                        </div>

                        {/* Schema Content */}
                        {schemaLoading ? (
                            <div className="flex flex-col items-center justify-center py-20 gap-3">
                                <div className="spinner" />
                                <p className="text-slate-500 text-sm">Loading schema...</p>
                            </div>
                        ) : schemaData ? (
                            <div>
                                {/* Datasets / Schemas */}
                                {(schemaData.type === 'datasets' || schemaData.type === 'schemas') && (() => {
                                    const items = (schemaData.datasets || schemaData.schemas) || []
                                    const filtered = items.filter(i => i.name.toLowerCase().includes(schemaSearch.toLowerCase()))
                                    return (
                                        <div>
                                            <div className="flex items-center gap-2 mb-4">
                                                <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                                                    {schemaData.type === 'datasets' ? 'Datasets' : 'Schemas'}
                                                </h3>
                                                <span className="text-[10px] text-slate-600 bg-white/[0.04] px-1.5 py-0.5 rounded-md font-medium">{filtered.length}</span>
                                            </div>
                                            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
                                                {filtered.map(item => (
                                                    <button
                                                        key={item.name}
                                                        onClick={() => loadSchema(item.name)}
                                                        className="group text-left rounded-xl border border-white/[0.07] bg-white/[0.015] p-4 hover:border-indigo-500/25 hover:bg-indigo-500/[0.03] transition-all"
                                                    >
                                                        <div className="flex items-center gap-3">
                                                            <div className="w-9 h-9 rounded-lg bg-indigo-500/10 border border-indigo-500/15 flex items-center justify-center shrink-0 group-hover:bg-indigo-500/15 transition">
                                                                <FolderOpen size={16} className="text-indigo-400" />
                                                            </div>
                                                            <div className="min-w-0 flex-1">
                                                                <p className="text-sm font-medium text-white truncate group-hover:text-indigo-300 transition">{item.name}</p>
                                                                {item.table_count !== undefined && (
                                                                    <p className="text-[11px] text-slate-500 mt-0.5">{item.table_count} table{item.table_count !== 1 ? 's' : ''}</p>
                                                                )}
                                                            </div>
                                                            <ChevronRight size={14} className="text-slate-700 group-hover:text-indigo-400 shrink-0 transition" />
                                                        </div>
                                                    </button>
                                                ))}
                                            </div>
                                            {filtered.length === 0 && (
                                                <p className="text-center text-sm text-slate-600 py-8">No matches found</p>
                                            )}
                                        </div>
                                    )
                                })()}

                                {/* Tables */}
                                {schemaData.type === 'tables' && (() => {
                                    const filtered = (schemaData.tables || []).filter(t => t.name.toLowerCase().includes(schemaSearch.toLowerCase()))
                                    return (
                                        <div>
                                            <div className="flex items-center gap-2 mb-4">
                                                <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Tables</h3>
                                                <span className="text-[10px] text-slate-600 bg-white/[0.04] px-1.5 py-0.5 rounded-md font-medium">{filtered.length}</span>
                                            </div>
                                            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
                                                {filtered.map(t => (
                                                    <button
                                                        key={t.name}
                                                        onClick={() => loadSchema(schemaData.dataset || schemaData.schema, t.name)}
                                                        className="group text-left rounded-xl border border-white/[0.07] bg-white/[0.015] p-4 hover:border-blue-500/25 hover:bg-blue-500/[0.03] transition-all"
                                                    >
                                                        <div className="flex items-center gap-3">
                                                            <div className="w-9 h-9 rounded-lg bg-blue-500/10 border border-blue-500/15 flex items-center justify-center shrink-0 group-hover:bg-blue-500/15 transition">
                                                                <Table size={16} className="text-blue-400" />
                                                            </div>
                                                            <div className="min-w-0 flex-1">
                                                                <p className="text-sm font-medium text-white truncate group-hover:text-blue-300 transition">{t.name}</p>
                                                                <div className="flex items-center gap-3 mt-0.5">
                                                                    {t.column_count !== undefined && (
                                                                        <span className="text-[11px] text-slate-500">{t.column_count} cols</span>
                                                                    )}
                                                                    {t.row_count !== undefined && (
                                                                        <span className="text-[11px] text-slate-500">{t.row_count.toLocaleString()} rows</span>
                                                                    )}
                                                                </div>
                                                            </div>
                                                            <ChevronRight size={14} className="text-slate-700 group-hover:text-blue-400 shrink-0 transition" />
                                                        </div>
                                                    </button>
                                                ))}
                                            </div>
                                            {filtered.length === 0 && (
                                                <p className="text-center text-sm text-slate-600 py-8">No tables match your filter</p>
                                            )}
                                        </div>
                                    )
                                })()}

                                {/* Column Schema */}
                                {schemaData.type === 'table_schema' && (() => {
                                    const columns = schemaData.schema || schemaData.columns || []
                                    return (
                                        <div className="rounded-xl border border-white/[0.07] overflow-hidden">
                                            <div className="px-5 py-3.5 border-b border-white/[0.06] bg-white/[0.015] flex items-center justify-between">
                                                <div className="flex items-center gap-2">
                                                    <Columns size={14} className="text-indigo-400" />
                                                    <h3 className="text-sm font-semibold text-white">{columns.length} column{columns.length !== 1 ? 's' : ''}</h3>
                                                </div>
                                                {schemaData.row_count !== undefined && (
                                                    <span className="text-[11px] text-slate-500 flex items-center gap-1.5">
                                                        <Hash size={11} />
                                                        {schemaData.row_count.toLocaleString()} rows
                                                    </span>
                                                )}
                                            </div>
                                            <div className="overflow-x-auto">
                                                <table className="w-full text-sm">
                                                    <thead>
                                                        <tr className="border-b border-white/[0.06] bg-white/[0.01]">
                                                            <th className="text-left px-5 py-2.5 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Column</th>
                                                            <th className="text-left px-5 py-2.5 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Type</th>
                                                            <th className="text-left px-5 py-2.5 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Nullable</th>
                                                            <th className="text-left px-5 py-2.5 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Info</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody className="divide-y divide-white/[0.04]">
                                                        {columns.map((col, idx) => (
                                                            <tr key={idx} className="hover:bg-white/[0.02] transition-colors">
                                                                <td className="px-5 py-3">
                                                                    <span className="font-mono text-[13px] text-indigo-400">{col.name}</span>
                                                                </td>
                                                                <td className="px-5 py-3">
                                                                    <span className="font-mono text-xs text-slate-400 bg-white/[0.03] px-1.5 py-0.5 rounded">{col.type}</span>
                                                                </td>
                                                                <td className="px-5 py-3">
                                                                    <span className={`text-[11px] font-medium px-2 py-0.5 rounded-full ${
                                                                        col.mode === 'REQUIRED' || col.nullable === false
                                                                            ? 'bg-red-500/10 text-red-400 border border-red-500/15'
                                                                            : 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/15'
                                                                    }`}>
                                                                        {col.mode || (col.nullable === false ? 'Required' : 'Nullable')}
                                                                    </span>
                                                                </td>
                                                                <td className="px-5 py-3 text-xs text-slate-500 max-w-[200px] truncate">
                                                                    {col.description || col.default || '--'}
                                                                </td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </div>
                                        </div>
                                    )
                                })()}
                            </div>
                        ) : (
                            <div className="flex flex-col items-center justify-center py-20 text-center">
                                <div className="w-14 h-14 rounded-2xl border border-white/[0.07] bg-white/[0.02] flex items-center justify-center mb-4">
                                    <Database size={24} className="text-slate-600" />
                                </div>
                                <h3 className="text-sm font-medium text-white mb-1">Schema not loaded</h3>
                                <p className="text-xs text-slate-500 mb-5 max-w-xs">
                                    We'll fetch your datastore's structure so you can browse datasets, tables, and columns.
                                </p>
                                <button onClick={() => loadSchema()} className="btn btn-primary text-xs">
                                    Load Schema
                                </button>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
        </div>
    )
}
