import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react'
import { supabase } from '../lib/supabase'

const ChatContext = createContext()

export function ChatProvider({ children }) {
    const [chatOpen, setChatOpen] = useState(false)
    const [chatList, setChatList] = useState([])
    const [currentChatId, setCurrentChatId] = useState(null)
    const [chatMessages, setChatMessages] = useState([])
    const [chatInput, setChatInput] = useState('')
    const [chatLoading, setChatLoading] = useState(false)
    const [showChatListDropdown, setShowChatListDropdown] = useState(false)
    const [editingChatId, setEditingChatId] = useState(null)
    const [editingChatTitle, setEditingChatTitle] = useState('')
    
    // Current link: board_id
    const [boardId, setBoardId] = useState(null)

    // Callback for when a message is submitted (to be overridden by pages)
    // Use a ref so handleChatSubmit always reads the latest callback at call time,
    // avoiding stale closures when effects re-run (e.g. after code changes)
    const onSubmitCallbackRef = useRef(null)
    const setOnSubmitCallback = useCallback((cb) => {
        onSubmitCallbackRef.current = cb
    }, [])
    
    // @mention support
    const [showMentionDropdown, setShowMentionDropdown] = useState(false)
    const [mentionSearch, setMentionSearch] = useState('')
    const [mentionOptions, setMentionOptions] = useState([])
    const [mentionCursorPos, setMentionCursorPos] = useState(0)
    const [mentionStartPos, setMentionStartPos] = useState(0)
    
    // /command support
    const [showCommandDropdown, setShowCommandDropdown] = useState(false)
    const [commandOptions, setCommandOptions] = useState([])
    const [commandCursorPos, setCommandCursorPos] = useState(0)
    const [commandStartPos, setCommandStartPos] = useState(0)

    const formatChatDate = (dateStr) => {
        const d = new Date(dateStr)
        return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    }

    const newChatTitle = () => {
        const now = new Date()
        return now.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    }

    const openChatFor = useCallback((id) => {
        setBoardId(id)
    }, [])

    const loadChats = useCallback(async (id) => {
        if (!id) {
            setChatList([])
            setCurrentChatId(null)
            return
        }
        try {
            const { data, error } = await supabase
                .from('chats')
                .select('id, title, updated_at, board_id')
                .eq('board_id', id)
                .order('updated_at', { ascending: false })

            if (error) throw error
            setChatList(data || [])
            setCurrentChatId(data?.length ? data[0].id : null)
        } catch (error) {
            console.error('Error loading chats:', error)
            setChatList([])
            setCurrentChatId(null)
        }
    }, [])

    const loadMessages = useCallback(async (chatId) => {
        if (!chatId) {
            setChatMessages([{ role: 'assistant', content: 'Hi! How can I help you?' }])
            return
        }
        try {
            const { data, error } = await supabase
                .from('chat_messages')
                .select('role, content')
                .eq('chat_id', chatId)
                .order('created_at', { ascending: true })

            if (error) throw error
            if (data?.length) {
                setChatMessages(data.map((m) => ({ role: m.role, content: m.content })))
            } else {
                setChatMessages([{ role: 'assistant', content: 'Hi! How can I help you?' }])
            }
        } catch (error) {
            console.error('Error loading chat messages:', error)
            setChatMessages([{ role: 'assistant', content: 'Hi! How can I help you?' }])
        }
    }, [])

    const ensureCurrentChat = useCallback(async () => {
        if (currentChatId) return currentChatId
        if (!boardId) throw new Error('No board linked for chat')
        
        // Generate smart title - this will be updated after first message
        const title = newChatTitle()
        const { data: { user } } = await supabase.auth.getUser()
        const { data, error } = await supabase
            .from('chats')
            .insert({ user_id: user.id, title, board_id: boardId })
            .select('id')
            .single()
        if (error) throw error
        setChatList((prev) => [{ id: data.id, title, updated_at: new Date().toISOString(), board_id: boardId }, ...prev])
        setCurrentChatId(data.id)
        return data.id
    }, [currentChatId, boardId])

    const generateChatTitle = useCallback(async (userPrompt, context) => {
        try {
            const backendUrl = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000'
            const response = await fetch(`${backendUrl}/generate-chat-title`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_prompt: userPrompt,
                    context: context,
                    ...(import.meta.env?.VITE_GEMINI_API_KEY && { gemini_api_key: import.meta.env.VITE_GEMINI_API_KEY })
                })
            })
            
            if (response.ok) {
                const data = await response.json()
                return data.title
            }
        } catch (err) {
            console.error('Error generating title:', err)
        }
        // Fallback
        return userPrompt.slice(0, 40) + (userPrompt.length > 40 ? '...' : '')
    }, [])

    const appendMessage = useCallback(async (chatId, role, content) => {
        try {
            await supabase.from('chat_messages').insert({ chat_id: chatId, role, content })
        } catch (err) {
            console.error('Error saving chat message:', err)
        }
    }, [])

    const startNewChat = useCallback(async () => {
        if (!boardId) return
        setShowChatListDropdown(false)
        const title = newChatTitle()
        const { data: { user } } = await supabase.auth.getUser()
        const { data, error } = await supabase
            .from('chats')
            .insert({ user_id: user.id, title, board_id: boardId })
            .select('id, title, updated_at')
            .single()
        if (error) {
            console.error('Error creating chat:', error)
            return
        }
        setChatList((prev) => [data, ...prev])
        setCurrentChatId(data.id)
        setChatMessages([{ role: 'assistant', content: 'Hi! How can I help you?' }])
    }, [boardId])

    const renameChat = useCallback(async (chatId, newTitle) => {
        const title = newTitle.trim()
        if (!title) return
        try {
            const { error } = await supabase.from('chats').update({ title }).eq('id', chatId)
            if (error) throw error
            setChatList((prev) => prev.map((c) => (c.id === chatId ? { ...c, title } : c)))
        } catch (err) {
            console.error('Error renaming chat:', err)
        }
    }, [])

    // @mention functionality
    const fetchMentionOptions = useCallback(async (search) => {
        try {
            const { data: { user } } = await supabase.auth.getUser()
            
            // Fetch boards
            const { data: boards } = await supabase
                .from('boards')
                .select('id, name')
                .or(`profile_id.eq.${user.id},organization_id.is.null`)
                .ilike('name', `%${search}%`)
                .limit(5)
            
            // Fetch queries for current board
            let queries = []
            if (boardId) {
                const { data: boardQueries } = await supabase
                    .from('board_queries')
                    .select('id, name')
                    .eq('board_id', boardId)
                    .ilike('name', `%${search}%`)
                    .limit(5)
                queries = boardQueries || []
            }
            
            const options = [
                ...(boards || []).map(b => ({ type: 'board', id: b.id, name: b.name })),
                ...(queries || []).map(q => ({ type: 'query', id: q.id, name: q.name }))
            ]
            
            setMentionOptions(options)
        } catch (err) {
            console.error('Error fetching mention options:', err)
        }
    }, [boardId])

    const handleChatInputChange = useCallback((value, cursorPos) => {
        setChatInput(value)
        
        // Detect @ mentions
        const beforeCursor = value.substring(0, cursorPos)
        const atIndex = beforeCursor.lastIndexOf('@')
        const slashIndex = beforeCursor.lastIndexOf('/')
        
        // Check for / commands first
        if (slashIndex !== -1 && slashIndex > atIndex) {
            const afterSlash = beforeCursor.substring(slashIndex + 1)
            if (!/\s/.test(afterSlash)) {
                setShowMentionDropdown(false)
                setShowCommandDropdown(true)
                setCommandStartPos(slashIndex)
                setCommandCursorPos(cursorPos)
                
                // Filter commands based on search
                const search = afterSlash.toLowerCase()
                const allCommands = [
                    { id: 'templates', name: 'templates', description: 'Browse component templates' }
                ]
                setCommandOptions(allCommands.filter(cmd => cmd.name.includes(search)))
            } else {
                setShowCommandDropdown(false)
            }
        }
        // Check for @ mentions
        else if (atIndex !== -1) {
            const afterAt = beforeCursor.substring(atIndex + 1)
            if (!/\s/.test(afterAt)) {
                setShowCommandDropdown(false)
                setShowMentionDropdown(true)
                setMentionSearch(afterAt)
                setMentionStartPos(atIndex)
                setMentionCursorPos(cursorPos)
                fetchMentionOptions(afterAt)
            } else {
                setShowMentionDropdown(false)
            }
        } else {
            setShowMentionDropdown(false)
            setShowCommandDropdown(false)
        }
    }, [fetchMentionOptions])

    const insertCommand = useCallback((command) => {
        const afterCursor = chatInput.substring(commandCursorPos)
        
        // Replace /command with the command text
        const newValue = chatInput.substring(0, commandStartPos) + `/${command.name} ` + afterCursor
        
        setChatInput(newValue)
        setShowCommandDropdown(false)
        
        // Trigger command-specific actions
        if (command.id === 'templates') {
            // Set a flag that pages can read
            if (!window._chatCommands) window._chatCommands = {}
            window._chatCommands.showTemplates = true
        }
    }, [chatInput, commandCursorPos, commandStartPos])

    const insertMention = useCallback((option) => {
        const beforeCursor = chatInput.substring(0, mentionCursorPos)
        const afterCursor = chatInput.substring(mentionCursorPos)
        
        // Simple format: just @name with hidden metadata as data attribute
        const mention = `@${option.name}`
        const newValue = chatInput.substring(0, mentionStartPos) + mention + ' ' + afterCursor
        
        setChatInput(newValue)
        setShowMentionDropdown(false)
        
        // Store the mapping internally for later retrieval
        if (!window._mentionMap) window._mentionMap = {}
        window._mentionMap[option.name] = { type: option.type, id: option.id }
    }, [chatInput, mentionCursorPos, mentionStartPos])

    // Parse @mentions from chat input using stored mappings
    const parseMentions = useCallback((text) => {
        const mentions = []
        const mentionMap = window._mentionMap || {}
        
        // Find all @word patterns
        const mentionRegex = /@(\w+)/g
        let match
        
        while ((match = mentionRegex.exec(text)) !== null) {
            const name = match[1]
            if (mentionMap[name]) {
                mentions.push({
                    type: mentionMap[name].type,
                    id: mentionMap[name].id,
                    name: name
                })
            }
        }
        
        return mentions
    }, [])

    // Fetch context for mentioned entities
    const fetchMentionedContext = useCallback(async (mentions) => {
        const context = []
        
        for (const mention of mentions) {
            try {
                if (mention.type === 'board') {
                    const { data: boardData } = await supabase
                        .from('board_code')
                        .select('code')
                        .eq('board_id', mention.id)
                        .order('version', { ascending: false })
                        .limit(1)
                        .maybeSingle()
                    
                    if (boardData) {
                        context.push({
                            type: 'board',
                            name: mention.name,
                            content: boardData.code
                        })
                    }
                } else if (mention.type === 'query') {
                    const { data: queryData } = await supabase
                        .from('board_queries')
                        .select('python_code, description')
                        .eq('id', mention.id)
                        .single()
                    
                    if (queryData) {
                        context.push({
                            type: 'query',
                            name: mention.name,
                            content: queryData.python_code,
                            description: queryData.description
                        })
                    }
                }
            } catch (err) {
                console.error(`Error fetching context for ${mention.type}:${mention.id}`, err)
            }
        }
        
        return context
    }, [])

    const handleChatSubmit = async (e) => {
        e.preventDefault()
        const prompt = chatInput.trim()
        if (!prompt || chatLoading) return

        let chatId
        try {
            chatId = await ensureCurrentChat()
        } catch (err) {
            setChatMessages((prev) => [...prev, { role: 'assistant', content: `Error: ${err?.message || err}` }])
            return
        }

        const userMessage = { role: 'user', content: prompt }
        setChatMessages((prev) => [...prev, userMessage])
        setChatInput('')
        setShowMentionDropdown(false)
        setShowCommandDropdown(false)
        setChatLoading(true)

        try {
            // Generate smart title for first message in chat
            const isFirstMessage = chatMessages.filter(m => m.role === 'user').length === 0
            if (isFirstMessage) {
                // Generate title in background
                generateChatTitle(prompt, 'board').then(async (title) => {
                    try {
                        await supabase.from('chats').update({ title }).eq('id', chatId)
                        setChatList((prev) => prev.map(c => c.id === chatId ? { ...c, title } : c))
                    } catch (err) {
                        console.error('Error updating chat title:', err)
                    }
                })
            }

            // Parse @mentions and fetch their context
            const mentions = parseMentions(prompt)
            const mentionedContext = mentions.length > 0 ? await fetchMentionedContext(mentions) : []

            // Include the user message in the messages array passed to callback
            // Filter out the initial greeting message - it's not real chat history
            const realMessages = chatMessages.filter(m => 
                !(m.role === 'assistant' && m.content === 'Hi! How can I help you?')
            )
            const messagesWithUser = [...realMessages, userMessage]

            // Call the page-specific submit handler if one is registered
            if (onSubmitCallbackRef.current) {
                await onSubmitCallbackRef.current(chatId, prompt, messagesWithUser, mentionedContext)
            } else {
                // Default: just save the message and show a generic response
                await appendMessage(chatId, 'user', prompt)
                const response = { role: 'assistant', content: 'Message received.' }
                setChatMessages((prev) => [...prev, response])
                await appendMessage(chatId, 'assistant', response.content)
            }
        } catch (err) {
            setChatMessages((prev) => [...prev, { role: 'assistant', content: `Error: ${err?.message || String(err)}` }])
        } finally {
            setChatLoading(false)
        }
    }

    // Load chats when boardId changes
    useEffect(() => {
        if (boardId) {
            loadChats(boardId)
        }
    }, [boardId, loadChats])

    // Load messages when currentChatId changes
    useEffect(() => {
        loadMessages(currentChatId)
    }, [currentChatId, loadMessages])

    const value = {
        chatOpen,
        setChatOpen,
        chatList,
        currentChatId,
        setCurrentChatId,
        chatMessages,
        setChatMessages,
        chatInput,
        setChatInput,
        chatLoading,
        setChatLoading,
        showChatListDropdown,
        setShowChatListDropdown,
        editingChatId,
        setEditingChatId,
        editingChatTitle,
        setEditingChatTitle,
        boardId,
        openChatFor,
        startNewChat,
        renameChat,
        formatChatDate,
        handleChatSubmit,
        ensureCurrentChat,
        appendMessage,
        setOnSubmitCallback,
        generateChatTitle,
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
    }

    return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>
}

export function useChat() {
    const context = useContext(ChatContext)
    if (!context) {
        throw new Error('useChat must be used within a ChatProvider')
    }
    return context
}
