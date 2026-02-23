import { useState, useEffect, useCallback, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import api from '../lib/api'
import { useHeader } from '../contexts/HeaderContext'
import { useChat } from '../contexts/ChatContext'
import CodeEditor from './CodeEditor'

function extractSqlBlocks(pythonCode) {
    const blocks = []
    const lines = pythonCode.split('\n')
    for (let i = 0; i < lines.length; i++) {
        const trimmed = lines[i].trim()
        if (trimmed.startsWith('# @query:')) {
            const nodeName = findNodeName(lines, i)
            const sql = trimmed.slice('# @query:'.length).trim()
            blocks.push({ lineIndex: i, sql, nodeName })
        }
    }
    return blocks
}

function findNodeName(lines, queryLineIndex) {
    for (let i = queryLineIndex - 1; i >= 0; i--) {
        const t = lines[i].trim()
        if (t.startsWith('# @node:')) return t.slice('# @node:'.length).trim()
        if (t === '' || (!t.startsWith('#') && t !== '')) break
    }
    return 'query'
}

function buildCombinedSql(blocks) {
    if (blocks.length === 0) return ''
    if (blocks.length === 1) return blocks[0].sql
    return blocks.map(b => `-- ${b.nodeName}\n${b.sql}`).join('\n\n')
}

function applySqlBackToCode(pythonCode, blocks, newCombinedSql) {
    if (blocks.length === 0) return pythonCode

    if (blocks.length === 1) {
        const lines = pythonCode.split('\n')
        const line = lines[blocks[0].lineIndex]
        const prefix = line.slice(0, line.indexOf('# @query:')) + '# @query: '
        lines[blocks[0].lineIndex] = prefix + newCombinedSql.trim()
        return lines.join('\n')
    }

    const sqlParts = newCombinedSql
        .split(/\n\s*--\s*\w[\w\s]*\n/)
        .map(s => s.replace(/^--\s*\w[\w\s]*\n/, '').trim())
        .filter(Boolean)

    if (sqlParts.length === 0) return pythonCode

    const lines = pythonCode.split('\n')
    blocks.forEach((block, idx) => {
        if (idx < sqlParts.length) {
            const line = lines[block.lineIndex]
            const prefix = line.slice(0, line.indexOf('# @query:')) + '# @query: '
            lines[block.lineIndex] = prefix + sqlParts[idx].replace(/\n/g, ' ').trim()
        }
    })
    return lines.join('\n')
}

function formatSql(sql) {
    const keywords = [
        'SELECT', 'FROM', 'WHERE', 'AND', 'OR', 'ORDER BY', 'GROUP BY',
        'HAVING', 'LIMIT', 'OFFSET', 'JOIN', 'LEFT JOIN', 'RIGHT JOIN',
        'INNER JOIN', 'OUTER JOIN', 'FULL JOIN', 'CROSS JOIN', 'ON',
        'UNION', 'UNION ALL', 'INSERT INTO', 'VALUES', 'UPDATE', 'SET',
        'DELETE FROM', 'CREATE TABLE', 'ALTER TABLE', 'DROP TABLE',
        'AS', 'DISTINCT', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END',
        'IN', 'NOT', 'NULL', 'IS', 'BETWEEN', 'LIKE', 'EXISTS',
        'WITH', 'PARTITION BY', 'OVER', 'SAFE_CAST', 'CAST',
    ]

    let formatted = sql.trim()

    keywords.forEach(kw => {
        const re = new RegExp(`\\b${kw.replace(/\s+/g, '\\s+')}\\b`, 'gi')
        formatted = formatted.replace(re, kw)
    })

    const breakBefore = [
        'FROM', 'WHERE', 'AND', 'OR', 'ORDER BY', 'GROUP BY',
        'HAVING', 'LIMIT', 'OFFSET', 'LEFT JOIN', 'RIGHT JOIN',
        'INNER JOIN', 'OUTER JOIN', 'FULL JOIN', 'CROSS JOIN', 'JOIN',
        'ON', 'UNION ALL', 'UNION', 'SET', 'VALUES',
    ]

    breakBefore.forEach(kw => {
        const re = new RegExp(`\\s+(${kw.replace(/\s+/g, '\\s+')})\\b`, 'gi')
        formatted = formatted.replace(re, (_, matched) => {
            const indent = ['AND', 'OR', 'ON'].includes(matched.toUpperCase()) ? '  ' : ''
            return '\n' + indent + matched.toUpperCase()
        })
    })

    formatted = formatted.replace(/,\s*/g, ',\n  ')

    const finalLines = formatted.split('\n').map(l => l.trimEnd())
    return finalLines.join('\n')
}


export default function QueryEditor() {
    const { boardId, queryId } = useParams()
    const navigate = useNavigate()
    const { setHeaderContent } = useHeader()
    const { openChatFor, setPageContext, setOnSubmitCallback, setChatMessages, appendMessage, selectedModel } = useChat()
    const [query, setQuery] = useState(null)
    const [pythonCode, setPythonCode] = useState('')
    const [loading, setLoading] = useState(true)
    const [saving, setSaving] = useState(false)
    const [executing, setExecuting] = useState(false)
    const [result, setResult] = useState(null)
    const [error, setError] = useState(null)
    const [activeTab, setActiveTab] = useState('python')

    const sqlBlocks = useMemo(() => extractSqlBlocks(pythonCode), [pythonCode])
    const combinedSql = useMemo(() => buildCombinedSql(sqlBlocks), [sqlBlocks])
    const hasSql = sqlBlocks.length > 0

    const [sqlEditorValue, setSqlEditorValue] = useState('')
    const [sqlDirty, setSqlDirty] = useState(false)

    useEffect(() => {
        if (!sqlDirty) {
            setSqlEditorValue(combinedSql)
        }
    }, [combinedSql, sqlDirty])

    const handleSqlChange = useCallback((val) => {
        setSqlEditorValue(val)
        setSqlDirty(true)
    }, [])

    const applySqlEdits = useCallback(() => {
        const newCode = applySqlBackToCode(pythonCode, sqlBlocks, sqlEditorValue)
        setPythonCode(newCode)
        setSqlDirty(false)
    }, [pythonCode, sqlBlocks, sqlEditorValue])

    const handleFormatSql = useCallback(() => {
        const formatted = formatSql(sqlEditorValue)
        setSqlEditorValue(formatted)
        setSqlDirty(true)
    }, [sqlEditorValue])

    const handleApplyAndFormat = useCallback(() => {
        const formatted = formatSql(sqlEditorValue)
        const newCode = applySqlBackToCode(pythonCode, sqlBlocks, formatted.replace(/\n/g, ' ').replace(/\s+/g, ' '))
        setPythonCode(newCode)
        setSqlEditorValue(formatted)
        setSqlDirty(false)
    }, [pythonCode, sqlBlocks, sqlEditorValue])

    useEffect(() => {
        loadQuery()
    }, [queryId])

    useEffect(() => {
        openChatFor(boardId)
        setPageContext({ type: 'query', boardId, queryId })
        return () => setPageContext({ type: 'general' })
    }, [boardId, queryId, openChatFor, setPageContext])

    useEffect(() => {
        const handleQueryChatSubmit = async (chatId, prompt, messages, mentionedContext = []) => {
            const backendUrl = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000'
            const token = localStorage.getItem('nubi_token')
            const streamHeaders = { 'Content-Type': 'application/json' }
            if (token) streamHeaders['Authorization'] = `Bearer ${token}`

            await appendMessage(chatId, 'user', prompt)

            let contextString = ''
            if (mentionedContext.length > 0) {
                contextString = '\n\nReferenced content:\n'
                mentionedContext.forEach(ctx => {
                    contextString += `\n--- ${ctx.type}: ${ctx.name} ---\n`
                    if (ctx.description) contextString += `Description: ${ctx.description}\n`
                    contextString += `${ctx.content}\n`
                })
            }

            const response = await fetch(`${backendUrl}/board-helper-stream`, {
                method: 'POST',
                headers: streamHeaders,
                body: JSON.stringify({
                    code: pythonCode,
                    user_prompt: prompt + contextString,
                    chat: messages.filter(m => m.role === 'user' || m.role === 'assistant').map(m => ({ role: m.role, content: m.content })),
                    context: 'query',
                    board_id: boardId,
                    query_id: queryId,
                    model: selectedModel,
                    chat_id: chatId,
                })
            })

            if (!response.ok) throw new Error('Stream request failed')

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
            let finalCode = null
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
                            finalCode = data.new_code
                            setChatMessages(prev => [...prev.slice(0, -1), { ...streamingMessage }])
                        } else if (data.type === 'test_result') {
                            streamingMessage.test_result = data
                            setChatMessages(prev => [...prev.slice(0, -1), { ...streamingMessage }])
                        } else if (data.type === 'needs_user_input') {
                            streamingMessage.needs_user_input = { message: data.message, error: data.error }
                            setChatMessages(prev => [...prev.slice(0, -1), { ...streamingMessage }])
                        } else if (data.type === 'final') {
                            finalCode = data.code || finalCode
                            finalSummary = data.message
                            streamingMessage.content = data.message + (progressLines.length ? '\n\n' + progressLines.join('\n') : '')
                            streamingMessage.isStreaming = false
                            setChatMessages(prev => [...prev.slice(0, -1), { ...streamingMessage }])

                            if (finalCode) {
                                setPythonCode(finalCode)
                            }
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
        }

        setOnSubmitCallback(handleQueryChatSubmit)
        return () => setOnSubmitCallback(null)
    }, [pythonCode, boardId, queryId, setOnSubmitCallback, setChatMessages, appendMessage, selectedModel])

    const loadQuery = async () => {
        setLoading(true)
        try {
            const data = await api.queries.get(queryId)
            setQuery(data)
            setPythonCode(data.python_code || '')
        } catch (err) {
            console.error('Error loading query:', err)
            setError('Failed to load query')
        } finally {
            setLoading(false)
        }
    }

    const saveQuery = async () => {
        setSaving(true)
        try {
            await api.queries.update(queryId, { python_code: pythonCode })
            console.log('Query saved successfully!')
        } catch (err) {
            console.error('Error saving query:', err)
            alert('Failed to save query: ' + err.message)
        } finally {
            setSaving(false)
        }
    }

    const executeQuery = async () => {
        setExecuting(true)
        setError(null)
        setResult(null)
        
        try {
            await saveQuery()
            
            const backendUrl = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000'
            const response = await fetch(`${backendUrl}/explore`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    query_id: queryId,
                    args: {}
                })
            })

            const data = await response.json()
            
            if (!response.ok) {
                throw new Error(data.detail || 'Query execution failed')
            }

            setResult(data)
        } catch (err) {
            console.error('Error executing query:', err)
            setError(err.message)
        } finally {
            setExecuting(false)
        }
    }

    useEffect(() => {
        setHeaderContent(
            <div className="flex items-center gap-3 flex-1 px-2">
                <button
                    className="flex items-center gap-1.5 px-2 py-1 rounded-md text-slate-400 hover:text-white hover:bg-slate-800 transition-all font-medium text-xs"
                    onClick={() => navigate(`/board/${boardId}`)}
                >
                    <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
                        <path fillRule="evenodd" d="M9.707 16.707a1 1 0 01-1.414 0l-6-6a1 1 0 010-1.414l6-6a1 1 0 011.414 1.414L5.414 9H17a1 1 0 110 2H5.414l4.293 4.293a1 1 0 010 1.414z" />
                    </svg>
                    <span>Board</span>
                </button>

                <div className="h-4 w-px bg-border-indigo-500" />

                <div className="flex-1 min-w-0">
                    <h1 className="text-[13px] font-semibold text-white truncate">
                        {query?.name || 'Loading...'}
                    </h1>
                    {query?.description && (
                        <p className="text-[10px] text-slate-400 truncate">{query.description}</p>
                    )}
                </div>

                <div className="ml-auto flex items-center gap-2">
                    <button 
                        className="btn btn-secondary"
                        onClick={executeQuery}
                        disabled={executing}
                    >
                        {executing ? (
                            <span className="inline-block w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full animate-spin" />
                        ) : (
                            <svg width="13" height="13" viewBox="0 0 20 20" fill="currentColor">
                                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z" />
                            </svg>
                        )}
                        Run
                    </button>
                    <button 
                        className="btn btn-primary"
                        onClick={saveQuery}
                        disabled={saving}
                    >
                        {saving ? (
                            <span className="inline-block w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                        ) : (
                            <svg width="13" height="13" viewBox="0 0 20 20" fill="currentColor">
                                <path d="M7.707 10.293a1 1 0 10-1.414 1.414l3 3a1 1 0 001.414 0l3-3a1 1 0 00-1.414-1.414L11 11.586V6h5a2 2 0 012 2v7a2 2 0 01-2 2H4a2 2 0 01-2-2V8a2 2 0 012-2h5v5.586l-1.293-1.293zM9 4a1 1 0 012 0v2H9V4z" />
                            </svg>
                        )}
                        Save
                    </button>
                </div>
            </div>
        )
        return () => setHeaderContent(null)
    }, [query, saving, executing, boardId])

    if (loading) {
        return (
            <div className="flex flex-col items-center justify-center h-screen gap-4">
                <div className="spinner" />
                <p className="text-slate-500 text-sm font-medium">Loading query...</p>
            </div>
        )
    }

    return (
        <div className="flex flex-col h-full bg-slate-950 overflow-hidden">
            {/* Editor area with tabs */}
            <div className="flex-1 flex flex-col overflow-hidden min-h-0">
                {/* Tab bar */}
                <div className="flex items-center justify-between px-1 border-b border-white/[0.07] bg-[#0c0e16] shrink-0">
                    <div className="flex items-center">
                        <button
                            onClick={() => { if (sqlDirty) applySqlEdits(); setActiveTab('python') }}
                            className={`flex items-center gap-2 px-4 py-2.5 text-xs font-medium border-b-2 transition-all ${
                                activeTab === 'python'
                                    ? 'border-indigo-500 text-indigo-400'
                                    : 'border-transparent text-slate-500 hover:text-slate-300'
                            }`}
                        >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10 10-4.5 10-10S17.5 2 12 2" />
                                <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
                                <path d="M12 17h.01" />
                            </svg>
                            Python
                        </button>
                        {hasSql && (
                            <button
                                onClick={() => { setSqlDirty(false); setSqlEditorValue(combinedSql); setActiveTab('sql') }}
                                className={`flex items-center gap-2 px-4 py-2.5 text-xs font-medium border-b-2 transition-all ${
                                    activeTab === 'sql'
                                        ? 'border-emerald-500 text-emerald-400'
                                        : 'border-transparent text-slate-500 hover:text-slate-300'
                                }`}
                            >
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                    <ellipse cx="12" cy="5" rx="9" ry="3" />
                                    <path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5" />
                                    <path d="M3 12c0 1.66 4.03 3 9 3s9-1.34 9-3" />
                                </svg>
                                SQL
                            </button>
                        )}
                    </div>

                    {/* SQL tab actions */}
                    {activeTab === 'sql' && (
                        <div className="flex items-center gap-1.5 pr-2">
                            <button
                                onClick={handleFormatSql}
                                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[11px] font-medium text-slate-400 hover:text-white hover:bg-white/[0.06] transition-all"
                                title="Format SQL"
                            >
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                    <path d="M21 10H3" /><path d="M21 6H3" /><path d="M21 14H3" /><path d="M21 18H3" />
                                </svg>
                                Format
                            </button>
                            {sqlDirty && (
                                <button
                                    onClick={handleApplyAndFormat}
                                    className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[11px] font-medium bg-emerald-500/15 text-emerald-400 hover:bg-emerald-500/25 transition-all"
                                    title="Apply changes back to Python code"
                                >
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                        <polyline points="20 6 9 17 4 12" />
                                    </svg>
                                    Apply to Code
                                </button>
                            )}
                        </div>
                    )}
                </div>

                {/* Editor */}
                <div className="flex-1 min-h-0">
                    {activeTab === 'python' ? (
                        <CodeEditor
                            value={pythonCode}
                            onChange={setPythonCode}
                            language="python"
                            onSave={saveQuery}
                            onRun={executeQuery}
                        />
                    ) : (
                        <CodeEditor
                            value={sqlEditorValue}
                            onChange={handleSqlChange}
                            language="sql"
                            onSave={() => { applySqlEdits(); saveQuery() }}
                            onRun={() => { applySqlEdits(); executeQuery() }}
                        />
                    )}
                </div>
            </div>

            {/* Results panel */}
            <div className="h-80 border-t border-white/[0.07] bg-slate-900 overflow-hidden flex flex-col">
                <div className="flex items-center justify-between px-6 py-3 border-b border-white/[0.07] bg-slate-800/30">
                    <div className="flex items-center gap-3">
                        <h3 className="text-sm font-bold text-white">
                            {executing ? 'Executing...' : error ? 'Error' : result ? `Results (${result?.table?.length || 0} rows)` : 'Query Results'}
                        </h3>
                        {executing && (
                            <span className="inline-block w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                        )}
                    </div>
                    {(result || error) && (
                        <button
                            onClick={() => { setResult(null); setError(null) }}
                            className="p-1.5 rounded-lg text-slate-500 hover:text-white hover:bg-white/[0.05] transition-all"
                            title="Clear results"
                        >
                            <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor">
                                <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" />
                            </svg>
                        </button>
                    )}
                </div>
                <div className="flex-1 overflow-auto">
                    {executing ? (
                        <div className="flex items-center justify-center h-full text-slate-500">
                            <div className="text-center">
                                <div className="inline-block w-8 h-8 border-3 border-indigo-500 border-t-transparent rounded-full animate-spin mb-3" />
                                <p className="text-sm font-medium">Executing query...</p>
                            </div>
                        </div>
                    ) : error ? (
                        <div className="p-6">
                            <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4">
                                <div className="flex items-start gap-3">
                                    <svg className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" viewBox="0 0 20 20" fill="currentColor">
                                        <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" />
                                    </svg>
                                    <div className="flex-1">
                                        <h4 className="text-sm font-bold text-red-400 mb-2">Query Execution Error</h4>
                                        <pre className="text-red-400/90 font-mono text-xs whitespace-pre-wrap break-words leading-relaxed">
                                            {error}
                                        </pre>
                                    </div>
                                </div>
                            </div>
                        </div>
                    ) : result?.table?.length > 0 ? (
                        <div className="p-6">
                            <div className="overflow-x-auto border border-white/[0.07] rounded-lg">
                                <table className="w-full text-sm text-left">
                                    <thead className="text-xs uppercase bg-slate-800 text-slate-400 sticky top-0">
                                        <tr>
                                            {Object.keys(result.table[0]).map(key => (
                                                <th key={key} className="px-4 py-3 font-semibold whitespace-nowrap border-b border-white/[0.07]">{key}</th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {result.table.map((row, idx) => (
                                            <tr key={idx} className="border-b border-white/[0.07] hover:bg-slate-800/30 transition-colors">
                                                {Object.values(row).map((val, i) => (
                                                    <td key={i} className="px-4 py-3 text-white whitespace-nowrap">
                                                        {val === null ? (
                                                            <span className="text-slate-500 italic">null</span>
                                                        ) : typeof val === 'number' ? (
                                                            <span className="text-blue-400 font-mono">{String(val)}</span>
                                                        ) : typeof val === 'boolean' ? (
                                                            <span className={val ? 'text-emerald-400' : 'text-red-400'}>{String(val)}</span>
                                                        ) : (
                                                            String(val)
                                                        )}
                                                    </td>
                                                ))}
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                            <div className="mt-4 text-xs text-slate-500">
                                Showing {result.table.length} row{result.table.length !== 1 ? 's' : ''}
                            </div>
                        </div>
                    ) : (
                        <div className="flex items-center justify-center h-full text-slate-500">
                            <div className="text-center max-w-md">
                                <svg className="w-16 h-16 mx-auto mb-4 text-slate-500/50" viewBox="0 0 20 20" fill="currentColor">
                                    <path fillRule="evenodd" d="M3 3a1 1 0 000 2v8a2 2 0 002 2h2.586l-1.293 1.293a1 1 0 101.414 1.414L10 15.414l2.293 2.293a1 1 0 001.414-1.414L12.414 15H15a2 2 0 002-2V5a1 1 0 100-2H3zm11.707 4.707a1 1 0 00-1.414-1.414L10 9.586 8.707 8.293a1 1 0 00-1.414 0l-2 2a1 1 0 101.414 1.414L8 10.414l1.293 1.293a1 1 0 001.414 0l4-4z" />
                                </svg>
                                <p className="text-sm font-medium mb-2">No results yet</p>
                                <p className="text-xs">
                                    Click <span className="font-semibold text-indigo-400">Run</span> or press <kbd className="px-2 py-1 bg-slate-800 rounded text-xs font-mono">Cmd + Enter</kbd> to execute your query
                                </p>
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}
