import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { useAuth } from '../contexts/AuthContext'
import { Plus, X } from 'lucide-react'

export default function BoardsList() {
    const navigate = useNavigate()
    const { user } = useAuth()
    const [boards, setBoards] = useState([])
    const [loading, setLoading] = useState(true)
    const [showCreateModal, setShowCreateModal] = useState(false)
    const [newBoardName, setNewBoardName] = useState('')
    const [newBoardDescription, setNewBoardDescription] = useState('')

    useEffect(() => { fetchBoards() }, [])

    const fetchBoards = async () => {
        try {
            const { data, error } = await supabase
                .from('boards')
                .select('*')
                .order('created_at', { ascending: false })

            if (error) throw error
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
            const { data, error } = await supabase
                .from('boards')
                .insert([{ name: newBoardName, description: newBoardDescription, profile_id: user.id }])
                .select()

            if (error) throw error
            setBoards([data[0], ...boards])
            setShowCreateModal(false)
            setNewBoardName('')
            setNewBoardDescription('')
        } catch (error) {
            console.error('Error creating board:', error)
        }
    }

    return (
        <div className="p-6 lg:p-8 max-w-6xl mx-auto">
            <div className="flex items-center justify-between mb-6">
                <h1 className="text-lg font-bold text-white">Boards</h1>
                <button className="btn btn-primary" onClick={() => setShowCreateModal(true)}>
                    <Plus className="h-3.5 w-3.5" />
                    New Board
                </button>
            </div>

            <div className="min-h-[300px]">
                {loading ? (
                    <div className="flex flex-col items-center justify-center min-h-[300px] gap-3">
                        <div className="spinner" />
                        <p className="text-slate-500 text-sm">Loading boards...</p>
                    </div>
                ) : boards.length === 0 ? (
                    <div className="flex flex-col items-center justify-center min-h-[300px] text-center animate-fade-in">
                        <div className="w-14 h-14 flex items-center justify-center rounded-xl border border-white/[0.07] bg-white/[0.02] mb-4 text-slate-600">
                            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                                <rect x="3" y="3" width="18" height="18" rx="2" />
                                <line x1="9" y1="9" x2="15" y2="9" />
                                <line x1="9" y1="15" x2="15" y2="15" />
                            </svg>
                        </div>
                        <h3 className="text-base font-semibold text-white mb-1">No boards yet</h3>
                        <p className="text-slate-500 text-sm mb-5">Create your first board to get started</p>
                        <button className="btn btn-primary" onClick={() => setShowCreateModal(true)}>
                            <Plus className="h-3.5 w-3.5" />
                            Create Board
                        </button>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 animate-fade-in">
                        {boards.map((board) => (
                            <div
                                key={board.id}
                                className="card-interactive flex flex-col"
                                onClick={() => navigate(`/board/${board.id}`)}
                            >
                                <div className="flex items-start justify-between gap-3 mb-2">
                                    <h3 className="text-sm font-semibold text-white flex-1 leading-snug">{board.name}</h3>
                                    <button className="icon-button" onClick={(e) => e.stopPropagation()}>
                                        <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor">
                                            <path d="M10 6a2 2 0 110-4 2 2 0 010 4zM10 12a2 2 0 110-4 2 2 0 010 4zM10 18a2 2 0 110-4 2 2 0 010 4z" />
                                        </svg>
                                    </button>
                                </div>
                                {board.description && (
                                    <p className="text-slate-500 text-xs leading-relaxed mb-3 line-clamp-2">{board.description}</p>
                                )}
                                <div className="mt-auto pt-3 border-t border-white/[0.06]">
                                    <span className="text-[11px] text-slate-600">
                                        {new Date(board.created_at).toLocaleDateString()}
                                    </span>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>

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
                                    placeholder="My Board"
                                    value={newBoardName}
                                    onChange={(e) => setNewBoardName(e.target.value)}
                                    required
                                    autoFocus
                                />
                            </div>
                            <div className="form-group">
                                <label className="form-label" htmlFor="board-description">Description (optional)</label>
                                <textarea
                                    id="board-description"
                                    className="form-input"
                                    placeholder="What is this board for?"
                                    value={newBoardDescription}
                                    onChange={(e) => setNewBoardDescription(e.target.value)}
                                    rows="3"
                                />
                            </div>
                            <div className="flex gap-3 justify-end mt-2">
                                <button type="button" className="btn btn-ghost" onClick={() => setShowCreateModal(false)}>Cancel</button>
                                <button type="submit" className="btn btn-primary">Create</button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    )
}
