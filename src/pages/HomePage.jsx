import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../lib/api'
import { useAuth } from '../contexts/AuthContext'
import { useOrg } from '../contexts/OrgContext'
import { useChat } from '../contexts/ChatContext'
import {
    Plus, LayoutGrid, Database, MessageSquare,
    ArrowRight, Sparkles, Send, Clock, BarChart3, Building2, Puzzle,
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
                <LayoutGrid className="h-6 w-6 text-slate-700" />
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

export default function HomePage() {
    const navigate = useNavigate()
    const { user } = useAuth()
    const { currentOrg, createOrg, setCurrentOrg } = useOrg()
    const [showNewOrg, setShowNewOrg] = useState(false)
    const [newOrgName, setNewOrgName] = useState('')
    const [creatingOrg, setCreatingOrg] = useState(false)
    const { setChatOpen, openChatFor, setPageContext } = useChat()

    useEffect(() => {
        setPageContext({ type: 'general' })
        openChatFor(null)
    }, [setPageContext, openChatFor])

    const [boards, setBoards] = useState([])
    const [widgets, setWidgets] = useState([])
    const [loading, setLoading] = useState(true)
    const [stats, setStats] = useState({ boards: 0, queries: 0, datastores: 0, chats: 0, widgets: 0 })
    const [chatPrompt, setChatPrompt] = useState('')
    const chatInputRef = useRef(null)

    useEffect(() => {
        if (currentOrg?.id) fetchData()
    }, [currentOrg?.id])

    const fetchData = async () => {
        setLoading(true)
        try {
            const [boardsData, statsData, widgetsData] = await Promise.all([
                api.boards.list(currentOrg.id),
                api.stats(currentOrg.id),
                api.widgets.list(currentOrg.id).catch(() => []),
            ])
            setBoards((boardsData || []).slice(0, 6))
            setWidgets((widgetsData || []).slice(0, 6))
            setStats({ ...statsData, widgets: (widgetsData || []).length })
        } catch (error) {
            console.error('Error fetching data:', error)
        } finally {
            setLoading(false)
        }
    }

    const handleChatStart = (e) => {
        e.preventDefault()
        if (!chatPrompt.trim()) return
        if (boards.length > 0) openChatFor(boards[0].id)
        setChatOpen(true)
        setChatPrompt('')
    }

    const firstName = user?.user_metadata?.full_name?.split(' ')[0]
        || user?.email?.split('@')[0]
        || 'there'

    const hour = new Date().getHours()
    const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'

    const statCards = [
        { label: 'Boards', value: stats.boards, icon: LayoutGrid, color: 'text-indigo-400 bg-indigo-500/10 border-indigo-500/15' },
        { label: 'Widgets', value: stats.widgets, icon: Puzzle, color: 'text-pink-400 bg-pink-500/10 border-pink-500/15' },
        { label: 'Datastores', value: stats.datastores, icon: Database, color: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/15' },
        { label: 'Queries', value: stats.queries, icon: BarChart3, color: 'text-purple-400 bg-purple-500/10 border-purple-500/15' },
    ]

    if (loading) {
        return (
            <div className="flex flex-col items-center justify-center min-h-[60vh] gap-3">
                <div className="spinner" />
                <p className="text-slate-500 text-sm">Loading...</p>
            </div>
        )
    }

    return (
        <div className="flex flex-col min-h-full">
            <div className="flex-1 p-6 lg:p-8 max-w-5xl mx-auto w-full">
                {/* Welcome */}
                <div className="mb-8">
                    <h1 className="text-2xl font-bold text-white mb-1">
                        {greeting}, {firstName}
                    </h1>
                    <p className="text-slate-500 text-sm">
                        Ask a question, explore your data, or pick up where you left off.
                    </p>
                </div>

                {/* Stats */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-8">
                    {statCards.map(({ label, value, icon: Icon, color }) => (
                        <div key={label} className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 flex items-center gap-3">
                            <div className={`w-9 h-9 rounded-lg border flex items-center justify-center shrink-0 ${color}`}>
                                <Icon className="h-4 w-4" />
                            </div>
                            <div>
                                <p className="text-lg font-bold text-white leading-none">{value}</p>
                                <p className="text-[11px] text-slate-500 mt-0.5">{label}</p>
                            </div>
                        </div>
                    ))}
                </div>

                {/* Quick actions */}
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 mb-8">
                    <button
                        onClick={() => navigate('/boards')}
                        className="group flex items-center gap-3 rounded-xl border border-dashed border-indigo-500/25 bg-indigo-500/[0.03] p-4 hover:bg-indigo-500/[0.06] hover:border-indigo-500/40 transition-all text-left"
                    >
                        <div className="w-9 h-9 rounded-lg bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center shrink-0 group-hover:bg-indigo-500/20 transition">
                            <Plus className="h-4 w-4 text-indigo-400" />
                        </div>
                        <div>
                            <p className="text-sm font-medium text-white">New Board</p>
                            <p className="text-[11px] text-slate-500">Create a dashboard</p>
                        </div>
                    </button>
                    <button
                        onClick={() => navigate('/datastores')}
                        className="group flex items-center gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 hover:bg-white/[0.04] hover:border-white/[0.12] transition-all text-left"
                    >
                        <div className="w-9 h-9 rounded-lg bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center shrink-0 group-hover:bg-emerald-500/20 transition">
                            <Database className="h-4 w-4 text-emerald-400" />
                        </div>
                        <div>
                            <p className="text-sm font-medium text-white">Data Sources</p>
                            <p className="text-[11px] text-slate-500">Connect a database</p>
                        </div>
                    </button>
                    <button
                        onClick={() => { if (boards.length > 0) openChatFor(boards[0].id); setChatOpen(true) }}
                        className="group flex items-center gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 hover:bg-white/[0.04] hover:border-white/[0.12] transition-all text-left"
                    >
                        <div className="w-9 h-9 rounded-lg bg-purple-500/10 border border-purple-500/20 flex items-center justify-center shrink-0 group-hover:bg-purple-500/20 transition">
                            <Sparkles className="h-4 w-4 text-purple-400" />
                        </div>
                        <div>
                            <p className="text-sm font-medium text-white">Ask AI</p>
                            <p className="text-[11px] text-slate-500">Chat with your data</p>
                        </div>
                    </button>
                    <button
                        onClick={() => navigate('/widgets')}
                        className="group flex items-center gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 hover:bg-white/[0.04] hover:border-white/[0.12] transition-all text-left"
                    >
                        <div className="w-9 h-9 rounded-lg bg-pink-500/10 border border-pink-500/20 flex items-center justify-center shrink-0 group-hover:bg-pink-500/20 transition">
                            <Puzzle className="h-4 w-4 text-pink-400" />
                        </div>
                        <div>
                            <p className="text-sm font-medium text-white">Widgets</p>
                            <p className="text-[11px] text-slate-500">Build embeddable UI</p>
                        </div>
                    </button>
                </div>

                {/* Create org inline form */}
                {showNewOrg && (
                    <div className="mb-8 rounded-xl border border-amber-500/20 bg-amber-500/[0.03] p-5">
                        <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
                            <Building2 className="h-4 w-4 text-amber-400" />
                            Create a new organization
                        </h3>
                        <form
                            onSubmit={async (e) => {
                                e.preventDefault()
                                if (!newOrgName.trim()) return
                                setCreatingOrg(true)
                                try {
                                    const org = await createOrg(newOrgName.trim())
                                    setCurrentOrg(org)
                                    setNewOrgName('')
                                    setShowNewOrg(false)
                                } catch (err) {
                                    console.error('Failed to create org:', err)
                                } finally {
                                    setCreatingOrg(false)
                                }
                            }}
                            className="flex gap-2"
                        >
                            <input
                                autoFocus
                                type="text"
                                value={newOrgName}
                                onChange={(e) => setNewOrgName(e.target.value)}
                                onKeyDown={(e) => e.key === 'Escape' && setShowNewOrg(false)}
                                placeholder="Organization name"
                                className="flex-1 rounded-lg border border-white/[0.1] bg-white/[0.04] px-3 py-2 text-sm text-white placeholder:text-slate-500 focus:border-amber-500/50 focus:outline-none transition"
                            />
                            <button
                                type="submit"
                                disabled={creatingOrg || !newOrgName.trim()}
                                className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-500 active:scale-[0.98] transition disabled:opacity-50"
                            >
                                {creatingOrg ? 'Creating...' : 'Create'}
                            </button>
                            <button
                                type="button"
                                onClick={() => { setShowNewOrg(false); setNewOrgName('') }}
                                className="rounded-lg border border-white/[0.1] px-3 py-2 text-sm text-slate-400 hover:text-white hover:bg-white/[0.05] transition"
                            >
                                Cancel
                            </button>
                        </form>
                    </div>
                )}

                {/* Recent boards */}
                <div className="mb-8">
                    <div className="flex items-center justify-between mb-4">
                        <h2 className="text-sm font-semibold text-slate-400 flex items-center gap-2">
                            <Clock className="h-3.5 w-3.5" />
                            Recent Boards
                        </h2>
                        {boards.length > 0 && (
                            <button
                                onClick={() => navigate('/boards')}
                                className="text-xs text-indigo-400 hover:text-indigo-300 transition flex items-center gap-1"
                            >
                                View all <ArrowRight className="h-3 w-3" />
                            </button>
                        )}
                    </div>

                    {boards.length === 0 ? (
                        <div className="rounded-xl border border-white/[0.07] bg-white/[0.01] p-8 text-center">
                            <div className="w-12 h-12 rounded-xl border border-white/[0.07] bg-white/[0.02] flex items-center justify-center mx-auto mb-3">
                                <LayoutGrid className="h-5 w-5 text-slate-600" />
                            </div>
                            <h3 className="text-sm font-medium text-white mb-1">No boards yet</h3>
                            <p className="text-xs text-slate-500 mb-4">Create your first board to start building dashboards</p>
                            <button className="btn btn-primary text-xs" onClick={() => navigate('/boards')}>
                                <Plus className="h-3.5 w-3.5" />
                                Create Board
                            </button>
                        </div>
                    ) : (
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                            {boards.map((board) => (
                                <button
                                    key={board.id}
                                    onClick={() => navigate(`/board/${board.id}`)}
                                    className="group text-left rounded-xl border border-white/[0.07] bg-white/[0.02] overflow-hidden hover:border-white/[0.12] hover:bg-white/[0.04] hover:-translate-y-0.5 hover:shadow-lg hover:shadow-black/20 transition-all"
                                >
                                    <div className="w-full h-24 overflow-hidden bg-[#0a0e1a] border-b border-white/[0.05]">
                                        <BoardPreview boardId={board.id} />
                                    </div>
                                    <div className="p-4">
                                        <div className="flex items-start justify-between gap-2 mb-1">
                                            <h3 className="text-sm font-semibold text-white group-hover:text-indigo-300 transition-colors leading-snug line-clamp-1">
                                                {board.name}
                                            </h3>
                                            <ArrowRight className="h-3.5 w-3.5 text-slate-700 group-hover:text-indigo-400 shrink-0 mt-0.5 transition-colors" />
                                        </div>
                                        {board.description && (
                                            <p className="text-xs text-slate-500 leading-relaxed line-clamp-1 mb-2">{board.description}</p>
                                        )}
                                        <div className="flex items-center gap-2 text-[11px] text-slate-600">
                                            <Clock className="h-3 w-3" />
                                            {new Date(board.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                                        </div>
                                    </div>
                                </button>
                            ))}
                        </div>
                    )}
                </div>

                {/* Recent widgets */}
                <div className="mb-6">
                    <div className="flex items-center justify-between mb-4">
                        <h2 className="text-sm font-semibold text-slate-400 flex items-center gap-2">
                            <Puzzle className="h-3.5 w-3.5" />
                            Recent Widgets
                        </h2>
                        {widgets.length > 0 && (
                            <button
                                onClick={() => navigate('/widgets')}
                                className="text-xs text-indigo-400 hover:text-indigo-300 transition flex items-center gap-1"
                            >
                                View all <ArrowRight className="h-3 w-3" />
                            </button>
                        )}
                    </div>

                    {widgets.length === 0 ? (
                        <div className="rounded-xl border border-white/[0.07] bg-white/[0.01] p-8 text-center">
                            <div className="w-12 h-12 rounded-xl border border-white/[0.07] bg-white/[0.02] flex items-center justify-center mx-auto mb-3">
                                <Puzzle className="h-5 w-5 text-slate-600" />
                            </div>
                            <h3 className="text-sm font-medium text-white mb-1">No widgets yet</h3>
                            <p className="text-xs text-slate-500 mb-4">Build embeddable components with code and live preview</p>
                            <button className="btn btn-primary text-xs" onClick={() => navigate('/widgets')}>
                                <Plus className="h-3.5 w-3.5" />
                                Create Widget
                            </button>
                        </div>
                    ) : (
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                            {widgets.map((widget) => (
                                <button
                                    key={widget.id}
                                    onClick={() => navigate(`/widgets/${widget.id}`)}
                                    className="group text-left rounded-xl border border-white/[0.07] bg-white/[0.02] overflow-hidden hover:border-white/[0.12] hover:bg-white/[0.04] hover:-translate-y-0.5 hover:shadow-lg hover:shadow-black/20 transition-all"
                                >
                                    <div className="w-full h-24 overflow-hidden bg-[#0a0e1a] border-b border-white/[0.05]">
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
                                        <div className="flex items-start justify-between gap-2 mb-1">
                                            <h3 className="text-sm font-semibold text-white group-hover:text-pink-300 transition-colors leading-snug line-clamp-1">
                                                {widget.name}
                                            </h3>
                                            <ArrowRight className="h-3.5 w-3.5 text-slate-700 group-hover:text-pink-400 shrink-0 mt-0.5 transition-colors" />
                                        </div>
                                        <div className="flex items-center gap-2 text-[11px] text-slate-600">
                                            <Clock className="h-3 w-3" />
                                            {new Date(widget.updated_at || widget.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                                        </div>
                                    </div>
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            </div>

            {/* Bottom chat input */}
            <div className="sticky bottom-0 border-t border-white/[0.07] bg-slate-950/90 backdrop-blur-xl px-6 lg:px-8 py-4">
                <form onSubmit={handleChatStart} className="max-w-5xl mx-auto">
                    <div className="flex items-center gap-3 rounded-xl border border-white/[0.08] bg-white/[0.03] pl-4 pr-2 py-2 transition-all focus-within:border-indigo-500/40 focus-within:bg-white/[0.04] focus-within:shadow-lg focus-within:shadow-indigo-500/5">
                        <Sparkles className="h-4 w-4 text-indigo-400 shrink-0" />
                        <input
                            ref={chatInputRef}
                            type="text"
                            value={chatPrompt}
                            onChange={(e) => setChatPrompt(e.target.value)}
                            placeholder="Ask anything about your data..."
                            className="flex-1 bg-transparent text-sm text-white placeholder:text-slate-500 outline-none"
                        />
                        <button
                            type="submit"
                            className="p-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 active:scale-95 transition shadow-lg shadow-indigo-900/30 disabled:opacity-50 shrink-0"
                            disabled={!chatPrompt.trim()}
                        >
                            <Send className="h-3.5 w-3.5" />
                        </button>
                    </div>
                    <p className="text-[11px] text-slate-600 mt-2 text-center">
                        Press Enter to open AI chat &middot; Ask questions in plain English
                    </p>
                </form>
            </div>
        </div>
    )
}
