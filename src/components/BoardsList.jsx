import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../lib/api'
import { useAuth } from '../contexts/AuthContext'
import { useOrg } from '../contexts/OrgContext'
import { useChat } from '../contexts/ChatContext'
import {
    Plus, X, Search, LayoutDashboard, ArrowRight, Clock,
    Sparkles, MessageSquare, BarChart3,
} from 'lucide-react'

function BoardPreview({ boardId }) {
    const [code, setCode] = useState(null)

    useEffect(() => {
        let cancelled = false
        api.boards.getCode(boardId).then(data => {
            if (!cancelled && data?.code) setCode(data.code)
        }).catch(() => {})
        return () => { cancelled = true }
    }, [boardId])

    if (!code) {
        return (
            <div className="w-full h-full flex items-center justify-center bg-[#0a0e1a]">
                <LayoutDashboard className="h-6 w-6 text-slate-700" />
            </div>
        )
    }

    return (
        <iframe
            srcDoc={code}
            className="w-[400%] h-[400%] border-none pointer-events-none"
            style={{ transform: 'scale(0.25)', transformOrigin: 'top left' }}
            title="Board preview"
            sandbox="allow-scripts"
            tabIndex={-1}
        />
    )
}

export default function BoardsList() {
    const navigate = useNavigate()
    const { user } = useAuth()
    const { currentOrg } = useOrg()
    const { setPageContext, openChatFor } = useChat()
    const [boards, setBoards] = useState([])
    const [loading, setLoading] = useState(true)
    const [showCreateModal, setShowCreateModal] = useState(false)
    const [newBoardName, setNewBoardName] = useState('')
    const [newBoardDescription, setNewBoardDescription] = useState('')
    const [search, setSearch] = useState('')
    const [deleteConfirm, setDeleteConfirm] = useState(null)
    const [deleting, setDeleting] = useState(false)

    useEffect(() => {
        setPageContext({ type: 'general' })
        openChatFor(null)
    }, [setPageContext, openChatFor])

    useEffect(() => {
        if (currentOrg?.id) fetchBoards()
    }, [currentOrg?.id])

    const fetchBoards = async () => {
        setLoading(true)
        try {
            const data = await api.boards.list(currentOrg.id)
            setBoards(data || [])
        } catch (error) {
            console.error('Error fetching boards:', error)
        } finally {
            setLoading(false)
        }
    }

    const createBoard = async (e) => {
        e.preventDefault()
        try {
            const data = await api.boards.create({
                name: newBoardName,
                description: newBoardDescription || null,
                organization_id: currentOrg.id,
            })
            setBoards([data, ...boards])
            setShowCreateModal(false)
            setNewBoardName('')
            setNewBoardDescription('')
            navigate(`/board/${data.id}`)
        } catch (error) {
            console.error('Error creating board:', error)
        }
    }

    const handleDelete = async (id) => {
        setDeleting(true)
        try {
            await api.delete(`/boards/${id}`)
            setDeleteConfirm(null)
            await fetchBoards()
        } catch (error) {
            console.error('Error deleting board:', error)
        } finally {
            setDeleting(false)
        }
    }

    const filtered = boards.filter(b =>
        b.name?.toLowerCase().includes(search.toLowerCase()) ||
        b.description?.toLowerCase().includes(search.toLowerCase())
    )

    if (loading) {
        return (
            <div className="flex flex-col items-center justify-center min-h-[60vh] gap-3">
                <div className="spinner" />
                <p className="text-slate-500 text-sm">Loading boards...</p>
            </div>
        )
    }

    return (
        <div className="p-6 lg:p-8 max-w-5xl mx-auto w-full">
            {/* Header */}
            <div className="flex items-start justify-between mb-8 gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-white mb-1">Boards</h1>
                    <p className="text-slate-500 text-sm">
                        Build dashboards with drag-and-drop widgets and live data
                    </p>
                </div>
                <button className="btn btn-primary shrink-0" onClick={() => setShowCreateModal(true)}>
                    <Plus className="h-4 w-4" />
                    New Board
                </button>
            </div>

            {/* Search */}
            {boards.length > 0 && (
                <div className="relative mb-6">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-500" />
                    <input
                        type="text"
                        placeholder="Search boards..."
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
            {boards.length === 0 ? (
                <div className="rounded-xl border border-white/[0.07] bg-white/[0.01] p-12 text-center animate-fade-in">
                    <div className="w-16 h-16 rounded-2xl border border-white/[0.07] bg-white/[0.02] flex items-center justify-center mx-auto mb-5">
                        <LayoutDashboard className="h-7 w-7 text-slate-600" />
                    </div>
                    <h3 className="text-lg font-semibold text-white mb-2">Create your first board</h3>
                    <p className="text-sm text-slate-500 mb-8 max-w-md mx-auto leading-relaxed">
                        Boards are interactive dashboards where you combine widgets, charts, and live data queries. Start with a blank canvas and build something great.
                    </p>
                    <button className="btn btn-primary" onClick={() => setShowCreateModal(true)}>
                        <Plus className="h-4 w-4" />
                        Create Board
                    </button>

                    <div className="mt-10 pt-8 border-t border-white/[0.05] grid grid-cols-1 sm:grid-cols-3 gap-4 max-w-lg mx-auto">
                        {[
                            { icon: MessageSquare, label: 'Chat to build', desc: 'Describe what you want in plain English' },
                            { icon: BarChart3, label: 'Live widgets', desc: 'Charts, KPIs, and tables with real data' },
                            { icon: Sparkles, label: 'AI-powered', desc: 'Gemini generates code and queries for you' },
                        ].map(({ icon: Icon, label, desc }) => (
                            <div key={label} className="text-center">
                                <Icon className="h-4 w-4 text-indigo-400 mx-auto mb-2" />
                                <div className="text-xs font-medium text-slate-300 mb-0.5">{label}</div>
                                <div className="text-[11px] text-slate-600 leading-relaxed">{desc}</div>
                            </div>
                        ))}
                    </div>
                </div>
            ) : filtered.length === 0 ? (
                <div className="text-center py-16">
                    <p className="text-slate-500 text-sm">No boards match your search.</p>
                </div>
            ) : (
                <div className="animate-fade-in">
                    {/* Recent Boards */}
                    {!search && boards.length > 1 && (
                        <div className="mb-8">
                            <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3 flex items-center gap-2">
                                <Clock className="h-3.5 w-3.5" />
                                Recent
                            </h2>
                            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                                {[...boards]
                                    .sort((a, b) => new Date(b.updated_at || b.created_at) - new Date(a.updated_at || a.created_at))
                                    .slice(0, 4)
                                    .map((board) => (
                                    <div
                                        key={board.id}
                                        className="group rounded-xl border border-white/[0.07] bg-white/[0.02] overflow-hidden hover:border-indigo-500/30 hover:bg-white/[0.04] hover:-translate-y-0.5 hover:shadow-lg hover:shadow-black/20 transition-all cursor-pointer"
                                        onClick={() => navigate(`/board/${board.id}`)}
                                    >
                                        <div className="w-full h-28 overflow-hidden bg-[#0a0e1a] border-b border-white/[0.05] relative">
                                            <BoardPreview boardId={board.id} />
                                        </div>
                                        <div className="px-3.5 py-3">
                                            <h3 className="text-xs font-semibold text-white truncate group-hover:text-indigo-300 transition-colors">
                                                {board.name}
                                            </h3>
                                            <p className="text-[10px] text-slate-600 mt-0.5 flex items-center gap-1">
                                                <Clock className="h-2.5 w-2.5" />
                                                {new Date(board.updated_at || board.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                                            </p>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* All Boards */}
                    {!search && boards.length > 1 && (
                        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3 flex items-center gap-2">
                            <LayoutDashboard className="h-3.5 w-3.5" />
                            All Boards
                        </h2>
                    )}
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                        {filtered.map((board) => (
                            <div
                                key={board.id}
                                className="group text-left rounded-xl border border-white/[0.07] bg-white/[0.02] overflow-hidden hover:border-white/[0.12] hover:bg-white/[0.04] hover:-translate-y-0.5 hover:shadow-lg hover:shadow-black/20 transition-all cursor-pointer relative"
                                onClick={() => navigate(`/board/${board.id}`)}
                            >
                                <div className="w-full h-24 overflow-hidden bg-[#0a0e1a] border-b border-white/[0.05]">
                                    <BoardPreview boardId={board.id} />
                                </div>
                                <div className="p-4">
                                    <div className="flex items-start justify-between gap-2 mb-2">
                                        <div className="min-w-0">
                                            <h3 className="text-sm font-semibold text-white group-hover:text-indigo-300 transition-colors leading-snug truncate">
                                                {board.name}
                                            </h3>
                                            {board.description && (
                                                <p className="text-xs text-slate-500 leading-relaxed line-clamp-1 mt-0.5">{board.description}</p>
                                            )}
                                        </div>
                                        <ArrowRight className="h-3.5 w-3.5 text-slate-700 group-hover:text-indigo-400 shrink-0 mt-0.5 transition-colors" />
                                    </div>

                                    <div className="flex items-center justify-between pt-2.5 border-t border-white/[0.06]">
                                        <div className="flex items-center gap-3 text-[11px] text-slate-600">
                                            <span className="flex items-center gap-1">
                                                <Clock className="h-3 w-3" />
                                                {new Date(board.updated_at || board.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                                            </span>
                                        </div>
                                        <button
                                            onClick={(e) => { e.stopPropagation(); setDeleteConfirm(board.id) }}
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
                </div>
            )}

            {/* Create Modal */}
            {showCreateModal && (
                <div className="modal-overlay" onClick={() => setShowCreateModal(false)}>
                    <div className="modal-container max-w-md" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-between mb-5">
                            <h3 className="text-base font-semibold text-white">New Board</h3>
                            <button className="icon-button" onClick={() => setShowCreateModal(false)}>
                                <X className="h-4 w-4" />
                            </button>
                        </div>
                        <form onSubmit={createBoard} className="flex flex-col gap-4">
                            <div className="form-group">
                                <label className="form-label" htmlFor="board-name">Board Name</label>
                                <input
                                    id="board-name"
                                    className="form-input"
                                    type="text"
                                    placeholder="e.g. Sales Dashboard"
                                    value={newBoardName}
                                    onChange={(e) => setNewBoardName(e.target.value)}
                                    required
                                    autoFocus
                                />
                            </div>
                            <div className="form-group">
                                <label className="form-label" htmlFor="board-description">
                                    Description <span className="text-slate-500 font-normal">(optional)</span>
                                </label>
                                <textarea
                                    id="board-description"
                                    className="form-input"
                                    placeholder="What is this board for?"
                                    value={newBoardDescription}
                                    onChange={(e) => setNewBoardDescription(e.target.value)}
                                    rows="2"
                                />
                            </div>
                            <div className="flex gap-3 justify-end mt-2">
                                <button type="button" className="btn btn-ghost" onClick={() => setShowCreateModal(false)}>Cancel</button>
                                <button type="submit" className="btn btn-primary" disabled={!newBoardName.trim()}>
                                    <Plus className="h-3.5 w-3.5" />
                                    Create
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}

            {/* Delete Confirm */}
            {deleteConfirm && (
                <div className="modal-overlay" onClick={() => !deleting && setDeleteConfirm(null)}>
                    <div className="modal-container max-w-sm" onClick={(e) => e.stopPropagation()}>
                        <div className="flex flex-col items-center text-center pt-2 pb-1">
                            <div className="w-12 h-12 rounded-full bg-red-500/10 flex items-center justify-center mb-4">
                                <X className="h-6 w-6 text-red-400" />
                            </div>
                            <h3 className="text-base font-semibold text-white mb-1.5">Delete Board</h3>
                            <p className="text-sm text-slate-400 leading-relaxed">
                                This will permanently remove this board and all its queries. This cannot be undone.
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
