import { useState, useEffect, useRef } from 'react'
import { Link, useLocation, Outlet } from 'react-router-dom'
import { Database } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import { useHeader } from '../contexts/HeaderContext'
import { ChatProvider, useChat } from '../contexts/ChatContext'
import ChatMessage from '../components/ChatMessage'

function MainLayoutContent() {
    const { user, signOut } = useAuth()
    const { headerContent } = useHeader()
    const [sidebarOpen, setSidebarOpen] = useState(true)
    const location = useLocation()
    const messagesEndRef = useRef(null)
    const textareaRef = useRef(null)
    
    const {
        chatOpen,
        setChatOpen,
        chatList,
        currentChatId,
        setCurrentChatId,
        chatMessages,
        chatInput,
        setChatInput,
        chatLoading,
        showChatListDropdown,
        setShowChatListDropdown,
        editingChatId,
        setEditingChatId,
        editingChatTitle,
        setEditingChatTitle,
        startNewChat,
        renameChat,
        formatChatDate,
        handleChatSubmit,
        // @mention support
        showMentionDropdown,
        setShowMentionDropdown,
        mentionOptions,
        insertMention,
        handleChatInputChange,
        // /command support
        showCommandDropdown,
        setShowCommandDropdown,
        commandOptions,
        insertCommand,
    } = useChat()

    // Auto-scroll to bottom when new messages arrive
    useEffect(() => {
        if (messagesEndRef.current) {
            messagesEndRef.current.scrollIntoView({ behavior: 'smooth' })
        }
    }, [chatMessages])

    // Auto-resize textarea as user types
    useEffect(() => {
        if (textareaRef.current) {
            textareaRef.current.style.height = 'auto'
            textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 150) + 'px'
        }
    }, [chatInput])

    return (
        <div className="flex h-screen bg-background-primary overflow-hidden">
            {/* Sidebar */}
            <aside className={`bg-background-secondary border-r border-border-primary flex flex-col transition-all duration-300 ease-in-out relative z-10 h-screen overflow-hidden ${sidebarOpen ? 'w-[260px]' : 'w-[72px]'} md:translate-x-0 ${!sidebarOpen ? '-translate-x-full md:translate-x-0' : 'translate-x-0'}`}>
                <div className={`h-[72px] flex items-center border-b border-border-primary flex-shrink-0 transition-all duration-300 ${sidebarOpen ? 'px-4' : 'px-[15px]'}`}>
                    <div className="flex items-center gap-4 w-full">
                        <div className="w-10 h-10 flex items-center justify-center bg-background-tertiary border border-border-primary rounded-lg p-2 flex-shrink-0">
                            <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                                <path d="M12 2L2 7L12 12L22 7L12 2Z" fill="url(#gradient)" />
                                <path d="M2 17L12 22L22 17" stroke="url(#gradient)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                                <path d="M2 12L12 17L22 12" stroke="url(#gradient)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                                <defs>
                                    <linearGradient id="gradient" x1="2" y1="2" x2="22" y2="22">
                                        <stop offset="0%" stopColor="#6366f1" />
                                        <stop offset="100%" stopColor="#8b5cf6" />
                                    </linearGradient>
                                </defs>
                            </svg>
                        </div>
                        <h1 className={`text-xl font-bold gradient-text whitespace-nowrap transition-all duration-200 ${sidebarOpen ? 'opacity-100 scale-100' : 'opacity-0 scale-95 pointer-events-none'}`}>Nubi</h1>
                    </div>
                </div>

                <nav className={`flex-1 flex flex-col gap-1 overflow-y-auto overflow-x-hidden transition-all duration-300 py-4 ${sidebarOpen ? 'px-2' : 'px-[15px]'}`}>
                    <Link to="/portal" className={`flex items-center min-h-[44px] px-3 rounded-lg transition-all duration-200 font-medium whitespace-nowrap gap-4 ${location.pathname === '/portal' || location.pathname === '/' ? 'bg-background-tertiary text-accent-primary border border-border-secondary shadow-sm' : 'text-text-secondary hover:bg-background-hover hover:text-text-primary'}`} title="Boards">
                        <svg className="flex-shrink-0" width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
                            <path d="M3 4a1 1 0 011-1h12a1 1 0 011 1v2a1 1 0 01-1 1H4a1 1 0 01-1-1V4zM3 10a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H4a1 1 0 01-1-1v-6zM14 9a1 1 0 00-1 1v6a1 1 0 001 1h2a1 1 0 001-1v-6a1 1 0 00-1-1h-2z" />
                        </svg>
                        <span className={`transition-all duration-200 ${sidebarOpen ? 'opacity-100' : 'opacity-0 translate-x-[-10px] pointer-events-none'}`}>Boards</span>
                    </Link>
                    <Link to="/datastores" className={`flex items-center min-h-[44px] px-3 rounded-lg transition-all duration-200 font-medium whitespace-nowrap gap-4 ${location.pathname === '/datastores' || location.pathname.startsWith('/datastores/') ? 'bg-background-tertiary text-accent-primary border border-border-secondary shadow-sm' : 'text-text-secondary hover:bg-background-hover hover:text-text-primary'}`} title="Datastores">
                        <Database className="flex-shrink-0" size={20} />
                        <span className={`transition-all duration-200 ${sidebarOpen ? 'opacity-100' : 'opacity-0 translate-x-[-10px] pointer-events-none'}`}>Datastores</span>
                    </Link>
                </nav>

                <div className={`p-4 border-t border-border-primary transition-all duration-300 ${sidebarOpen ? 'px-4' : 'px-[15px]'}`}>
                    <div className="flex items-center gap-4 min-h-[48px]">
                        <div className="w-10 h-10 rounded-full bg-accent-gradient flex items-center justify-center font-bold text-base flex-shrink-0 text-white shadow-lg">
                            {user?.email?.[0]?.toUpperCase() || 'U'}
                        </div>
                        <div className={`flex-1 min-w-0 transition-all duration-200 ${sidebarOpen ? 'opacity-100' : 'opacity-0 translate-x-[-10px] pointer-events-none'}`}>
                            <div className="text-sm font-medium text-text-primary truncate">{user?.email}</div>
                            <button onClick={signOut} className="text-[0.7rem] text-text-muted hover:text-accent-primary transition-colors uppercase tracking-wider font-bold">
                                Sign out
                            </button>
                        </div>
                    </div>
                </div>
            </aside>

            {/* Main Content */}
            <div className="flex-1 flex flex-col min-w-0 h-screen overflow-hidden">
                {/* Top Bar */}
                <header className="h-[72px] bg-background-secondary border-b border-border-primary flex items-center gap-6 px-4 md:px-8 flex-shrink-0 z-5 relative">
                    <button
                        className="p-2 rounded-lg text-text-secondary hover:bg-background-hover hover:text-text-primary transition-all"
                        onClick={() => setSidebarOpen(!sidebarOpen)}
                    >
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <line x1="3" y1="12" x2="21" y2="12" />
                            <line x1="3" y1="6" x2="21" y2="6" />
                            <line x1="3" y1="18" x2="21" y2="18" />
                        </svg>
                    </button>
                    <div className="flex-1">{headerContent}</div>
                </header>

                {/* Page Content */}
                <main className="flex-1 overflow-y-auto min-h-0 bg-background-primary">
                    <Outlet />
                </main>
            </div>

            {/* Global AI Chat Panel */}
            <div className={`fixed right-0 top-16 bottom-0 w-[400px] bg-background-secondary border-l border-border-primary flex flex-col transition-all duration-500 ease-in-out z-50 ${chatOpen ? 'translate-x-0 shadow-[-20px_0_40px_rgba(0,0,0,0.5)]' : 'translate-x-full'}`}>
                <div className="border-b border-border-primary">
                    <div className="flex items-center justify-between p-4">
                        <div className="flex items-center gap-2 min-w-0">
                            <div className="relative">
                                <button
                                    type="button"
                                    onClick={() => setShowChatListDropdown((v) => !v)}
                                    className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-text-secondary hover:text-text-primary hover:bg-background-tertiary transition-all text-sm font-medium"
                                    title="Chats"
                                >
                                    <svg width="18" height="18" viewBox="0 0 20 20" fill="currentColor">
                                        <path d="M2 5a2 2 0 012-2h7a2 2 0 012 2v4a2 2 0 01-2 2H9l-3 3v-3H4a2 2 0 01-2-2V5z" />
                                        <path d="M15 7v2a4 4 0 01-4 4H9.828l-1.766 1.767c.28.149.599.233.938.233h2l3 3v-3h2a2 2 0 002-2V9a2 2 0 00-2-2h-2z" />
                                    </svg>
                                    <span className="truncate">{chatList.find((c) => c.id === currentChatId)?.title || 'Chat'}</span>
                                    <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor" className={`shrink-0 transition-transform ${showChatListDropdown ? 'rotate-180' : ''}`}>
                                        <path fillRule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" />
                                    </svg>
                                </button>
                                {showChatListDropdown && (
                                    <>
                                        <div className="absolute left-0 top-full mt-1 w-64 max-h-60 overflow-y-auto rounded-xl bg-background-tertiary border border-border-primary shadow-xl z-[60] py-1">
                                            <button
                                                type="button"
                                                onClick={startNewChat}
                                                className="w-full flex items-center gap-2 px-3 py-2 text-left text-sm text-accent-primary hover:bg-background-secondary font-medium"
                                            >
                                                <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor">
                                                    <path fillRule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" />
                                                </svg>
                                                New chat
                                            </button>
                                            {chatList.length === 0 && (
                                                <div className="px-3 py-4 text-center text-text-muted text-sm">No chats yet</div>
                                            )}
                                            {chatList.map((c) => {
                                                return (
                                                <div
                                                    key={c.id}
                                                    className={`flex items-center gap-1 px-3 py-2 text-sm border-l-2 ${c.id === currentChatId ? 'bg-accent-primary/10 border-l-accent-primary' : 'hover:bg-background-secondary border-l-border-primary'}`}
                                                >
                                                    {editingChatId === c.id ? (
                                                        <input
                                                            autoFocus
                                                            type="text"
                                                            value={editingChatTitle}
                                                            onChange={(e) => setEditingChatTitle(e.target.value)}
                                                            onBlur={() => { renameChat(c.id, editingChatTitle); setEditingChatId(null) }}
                                                            onKeyDown={(e) => { if (e.key === 'Enter') { renameChat(c.id, editingChatTitle); setEditingChatId(null) } if (e.key === 'Escape') setEditingChatId(null) }}
                                                            className="flex-1 min-w-0 bg-background-primary border border-accent-primary rounded px-1.5 py-0.5 text-sm text-text-primary outline-none"
                                                        />
                                                    ) : (
                                                        <button
                                                            type="button"
                                                            onClick={() => { setCurrentChatId(c.id); setShowChatListDropdown(false) }}
                                                            className={`flex-1 min-w-0 text-left truncate ${c.id === currentChatId ? 'text-accent-primary' : 'text-text-primary'}`}
                                                        >
                                                            <span className="block truncate font-medium">{c.title}</span>
                                                            <span className="block text-[10px] text-text-muted mt-0.5">{formatChatDate(c.updated_at)}</span>
                                                        </button>
                                                    )}
                                                    <button
                                                        type="button"
                                                        onClick={(e) => { e.stopPropagation(); setEditingChatId(c.id); setEditingChatTitle(c.title) }}
                                                        className="shrink-0 p-1 rounded text-text-muted hover:text-text-primary hover:bg-background-tertiary transition-all"
                                                        title="Rename"
                                                    >
                                                        <svg width="12" height="12" viewBox="0 0 20 20" fill="currentColor">
                                                            <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z" />
                                                        </svg>
                                                    </button>
                                                </div>
                                                );
                                            })}
                                        </div>
                                        <div className="fixed inset-0 z-[59]" onClick={() => setShowChatListDropdown(false)} aria-hidden="true" />
                                    </>
                                )}
                            </div>
                            <button
                                type="button"
                                onClick={startNewChat}
                                className="p-1.5 rounded-lg text-text-secondary hover:text-accent-primary hover:bg-background-tertiary transition-all shrink-0"
                                title="New chat"
                            >
                                <svg width="18" height="18" viewBox="0 0 20 20" fill="currentColor">
                                    <path fillRule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" />
                                </svg>
                            </button>
                        </div>
                        <button
                            className="p-2 text-text-secondary hover:text-text-primary hover:bg-background-tertiary rounded-lg transition-all shrink-0"
                            onClick={() => setChatOpen(false)}
                        >
                            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
                                <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" />
                            </svg>
                        </button>
                    </div>
                </div>

                <div className="flex-1 overflow-y-auto p-6 flex flex-col gap-6 custom-scrollbar">
                    {chatMessages.map((msg, idx) => (
                        <ChatMessage
                            key={idx}
                            message={msg}
                            isStreaming={idx === chatMessages.length - 1 && chatLoading && msg.role === 'assistant'}
                        />
                    ))}
                    <div ref={messagesEndRef} />
                </div>

                <form className="p-6 border-t border-border-primary flex gap-3 relative" onSubmit={handleChatSubmit}>
                    <div className="flex-1 relative">
                        <textarea
                            ref={textareaRef}
                            placeholder="Ask AI for help... (Use @ for mentions, / for commands)"
                            value={chatInput}
                            onChange={(e) => {
                                const target = e.target
                                handleChatInputChange(e.target.value, target.selectionStart)
                            }}
                            onKeyDown={(e) => {
                                if (e.key === 'Escape') {
                                    setShowMentionDropdown(false)
                                    setShowCommandDropdown(false)
                                }
                                if (e.key === 'Enter' && !e.shiftKey) {
                                    e.preventDefault()
                                    handleChatSubmit(e)
                                }
                            }}
                            disabled={chatLoading}
                            rows={1}
                            className="w-full p-3.5 bg-background-tertiary border border-border-primary rounded-xl text-text-primary text-sm focus:border-accent-primary focus:ring-1 focus:ring-accent-primary outline-none transition-all placeholder:text-text-muted/50 disabled:opacity-60 resize-none overflow-hidden"
                        />
                        
                        {/* @mention dropdown */}
                        {showMentionDropdown && mentionOptions.length > 0 && (
                            <div className="absolute bottom-full left-0 mb-2 w-full max-h-48 overflow-y-auto rounded-xl bg-background-tertiary border border-border-primary shadow-xl z-[70] py-1">
                                {mentionOptions.map((option, idx) => (
                                    <button
                                        key={`${option.type}-${option.id}`}
                                        type="button"
                                        onClick={() => insertMention(option)}
                                        className="w-full flex items-center gap-3 px-4 py-2 text-left text-sm hover:bg-background-secondary transition-colors"
                                    >
                                        <div className={`w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold ${
                                            option.type === 'board' ? 'bg-blue-500/20 text-blue-400' : 'bg-purple-500/20 text-purple-400'
                                        }`}>
                                            {option.type === 'board' ? 'B' : 'Q'}
                                        </div>
                                        <div className="flex-1 min-w-0">
                                            <div className="font-medium text-text-primary truncate">{option.name}</div>
                                            <div className="text-[10px] text-text-muted uppercase">{option.type}</div>
                                        </div>
                                    </button>
                                ))}
                            </div>
                        )}
                        
                        {/* /command dropdown */}
                        {showCommandDropdown && commandOptions.length > 0 && (
                            <div className="absolute bottom-full left-0 mb-2 w-full max-h-48 overflow-y-auto rounded-xl bg-background-tertiary border border-border-primary shadow-xl z-[70] py-1">
                                {commandOptions.map((command) => (
                                    <button
                                        key={command.id}
                                        type="button"
                                        onClick={() => insertCommand(command)}
                                        className="w-full flex items-center gap-3 px-4 py-2 text-left text-sm hover:bg-background-secondary transition-colors"
                                    >
                                        <div className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold bg-green-500/20 text-green-400">
                                            /
                                        </div>
                                        <div className="flex-1 min-w-0">
                                            <div className="font-medium text-text-primary">/{command.name}</div>
                                            <div className="text-[10px] text-text-muted">{command.description}</div>
                                        </div>
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                    <button type="submit" disabled={chatLoading} className="p-3.5 bg-accent-gradient text-white rounded-xl shadow-lg hover:shadow-accent-primary/20 hover:-translate-y-0.5 active:translate-y-0 transition-all disabled:opacity-60 disabled:pointer-events-none">
                        {chatLoading ? (
                            <span className="inline-block w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                        ) : (
                            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor" className="rotate-90">
                                <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z" />
                            </svg>
                        )}
                    </button>
                </form>
            </div>

            {/* Chat Toggle Button (when closed) */}
            {!chatOpen && (
                <button className="fixed bottom-10 right-10 w-16 h-16 flex items-center justify-center bg-accent-gradient text-white rounded-full shadow-2xl hover:scale-110 hover:shadow-accent-primary/30 transition-all duration-300 z-40 group animate-bounce-subtle" onClick={() => setChatOpen(true)}>
                    <svg width="28" height="28" viewBox="0 0 20 20" fill="currentColor">
                        <path fillRule="evenodd" d="M18 10c0 3.866-3.582 7-8 7a8.841 8.841 0 01-4.083-.98L2 17l1.338-3.123C2.493 12.767 2 11.434 2 10c0-3.866 3.582-7 8-7s8 3.134 8 7zM7 9H5v2h2V9zm8 0h-2v2h2V9zM9 9h2v2H9V9z" />
                    </svg>
                    <div className="absolute -top-1 -right-1 w-4 h-4 bg-status-success border-2 border-background-primary rounded-full group-hover:animate-ping" />
                </button>
            )}
        </div>
    )
}

export default function MainLayout() {
    return (
        <ChatProvider>
            <MainLayoutContent />
        </ChatProvider>
    )
}
