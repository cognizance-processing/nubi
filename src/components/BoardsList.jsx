import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { useAuth } from '../contexts/AuthContext'
export default function BoardsList() {
    const navigate = useNavigate()
    const { user } = useAuth()
    const [boards, setBoards] = useState([])
    const [loading, setLoading] = useState(true)
    const [showCreateModal, setShowCreateModal] = useState(false)
    const [newBoardName, setNewBoardName] = useState('')
    const [newBoardDescription, setNewBoardDescription] = useState('')

    useEffect(() => {
        fetchBoards()
    }, [])

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
                .insert([
                    {
                        name: newBoardName,
                        description: newBoardDescription,
                        profile_id: user.id,
                    },
                ])
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
        <div className="p-8 max-w-7xl mx-auto">
            {/* Page Header */}
            <div className="flex items-center justify-between mb-10">
                <h1 className="text-3xl font-bold text-text-primary">Boards</h1>
                <button
                    className="btn btn-primary"
                    onClick={() => setShowCreateModal(true)}
                >
                    <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
                        <path fillRule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" />
                    </svg>
                    Create Board
                </button>
            </div>

            {/* Boards Content */}
            <div className="min-h-[400px]">
                {loading ? (
                    <div className="flex flex-col items-center justify-center min-h-[400px] gap-6">
                        <div className="spinner"></div>
                        <p className="text-text-secondary">Loading boards...</p>
                    </div>
                ) : boards.length === 0 ? (
                    <div className="flex flex-col items-center justify-center min-h-[400px] text-center animate-fade-in">
                        <div className="w-20 h-20 flex items-center justify-center bg-background-tertiary border border-border-primary rounded-2xl mb-6 text-text-muted">
                            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                                <rect x="3" y="3" width="18" height="18" rx="2" />
                                <line x1="9" y1="9" x2="15" y2="9" />
                                <line x1="9" y1="15" x2="15" y2="15" />
                            </svg>
                        </div>
                        <h3 className="text-2xl font-semibold mb-2">No boards yet</h3>
                        <p className="text-text-secondary mb-8">Create your first board to get started</p>
                        <button
                            className="btn btn-primary"
                            onClick={() => setShowCreateModal(true)}
                        >
                            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
                                <path fillRule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" />
                            </svg>
                            Create Board
                        </button>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 animate-fade-in">
                        {boards.map((board) => (
                            <div
                                key={board.id}
                                className="card-interactive flex flex-col"
                                onClick={() => navigate(`/board/${board.id}`)}
                            >
                                <div className="flex items-start justify-between gap-4 mb-4">
                                    <h3 className="text-xl font-semibold text-text-primary flex-1">{board.name}</h3>
                                    <button className="icon-button" onClick={(e) => e.stopPropagation()}>
                                        <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
                                            <path d="M10 6a2 2 0 110-4 2 2 0 010 4zM10 12a2 2 0 110-4 2 2 0 010 4zM10 18a2 2 0 110-4 2 2 0 010 4z" />
                                        </svg>
                                    </button>
                                </div>
                                {board.description && (
                                    <p className="text-text-secondary text-sm leading-relaxed mb-6 line-clamp-2">{board.description}</p>
                                )}
                                <div className="mt-auto pt-4 border-t border-border-primary">
                                    <span className="text-[0.75rem] text-text-muted">
                                        {new Date(board.created_at).toLocaleDateString()}
                                    </span>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* Create Board Modal */}
            {showCreateModal && (
                <div className="modal-overlay" onClick={() => setShowCreateModal(false)}>
                    <div className="modal-container" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-between mb-8">
                            <h3 className="text-2xl font-semibold">Create New Board</h3>
                            <button
                                className="icon-button"
                                onClick={() => setShowCreateModal(false)}
                            >
                                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <line x1="18" y1="6" x2="6" y2="18" />
                                    <line x1="6" y1="6" x2="18" y2="18" />
                                </svg>
                            </button>
                        </div>

                        <form onSubmit={createBoard} className="flex flex-col gap-6">
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

                            <div className="flex gap-4 justify-end mt-4">
                                <button
                                    type="button"
                                    className="btn btn-secondary"
                                    onClick={() => setShowCreateModal(false)}
                                >
                                    Cancel
                                </button>
                                <button type="submit" className="btn btn-primary">
                                    Create Board
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    )
}
