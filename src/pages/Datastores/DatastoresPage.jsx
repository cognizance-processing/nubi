import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../../lib/api'
import { useOrg } from '../../contexts/OrgContext'
import { useChat } from '../../contexts/ChatContext'
import {
    Plus, Trash2, Settings, CheckCircle2, XCircle,
    Loader2, Search, Wifi, X, ArrowRight, ArrowLeft, Upload
} from 'lucide-react'

const DB_TYPES = {
    postgres: {
        name: 'PostgreSQL',
        description: 'Open-source relational database',
        color: 'from-[#336791] to-[#264d6b]',
        badgeCls: 'bg-[#336791]/15 text-[#5b9bd5]',
        configType: 'connection_string',
        placeholder: 'postgresql://user:pass@host:5432/mydb',
        hint: 'Standard PostgreSQL connection URI',
        logo: (
            <svg viewBox="0 0 32 32" className="w-7 h-7">
                <path d="M16 4c-5.5 0-10 4.5-10 10s4.5 10 10 10c1.8 0 3.5-.5 5-1.3" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"/>
                <path d="M21 12.7c0-2.5-2-5-5-5s-5 2.5-5 5c0 3.5 2.5 5.5 5 7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
                <path d="M21 12.7c0 3-1 6-1 8.3 0 1.5 1 2 2 2s2.5-1 2.5-3" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
                <circle cx="14.5" cy="12" r="1.2" fill="currentColor"/>
            </svg>
        ),
    },
    mysql: {
        name: 'MySQL',
        description: 'Popular open-source SQL database',
        color: 'from-[#4479A1] to-[#336085]',
        badgeCls: 'bg-[#4479A1]/15 text-[#6db3f0]',
        configType: 'connection_string',
        placeholder: 'mysql+pymysql://user:pass@host:3306/mydb',
        hint: 'MySQL connection string using PyMySQL driver',
        logo: (
            <svg viewBox="0 0 32 32" className="w-7 h-7">
                <path d="M8 22c2-2 4-5 4-8s-1-6 0-9" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
                <path d="M14 22c1.5-2 3-5 3-8s-.5-6 .5-9" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
                <path d="M20 5c1.5 2 4 5 4 9s-1 6 0 8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
                <path d="M7 18h18" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" opacity="0.4"/>
            </svg>
        ),
    },
    bigquery: {
        name: 'BigQuery',
        description: 'Google Cloud serverless data warehouse',
        color: 'from-[#4285F4] to-[#2b6de0]',
        badgeCls: 'bg-[#4285F4]/15 text-[#6ea6ff]',
        configType: 'bigquery',
        logo: (
            <svg viewBox="0 0 32 32" className="w-7 h-7">
                <rect x="7" y="10" width="5" height="14" rx="1.5" fill="currentColor" opacity="0.4"/>
                <rect x="13.5" y="6" width="5" height="18" rx="1.5" fill="currentColor" opacity="0.6"/>
                <rect x="20" y="13" width="5" height="11" rx="1.5" fill="currentColor" opacity="0.8"/>
                <circle cx="22.5" cy="9" r="3" fill="none" stroke="currentColor" strokeWidth="2"/>
                <line x1="24.6" y1="11.1" x2="27" y2="13.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
            </svg>
        ),
    },
    athena: {
        name: 'AWS Athena',
        description: 'Serverless query service for S3 data',
        color: 'from-[#FF9900] to-[#cc7a00]',
        badgeCls: 'bg-[#FF9900]/15 text-[#ffb84d]',
        configType: 'athena',
        logo: (
            <svg viewBox="0 0 32 32" className="w-7 h-7">
                <path d="M16 4L6 10v12l10 6 10-6V10L16 4z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round"/>
                <path d="M16 4v24M6 10l10 6 10-6" fill="none" stroke="currentColor" strokeWidth="1.5" opacity="0.5"/>
            </svg>
        ),
    },
    mssql: {
        name: 'Azure SQL',
        description: 'Microsoft SQL Server / Azure SQL',
        color: 'from-[#0078D4] to-[#005a9e]',
        badgeCls: 'bg-[#0078D4]/15 text-[#4da6ff]',
        configType: 'connection_string',
        placeholder: 'mssql+pymssql://user:pass@host:1433/mydb',
        hint: 'SQL Server connection string using pymssql driver',
        logo: (
            <svg viewBox="0 0 32 32" className="w-7 h-7">
                <ellipse cx="16" cy="9" rx="9" ry="4" fill="none" stroke="currentColor" strokeWidth="2"/>
                <path d="M7 9v14c0 2.2 4 4 9 4s9-1.8 9-4V9" fill="none" stroke="currentColor" strokeWidth="2"/>
                <path d="M7 16c0 2.2 4 4 9 4s9-1.8 9-4" fill="none" stroke="currentColor" strokeWidth="1.5" opacity="0.4"/>
            </svg>
        ),
    },
    duckdb: {
        name: 'DuckDB',
        description: 'In-process analytical database',
        color: 'from-[#FFC107] to-[#e6a800]',
        badgeCls: 'bg-[#FFC107]/15 text-[#ffd54f]',
        configType: 'duckdb',
        logo: (
            <svg viewBox="0 0 32 32" className="w-7 h-7">
                <ellipse cx="16" cy="16" rx="10" ry="9" fill="none" stroke="currentColor" strokeWidth="2"/>
                <circle cx="12.5" cy="13.5" r="1.5" fill="currentColor"/>
                <path d="M20 16c0 0 2-.5 4 0" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
                <path d="M11 19c1.5 1.5 3.5 2 5 2s3-.5 4-1.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
        ),
    },
}

