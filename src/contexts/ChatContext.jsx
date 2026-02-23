import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react'
import api from '../lib/api'
import { useOrg } from './OrgContext'

const ChatContext = createContext()

const DEFAULT_MODEL = 'gemini-2.0-flash'

export function ChatProvider({ children }) {
    const { currentOrg } = useOrg()
    const [chatOpen, setChatOpen] = useState(false)
    const [chatList, setChatList] = useState([])
    const [currentChatId, setCurrentChatId] = useState(null)
    const [chatMessages, setChatMessages] = useState([])
    const [chatInput, setChatInput] = useState('')
    const [chatLoading, setChatLoading] = useState(false)
    const [showChatListDropdown, setShowChatListDropdown] = useState(false)
    const [editingChatId, setEditingChatId] = useState(null)
    const [editingChatTitle, setEditingChatTitle] = useState('')
    
    const [boardId, setBoardId] = useState(null)

    // Page context: { type: 'board'|'query'|'datastore'|'general', boardId?, datastoreId?, queryId? }
    const [pageContext, setPageContext] = useState({ type: 'general' })

    const [selectedModel, setSelectedModel] = useState(() => localStorage.getItem('nubi_selected_model') || DEFAULT_MODEL)
    const [availableModels, setAvailableModels] = useState([])

    const onSubmitCallbackRef = useRef(null)
    const setOnSubmitCallback = useCallback((cb) => {
        onSubmitCallbackRef.current = cb
    }, [])
    
    const [showMentionDropdown, setShowMentionDropdown] = useState(false)
    const [mentionSearch, setMentionSearch] = useState('')
    const [mentionOptions, setMentionOptions] = useState([])
    const [mentionCursorPos, setMentionCursorPos] = useState(0)
    const [mentionStartPos, setMentionStartPos] = useState(0)
    
    const [showCommandDropdown, setShowCommandDropdown] = useState(false)
    const [commandOptions, setCommandOptions] = useState([])
    const [commandCursorPos, setCommandCursorPos] = useState(0)
    const [commandStartPos, setCommandStartPos] = useState(0)

    const [attachedFiles, setAttachedFiles] = useState([])
    const addAttachedFile = useCallback((file) => {
        setAttachedFiles(prev => [...prev, file])
    }, [])
    const removeAttachedFile = useCallback((idx) => {
        setAttachedFiles(prev => prev.filter((_, i) => i !== idx))
    }, [])
    const clearAttachedFiles = useCallback(() => setAttachedFiles([]), [])

    useEffect(() => {
        api.models.list()
            .then(models => {
                setAvailableModels(models || [])
                if (models && models.length > 0) {
                    const ids = models.map(m => m.id)
                    if (!ids.includes(selectedModel)) {
                        setSelectedModel(ids[0])
                    }
                }
            })
            .catch(err => console.error('Error fetching models:', err))
    }, [])

    useEffect(() => {
        localStorage.setItem('nubi_selected_model', selectedModel)
    }, [selectedModel])

    const formatChatDate = (dateStr) => {
        const d = new Date(dateStr)
        return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    }

    const quickTitle = (text) => {
        if (!text) return 'New Chat'
        const cleaned = text.replace(/\s+/g, ' ').trim()
        if (cleaned.length <= 40) return cleaned
        return cleaned.slice(0, 37) + '...'
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
            const data = await api.chats.list(id)
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
            const data = await api.chats.listMessages(chatId)
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

    const ensureCurrentChat = useCallback(async (userPrompt) => {
        if (currentChatId) return currentChatId
        if (!boardId) {
            const title = quickTitle(userPrompt)
            const data = await api.chats.create({ title, organization_id: currentOrg?.id })
            setChatList((prev) => [{ id: data.id, title, updated_at: new Date().toISOString() }, ...prev])
            setCurrentChatId(data.id)
            return data.id
        }
        
        const title = quickTitle(userPrompt)
        const data = await api.chats.create({ title, board_id: boardId, organization_id: currentOrg?.id })
        setChatList((prev) => [{ id: data.id, title, updated_at: new Date().toISOString(), board_id: boardId }, ...prev])
        setCurrentChatId(data.id)
        return data.id
    }, [currentChatId, boardId, currentOrg?.id])

    const generateChatTitle = useCallback(async (userPrompt, context) => {
        try {
            const backendUrl = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000'
            const token = localStorage.getItem('nubi_token')
            const headers = { 'Content-Type': 'application/json' }
            if (token) headers['Authorization'] = `Bearer ${token}`
            const response = await fetch(`${backendUrl}/generate-chat-title`, {
                method: 'POST',
                headers,
                body: JSON.stringify({
                    user_prompt: userPrompt,
                    context: context,
                    model: selectedModel,
                })
            })
            
            if (response.ok) {
                const data = await response.json()
                return data.title
            }
        } catch (err) {
            console.error('Error generating title:', err)
        }
        return userPrompt.slice(0, 40) + (userPrompt.length > 40 ? '...' : '')
    }, [selectedModel])

    const appendMessage = useCallback(async (chatId, role, content) => {
        try {
            await api.chats.createMessage(chatId, role, content)
        } catch (err) {
            console.error('Error saving chat message:', err)
        }
    }, [])

    const startNewChat = useCallback(async () => {
        setShowChatListDropdown(false)
        setCurrentChatId(null)
        setChatMessages([{ role: 'assistant', content: 'Hi! How can I help you?' }])
    }, [])

    const renameChat = useCallback(async (chatId, newTitle) => {
        const title = newTitle.trim()
        if (!title) return
        try {
            await api.chats.update(chatId, title)
            setChatList((prev) => prev.map((c) => (c.id === chatId ? { ...c, title } : c)))
        } catch (err) {
            console.error('Error renaming chat:', err)
        }
    }, [])

    const fetchMentionOptions = useCallback(async (search) => {
        try {
            const boards = await api.boards.list()
            let queries = []
            if (boardId) {
                queries = await api.boards.listQueries(boardId)
            }
            
            const options = [
                ...(boards || [])
                    .filter(b => b.name.toLowerCase().includes(search.toLowerCase()))
                    .slice(0, 5)
                    .map(b => ({ type: 'board', id: b.id, name: b.name })),
                ...(queries || [])
                    .filter(q => q.name.toLowerCase().includes(search.toLowerCase()))
                    .slice(0, 5)
                    .map(q => ({ type: 'query', id: q.id, name: q.name }))
            ]
            
            setMentionOptions(options)
        } catch (err) {
            console.error('Error fetching mention options:', err)
        }
    }, [boardId])

    const handleChatInputChange = useCallback((value, cursorPos) => {
        setChatInput(value)
        
        const beforeCursor = value.substring(0, cursorPos)
        const atIndex = beforeCursor.lastIndexOf('@')
        const slashIndex = beforeCursor.lastIndexOf('/')
        
        if (slashIndex !== -1 && slashIndex > atIndex) {
            const afterSlash = beforeCursor.substring(slashIndex + 1)
            if (!/\s/.test(afterSlash)) {
                setShowMentionDropdown(false)
                setShowCommandDropdown(true)
                setCommandStartPos(slashIndex)
                setCommandCursorPos(cursorPos)
                
                const search = afterSlash.toLowerCase()
                const allCommands = [
                    { id: 'templates', name: 'templates', description: 'Browse component templates' }
                ]
                setCommandOptions(allCommands.filter(cmd => cmd.name.includes(search)))
            } else {
                setShowCommandDropdown(false)
            }
        }
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
        const newValue = chatInput.substring(0, commandStartPos) + `/${command.name} ` + afterCursor
        setChatInput(newValue)
        setShowCommandDropdown(false)
        if (command.id === 'templates') {
            if (!window._chatCommands) window._chatCommands = {}
            window._chatCommands.showTemplates = true
        }
    }, [chatInput, commandCursorPos, commandStartPos])

    const insertMention = useCallback((option) => {
        const afterCursor = chatInput.substring(mentionCursorPos)
        const mention = `@${option.name}`
        const newValue = chatInput.substring(0, mentionStartPos) + mention + ' ' + afterCursor
        setChatInput(newValue)
        setShowMentionDropdown(false)
        if (!window._mentionMap) window._mentionMap = {}
        window._mentionMap[option.name] = { type: option.type, id: option.id }
    }, [chatInput, mentionCursorPos, mentionStartPos])

    const parseMentions = useCallback((text) => {
        const mentions = []
        const mentionMap = window._mentionMap || {}
        const mentionRegex = /@(\w+)/g
        let match
        while ((match = mentionRegex.exec(text)) !== null) {
            const name = match[1]
            if (mentionMap[name]) {
                mentions.push({ type: mentionMap[name].type, id: mentionMap[name].id, name })
            }
        }
        return mentions
    }, [])

    const fetchMentionedContext = useCallback(async (mentions) => {
        const context = []
        for (const mention of mentions) {
            try {
                if (mention.type === 'board') {
                    const data = await api.boards.getCode(mention.id)
                    if (data?.code) {
                        context.push({ type: 'board', name: mention.name, content: data.code })
                    }
                } else if (mention.type === 'query') {
                    const data = await api.queries.get(mention.id)
                    if (data) {
                        context.push({ type: 'query', name: mention.name, content: data.python_code, description: data.description })
                    }
                }
            } catch (err) {
                console.error(`Error fetching context for ${mention.type}:${mention.id}`, err)
            }
        }
        return context
    }, [])

    /**
     * Upload keyfiles (JSON) from attached files and return their stored paths.
     */
    const uploadKeyfiles = useCallback(async (files) => {
        const paths = []
        for (const file of files) {
            if (file.name.endsWith('.json') || file.type === 'application/json') {
                try {
                    const result = await api.upload(file)
                    if (result?.path) paths.push(result.path)
                } catch (err) {
                    console.error('Error uploading keyfile:', err)
                }
            }
        }
        return paths
    }, [])

    /**
     * Universal stream handler for non-board contexts (datastore, general, query).
     * Used when no custom onSubmitCallback is registered.
     */
    const handleUniversalStream = useCallback(async (chatId, prompt, messages, mentionedContext = []) => {
        const backendUrl = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000'
        const token = localStorage.getItem('nubi_token')
        const streamHeaders = { 'Content-Type': 'application/json' }
        if (token) streamHeaders['Authorization'] = `Bearer ${token}`

        await appendMessage(chatId, 'user', prompt)

        // Upload any JSON keyfiles before sending to AI
        let uploadedFilePaths = []
        if (attachedFiles.length > 0) {
            uploadedFilePaths = await uploadKeyfiles(attachedFiles)
        }

        let contextString = ''
        if (mentionedContext.length > 0) {
            contextString = '\n\nReferenced content:\n'
            mentionedContext.forEach(ctx => {
                contextString += `\n--- ${ctx.type}: ${ctx.name} ---\n`
                if (ctx.description) contextString += `Description: ${ctx.description}\n`
                contextString += `${ctx.content}\n`
            })
        }

        const ctx = pageContext.type || 'general'

        const body = {
            user_prompt: prompt + contextString,
            chat: messages.filter(m => m.role === 'user' || m.role === 'assistant').map(m => ({ role: m.role, content: m.content })),
            context: ctx,
            model: selectedModel,
            chat_id: chatId,
            organization_id: currentOrg?.id,
        }

        if (pageContext.boardId) body.board_id = pageContext.boardId
        if (pageContext.datastoreId) body.datastore_id = pageContext.datastoreId
        if (pageContext.queryId) body.query_id = pageContext.queryId
        if (uploadedFilePaths.length > 0) body.uploaded_file_paths = uploadedFilePaths

        const response = await fetch(`${backendUrl}/board-helper-stream`, {
            method: 'POST',
            headers: streamHeaders,
            body: JSON.stringify(body)
        })

        if (!response.ok) {
            throw new Error('Stream request failed')
        }

        const streamingMessage = {
            role: 'assistant',
            content: '',
            thinking: null,
            code_delta: null,
            needs_user_input: null,
            tool_calls: [],
            isStreaming: true,
        }
        setChatMessages(prev => [...prev, streamingMessage])

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        let progressLines = []
        let finalSummary = ''
        const toolCallsMap = new Map()

        try {
            while (true) {
                const { done, value } = await reader.read()
                if (done) break

                buffer += decoder.decode(value, { stream: true })
                const lines = buffer.split('\n')
                buffer = lines.pop() || ''

                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue
                    let data
                    try { data = JSON.parse(line.slice(6)) } catch { continue }

                    if (data.type === 'thinking') {
                        streamingMessage.thinking = data.content
                        setChatMessages(prev => [...prev.slice(0, -1), { ...streamingMessage }])
                    } else if (data.type === 'tool_call') {
                        const toolKey = `${data.tool}_${toolCallsMap.size}`
                        toolCallsMap.set(toolKey, { tool: data.tool, status: data.status, args: data.args })
                        streamingMessage.tool_calls = Array.from(toolCallsMap.values())
                        setChatMessages(prev => [...prev.slice(0, -1), { ...streamingMessage }])
                    } else if (data.type === 'tool_result') {
                        const matchingKeys = Array.from(toolCallsMap.keys()).filter(k => k.startsWith(data.tool + '_'))
                        const pendingKey = matchingKeys.reverse().find(k => toolCallsMap.get(k)?.status === 'started')
                        const toolKey = pendingKey || matchingKeys[matchingKeys.length - 1]
                        if (toolKey) {
                            const existing = toolCallsMap.get(toolKey)
                            toolCallsMap.set(toolKey, { ...existing, status: data.status, result: data.result, error: data.error })
                        } else {
                            toolCallsMap.set(`${data.tool}_${toolCallsMap.size}`, { tool: data.tool, status: data.status, result: data.result, error: data.error })
                        }
                        streamingMessage.tool_calls = Array.from(toolCallsMap.values())
                        setChatMessages(prev => [...prev.slice(0, -1), { ...streamingMessage }])
                    } else if (data.type === 'progress') {
                        progressLines.push(data.content)
                        streamingMessage.content = progressLines.join('\n')
                        setChatMessages(prev => [...prev.slice(0, -1), { ...streamingMessage }])
                    } else if (data.type === 'code_delta') {
                        streamingMessage.code_delta = { old_code: data.old_code, new_code: data.new_code }
                        setChatMessages(prev => [...prev.slice(0, -1), { ...streamingMessage }])
                    } else if (data.type === 'needs_user_input') {
                        streamingMessage.needs_user_input = { message: data.message, error: data.error }
                        setChatMessages(prev => [...prev.slice(0, -1), { ...streamingMessage }])
                    } else if (data.type === 'final') {
                        finalSummary = data.message
                        streamingMessage.content = data.message + (progressLines.length ? '\n\n' + progressLines.join('\n') : '')
                        streamingMessage.isStreaming = false
                        setChatMessages(prev => [...prev.slice(0, -1), { ...streamingMessage }])
                    } else if (data.type === 'error') {
                        streamingMessage.content = `Error: ${data.content}`
                        streamingMessage.isStreaming = false
                        setChatMessages(prev => [...prev.slice(0, -1), { ...streamingMessage }])
                    }
                }
            }

            const messageToSave = finalSummary || streamingMessage.content
            await appendMessage(chatId, 'assistant', messageToSave)
        } catch (error) {
            streamingMessage.content = `Error during streaming: ${error.message}`
            streamingMessage.isStreaming = false
            setChatMessages(prev => [...prev.slice(0, -1), { ...streamingMessage }])
            throw error
        }
    }, [pageContext, selectedModel, currentOrg?.id, appendMessage, attachedFiles, uploadKeyfiles])

    const handleChatSubmit = async (e) => {
        e.preventDefault()
        const prompt = chatInput.trim()
        if (!prompt || chatLoading) return

        let chatId
        const isFirstMessage = chatMessages.filter(m => m.role === 'user').length === 0
        try {
            chatId = await ensureCurrentChat(prompt)
        } catch (err) {
            setChatMessages((prev) => [...prev, { role: 'assistant', content: `Error: ${err?.message || err}` }])
            return
        }

        let fileContext = ''
        if (attachedFiles.length > 0) {
            const fileNames = attachedFiles.map(f => f.name).join(', ')
            fileContext = `\n\n[Attached files: ${fileNames}]`
            for (const file of attachedFiles) {
                if (file.type === 'application/json' || file.name.endsWith('.json') || file.name.endsWith('.csv') || file.name.endsWith('.txt') || file.name.endsWith('.sql')) {
                    try {
                        const text = await file.text()
                        fileContext += `\n\n--- File: ${file.name} ---\n${text.slice(0, 10000)}`
                    } catch {}
                }
            }
        }

        const fullPrompt = prompt + fileContext
        const userMessage = { role: 'user', content: fullPrompt }
        setChatMessages((prev) => [...prev, { role: 'user', content: prompt, files: attachedFiles.map(f => f.name) }])
        setChatInput('')
        const filesToUpload = [...attachedFiles]
        clearAttachedFiles()
        setShowMentionDropdown(false)
        setShowCommandDropdown(false)
        setChatLoading(true)

        try {
            if (isFirstMessage) {
                generateChatTitle(prompt, pageContext.type || 'general').then(async (title) => {
                    try {
                        await api.chats.update(chatId, title)
                        setChatList((prev) => prev.map(c => c.id === chatId ? { ...c, title } : c))
                    } catch (err) {
                        console.error('Error updating chat title:', err)
                    }
                })
            }

            const mentions = parseMentions(fullPrompt)
            const mentionedContext = mentions.length > 0 ? await fetchMentionedContext(mentions) : []

            const realMessages = chatMessages.filter(m => 
                !(m.role === 'assistant' && m.content === 'Hi! How can I help you?')
            )
            const messagesWithUser = [...realMessages, userMessage]

            if (onSubmitCallbackRef.current) {
                await onSubmitCallbackRef.current(chatId, fullPrompt, messagesWithUser, mentionedContext)
            } else {
                // Use the universal stream handler for all non-board contexts
                await handleUniversalStream(chatId, fullPrompt, messagesWithUser, mentionedContext)
            }
        } catch (err) {
            setChatMessages((prev) => [...prev, { role: 'assistant', content: `Error: ${err?.message || String(err)}` }])
        } finally {
            setChatLoading(false)
        }
    }

    useEffect(() => {
        if (boardId) loadChats(boardId)
    }, [boardId, loadChats])

    // Load chats for non-board contexts based on org
    useEffect(() => {
        if (!boardId && currentOrg?.id) {
            api.chats.list(null, currentOrg.id)
                .then(data => {
                    setChatList(data || [])
                    if (!currentChatId && data?.length) {
                        setCurrentChatId(data[0].id)
                    }
                })
                .catch(err => console.error('Error loading org chats:', err))
        }
    }, [boardId, currentOrg?.id])

    useEffect(() => {
        loadMessages(currentChatId)
    }, [currentChatId, loadMessages])

    const value = {
        chatOpen, setChatOpen,
        chatList, currentChatId, setCurrentChatId,
        chatMessages, setChatMessages,
        chatInput, setChatInput,
        chatLoading, setChatLoading,
        showChatListDropdown, setShowChatListDropdown,
        editingChatId, setEditingChatId,
        editingChatTitle, setEditingChatTitle,
        boardId, openChatFor,
        pageContext, setPageContext,
        startNewChat, renameChat, formatChatDate,
        handleChatSubmit, ensureCurrentChat,
        appendMessage, setOnSubmitCallback,
        generateChatTitle,
        selectedModel, setSelectedModel, availableModels,
        showMentionDropdown, setShowMentionDropdown,
        mentionOptions, insertMention,
        handleChatInputChange,
        showCommandDropdown, setShowCommandDropdown,
        commandOptions, insertCommand,
        attachedFiles, addAttachedFile, removeAttachedFile, clearAttachedFiles,
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
