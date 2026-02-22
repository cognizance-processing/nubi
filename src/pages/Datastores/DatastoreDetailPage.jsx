import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { supabase, getSchema } from '../../lib/supabase'
import { useHeader } from '../../contexts/HeaderContext'
import { ArrowLeft, Database, Save, Play, Loader2, CheckCircle2, XCircle, Columns, Table, ChevronRight } from 'lucide-react'
import Editor from '@monaco-editor/react'

export default function DatastoreDetailPage() {
    const { datastoreId } = useParams()
    const navigate = useNavigate()
    const { setHeaderContent } = useHeader()
    
    const [activeTab, setActiveTab] = useState('details')
    const [datastore, setDatastore] = useState(null)
    const [loading, setLoading] = useState(true)
    const [saving, setSaving] = useState(false)
    const [testing, setTesting] = useState(false)
    const [testResult, setTestResult] = useState(null)
    
    // Details tab state
    const [name, setName] = useState('')
    const [type, setType] = useState('postgres')
    const [connectionString, setConnectionString] = useState('')
    const [projectId, setProjectId] = useState('')
    const [keyfilePath, setKeyfilePath] = useState('')
    
    // Schema tab state
    const [schemaData, setSchemaData] = useState(null)
    const [schemaLoading, setSchemaLoading] = useState(false)
    const [breadcrumbs, setBreadcrumbs] = useState([])
    
    // Query tab state
    const [queryCode, setQueryCode] = useState('SELECT * FROM table_name LIMIT 10;')
    const [queryRunning, setQueryRunning] = useState(false)
    const [queryResults, setQueryResults] = useState(null)
    const [queryError, setQueryError] = useState(null)

    useEffect(() => {
        loadDatastore()
    }, [datastoreId])

    useEffect(() => {
        if (datastore) {
            setName(datastore.name)
            setType(datastore.type)
            setConnectionString(datastore.config?.connection_string || '')
            setProjectId(datastore.config?.project_id || '')
            setKeyfilePath(datastore.config?.keyfile_path || '')
        }
    }, [datastore])

    useEffect(() => {
        setHeaderContent(
            <div className="flex items-center gap-3 flex-1 px-2">
                <button
                    className="flex items-center gap-1.5 px-2 py-1 rounded-md text-slate-400 hover:text-white hover:bg-slate-800 transition-all font-medium text-xs"
                    onClick={() => navigate('/datastores')}
                >
                    <ArrowLeft size={14} />
                    <span>Back</span>
                </button>

                <div className="h-4 w-px bg-border-primary" />

                <div className="flex-1 min-w-0">
                    <h2 className="text-[13px] font-semibold text-white truncate">
                        {datastore?.name || 'Loading...'}
                    </h2>
                </div>

                <div className="flex bg-slate-800 p-0.5 rounded-lg border border-white/[0.07]/50">
                    <button
                        className={`px-3 py-1 rounded-md text-xs font-medium transition-all duration-150 ${activeTab === 'details'
                            ? 'bg-slate-900 text-indigo-400 shadow-sm'
                            : 'text-slate-400 hover:text-white'
                            }`}
                        onClick={() => setActiveTab('details')}
                    >
                        Details
                    </button>
                    <button
                        className={`px-3 py-1 rounded-md text-xs font-medium transition-all duration-150 ${activeTab === 'schema'
                            ? 'bg-slate-900 text-indigo-400 shadow-sm'
                            : 'text-slate-400 hover:text-white'
                            }`}
                        onClick={() => setActiveTab('schema')}
                    >
                        Schema
                    </button>
                    <button
                        className={`px-3 py-1 rounded-md text-xs font-medium transition-all duration-150 ${activeTab === 'query'
                            ? 'bg-slate-900 text-indigo-400 shadow-sm'
                            : 'text-slate-400 hover:text-white'
                            }`}
                        onClick={() => setActiveTab('query')}
                    >
                        Query
                    </button>
                </div>

                {activeTab === 'details' && (
                    <button className="btn btn-primary" onClick={saveDatastore} disabled={saving}>
                        {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
                        Save
                    </button>
                )}

                {activeTab === 'query' && (
                    <button className="btn btn-primary" onClick={runQuery} disabled={queryRunning}>
                        {queryRunning ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                        Run
                    </button>
                )}
            </div>
        )
        return () => setHeaderContent(null)
    }, [activeTab, datastore, saving, queryRunning])

    async function loadDatastore() {
        setLoading(true)
        const { data, error } = await supabase
            .from('datastores')
            .select('*')
            .eq('id', datastoreId)
            .single()
        
        if (error) {
            console.error('Error loading datastore:', error)
            alert('Failed to load datastore')
            navigate('/datastores')
        } else {
            setDatastore(data)
            // Auto-load root schema for schema tab
            if (activeTab === 'schema') {
                loadSchema()
            }
        }
        setLoading(false)
    }

    async function saveDatastore() {
        setSaving(true)
        try {
            const config = type === 'postgres'
                ? { connection_string: connectionString }
                : { project_id: projectId, keyfile_path: keyfilePath }
            
            const { error } = await supabase
                .from('datastores')
                .update({ name, type, config })
                .eq('id', datastoreId)
            
            if (error) throw error
            
            setDatastore({ ...datastore, name, type, config })
            alert('Datastore updated successfully!')
        } catch (err) {
            console.error('Save error:', err)
            alert('Failed to save: ' + err.message)
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
            setTestResult(result.status === 'success')
        } catch (err) {
            setTestResult(false)
        } finally {
            setTesting(false)
        }
    }

    async function loadSchema(database = null, table = null) {
        setSchemaLoading(true)
        const { data, error } = await getSchema({
            datastore_id: datastoreId,
            database,
            table
        })
        
        if (error) {
            console.error('Schema error:', error)
            alert('Failed to load schema: ' + error.message)
        } else {
            setSchemaData(data)
            
            // Update breadcrumbs
            const crumbs = [{ label: datastore.name, database: null, table: null }]
            if (database) crumbs.push({ label: database, database, table: null })
            if (table) crumbs.push({ label: table, database, table })
            setBreadcrumbs(crumbs)
        }
        setSchemaLoading(false)
    }

    async function runQuery() {
        setQueryRunning(true)
        setQueryError(null)
        setQueryResults(null)
        
        try {
            const backendUrl = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000'
            const response = await fetch(`${backendUrl}/query`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    datastore_id: datastoreId,
                    sql: queryCode,
                    args: {}
                })
            })
            
            const data = await response.json()
            
            if (!response.ok) {
                throw new Error(data.detail || 'Query failed')
            }
            
            setQueryResults(data)
        } catch (err) {
            setQueryError(err.message)
        } finally {
            setQueryRunning(false)
        }
    }

    useEffect(() => {
        if (activeTab === 'schema' && datastore && !schemaData) {
            loadSchema()
        }
    }, [activeTab])

    if (loading) {
        return (
            <div className="flex items-center justify-center h-screen">
                <div className="spinner"></div>
            </div>
        )
    }

    return (
        <div className="flex flex-col h-screen bg-slate-950 overflow-hidden">
            {activeTab === 'details' && (
                <div className="flex-1 overflow-y-auto p-5">
                    <div className="max-w-2xl mx-auto space-y-4">
                        <div className="card">
                            <h3 className="text-sm font-semibold text-white mb-4">Connection Details</h3>
                            
                            <div className="space-y-6">
                                <div className="form-group">
                                    <label className="form-label">Name</label>
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
                                    <select
                                        value={type}
                                        onChange={(e) => setType(e.target.value)}
                                        className="form-input"
                                    >
                                        <option value="postgres">PostgreSQL</option>
                                        <option value="bigquery">Google BigQuery</option>
                                    </select>
                                </div>

                                {type === 'postgres' ? (
                                    <div className="form-group">
                                        <label className="form-label">Connection String</label>
                                        <input
                                            type="text"
                                            value={connectionString}
                                            onChange={(e) => setConnectionString(e.target.value)}
                                            className="form-input"
                                            placeholder="postgresql://user:pass@host:port/db"
                                        />
                                    </div>
                                ) : (
                                    <>
                                        <div className="form-group">
                                            <label className="form-label">Project ID</label>
                                            <input
                                                type="text"
                                                value={projectId}
                                                onChange={(e) => setProjectId(e.target.value)}
                                                className="form-input"
                                                placeholder="my-gcp-project-123"
                                            />
                                        </div>
                                        <div className="form-group">
                                            <label className="form-label">Keyfile Path</label>
                                            <input
                                                type="text"
                                                value={keyfilePath}
                                                readOnly
                                                className="form-input bg-slate-800"
                                                placeholder="Upload via edit modal"
                                            />
                                        </div>
                                    </>
                                )}

                                <div className="pt-4 border-t border-white/[0.07]">
                                    <button
                                        onClick={testConnection}
                                        disabled={testing}
                                        className={`btn ${
                                            testResult === true ? 'btn-secondary bg-emerald-500/10 text-emerald-400 border-emerald-500/20' : 
                                            testResult === false ? 'btn-secondary bg-red-500/10 text-red-400 border-red-500/20' : 
                                            'btn-secondary'
                                        }`}
                                    >
                                        {testing ? (
                                            <><Loader2 size={16} className="animate-spin" /> Testing...</>
                                        ) : testResult === true ? (
                                            <><CheckCircle2 size={16} /> Connection Verified</>
                                        ) : testResult === false ? (
                                            <><XCircle size={16} /> Connection Failed</>
                                        ) : (
                                            <>Test Connection</>
                                        )}
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {activeTab === 'schema' && (
                <div className="flex-1 overflow-y-auto p-5">
                    <div className="max-w-5xl mx-auto">
                        <div className="card">
                            {breadcrumbs.length > 0 && (
                                <div className="flex items-center gap-2 mb-6 text-sm">
                                    {breadcrumbs.map((crumb, idx) => (
                                        <div key={idx} className="flex items-center gap-2">
                                            {idx > 0 && <ChevronRight size={14} className="text-slate-500" />}
                                            <button
                                                onClick={() => loadSchema(crumb.database, crumb.table)}
                                                className={`${
                                                    idx === breadcrumbs.length - 1
                                                        ? 'text-indigo-400 font-medium'
                                                        : 'text-slate-400 hover:text-white'
                                                }`}
                                            >
                                                {crumb.label}
                                            </button>
                                        </div>
                                    ))}
                                </div>
                            )}

                            {schemaLoading ? (
                                <div className="py-12 text-center">
                                    <div className="spinner mx-auto mb-4"></div>
                                    <p className="text-slate-500">Loading schema...</p>
                                </div>
                            ) : schemaData ? (
                                <div>
                                    {/* Datasets/Schemas List */}
                                    {(schemaData.type === 'datasets' || schemaData.type === 'schemas') && (
                                        <div className="space-y-2">
                                            <h3 className="text-sm font-bold text-white mb-3">
                                                {schemaData.type === 'datasets' ? 'Datasets' : 'Schemas'} ({(schemaData.datasets || schemaData.schemas).length})
                                            </h3>
                                            {(schemaData.datasets || schemaData.schemas).map(item => (
                                                <button
                                                    key={item.name}
                                                    onClick={() => loadSchema(item.name)}
                                                    className="w-full text-left px-4 py-3 rounded-lg bg-slate-800 hover:bg-slate-900 transition-all"
                                                >
                                                    <div className="flex items-center gap-3">
                                                        <Database size={20} className="text-indigo-400" />
                                                        <div className="font-medium text-white">{item.name}</div>
                                                    </div>
                                                </button>
                                            ))}
                                        </div>
                                    )}

                                    {/* Tables List */}
                                    {schemaData.type === 'tables' && (
                                        <div className="space-y-2">
                                            <h3 className="text-sm font-bold text-white mb-3">
                                                Tables ({schemaData.tables.length})
                                            </h3>
                                            {schemaData.tables.map(t => (
                                                <button
                                                    key={t.name}
                                                    onClick={() => loadSchema(schemaData.dataset || schemaData.schema, t.name)}
                                                    className="w-full text-left px-4 py-3 rounded-lg bg-slate-800 hover:bg-slate-900 transition-all"
                                                >
                                                    <div className="flex items-center gap-3">
                                                        <Table size={20} className="text-blue-400" />
                                                        <div className="font-medium text-white">{t.name}</div>
                                                    </div>
                                                </button>
                                            ))}
                                        </div>
                                    )}

                                    {/* Table Schema */}
                                    {schemaData.type === 'table_schema' && (
                                        <div>
                                            <div className="flex items-center justify-between mb-4">
                                                <h3 className="text-sm font-bold text-white">
                                                    Columns ({schemaData.schema?.length || schemaData.columns?.length || 0})
                                                </h3>
                                                {schemaData.row_count !== undefined && (
                                                    <span className="text-xs text-slate-500">
                                                        {schemaData.row_count.toLocaleString()} rows
                                                    </span>
                                                )}
                                            </div>
                                            <div className="overflow-x-auto">
                                                <table className="w-full text-sm">
                                                    <thead className="border-b border-white/[0.07]">
                                                        <tr>
                                                            <th className="text-left p-3 font-bold text-white">Column</th>
                                                            <th className="text-left p-3 font-bold text-white">Type</th>
                                                            <th className="text-left p-3 font-bold text-white">Mode</th>
                                                            <th className="text-left p-3 font-bold text-white">Info</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {(schemaData.schema || schemaData.columns || []).map((col, idx) => (
                                                            <tr key={idx} className="border-b border-white/[0.07]/50 hover:bg-slate-800/50">
                                                                <td className="p-3">
                                                                    <div className="flex items-center gap-2">
                                                                        <Columns size={14} className="text-slate-500" />
                                                                        <span className="font-mono text-indigo-400">{col.name}</span>
                                                                    </div>
                                                                </td>
                                                                <td className="p-3 font-mono text-slate-400">{col.type}</td>
                                                                <td className="p-3">
                                                                    <span className={`text-xs px-2 py-1 rounded ${
                                                                        col.mode === 'REQUIRED' || col.nullable === false
                                                                            ? 'bg-red-500/20 text-red-400'
                                                                            : 'bg-green-500/20 text-green-400'
                                                                    }`}>
                                                                        {col.mode || (col.nullable === false ? 'REQUIRED' : 'NULLABLE')}
                                                                    </span>
                                                                </td>
                                                                <td className="p-3 text-slate-500 text-xs">
                                                                    {col.description || col.default || '-'}
                                                                </td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            ) : (
                                <div className="py-12 text-center text-slate-500">
                                    <Database size={48} className="mx-auto mb-4 opacity-50" />
                                    <p>Click a dataset or table to view its schema</p>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}

            {activeTab === 'query' && (
                <div className="flex-1 flex flex-col overflow-hidden">
                    <div className="flex-1 overflow-hidden">
                        <Editor
                            height="100%"
                            defaultLanguage="sql"
                            value={queryCode}
                            onChange={(value) => setQueryCode(value || '')}
                            theme="vs-dark"
                            options={{
                                minimap: { enabled: false },
                                fontSize: 14,
                                lineNumbers: 'on',
                                scrollBeyondLastLine: false,
                                automaticLayout: true,
                                tabSize: 2,
                            }}
                        />
                    </div>

                    {(queryResults || queryError) && (
                        <div className="h-80 border-t border-white/[0.07] bg-slate-900 overflow-hidden flex flex-col">
                            <div className="px-6 py-3 border-b border-white/[0.07] bg-slate-800">
                                <div className="flex items-center justify-between">
                                    <h3 className="text-sm font-bold text-white">Results</h3>
                                    {queryResults && (
                                        <span className="text-xs text-slate-500">
                                            {queryResults.count || 0} rows × {queryResults.columns?.length || 0} columns
                                        </span>
                                    )}
                                </div>
                            </div>
                            <div className="flex-1 overflow-auto p-6">
                                {queryError ? (
                                    <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4 text-red-400 text-sm">
                                        <strong>Error:</strong> {queryError}
                                    </div>
                                ) : queryResults?.table?.length > 0 ? (
                                    <div className="overflow-x-auto">
                                        <table className="w-full text-sm border-collapse">
                                            <thead>
                                                <tr className="border-b border-white/[0.07]">
                                                    {Object.keys(queryResults.table[0]).map(key => (
                                                        <th key={key} className="text-left px-4 py-2 font-bold text-white bg-slate-800 sticky top-0">
                                                            {key}
                                                        </th>
                                                    ))}
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {queryResults.table.map((row, idx) => (
                                                    <tr key={idx} className="border-b border-white/[0.07]/50 hover:bg-slate-800/50">
                                                        {Object.values(row).map((val, i) => (
                                                            <td key={i} className="px-4 py-2 text-slate-400">
                                                                {val === null ? <span className="text-slate-500 italic">null</span> : String(val)}
                                                            </td>
                                                        ))}
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                ) : queryResults ? (
                                    <div className="text-center text-slate-500 py-8">
                                        No results returned
                                    </div>
                                ) : null}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}