export default function DatastoresPage() {
    const navigate = useNavigate()
    const { currentOrg } = useOrg()
    const { setPageContext, openChatFor } = useChat()
    const [loading, setLoading] = useState(true)
    const [datastores, setDatastores] = useState([])
    const [searchQuery, setSearchQuery] = useState('')

    useEffect(() => {
        setPageContext({ type: 'datastore' })
        openChatFor(null)
        return () => setPageContext({ type: 'general' })
    }, [setPageContext, openChatFor])
    const [testingStoreId, setTestingStoreId] = useState(null)
    const [testResults, setTestResults] = useState({})

    // Wizard state
    const [wizardOpen, setWizardOpen] = useState(false)
    const [wizardStep, setWizardStep] = useState(1)
    const [editingStore, setEditingStore] = useState(null)
    const [formType, setFormType] = useState('postgres')
    const [saving, setSaving] = useState(false)
    const [detectedProjectId, setDetectedProjectId] = useState('')
    const [testingNew, setTestingNew] = useState(false)
    const [testNewResult, setTestNewResult] = useState(null)

    useEffect(() => { fetchStores() }, [currentOrg?.id])

    async function fetchStores() {
        setLoading(true)
        try {
            const data = await api.datastores.list(currentOrg?.id)
            if (data) setDatastores(data)
        } catch (err) { console.error('Error fetching datastores:', err) }
        setLoading(false)
    }

    const filteredStores = datastores.filter(s =>
        s.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        s.type.toLowerCase().includes(searchQuery.toLowerCase())
    )

    async function testStore(store) {
        setTestingStoreId(store.id)
        try {
            const backendUrl = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000'
            const res = await fetch(`${backendUrl}/test-datastore`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ datastore_id: store.id })
            })
            const result = await res.json()
            setTestResults(prev => ({ ...prev, [store.id]: result.status === 'success' }))
        } catch (e) {
            setTestResults(prev => ({ ...prev, [store.id]: false }))
        }
        setTestingStoreId(null)
    }

    async function deleteStore(id, e) {
        e.stopPropagation()
        if (!confirm('Delete this datastore? Any dependent queries may break.')) return
        await api.datastores.delete(id)
        setDatastores(datastores.filter(s => s.id !== id))
    }

    function openWizard(store = null) {
        setEditingStore(store)
        setFormType(store?.type || 'postgres')
        setDetectedProjectId('')
        setTestNewResult(null)
        setWizardStep(store ? 2 : 1)
        setWizardOpen(true)
    }

    function closeWizard() {
        setWizardOpen(false)
        setEditingStore(null)
        setWizardStep(1)
        setDetectedProjectId('')
        setTestNewResult(null)
    }

    function selectType(type) {
        setFormType(type)
        setWizardStep(2)
    }

    async function handleSave(e) {
        e.preventDefault()
        setSaving(true)
        try {
            const fd = new FormData(e.target)
            const type = formType
            let config = {}

            if (type === 'postgres' || type === 'mysql' || type === 'mssql') {
                config = { connection_string: fd.get('connection_string') }
            } else if (type === 'bigquery') {
                config = { project_id: fd.get('project_id'), keyfile_path: editingStore?.config?.keyfile_path }
                const keyfile = fd.get('keyfile')
                if (keyfile && keyfile.size > 0) {
                    const uploadResult = await api.upload(keyfile)
                    config.keyfile_path = uploadResult.path
                }
            } else if (type === 'athena') {
                config = {
                    region: fd.get('region'), s3_output_location: fd.get('s3_output_location'),
                    database: fd.get('database'), access_key_id: fd.get('access_key_id'),
                    secret_access_key: fd.get('secret_access_key'),
                }
            } else if (type === 'duckdb') {
                config = { file_path: editingStore?.config?.file_path || '' }
                const dbfile = fd.get('dbfile')
                if (dbfile && dbfile.size > 0) {
                    const uploadResult = await api.upload(dbfile)
                    config.file_path = uploadResult.path
                }
            }

            const payload = { name: fd.get('name'), type, config, organization_id: currentOrg?.id }
            if (editingStore) {
                await api.datastores.update(editingStore.id, payload)
            } else {
                await api.datastores.create(payload)
            }
            closeWizard()
            fetchStores()
        } catch (err) {
            alert(`Failed to save: ${err.message}`)
        } finally {
            setSaving(false)
        }
    }

    const meta = DB_TYPES[formType] || DB_TYPES.postgres

    return (
        <div className="p-5 max-w-6xl mx-auto space-y-5">
            <header className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                    <h1 className="text-lg font-semibold text-white">Datastores</h1>
                    <p className="text-slate-400 text-xs mt-0.5">Configure and manage your data connections</p>
                </div>
                <div className="flex items-center gap-3">
                    <div className="relative group">
                        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 group-focus-within:text-indigo-400 transition-colors" />
                        <input className="form-input pl-8 w-full sm:w-56" placeholder="Search..." value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} />
                    </div>
                    <button onClick={() => openWizard()} className="btn btn-primary">
                        <Plus size={15} /> Add Datastore
                    </button>
                </div>
            </header>

            {loading ? (
                <div className="flex flex-col items-center justify-center py-20 gap-3">
                    <div className="spinner"></div>
                    <p className="text-slate-500 text-xs font-medium">Loading datastores...</p>
                </div>
            ) : filteredStores.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 bg-slate-900/50 rounded-xl border border-dashed border-white/[0.07] animate-fade-in text-center px-6">
                    <div className="flex items-center gap-3 mb-5">
                        {Object.entries(DB_TYPES).slice(0, 4).map(([key, t]) => (
                            <div key={key} className={`w-10 h-10 rounded-xl bg-gradient-to-br ${t.color} text-white/80 flex items-center justify-center shadow-lg`}>
                                {t.logo}
                            </div>
                        ))}
                    </div>
                    <h3 className="text-sm font-semibold mb-1 text-white">No datastores yet</h3>
                    <p className="text-slate-400 text-xs mb-5 max-w-sm">Connect your first database, warehouse, or file to start querying.</p>
                    <button onClick={() => openWizard()} className="btn btn-primary"><Plus size={15} /> Add Datastore</button>
                </div>
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 animate-fade-in">
                    {filteredStores.map(store => {
                        const t = DB_TYPES[store.type] || DB_TYPES.postgres
                        return (
                            <div key={store.id} className="card-interactive group flex flex-col cursor-pointer" onClick={() => navigate(`/datastores/${store.id}`)}>
                                <div className="flex items-center gap-3 mb-3">
                                    <div className={`w-10 h-10 rounded-xl bg-gradient-to-br ${t.color} text-white flex items-center justify-center shadow-lg shadow-black/20 group-hover:scale-105 transition-transform duration-200`}>
                                        {t.logo}
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <span className={`text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded ${t.badgeCls}`}>
                                            {t.name}
                                        </span>
                                        <h3 className="text-sm font-semibold text-white truncate leading-tight mt-0.5">{store.name}</h3>
                                    </div>
                                </div>
                                <div className="flex items-center justify-between pt-3 border-t border-white/[0.07]">
                                    <button
                                        onClick={(e) => { e.stopPropagation(); testStore(store) }}
                                        disabled={testingStoreId === store.id}
                                        className={`text-[11px] font-medium px-2 py-1 rounded-md transition-all flex items-center gap-1.5 border ${
                                            testingStoreId === store.id ? 'bg-slate-800 text-slate-500 border-white/[0.07] cursor-not-allowed'
                                            : testResults[store.id] === true ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                                            : testResults[store.id] === false ? 'bg-red-500/10 text-red-400 border-red-500/20'
                                            : 'bg-indigo-500/5 text-indigo-400 border-indigo-500/10 hover:bg-indigo-500/10'
                                        }`}
                                    >
                                        {testingStoreId === store.id ? <><Loader2 size={11} className="animate-spin" /><span>Testing...</span></>
                                        : testResults[store.id] === true ? <><CheckCircle2 size={11} /><span>Connected</span></>
                                        : testResults[store.id] === false ? <><XCircle size={11} /><span>Failed</span></>
                                        : <><Wifi size={11} /><span>Test</span></>}
                                    </button>
                                    <div className="flex items-center gap-0.5">
                                        <button className="icon-button" onClick={(e) => { e.stopPropagation(); openWizard(store) }} title="Settings"><Settings size={14} /></button>
                                        <button onClick={(e) => deleteStore(store.id, e)} className="icon-button hover:bg-red-500/10 hover:text-red-400" title="Delete"><Trash2 size={14} /></button>
                                    </div>
                                </div>
                            </div>
                        )
                    })}
                </div>
            )}

            {/* ── Wizard Modal ── */}
            {wizardOpen && (
                <div className="modal-overlay">
                    <div className="modal-container max-w-xl">
                        {/* Header */}
                        <div className="flex items-center justify-between mb-1">
                            <div className="flex items-center gap-3">
                                {wizardStep === 2 && !editingStore && (
                                    <button type="button" onClick={() => setWizardStep(1)} className="p-1 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.05] transition">
                                        <ArrowLeft size={16} />
                                    </button>
                                )}
                                <h2 className="text-base font-semibold text-white">
                                    {editingStore ? 'Edit Datastore' : wizardStep === 1 ? 'Choose Database Type' : `Configure ${meta.name}`}
                                </h2>
                            </div>
                            <button onClick={closeWizard} className="icon-button"><X size={18} /></button>
                        </div>

                        {!editingStore && (
                            <div className="flex items-center gap-2 mb-5">
                                {[1, 2].map(s => (
                                    <div key={s} className="flex items-center gap-2">
                                        <div className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold transition-all ${wizardStep >= s ? 'bg-indigo-600 text-white' : 'bg-slate-800 text-slate-500'}`}>{s}</div>
                                        <span className={`text-[11px] font-medium ${wizardStep >= s ? 'text-slate-300' : 'text-slate-600'}`}>
                                            {s === 1 ? 'Select Type' : 'Configure'}
                                        </span>
                                        {s < 2 && <div className={`w-8 h-px ${wizardStep > s ? 'bg-indigo-500' : 'bg-slate-800'}`} />}
                                    </div>
                                ))}
                            </div>
                        )}

                        {/* Step 1: Type Selection */}
                        {wizardStep === 1 && (
                            <div className="grid grid-cols-2 gap-3">
                                {Object.entries(DB_TYPES).map(([key, t]) => (
                                    <button
                                        key={key}
                                        onClick={() => selectType(key)}
                                        className="group/card flex items-start gap-3 p-4 rounded-xl border border-white/[0.07] bg-white/[0.02] hover:bg-white/[0.05] hover:border-white/[0.15] transition-all text-left"
                                    >
                                        <div className={`w-10 h-10 rounded-xl bg-gradient-to-br ${t.color} text-white flex items-center justify-center shadow-lg shrink-0 group-hover/card:scale-110 transition-transform`}>
                                            {t.logo}
                                        </div>
                                        <div className="min-w-0 flex-1">
                                            <div className="text-sm font-semibold text-white">{t.name}</div>
                                            <div className="text-[11px] text-slate-500 mt-0.5 leading-snug">{t.description}</div>
                                        </div>
                                        <ArrowRight size={14} className="text-slate-600 group-hover/card:text-indigo-400 shrink-0 mt-1 transition-colors" />
                                    </button>
                                ))}
                            </div>
                        )}

                        {/* Step 2: Configure */}
                        {wizardStep === 2 && (
                            <form className="space-y-4" onSubmit={handleSave}>
                                {/* Type indicator */}
                                {!editingStore && (
                                    <div className="flex items-center gap-3 p-3 rounded-lg bg-white/[0.03] border border-white/[0.06]">
                                        <div className={`w-8 h-8 rounded-lg bg-gradient-to-br ${meta.color} text-white flex items-center justify-center`}>
                                            {meta.logo}
                                        </div>
                                        <div>
                                            <div className="text-xs font-semibold text-white">{meta.name}</div>
                                            <div className="text-[10px] text-slate-500">{meta.description}</div>
                                        </div>
                                    </div>
                                )}

                                <div className="form-group">
                                    <label className="form-label">Display Name</label>
                                    <input name="name" defaultValue={editingStore?.name} className="form-input" placeholder={`e.g. Production ${meta.name}`} required />
                                </div>

                                {editingStore && (
                                    <div className="form-group">
                                        <label className="form-label">Type</label>
                                        <select name="type" value={formType} onChange={(e) => setFormType(e.target.value)} className="form-input">
                                            {Object.entries(DB_TYPES).map(([k, v]) => <option key={k} value={k}>{v.name}</option>)}
                                        </select>
                                    </div>
                                )}

                                {/* Connection string types */}
                                {(meta.configType === 'connection_string') && (
                                    <div className="form-group">
                                        <label className="form-label">Connection String</label>
                                        <input name="connection_string" defaultValue={editingStore?.config?.connection_string} className="form-input font-mono text-xs" placeholder={meta.placeholder} required />
                                        <p className="text-[10px] text-slate-600 mt-1">{meta.hint}</p>
                                    </div>
                                )}

                                {/* BigQuery */}
                                {meta.configType === 'bigquery' && (
                                    <div className="space-y-4">
                                        <div className="form-group">
                                            <label className="form-label">Service Account Keyfile</label>
                                            <label className="flex items-center gap-3 p-3 rounded-lg border border-dashed border-white/[0.1] hover:border-indigo-500/30 bg-white/[0.02] hover:bg-indigo-500/5 transition cursor-pointer">
                                                <Upload size={16} className="text-slate-500" />
                                                <span className="text-xs text-slate-400">Click to upload JSON keyfile</span>
                                                <input type="file" name="keyfile" className="hidden" accept=".json" onChange={(e) => {
                                                    const file = e.target.files?.[0]
                                                    if (!file) return
                                                    const reader = new FileReader()
                                                    reader.onload = (ev) => {
                                                        try { const p = JSON.parse(ev.target.result); if (p.project_id) setDetectedProjectId(p.project_id) } catch {}
                                                    }
                                                    reader.readAsText(file)
                                                }} />
                                            </label>
                                            {editingStore?.config?.keyfile_path && (
                                                <p className="text-[10px] text-emerald-400/70 mt-1">Keyfile configured</p>
                                            )}
                                        </div>
                                        <div className="form-group">
                                            <label className="form-label">
                                                Project ID
                                                {detectedProjectId && <span className="text-emerald-400 text-[10px] ml-1.5 font-normal">(detected from keyfile)</span>}
                                            </label>
                                            <input name="project_id" defaultValue={detectedProjectId || editingStore?.config?.project_id || ''} key={detectedProjectId} className="form-input" placeholder="my-gcp-project-123" required />
                                        </div>
                                    </div>
                                )}

                                {/* Athena */}
                                {meta.configType === 'athena' && (
                                    <div className="space-y-4">
                                        <div className="grid grid-cols-2 gap-3">
                                            <div className="form-group">
                                                <label className="form-label">AWS Region</label>
                                                <input name="region" defaultValue={editingStore?.config?.region} className="form-input" placeholder="us-east-1" required />
                                            </div>
                                            <div className="form-group">
                                                <label className="form-label">Database</label>
                                                <input name="database" defaultValue={editingStore?.config?.database} className="form-input" placeholder="my_database" required />
                                            </div>
                                        </div>
                                        <div className="form-group">
                                            <label className="form-label">S3 Output Location</label>
                                            <input name="s3_output_location" defaultValue={editingStore?.config?.s3_output_location} className="form-input font-mono text-xs" placeholder="s3://my-bucket/athena-results/" required />
                                        </div>
                                        <div className="form-group">
                                            <label className="form-label">Access Key ID</label>
                                            <input name="access_key_id" defaultValue={editingStore?.config?.access_key_id} className="form-input font-mono text-xs" placeholder="AKIA..." required />
                                        </div>
                                        <div className="form-group">
                                            <label className="form-label">Secret Access Key</label>
                                            <input name="secret_access_key" type="password" defaultValue={editingStore?.config?.secret_access_key} className="form-input font-mono text-xs" placeholder="••••••••" required />
                                        </div>
                                    </div>
                                )}

                                {/* DuckDB */}
                                {meta.configType === 'duckdb' && (
                                    <div className="form-group">
                                        <label className="form-label">Database File</label>
                                        <label className="flex items-center gap-3 p-4 rounded-lg border border-dashed border-white/[0.1] hover:border-indigo-500/30 bg-white/[0.02] hover:bg-indigo-500/5 transition cursor-pointer">
                                            <Upload size={18} className="text-slate-500" />
                                            <div>
                                                <span className="text-xs text-slate-300 font-medium block">Upload DuckDB, Parquet, or CSV file</span>
                                                <span className="text-[10px] text-slate-600">Supports .duckdb, .parquet, .csv formats</span>
                                            </div>
                                            <input type="file" name="dbfile" className="hidden" accept=".duckdb,.parquet,.csv,.db" />
                                        </label>
                                        {editingStore?.config?.file_path && (
                                            <p className="text-[10px] text-emerald-400/70 mt-1.5">File configured: {editingStore.config.file_path.split('/').pop()}</p>
                                        )}
                                    </div>
                                )}

                                <div className="flex gap-3 justify-end pt-3 border-t border-white/[0.05]">
                                    <button type="button" onClick={closeWizard} className="btn btn-ghost">Cancel</button>
                                    <button type="submit" className="btn btn-primary" disabled={saving}>
                                        {saving ? <><Loader2 size={13} className="animate-spin" /> Saving...</> : <><CheckCircle2 size={13} /> {editingStore ? 'Update' : 'Create'} Datastore</>}
                                    </button>
                                </div>
                            </form>
                        )}
                    </div>
                </div>
            )}
        </div>
    )
}
