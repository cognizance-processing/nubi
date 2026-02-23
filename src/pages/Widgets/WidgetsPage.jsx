import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../../lib/api'
import { useOrg } from '../../contexts/OrgContext'
import templates from './widgetTemplates'
import {
    Plus, Puzzle, Globe, Lock, Clock, ArrowRight,
    Search, X, BookOpen, ChevronDown,
} from 'lucide-react'

export default function WidgetsPage() {
    const navigate = useNavigate()
    const { currentOrg } = useOrg()
    const [widgets, setWidgets] = useState([])
    const [loading, setLoading] = useState(true)
    const [createName, setCreateName] = useState('')
    const [creating, setCreating] = useState(false)
    const [showCreate, setShowCreate] = useState(false)
    const [search, setSearch] = useState('')
    const [deleteConfirm, setDeleteConfirm] = useState(null)
    const [deleting, setDeleting] = useState(false)
    const [showTemplates, setShowTemplates] = useState(false)
    const [creatingFromTemplate, setCreatingFromTemplate] = useState(null)

    useEffect(() => { fetchWidgets() }, [currentOrg?.id])

    const fetchWidgets = async () => {
        setLoading(true)
        try {
            const data = await api.widgets.list(currentOrg?.id)
            setWidgets(data || [])
        } catch (err) {
            console.error('Error fetching widgets:', err)
        } finally {
            setLoading(false)
        }
    }

    const handleCreate = async (e) => {
        e.preventDefault()
        if (!createName.trim() || creating) return
        setCreating(true)
        try {
            const widget = await api.widgets.create({ name: createName.trim(), organization_id: currentOrg?.id })
            setShowCreate(false)
            setCreateName('')
            navigate(`/widgets/${widget.id}`)
        } catch (err) {
            console.error('Error creating widget:', err)
        } finally {
            setCreating(false)
        }
    }

    const createFromTemplate = async (tmpl) => {
        setCreatingFromTemplate(tmpl.id)
        try {
            const widget = await api.widgets.create({ name: tmpl.name, html_code: tmpl.code, organization_id: currentOrg?.id })
            navigate(`/widgets/${widget.id}`)
        } catch (err) {
            console.error('Error creating from template:', err)
        } finally {
            setCreatingFromTemplate(null)
        }
    }

    const handleDelete = async (id) => {
        setDeleting(true)
        try {
            await api.widgets.delete(id)
            setDeleteConfirm(null)
            await fetchWidgets()
        } catch (err) {
            console.error('Error deleting widget:', err)
        } finally {
            setDeleting(false)
        }
    }

    const filtered = widgets.filter(w =>
        w.name?.toLowerCase().includes(search.toLowerCase()) ||
        w.description?.toLowerCase().includes(search.toLowerCase())
    )

    if (loading) {
        return (
            <div className="flex flex-col items-center justify-center min-h-[60vh] gap-3">
                <div className="spinner" />
                <p className="text-slate-500 text-sm">Loading widgets...</p>
            </div>
        )
    }

    return (
        <div className="p-6 lg:p-8 max-w-5xl mx-auto w-full">
            {/* Header */}
            <div className="flex items-start justify-between mb-8 gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-white mb-1">Widgets</h1>
                    <p className="text-slate-500 text-sm">
                        Build embeddable components with live preview
                    </p>
                </div>
                <button onClick={() => setShowCreate(true)} className="btn btn-primary shrink-0">
                    <Plus className="h-4 w-4" />
                    New Widget
                </button>
            </div>

            {/* Search */}
            {widgets.length > 0 && (
                <div className="relative mb-6">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-500" />
                    <input
                        type="text"
                        placeholder="Search widgets..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="w-full pl-10 pr-4 py-2.5 rounded-xl border border-white/[0.08] bg-white/[0.02] text-sm text-slate-200 placeholder:text-slate-600 focus:border-indigo-500/50 focus:outline-none transition"
                    />
                    {search && (
                        <button onClick={() => setSearch('')} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-white transition">
                            <X className="h-4 w-4" />
                        </button>
                    )}
                </div>
            )}

            {/* Empty state */}
            {widgets.length === 0 ? (
                <div className="rounded-xl border border-white/[0.07] bg-white/[0.01] p-12 text-center">
                    <div className="w-16 h-16 rounded-2xl border border-white/[0.07] bg-white/[0.02] flex items-center justify-center mx-auto mb-4">
                        <Puzzle className="h-7 w-7 text-slate-600" />
                    </div>
                    <h3 className="text-base font-semibold text-white mb-1.5">No widgets yet</h3>
                    <p className="text-sm text-slate-500 mb-6 max-w-sm mx-auto">
                        Create your first widget to start building embeddable components with code and live preview.
                    </p>
                    <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
                        <Plus className="h-4 w-4" />
                        Create Widget
                    </button>
                </div>
            ) : filtered.length === 0 ? (
                <div className="text-center py-16">
                    <p className="text-slate-500 text-sm">No widgets match your search.</p>
                </div>
            ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 animate-fade-in">
                    {filtered.map((widget) => (
                        <div
                            key={widget.id}
                            onClick={() => navigate(`/widgets/${widget.id}`)}
                            className="group text-left rounded-xl border border-white/[0.07] bg-white/[0.02] overflow-hidden hover:border-white/[0.12] hover:bg-white/[0.04] hover:-translate-y-0.5 hover:shadow-lg hover:shadow-black/20 transition-all cursor-pointer relative"
                        >
                            {/* Preview thumbnail */}
                            <div className="w-full h-28 overflow-hidden bg-[#0a0e1a] border-b border-white/[0.05]">
                                {widget.html_code ? (
                                    <iframe
                                        srcDoc={widget.html_code}
                                        className="w-[400%] h-[400%] border-none pointer-events-none"
                                        style={{ transform: 'scale(0.25)', transformOrigin: 'top left' }}
                                        title={widget.name}
                                        sandbox="allow-scripts"
                                        tabIndex={-1}
                                    />
                                ) : (
                                    <div className="w-full h-full flex items-center justify-center">
                                        <Puzzle className="h-6 w-6 text-slate-700" />
                                    </div>
                                )}
                            </div>

                            <div className="p-4">
                                <div className="flex items-start justify-between gap-2 mb-2">
                                    <div className="min-w-0">
                                        <h3 className="text-sm font-semibold text-white group-hover:text-indigo-300 transition-colors leading-snug truncate">
                                            {widget.name}
                                        </h3>
                                        {widget.description && (
                                            <p className="text-xs text-slate-500 leading-relaxed line-clamp-1 mt-0.5">{widget.description}</p>
                                        )}
                                    </div>
                                    <ArrowRight className="h-3.5 w-3.5 text-slate-700 group-hover:text-indigo-400 shrink-0 mt-0.5 transition-colors" />
                                </div>

                                <div className="flex items-center justify-between pt-2.5 border-t border-white/[0.06]">
                                    <div className="flex items-center gap-3 text-[11px] text-slate-600">
                                        <span className="flex items-center gap-1">
                                            {widget.is_public ? (
                                                <><Globe className="h-3 w-3 text-emerald-500" /> <span className="text-emerald-500/80">Public</span></>
                                            ) : (
                                                <><Lock className="h-3 w-3" /> Private</>
                                            )}
                                        </span>
                                        <span className="flex items-center gap-1">
                                            <Clock className="h-3 w-3" />
                                            {new Date(widget.updated_at || widget.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                                        </span>
                                    </div>
                                    <button
                                        onClick={(e) => { e.stopPropagation(); setDeleteConfirm(widget.id) }}
                                        className="p-1 rounded-md text-slate-600 opacity-0 group-hover:opacity-100 hover:bg-red-500/10 hover:text-red-400 transition-all"
                                        title="Delete"
                                    >
                                        <X className="h-3.5 w-3.5" />
                                    </button>
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            )}

            {/* Inline create */}
            {showCreate && (
                <div className="mb-6 rounded-xl border border-indigo-500/20 bg-indigo-500/[0.03] p-4 animate-fade-in">
                    <form onSubmit={handleCreate} className="flex gap-2">
                        <input
                            autoFocus
                            type="text"
                            value={createName}
                            onChange={(e) => setCreateName(e.target.value)}
                            onKeyDown={(e) => e.key === 'Escape' && (setShowCreate(false), setCreateName(''))}
                            placeholder="Widget name..."
                            className="flex-1 rounded-lg border border-white/[0.1] bg-white/[0.04] px-3 py-2 text-sm text-white placeholder:text-slate-500 focus:border-indigo-500/50 focus:outline-none transition"
                        />
                        <button
                            type="submit"
                            disabled={creating || !createName.trim()}
                            className="btn btn-primary px-4"
                        >
                            {creating ? (
                                <span className="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                            ) : (
                                <Plus className="h-4 w-4" />
                            )}
                            Create
                        </button>
                        <button
                            type="button"
                            onClick={() => { setShowCreate(false); setCreateName('') }}
                            className="btn btn-ghost text-slate-400"
                        >
                            Cancel
                        </button>
                    </form>
                </div>
            )}

            {/* Templates section */}
            <div className="mt-10 mb-6">
                <button
                    onClick={() => setShowTemplates(v => !v)}
                    className="flex items-center gap-2 text-sm font-semibold text-slate-400 hover:text-white transition mb-4 group"
                >
                    <BookOpen className="h-4 w-4" />
                    Start from a template
                    <ChevronDown className={`h-3.5 w-3.5 text-slate-600 group-hover:text-slate-400 transition-transform ${showTemplates ? 'rotate-180' : ''}`} />
                </button>

                {showTemplates && (
                    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3 animate-fade-in">
                        {templates.map((tmpl) => (
                            <button
                                key={tmpl.id}
                                onClick={() => createFromTemplate(tmpl)}
                                disabled={creatingFromTemplate === tmpl.id}
                                className="group/t text-left rounded-xl border border-white/[0.06] bg-white/[0.015] overflow-hidden hover:border-indigo-500/30 hover:bg-indigo-500/[0.04] transition-all relative"
                            >
                                {/* Preview thumbnail */}
                                <div className="w-full h-24 overflow-hidden bg-[#0a0e1a] border-b border-white/[0.04]">
                                    <iframe
                                        srcDoc={tmpl.code}
                                        className="w-[400%] h-[400%] border-none pointer-events-none"
                                        style={{ transform: 'scale(0.25)', transformOrigin: 'top left' }}
                                        title={tmpl.name}
                                        sandbox="allow-scripts"
                                        tabIndex={-1}
                                    />
                                </div>
                                <div className="p-3">
                                    <div className="flex items-center gap-2 mb-1.5">
                                        <div className={`w-6 h-6 rounded-md border flex items-center justify-center text-xs shrink-0 ${tmpl.color}`}>
                                            {tmpl.icon}
                                        </div>
                                        <h4 className="text-xs font-semibold text-white group-hover/t:text-indigo-300 transition-colors truncate leading-tight">
                                            {tmpl.name}
                                        </h4>
                                    </div>
                                    <p className="text-[10px] text-slate-500 leading-relaxed line-clamp-2">
                                        {tmpl.description}
                                    </p>
                                </div>
                                {creatingFromTemplate === tmpl.id && (
                                    <div className="absolute inset-0 flex items-center justify-center bg-slate-950/60 rounded-xl">
                                        <span className="inline-block w-4 h-4 border-2 border-white/20 border-t-indigo-400 rounded-full animate-spin" />
                                    </div>
                                )}
                            </button>
                        ))}
                    </div>
                )}
            </div>

            {/* Delete Confirm */}
            {deleteConfirm && (
                <div className="modal-overlay" onClick={() => !deleting && setDeleteConfirm(null)}>
                    <div className="modal-container max-w-sm" onClick={(e) => e.stopPropagation()}>
                        <div className="flex flex-col items-center text-center pt-2 pb-1">
                            <div className="w-12 h-12 rounded-full bg-red-500/10 flex items-center justify-center mb-4">
                                <X className="h-6 w-6 text-red-400" />
                            </div>
                            <h3 className="text-base font-semibold text-white mb-1.5">Delete Widget</h3>
                            <p className="text-sm text-slate-400 leading-relaxed">
                                This will permanently remove this widget. This cannot be undone.
                            </p>
                        </div>
                        <div className="flex items-center gap-3 pt-5">
                            <button onClick={() => setDeleteConfirm(null)} disabled={deleting} className="btn btn-secondary flex-1 py-2.5 h-auto text-sm">
                                Cancel
                            </button>
                            <button
                                onClick={() => handleDelete(deleteConfirm)}
                                disabled={deleting}
                                className="flex-1 py-2.5 h-auto text-sm font-medium rounded-lg bg-red-500 hover:bg-red-500/90 text-white transition flex items-center justify-center gap-2 disabled:opacity-50"
                            >
                                {deleting && <span className="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />}
                                Delete
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}
