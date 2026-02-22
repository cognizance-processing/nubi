import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { supabase } from '../../lib/supabase'
import {
    Plus,
    Database,
    Trash2,
    Settings,
    CheckCircle2,
    XCircle,
    Loader2,
    Search,
    Wifi,
    X
} from 'lucide-react'
export default function DatastoresPage() {
    const navigate = useNavigate()
    const [loading, setLoading] = useState(true)
    const [datastores, setDatastores] = useState([])
    const [searchQuery, setSearchQuery] = useState('')
    const [isModalOpen, setIsModalOpen] = useState(false)
    const [editingStore, setEditingStore] = useState(null)
    const [testingStoreId, setTestingStoreId] = useState(null)
    const [testResults, setTestResults] = useState({})
    const [formType, setFormType] = useState('postgres')
    const [isUploading, setIsUploading] = useState(false)

    useEffect(() => {
        fetchStores()
    }, [])

    async function fetchStores() {
        setLoading(true)
        const { data } = await supabase.from('datastores').select('*').order('created_at', { ascending: false })
        if (data) setDatastores(data)
        setLoading(false)
    }

    const filteredStores = datastores.filter(s =>
        s.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        s.type.toLowerCase().includes(searchQuery.toLowerCase())
    )

    async function testStore(store) {
        setTestingStoreId(store.id)
        try {
            const res = await fetch('http://localhost:8000/test-datastore', {
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
        if (!confirm('Delete this infrastructure node? Any dependent pipelines might fail.')) return
        await supabase.from('datastores').delete().eq('id', id)
        setDatastores(datastores.filter(s => s.id !== id))
    }

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
                        <input
                            className="form-input pl-8 w-full sm:w-56"
                            placeholder="Search..."
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                        />
                    </div>
                    <button
                        onClick={() => {
                            setEditingStore(null);
                            setFormType('postgres');
                            setIsModalOpen(true);
                        }}
                        className="btn btn-primary"
                    >
                        <Plus size={15} /> Add Store
                    </button>
                </div>
            </header>

            {loading ? (
                <div className="flex flex-col items-center justify-center py-20 gap-3">
                    <div className="spinner"></div>
                    <p className="text-slate-500 text-xs font-medium">Loading datastores...</p>
                </div>
            ) : (
                <>
                    {filteredStores.length === 0 ? (
                        <div className="flex flex-col items-center justify-center py-16 bg-slate-900/50 rounded-xl border border-dashed border-white/[0.07] animate-fade-in text-center px-6">
                            <div className="w-11 h-11 rounded-lg bg-slate-800 flex items-center justify-center text-slate-500 mb-4">
                                <Database size={22} />
                            </div>
                            <h3 className="text-sm font-semibold mb-1 text-white">No datastores yet</h3>
                            <p className="text-slate-400 text-xs mb-5 max-w-sm">Connect your first database or warehouse to get started.</p>
                            <button onClick={() => setIsModalOpen(true)} className="btn btn-primary">
                                <Plus size={15} /> Add Store
                            </button>
                        </div>
                    ) : (
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 animate-fade-in">
                            {filteredStores.map(store => (
                                <div 
                                    key={store.id} 
                                    className="card-interactive group flex flex-col cursor-pointer"
                                    onClick={() => navigate(`/datastores/${store.id}`)}
                                >
                                    <div className="flex items-center gap-3 mb-3">
                                        <div className="w-9 h-9 flex items-center justify-center bg-slate-800 border border-white/[0.07] rounded-lg text-indigo-400 group-hover:bg-indigo-500/10 transition-all duration-200">
                                            <Database size={18} />
                                        </div>
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-1.5 mb-0.5">
                                                <span className={`text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded ${store.type === 'postgres' ? 'bg-indigo-500/10 text-indigo-400' : 'bg-orange-500/10 text-orange-400'}`}>
                                                    {store.type}
                                                </span>
                                            </div>
                                            <h3 className="text-sm font-semibold text-white truncate leading-tight">{store.name}</h3>
                                        </div>
                                    </div>

                                    <div className="flex items-center justify-between pt-3 border-t border-white/[0.07]">
                                        <button
                                            onClick={(e) => { e.stopPropagation(); testStore(store) }}
                                            disabled={testingStoreId === store.id}
                                            className={`text-[11px] font-medium px-2 py-1 rounded-md transition-all flex items-center gap-1.5 border ${testingStoreId === store.id
                                                ? 'bg-slate-800 text-slate-500 border-white/[0.07] cursor-not-allowed'
                                                : testResults[store.id] === true
                                                    ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                                                    : testResults[store.id] === false
                                                        ? 'bg-red-500/10 text-red-400 border-red-500/20'
                                                        : 'bg-indigo-500/5 text-indigo-400 border-indigo-500/10 hover:bg-indigo-500/10'
                                                }`}
                                        >
                                            {testingStoreId === store.id ? (
                                                <>
                                                    <Loader2 size={11} className="animate-spin" />
                                                    <span>Testing...</span>
                                                </>
                                            ) : testResults[store.id] === true ? (
                                                <>
                                                    <CheckCircle2 size={11} />
                                                    <span>Connected</span>
                                                </>
                                            ) : testResults[store.id] === false ? (
                                                <>
                                                    <XCircle size={11} />
                                                    <span>Failed</span>
                                                </>
                                            ) : (
                                                <>
                                                    <Wifi size={11} />
                                                    <span>Test</span>
                                                </>
                                            )}
                                        </button>

                                        <div className="flex items-center gap-0.5">
                                            <button
                                                className="icon-button"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    setEditingStore(store);
                                                    setFormType(store.type);
                                                    setIsModalOpen(true);
                                                }}
                                                title="Settings"
                                            >
                                                <Settings size={14} />
                                            </button>
                                            <button
                                                onClick={(e) => deleteStore(store.id, e)}
                                                className="icon-button hover:bg-red-500/10 hover:text-red-400"
                                                title="Delete"
                                            >
                                                <Trash2 size={14} />
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </>
            )}

            {isModalOpen && (
                <div className="modal-overlay">
                    <div className="modal-container max-w-lg">
                        <div className="flex items-center justify-between mb-5">
                            <h2 className="text-base font-semibold">{editingStore ? 'Edit Datastore' : 'Add Datastore'}</h2>
                            <button
                                onClick={() => { setIsModalOpen(false); setEditingStore(null); }}
                                className="icon-button"
                            >
                                <X size={18} />
                            </button>
                        </div>
                        <form className="space-y-4" onSubmit={async (e) => {
                            e.preventDefault()
                            setIsUploading(true)
                            try {
                                const { user } = (await supabase.auth.getUser()).data
                                const fd = new FormData(e.target)
                                const type = fd.get('type')

                                let config = {}
                                if (type === 'postgres') {
                                    config = { connection_string: fd.get('connection_string') }
                                } else {
                                    config = { project_id: fd.get('project_id'), keyfile_path: editingStore?.config?.keyfile_path }

                                    const keyfile = fd.get('keyfile')
                                    if (keyfile && keyfile.size > 0) {
                                        const fileExt = 'json'
                                        const fileName = `${user.id}/${Math.random()}.${fileExt}`
                                        const { data: uploadData, error: uploadError } = await supabase.storage
                                            .from('secret-files')
                                            .upload(fileName, keyfile)

                                        if (uploadError) throw uploadError
                                        config.keyfile_path = uploadData.path
                                    }
                                }

                                const payload = {
                                    name: fd.get('name'),
                                    type: type,
                                    config: config,
                                    user_id: user.id
                                }

                                if (editingStore) {
                                    await supabase.from('datastores').update(payload).eq('id', editingStore.id)
                                } else {
                                    await supabase.from('datastores').insert([payload])
                                }

                                setIsModalOpen(false)
                                setEditingStore(null)
                                fetchStores()
                            } catch (err) {
                                alert(`Failed to save datastore: ${err.message}`)
                            } finally {
                                setIsUploading(false)
                            }
                        }}>
                            <div className="form-group">
                                <label className="form-label">Name</label>
                                <input name="name" defaultValue={editingStore?.name} className="form-input" placeholder="e.g. Production DB" required />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Type</label>
                                <select
                                    name="type"
                                    defaultValue={editingStore?.type || 'postgres'}
                                    className="form-input"
                                    onChange={(e) => setFormType(e.target.value)}
                                >
                                    <option value="postgres">PostgreSQL (Unified)</option>
                                    <option value="bigquery">Google BigQuery (Serverless)</option>
                                </select>
                            </div>

                            {formType === 'postgres' ? (
                                <div className="form-group">
                                    <label className="form-label">Connection String</label>
                                    <input
                                        name="connection_string"
                                        defaultValue={editingStore?.config?.connection_string}
                                        className="form-input font-mono text-xs"
                                        placeholder="postgresql://user:pass@host:port/db"
                                        required
                                    />
                                </div>
                            ) : (
                                <div className="space-y-4">
                                    <div className="form-group">
                                        <label className="form-label">Project ID</label>
                                        <input
                                            name="project_id"
                                            defaultValue={editingStore?.config?.project_id}
                                            className="form-input"
                                            placeholder="my-gcp-project-123"
                                            required
                                        />
                                    </div>
                                    <div className="form-group">
                                        <label className="form-label">Service Account JSON (Keyfile)</label>
                                        <div className="relative">
                                            <input
                                                type="file"
                                                name="keyfile"
                                                className="form-input file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-semibold file:bg-indigo-500/10 file:text-indigo-400 hover:file:bg-indigo-500/20"
                                                accept=".json"
                                            />
                                        </div>
                                        {editingStore?.config?.keyfile_path && (
                                            <p className="text-[10px] text-slate-500 mt-1 bg-slate-800 p-2 rounded border border-white/[0.07] truncate">
                                                Existing: {editingStore.config.keyfile_path}
                                            </p>
                                        )}
                                    </div>
                                </div>
                            )}

                            <div className="flex gap-3 justify-end pt-3">
                                <button
                                    type="button"
                                    onClick={() => setIsModalOpen(false)}
                                    className="btn btn-ghost"
                                >
                                    Cancel
                                </button>
                                <button
                                    type="submit"
                                    className="btn btn-primary"
                                    disabled={isUploading}
                                >
                                    {isUploading ? (
                                        <div className="flex items-center gap-1.5">
                                            <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin"></div>
                                            <span>Saving...</span>
                                        </div>
                                    ) : (
                                        <span>Save</span>
                                    )}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    )
}
