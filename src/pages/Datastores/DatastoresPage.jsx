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
        <div className="p-8 max-w-7xl mx-auto space-y-10">
            <header className="flex flex-col md:flex-row md:items-end justify-between gap-6">
                <div className="space-y-2">
                    <h1 className="text-4xl font-bold gradient-text pb-1">Datastores</h1>
                    <p className="text-text-secondary text-lg">Configure and verify your distributed data infrastructure</p>
                </div>

                <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-4">
                    <div className="relative group">
                        <Search size={18} className="absolute left-4 top-1/2 -translate-y-1/2 text-text-muted group-focus-within:text-accent-primary transition-colors" />
                        <input
                            className="form-input pl-11 w-full sm:w-72"
                            placeholder="Identify a node..."
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
                        <Plus size={20} /> Provision Store
                    </button>
                </div>
            </header>

            {loading ? (
                <div className="flex flex-col items-center justify-center py-32 gap-6">
                    <div className="spinner"></div>
                    <p className="text-text-muted font-medium tracking-wide">Scanning ecosystem nodes...</p>
                </div>
            ) : (
                <>
                    {filteredStores.length === 0 ? (
                        <div className="flex flex-col items-center justify-center py-20 bg-background-secondary/50 rounded-3xl border border-dashed border-border-primary animate-fade-in text-center px-6">
                            <div className="w-16 h-16 rounded-full bg-background-tertiary flex items-center justify-center text-text-muted mb-6">
                                <Database size={32} />
                            </div>
                            <h3 className="text-xl font-semibold mb-2 text-text-primary">Infrastructure Empty</h3>
                            <p className="text-text-secondary mb-8 max-w-sm">Connect your first database or warehouse to get started.</p>
                            <button onClick={() => setIsModalOpen(true)} className="btn btn-primary">
                                <Plus size={18} /> Connect Provider
                            </button>
                        </div>
                    ) : (
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 animate-fade-in">
                            {filteredStores.map(store => (
                                <div 
                                    key={store.id} 
                                    className="card-interactive group flex flex-col cursor-pointer"
                                    onClick={() => navigate(`/datastores/${store.id}`)}
                                >
                                    <div className="flex items-center gap-4 mb-6">
                                        <div className="w-12 h-12 flex items-center justify-center bg-background-tertiary border border-border-primary rounded-xl text-accent-primary group-hover:bg-accent-primary/10 transition-all duration-300">
                                            <Database size={24} />
                                        </div>
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2 mb-0.5">
                                                <span className={`text-[0.65rem] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded ${store.type === 'postgres' ? 'bg-indigo-500/10 text-indigo-400' : 'bg-orange-500/10 text-orange-400'}`}>
                                                    {store.type}
                                                </span>
                                                <span className="text-[0.65rem] font-bold uppercase tracking-widest text-text-muted text-opacity-50">Node</span>
                                            </div>
                                            <h3 className="text-xl font-bold text-text-primary truncate leading-tight">{store.name}</h3>
                                        </div>
                                    </div>

                                    <div className="flex-1 mb-8">
                                        <p className="text-text-secondary text-sm leading-relaxed line-clamp-3">
                                            Operational endpoint for high-concurrency {store.type} operations. Configured for secure IAM-based identity access.
                                        </p>
                                    </div>

                                    <div className="flex flex-col gap-4 pt-5 border-t border-border-primary">
                                        <div className="flex items-center justify-between">
                                            <button
                                                onClick={() => testStore(store)}
                                                disabled={testingStoreId === store.id}
                                                className={`text-xs font-bold px-3 py-1.5 rounded-lg transition-all flex items-center gap-2 border ${testingStoreId === store.id
                                                    ? 'bg-background-tertiary text-text-muted border-border-primary cursor-not-allowed'
                                                    : testResults[store.id] === true
                                                        ? 'bg-status-success/10 text-status-success border-status-success/20'
                                                        : testResults[store.id] === false
                                                            ? 'bg-status-error/10 text-status-error border-status-error/20'
                                                            : 'bg-accent-primary/5 text-accent-primary border-accent-primary/10 hover:bg-accent-primary/10 hover:border-accent-primary/30'
                                                    }`}
                                            >
                                                {testingStoreId === store.id ? (
                                                    <>
                                                        <Loader2 size={12} className="animate-spin" />
                                                        <span>Verifying...</span>
                                                    </>
                                                ) : testResults[store.id] === true ? (
                                                    <>
                                                        <CheckCircle2 size={12} />
                                                        <span>Verified</span>
                                                    </>
                                                ) : testResults[store.id] === false ? (
                                                    <>
                                                        <XCircle size={12} />
                                                        <span>Failed</span>
                                                    </>
                                                ) : (
                                                    <>
                                                        <Wifi size={12} />
                                                        <span>Test Connection</span>
                                                    </>
                                                )}
                                            </button>

                                            <div className="flex items-center gap-1">
                                                <button
                                                    className="icon-button"
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        setEditingStore(store);
                                                        setFormType(store.type);
                                                        setIsModalOpen(true);
                                                    }}
                                                    title="Environment Config"
                                                >
                                                    <Settings size={16} />
                                                </button>
                                                <button
                                                    onClick={(e) => deleteStore(store.id, e)}
                                                    className="icon-button hover:bg-status-error/10 hover:text-status-error"
                                                    title="Withdraw Node"
                                                >
                                                    <Trash2 size={16} />
                                                </button>
                                            </div>
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
                    <div className="modal-container max-w-2xl">
                        <div className="flex items-center justify-between mb-8">
                            <h2 className="text-2xl font-bold">{editingStore ? 'Node Configuration' : 'Provision Infrastructure'}</h2>
                            <button
                                onClick={() => { setIsModalOpen(false); setEditingStore(null); }}
                                className="icon-button"
                            >
                                <X size={24} />
                            </button>
                        </div>
                        <form className="space-y-6" onSubmit={async (e) => {
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
                                <label className="form-label">Provider Alias</label>
                                <input name="name" defaultValue={editingStore?.name} className="form-input" placeholder="e.g. Production Warehouse" required />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Compute Engine</label>
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
                                        className="form-input"
                                        placeholder="postgresql://user:pass@host:port/db"
                                        required
                                    />
                                </div>
                            ) : (
                                <div className="space-y-6">
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
                                                className="form-input file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-semibold file:bg-accent-primary/10 file:text-accent-primary hover:file:bg-accent-primary/20"
                                                accept=".json"
                                            />
                                        </div>
                                        {editingStore?.config?.keyfile_path && (
                                            <p className="text-[10px] text-text-muted mt-1 bg-background-tertiary p-2 rounded border border-border-primary truncate">
                                                Existing: {editingStore.config.keyfile_path}
                                            </p>
                                        )}
                                    </div>
                                </div>
                            )}

                            <div className="flex gap-4 justify-end pt-6">
                                <button
                                    type="button"
                                    onClick={() => setIsModalOpen(false)}
                                    className="btn btn-ghost"
                                >
                                    Abort
                                </button>
                                <button
                                    type="submit"
                                    className="btn btn-primary min-w-[180px]"
                                    disabled={isUploading}
                                >
                                    {isUploading ? (
                                        <div className="flex items-center gap-2">
                                            <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"></div>
                                            <span>Committing...</span>
                                        </div>
                                    ) : (
                                        <span>Commit Architecture</span>
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
