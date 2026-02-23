import { useState, useEffect, useRef } from 'react'
import { Link, useLocation, Outlet } from 'react-router-dom'
import { Database, LayoutGrid, Menu, X, LogOut, PanelRightOpen, PanelRightClose, Home, ChevronDown, Plus, Check, Building2, BarChart3, Puzzle, Paperclip, File as FileIcon } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import { useOrg } from '../contexts/OrgContext'
import { useHeader } from '../contexts/HeaderContext'
import { ChatProvider, useChat } from '../contexts/ChatContext'
import ChatMessage from '../components/ChatMessage'

const navItems = [
    { to: '/portal', label: 'Home', icon: Home, end: true },
    { to: '/boards', label: 'Boards', icon: LayoutGrid },
    { to: '/datastores', label: 'Datastores', icon: Database },
    { to: '/widgets', label: 'Widgets', icon: Puzzle },
    { to: '/usage', label: 'Usage', icon: BarChart3 },
]

function OrgSelector() {
    const { organizations, currentOrg, setCurrentOrg, createOrg } = useOrg()
    const [open, setOpen] = useState(false)
    const [creating, setCreating] = useState(false)
    const [newName, setNewName] = useState('')
    const ref = useRef(null)

    useEffect(() => {
        const handler = (e) => {
            if (ref.current && !ref.current.contains(e.target)) {
                setOpen(false)
                setCreating(false)
            }
        }
        document.addEventListener('mousedown', handler)
        return () => document.removeEventListener('mousedown', handler)
    }, [])

    const handleCreate = async (e) => {
        e.preventDefault()
        if (!newName.trim()) return
        try {
            const org = await createOrg(newName.trim())
            setCurrentOrg(org)
            setNewName('')
            setCreating(false)
            setOpen(false)
        } catch (err) {
            console.error('Failed to create org:', err)
        }
    }

    if (!currentOrg) return null

    return (
        <div className="relative px-3 py-2" ref={ref}>
            <button
                onClick={() => setOpen(!open)}
                className="w-full flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition hover:bg-white/[0.05] group"
            >
                <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-indigo-500/15 text-indigo-400 shrink-0">
                    <Building2 className="h-3.5 w-3.5" />
                </div>
                <span className="flex-1 min-w-0 text-sm font-medium text-slate-200 truncate">
                    {currentOrg.name}
                </span>
                <ChevronDown className={`h-3.5 w-3.5 text-slate-500 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
            </button>

            {open && (
                <div className="absolute left-3 right-3 top-full mt-1 z-[60] rounded-xl border border-white/[0.08] bg-slate-900/95 backdrop-blur-xl shadow-2xl shadow-black/40 overflow-hidden">
                    <div className="py-1 max-h-52 overflow-y-auto scrollbar-none">
                        {organizations.map((org) => (
                            <button
                                key={org.id}
                                onClick={() => {
                                    setCurrentOrg(org)
                                    setOpen(false)
                                }}
                                className={`w-full flex items-center gap-2.5 px-3 py-2 text-left text-sm transition ${
                                    org.id === currentOrg.id
                                        ? 'bg-indigo-600/10 text-indigo-400'
                                        : 'text-slate-300 hover:bg-white/[0.04] hover:text-white'
                                }`}
                            >
                                <div className={`flex h-6 w-6 items-center justify-center rounded-md text-[10px] font-bold shrink-0 ${
                                    org.id === currentOrg.id
                                        ? 'bg-indigo-500/20 text-indigo-400'
                                        : 'bg-white/[0.06] text-slate-500'
                                }`}>
                                    {org.name[0]?.toUpperCase()}
                                </div>
                                <span className="flex-1 min-w-0 truncate font-medium">{org.name}</span>
                                {org.id === currentOrg.id && (
                                    <Check className="h-3.5 w-3.5 text-indigo-400 shrink-0" />
                                )}
                            </button>
                        ))}
                    </div>

                    <div className="border-t border-white/[0.06]">
                        {creating ? (
                            <form onSubmit={handleCreate} className="p-2 flex gap-1.5">
                                <input
                                    autoFocus
                                    type="text"
                                    value={newName}
                                    onChange={(e) => setNewName(e.target.value)}
                                    onKeyDown={(e) => e.key === 'Escape' && setCreating(false)}
                                    placeholder="Organization name"
                                    className="flex-1 min-w-0 px-2.5 py-1.5 bg-white/[0.04] border border-white/[0.1] rounded-lg text-xs text-slate-200 placeholder:text-slate-600 focus:border-indigo-500/50 focus:outline-none"
                                />
                                <button
                                    type="submit"
                                    className="px-2.5 py-1.5 rounded-lg bg-indigo-600 text-white text-xs font-medium hover:bg-indigo-500 transition"
                                >
                                    Add
                                </button>
                            </form>
                        ) : (
                            <button
                                onClick={() => setCreating(true)}
                                className="w-full flex items-center gap-2 px-3 py-2.5 text-xs font-medium text-slate-400 hover:text-indigo-400 hover:bg-white/[0.04] transition"
                            >
                                <Plus className="h-3.5 w-3.5" />
                                New organization
                            </button>
                        )}
                    </div>
                </div>
            )}
        </div>
    )
}

function CreateOrgGate() {
    const { createOrg, setCurrentOrg } = useOrg()
    const { user, signOut } = useAuth()

    const defaultName = () => {
        const base = user?.full_name || user?.email?.split('@')[0] || ''
        return base ? `${base}'s Workspace` : ''
    }

    const [name, setName] = useState(defaultName)
    const [submitting, setSubmitting] = useState(false)
    const [error, setError] = useState('')

    const handleSubmit = async (e) => {
        e.preventDefault()
        if (!name.trim()) return
        setSubmitting(true)
        setError('')
        try {
            const org = await createOrg(name.trim())
            setCurrentOrg(org)
        } catch (err) {
            setError(err.message || 'Failed to create organization')
        } finally {
            setSubmitting(false)
        }
    }

    return (
        <div className="flex min-h-screen items-center justify-center bg-slate-950 px-4">
            <div className="w-full max-w-md">
                <div className="text-center mb-8">
                    <img src="/nubi.png" alt="Nubi" className="h-14 w-14 rounded-2xl mx-auto mb-4" />
                    <h1 className="text-2xl font-bold text-white">Create your organization</h1>
                    <p className="mt-2 text-sm text-slate-400">You need an organization to get started. This is where your boards, datastores, and team live.</p>
                </div>
                <form onSubmit={handleSubmit} className="space-y-4">
                    <input
                        autoFocus
                        type="text"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        placeholder="e.g. Acme Corp, My Team, Personal"
                        className="w-full rounded-xl border border-white/[0.1] bg-white/[0.04] px-4 py-3 text-sm text-white placeholder:text-slate-500 focus:border-indigo-500/60 focus:outline-none transition"
                    />
                    {error && <p className="text-xs text-red-400">{error}</p>}
                    <button
                        type="submit"
                        disabled={submitting || !name.trim()}
                        className="w-full rounded-xl bg-indigo-600 py-3 text-sm font-semibold text-white shadow-lg shadow-indigo-900/30 hover:bg-indigo-500 active:scale-[0.98] transition disabled:opacity-50 disabled:pointer-events-none"
                    >
                        {submitting ? 'Creating...' : 'Create organization'}
                    </button>
                </form>
                <button
                    onClick={signOut}
                    className="mt-6 flex items-center justify-center gap-1.5 w-full text-xs text-slate-500 hover:text-slate-300 transition"
                >
                    <LogOut className="h-3.5 w-3.5" />
                    Sign out
                </button>
            </div>
        </div>
    )
}

function MainLayoutContent() {
    const { user, signOut } = useAuth()
    const { organizations, loading: orgLoading } = useOrg()
    const { headerContent } = useHeader()
    const [sidebarOpen, setSidebarOpen] = useState(false)
    const location = useLocation()
    const messagesEndRef = useRef(null)
    const textareaRef = useRef(null)

    const {
        chatOpen, setChatOpen,
        chatList, currentChatId, setCurrentChatId,
        chatMessages, chatInput, setChatInput, chatLoading,
        showChatListDropdown, setShowChatListDropdown,
        editingChatId, setEditingChatId,
        editingChatTitle, setEditingChatTitle,
        startNewChat, renameChat, formatChatDate, handleChatSubmit,
        selectedModel, setSelectedModel, availableModels,
        showMentionDropdown, setShowMentionDropdown,
        mentionOptions, insertMention, handleChatInputChange,
        showCommandDropdown, setShowCommandDropdown,
        commandOptions, insertCommand,
        attachedFiles, addAttachedFile, removeAttachedFile,
    } = useChat()

    useEffect(() => {
        if (messagesEndRef.current) {
            messagesEndRef.current.scrollIntoView({ behavior: 'smooth' })
        }
    }, [chatMessages])

    useEffect(() => {
        if (textareaRef.current) {
            textareaRef.current.style.height = 'auto'
            textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 150) + 'px'
        }
    }, [chatInput])

    const initials = (user?.email?.[0] || 'U').toUpperCase()

    if (!orgLoading && organizations.length === 0) {
        return <CreateOrgGate />
    }

    return (
        <div className="flex h-screen bg-slate-950 text-white antialiased">
            {/* Mobile sidebar overlay */}
            {sidebarOpen && (
                <div className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm lg:hidden" onClick={() => setSidebarOpen(false)} />
            )}

            {/* Sidebar */}
            <aside className={`fixed inset-y-0 left-0 z-50 flex w-60 flex-col border-r border-white/[0.07] bg-slate-950 transition-transform duration-200 ease-in-out lg:static lg:translate-x-0 ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}`}>
                {/* Logo */}
                <div className="flex h-14 items-center justify-between border-b border-white/[0.07] px-5">
                    <Link to="/" className="flex items-center gap-2.5">
                        <img src="/nubi.png" alt="Nubi" className="h-7 w-7 rounded" />
                        <span className="text-sm font-bold text-white tracking-tight">Nubi</span>
                    </Link>
                    <button onClick={() => setSidebarOpen(false)} className="text-slate-400 hover:text-white lg:hidden">
                        <X className="h-5 w-5" />
                    </button>
                </div>

                {/* Org selector */}
                <OrgSelector />

                {/* Nav */}
                <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 py-4 scrollbar-none">
                    {navItems.map(({ to, label, icon: Icon }) => {
                        const isActive = location.pathname === to || location.pathname.startsWith(to + '/')
                        return (
                            <Link
                                key={to}
                                to={to}
                                onClick={() => setSidebarOpen(false)}
                                className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ${
                                    isActive
                                        ? 'bg-indigo-600/10 text-indigo-400'
                                        : 'text-slate-400 hover:bg-white/[0.05] hover:text-white'
                                }`}
                            >
                                <Icon className="h-[18px] w-[18px] shrink-0" />
                                {label}
                            </Link>
                        )
                    })}
                </nav>

                {/* User footer */}
                <div className="border-t border-white/[0.07] px-3 py-3">
                    <div className="flex items-center gap-2.5 rounded-lg px-2.5 py-2">
                        <div className="flex h-7 w-7 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 text-[10px] font-bold text-white shrink-0">
                            {initials}
                        </div>
                        <div className="flex-1 min-w-0">
                            <p className="truncate text-sm font-medium text-white leading-tight">{user?.email}</p>
                        </div>
                        <button
                            onClick={signOut}
                            className="rounded-lg p-1.5 text-slate-500 hover:bg-white/[0.05] hover:text-white transition shrink-0"
                            title="Sign out"
                        >
                            <LogOut className="h-4 w-4" />
                        </button>
                    </div>
                </div>
            </aside>

            {/* Main content */}
            <div className="flex flex-1 flex-col overflow-hidden">
                {/* Top bar */}
                <header className="flex h-14 shrink-0 items-center gap-3 border-b border-white/[0.07] bg-slate-950/80 px-4 backdrop-blur-sm">
                    <button
                        onClick={() => setSidebarOpen(true)}
                        className="rounded-lg p-2 text-slate-400 hover:bg-white/[0.05] hover:text-white lg:hidden"
                    >
                        <Menu className="h-5 w-5" />
                    </button>

                    <div className="flex flex-1 items-center gap-3 min-w-0">
                        {headerContent}
                    </div>

                    <button
                        onClick={() => setChatOpen((o) => !o)}
                        className={`rounded-lg p-1.5 transition ${
                            chatOpen
                                ? 'bg-indigo-600/10 text-indigo-400'
                                : 'text-slate-400 hover:bg-white/[0.05] hover:text-white'
                        }`}
                        title={chatOpen ? 'Close AI Chat' : 'Open AI Chat'}
                    >
                        {chatOpen ? <PanelRightClose className="h-5 w-5" /> : <PanelRightOpen className="h-5 w-5" />}
                    </button>
                </header>

                {/* Content + Chat side panel */}
                <div className="flex flex-1 overflow-hidden">
                    <main className="flex-1 overflow-y-auto">
                        <Outlet />
                    </main>

                    {/* Chat panel (desktop: inline sidebar) */}
                    {chatOpen && (
                        <div className="hidden lg:flex w-[360px] shrink-0 border-l border-white/[0.07] flex-col bg-slate-950">
                            <ChatPanelInner
                                chatList={chatList}
                                currentChatId={currentChatId}
                                setCurrentChatId={setCurrentChatId}
                                chatMessages={chatMessages}
                                chatInput={chatInput}
                                setChatInput={setChatInput}
                                chatLoading={chatLoading}
                                showChatListDropdown={showChatListDropdown}
                                setShowChatListDropdown={setShowChatListDropdown}
                                editingChatId={editingChatId}
                                setEditingChatId={setEditingChatId}
                                editingChatTitle={editingChatTitle}
                                setEditingChatTitle={setEditingChatTitle}
                                startNewChat={startNewChat}
                                renameChat={renameChat}
                                formatChatDate={formatChatDate}
                                handleChatSubmit={handleChatSubmit}
                                selectedModel={selectedModel}
                                setSelectedModel={setSelectedModel}
                                availableModels={availableModels}
                                showMentionDropdown={showMentionDropdown}
                                setShowMentionDropdown={setShowMentionDropdown}
                                mentionOptions={mentionOptions}
                                insertMention={insertMention}
                                handleChatInputChange={handleChatInputChange}
                                showCommandDropdown={showCommandDropdown}
                                setShowCommandDropdown={setShowCommandDropdown}
                                commandOptions={commandOptions}
                                insertCommand={insertCommand}
                                attachedFiles={attachedFiles}
                                addAttachedFile={addAttachedFile}
                                removeAttachedFile={removeAttachedFile}
                                messagesEndRef={messagesEndRef}
                                textareaRef={textareaRef}
                            />
                        </div>
                    )}

                    {/* Chat panel (mobile: overlay) */}
                    {chatOpen && (
                        <div className="fixed inset-0 z-40 lg:hidden">
                            <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setChatOpen(false)} />
                            <div className="absolute inset-0 flex flex-col bg-slate-950 shadow-2xl sm:left-auto sm:w-full sm:max-w-md">
                                <div className="flex h-10 shrink-0 items-center justify-between border-b border-white/[0.07] px-3 sm:hidden">
                                    <span className="text-xs font-medium text-slate-400">AI Chat</span>
                                    <button onClick={() => setChatOpen(false)} className="rounded-lg p-1.5 text-slate-400 hover:bg-white/[0.05] hover:text-white">
                                        <X className="h-5 w-5" />
                                    </button>
                                </div>
                                <div className="flex-1 min-h-0 flex flex-col">
                                    <ChatPanelInner
                                        chatList={chatList}
                                        currentChatId={currentChatId}
                                        setCurrentChatId={setCurrentChatId}
                                        chatMessages={chatMessages}
                                        chatInput={chatInput}
                                        setChatInput={setChatInput}
                                        chatLoading={chatLoading}
                                        showChatListDropdown={showChatListDropdown}
                                        setShowChatListDropdown={setShowChatListDropdown}
                                        editingChatId={editingChatId}
                                        setEditingChatId={setEditingChatId}
                                        editingChatTitle={editingChatTitle}
                                        setEditingChatTitle={setEditingChatTitle}
                                        startNewChat={startNewChat}
                                        renameChat={renameChat}
                                        formatChatDate={formatChatDate}
                                        handleChatSubmit={handleChatSubmit}
                                        selectedModel={selectedModel}
                                        setSelectedModel={setSelectedModel}
                                        availableModels={availableModels}
                                        showMentionDropdown={showMentionDropdown}
                                        setShowMentionDropdown={setShowMentionDropdown}
                                        mentionOptions={mentionOptions}
                                        insertMention={insertMention}
                                        handleChatInputChange={handleChatInputChange}
                                        showCommandDropdown={showCommandDropdown}
                                        setShowCommandDropdown={setShowCommandDropdown}
                                        commandOptions={commandOptions}
                                        insertCommand={insertCommand}
                                        attachedFiles={attachedFiles}
                                        addAttachedFile={addAttachedFile}
                                        removeAttachedFile={removeAttachedFile}
                                        messagesEndRef={messagesEndRef}
                                        textareaRef={textareaRef}
                                    />
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}

function ChatPanelInner({
    chatList, currentChatId, setCurrentChatId,
    chatMessages, chatInput, setChatInput, chatLoading,
    showChatListDropdown, setShowChatListDropdown,
    editingChatId, setEditingChatId, editingChatTitle, setEditingChatTitle,
    startNewChat, renameChat, formatChatDate, handleChatSubmit,
    selectedModel, setSelectedModel, availableModels,
    showMentionDropdown, setShowMentionDropdown,
    mentionOptions, insertMention, handleChatInputChange,
    showCommandDropdown, setShowCommandDropdown,
    commandOptions, insertCommand,
    attachedFiles, addAttachedFile, removeAttachedFile,
    messagesEndRef, textareaRef,
}) {
    const [showModelDropdown, setShowModelDropdown] = useState(false)
    const modelDropdownRef = useRef(null)

    useEffect(() => {
        const handler = (e) => {
            if (modelDropdownRef.current && !modelDropdownRef.current.contains(e.target)) {
                setShowModelDropdown(false)
            }
        }
        document.addEventListener('mousedown', handler)
        return () => document.removeEventListener('mousedown', handler)
    }, [])

    const currentModelName = availableModels?.find(m => m.id === selectedModel)?.name || selectedModel
    return (
        <>
            {/* Chat header */}
            <div className="flex items-center justify-between border-b border-white/[0.07] px-4 py-2.5">
                <div className="flex items-center gap-2 min-w-0">
                    <div className="relative">
                        <button
                            type="button"
                            onClick={() => setShowChatListDropdown((v) => !v)}
                            className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs font-medium text-slate-400 hover:text-white hover:bg-white/[0.05] transition"
                        >
                            <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
                                <path d="M2 5a2 2 0 012-2h7a2 2 0 012 2v4a2 2 0 01-2 2H9l-3 3v-3H4a2 2 0 01-2-2V5z" />
                                <path d="M15 7v2a4 4 0 01-4 4H9.828l-1.766 1.767c.28.149.599.233.938.233h2l3 3v-3h2a2 2 0 002-2V9a2 2 0 00-2-2h-2z" />
                            </svg>
                            <span className="truncate max-w-[140px]">{chatList.find((c) => c.id === currentChatId)?.title || 'Chat'}</span>
                            <svg width="12" height="12" viewBox="0 0 20 20" fill="currentColor" className={`shrink-0 transition-transform ${showChatListDropdown ? 'rotate-180' : ''}`}>
                                <path fillRule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" />
                            </svg>
                        </button>
                        {showChatListDropdown && (
                            <>
                                <div className="absolute left-0 top-full mt-1 w-56 max-h-52 overflow-y-auto rounded-xl border border-white/[0.08] bg-slate-900 shadow-2xl z-[60] py-1">
                                    <button
                                        type="button"
                                        onClick={startNewChat}
                                        className="w-full flex items-center gap-1.5 px-3 py-2 text-left text-xs text-indigo-400 hover:bg-white/[0.04] font-medium"
                                    >
                                        <svg width="13" height="13" viewBox="0 0 20 20" fill="currentColor"><path fillRule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" /></svg>
                                        New chat
                                    </button>
                                    {chatList.length === 0 && (
                                        <div className="px-3 py-3 text-center text-slate-600 text-xs">No chats yet</div>
                                    )}
                                    {chatList.map((c) => (
                                        <div
                                            key={c.id}
                                            className={`flex items-center gap-1 px-3 py-2 text-xs border-l-2 ${c.id === currentChatId ? 'bg-indigo-600/10 border-l-indigo-500' : 'hover:bg-white/[0.04] border-l-transparent'}`}
                                        >
                                            {editingChatId === c.id ? (
                                                <input
                                                    autoFocus
                                                    type="text"
                                                    value={editingChatTitle}
                                                    onChange={(e) => setEditingChatTitle(e.target.value)}
                                                    onBlur={() => { renameChat(c.id, editingChatTitle); setEditingChatId(null) }}
                                                    onKeyDown={(e) => { if (e.key === 'Enter') { renameChat(c.id, editingChatTitle); setEditingChatId(null) } if (e.key === 'Escape') setEditingChatId(null) }}
                                                    className="flex-1 min-w-0 bg-slate-800 border border-indigo-500/50 rounded px-1.5 py-0.5 text-xs text-white outline-none"
                                                />
                                            ) : (
                                                <button
                                                    type="button"
                                                    onClick={() => { setCurrentChatId(c.id); setShowChatListDropdown(false) }}
                                                    className={`flex-1 min-w-0 text-left truncate ${c.id === currentChatId ? 'text-indigo-400' : 'text-slate-300'}`}
                                                >
                                                    <span className="block truncate font-medium">{c.title}</span>
                                                    <span className="block text-[9px] text-slate-600 mt-0.5">{formatChatDate(c.updated_at)}</span>
                                                </button>
                                            )}
                                            <button
                                                type="button"
                                                onClick={(e) => { e.stopPropagation(); setEditingChatId(c.id); setEditingChatTitle(c.title) }}
                                                className="shrink-0 p-0.5 rounded text-slate-600 hover:text-white hover:bg-white/[0.05] transition"
                                                title="Rename"
                                            >
                                                <svg width="11" height="11" viewBox="0 0 20 20" fill="currentColor"><path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z" /></svg>
                                            </button>
                                        </div>
                                    ))}
                                </div>
                                <div className="fixed inset-0 z-[59]" onClick={() => setShowChatListDropdown(false)} aria-hidden="true" />
                            </>
                        )}
                    </div>
                    <button
                        type="button"
                        onClick={startNewChat}
                        className="p-1 rounded-lg text-slate-500 hover:text-indigo-400 hover:bg-white/[0.05] transition shrink-0"
                        title="New chat"
                    >
                        <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor"><path fillRule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" /></svg>
                    </button>
                </div>
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-y-auto px-4 py-4 flex flex-col gap-4 scrollbar-none">
                {chatMessages.map((msg, idx) => (
                    <ChatMessage
                        key={idx}
                        message={msg}
                        isStreaming={idx === chatMessages.length - 1 && chatLoading && msg.role === 'assistant'}
                    />
                ))}
                <div ref={messagesEndRef} />
            </div>

            {/* Input */}
            <div className="px-4 pt-2 pb-0 flex items-center">
                <div className="relative" ref={modelDropdownRef}>
                    <button
                        type="button"
                        onClick={() => setShowModelDropdown(v => !v)}
                        className="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-slate-500 hover:text-slate-300 hover:bg-white/[0.04] transition"
                    >
                        <svg width="12" height="12" viewBox="0 0 20 20" fill="currentColor" className="shrink-0 opacity-60">
                            <path fillRule="evenodd" d="M11.3 1.046A1 1 0 0112 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z" clipRule="evenodd" />
                        </svg>
                        <span className="truncate max-w-[160px]">{currentModelName}</span>
                        <ChevronDown className={`h-3 w-3 shrink-0 transition-transform ${showModelDropdown ? 'rotate-180' : ''}`} />
                    </button>
                    {showModelDropdown && availableModels?.length > 0 && (
                        <div className="absolute left-0 bottom-full mb-1 w-56 max-h-60 overflow-y-auto rounded-xl border border-white/[0.08] bg-slate-900/95 backdrop-blur-xl shadow-2xl z-[70] py-1">
                            {availableModels.map((m) => (
                                <button
                                    key={m.id}
                                    type="button"
                                    onClick={() => { setSelectedModel(m.id); setShowModelDropdown(false) }}
                                    className={`w-full flex items-center gap-2 px-3 py-2 text-left text-xs transition ${
                                        m.id === selectedModel
                                            ? 'bg-indigo-600/10 text-indigo-400'
                                            : 'text-slate-300 hover:bg-white/[0.04] hover:text-white'
                                    }`}
                                >
                                    <div className="flex-1 min-w-0">
                                        <div className="font-medium truncate">{m.name}</div>
                                        <div className="text-[9px] text-slate-600 uppercase mt-0.5">{m.provider}</div>
                                    </div>
                                    {m.id === selectedModel && (
                                        <Check className="h-3.5 w-3.5 text-indigo-400 shrink-0" />
                                    )}
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            </div>
            {/* Attached files */}
            {attachedFiles?.length > 0 && (
                <div className="px-4 pt-2 flex flex-wrap gap-1.5">
                    {attachedFiles.map((f, i) => (
                        <div key={i} className="flex items-center gap-1.5 pl-2 pr-1 py-1 bg-indigo-500/10 border border-indigo-500/20 rounded-lg text-[11px]">
                            <FileIcon size={11} className="text-indigo-400 shrink-0" />
                            <span className="text-indigo-300 truncate max-w-[120px]">{f.name}</span>
                            <button type="button" onClick={() => removeAttachedFile(i)} className="p-0.5 rounded hover:bg-white/10 text-slate-500 hover:text-white transition">
                                <X size={10} />
                            </button>
                        </div>
                    ))}
                </div>
            )}
            <form className="px-4 py-2 border-t border-white/[0.07] flex gap-2 relative" onSubmit={handleChatSubmit}>
                <div className="flex-1 relative">
                    <textarea
                        ref={textareaRef}
                        placeholder="Ask AI... (@ mentions, / commands)"
                        value={chatInput}
                        onChange={(e) => {
                            const target = e.target
                            handleChatInputChange(e.target.value, target.selectionStart)
                        }}
                        onKeyDown={(e) => {
                            if (e.key === 'Escape') { setShowMentionDropdown(false); setShowCommandDropdown(false) }
                            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleChatSubmit(e) }
                        }}
                        disabled={chatLoading}
                        rows={1}
                        className="w-full px-3 py-2 bg-white/[0.03] border border-white/[0.08] rounded-lg text-[13px] text-slate-200 placeholder:text-slate-600 focus:border-indigo-500/50 focus:outline-none transition disabled:opacity-60 resize-none overflow-hidden"
                    />

                    {showMentionDropdown && mentionOptions.length > 0 && (
                        <div className="absolute bottom-full left-0 mb-1.5 w-full max-h-40 overflow-y-auto rounded-xl border border-white/[0.08] bg-slate-900 shadow-2xl z-[70] py-0.5">
                            {mentionOptions.map((option) => (
                                <button
                                    key={`${option.type}-${option.id}`}
                                    type="button"
                                    onClick={() => insertMention(option)}
                                    className="w-full flex items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-white/[0.04] transition-colors"
                                >
                                    <div className={`w-6 h-6 rounded flex items-center justify-center text-[10px] font-bold ${option.type === 'board' ? 'bg-blue-500/20 text-blue-400' : 'bg-purple-500/20 text-purple-400'}`}>
                                        {option.type === 'board' ? 'B' : 'Q'}
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <div className="font-medium text-white truncate text-xs">{option.name}</div>
                                        <div className="text-[9px] text-slate-600 uppercase">{option.type}</div>
                                    </div>
                                </button>
                            ))}
                        </div>
                    )}

                    {showCommandDropdown && commandOptions.length > 0 && (
                        <div className="absolute bottom-full left-0 mb-1.5 w-full max-h-40 overflow-y-auto rounded-xl border border-white/[0.08] bg-slate-900 shadow-2xl z-[70] py-0.5">
                            {commandOptions.map((command) => (
                                <button
                                    key={command.id}
                                    type="button"
                                    onClick={() => insertCommand(command)}
                                    className="w-full flex items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-white/[0.04] transition-colors"
                                >
                                    <div className="w-6 h-6 rounded flex items-center justify-center text-[10px] font-bold bg-emerald-500/20 text-emerald-400">/</div>
                                    <div className="flex-1 min-w-0">
                                        <div className="font-medium text-white text-xs">/{command.name}</div>
                                        <div className="text-[9px] text-slate-600">{command.description}</div>
                                    </div>
                                </button>
                            ))}
                        </div>
                    )}
                </div>
                <label className="p-2 rounded-lg text-slate-500 hover:text-indigo-400 hover:bg-white/[0.05] cursor-pointer transition self-end" title="Attach file">
                    <Paperclip size={16} />
                    <input type="file" className="hidden" multiple accept=".json,.csv,.txt,.sql,.parquet,.duckdb,.xlsx" onChange={(e) => {
                        if (e.target.files) {
                            Array.from(e.target.files).forEach(f => addAttachedFile(f))
                            e.target.value = ''
                        }
                    }} />
                </label>
                <button
                    type="submit"
                    disabled={chatLoading}
                    className="p-2 bg-indigo-600 text-white rounded-lg shadow-lg shadow-indigo-900/30 hover:bg-indigo-500 active:scale-95 transition disabled:opacity-60 disabled:pointer-events-none self-end"
                >
                    {chatLoading ? (
                        <span className="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    ) : (
                        <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor" className="rotate-90">
                            <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z" />
                        </svg>
                    )}
                </button>
            </form>
        </>
    )
}

export default function MainLayout() {
    return (
        <ChatProvider>
            <MainLayoutContent />
        </ChatProvider>
    )
}
