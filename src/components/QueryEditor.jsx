import { useState, useEffect, useRef, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { useHeader } from '../contexts/HeaderContext'
import Prism from 'prismjs'
import 'prismjs/themes/prism-tomorrow.css'
import 'prismjs/components/prism-python'

export default function QueryEditor() {
    const { boardId, queryId } = useParams()
    const navigate = useNavigate()
    const { setHeaderContent } = useHeader()
    const [query, setQuery] = useState(null)
    const [pythonCode, setPythonCode] = useState('')
    const [loading, setLoading] = useState(true)
    const [saving, setSaving] = useState(false)
    const [executing, setExecuting] = useState(false)
    const [result, setResult] = useState(null)
    const [error, setError] = useState(null)
    const preRef = useRef(null)
    const lineNumRef = useRef(null)
    const textareaRef = useRef(null)

    const highlightedCode = useMemo(() => {
        const codeToHighlight = pythonCode + (pythonCode.endsWith('\n') ? ' ' : '\n ')
        return Prism.highlight(codeToHighlight, Prism.languages.python, 'python')
    }, [pythonCode])

    useEffect(() => {
        loadQuery()
    }, [queryId])

    const loadQuery = async () => {
        setLoading(true)
        try {
            const { data, error } = await supabase
                .from('board_queries')
                .select('*')
                .eq('id', queryId)
                .single()

            if (error) throw error
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
            const { error } = await supabase
                .from('board_queries')
                .update({
                    python_code: pythonCode,
                    updated_at: new Date().toISOString()
                })
                .eq('id', queryId)

            if (error) throw error
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
            // Save first
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

    const handleKeyDown = (e) => {
        if (e.key === 'Tab') {
            e.preventDefault()
            const start = e.target.selectionStart
            const end = e.target.selectionEnd
            const newCode = pythonCode.substring(0, start) + '    ' + pythonCode.substring(end)
            setPythonCode(newCode)
            setTimeout(() => {
                e.target.selectionStart = e.target.selectionEnd = start + 4
            }, 0)
        } else if ((e.metaKey || e.ctrlKey) && e.key === 's') {
            e.preventDefault()
            saveQuery()
        } else if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
            e.preventDefault()
            executeQuery()
        }
    }

    // Header
    useEffect(() => {
        setHeaderContent(
            <div className="flex items-center gap-6 flex-1 px-4">
                <button
                    className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-text-secondary hover:text-text-primary hover:bg-background-tertiary transition-all font-medium text-sm"
                    onClick={() => navigate(`/board/${boardId}`)}
                >
                    <svg width="18" height="18" viewBox="0 0 20 20" fill="currentColor">
                        <path fillRule="evenodd" d="M9.707 16.707a1 1 0 01-1.414 0l-6-6a1 1 0 010-1.414l6-6a1 1 0 011.414 1.414L5.414 9H17a1 1 0 110 2H5.414l4.293 4.293a1 1 0 010 1.414z" />
                    </svg>
                    <span>Back to Board</span>
                </button>

                <div className="h-6 w-px bg-border-primary mx-2" />

                <div className="flex-1 min-w-0">
                    <h1 className="text-lg font-bold text-text-primary truncate">
                        {query?.name || 'Loading...'}
                    </h1>
                    {query?.description && (
                        <p className="text-xs text-text-secondary truncate">{query.description}</p>
                    )}
                </div>

                <div className="ml-auto flex items-center gap-3">
                    <button 
                        className="btn btn-secondary py-1.5 h-auto text-xs"
                        onClick={executeQuery}
                        disabled={executing}
                    >
                        {executing ? (
                            <span className="inline-block w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
                        ) : (
                            <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor">
                                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z" />
                            </svg>
                        )}
                        Test Run
                    </button>
                    <button 
                        className="btn btn-primary py-1.5 h-auto text-xs"
                        onClick={saveQuery}
                        disabled={saving}
                    >
                        {saving ? (
                            <span className="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                        ) : (
                            <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor">
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

    const lineNumbers = pythonCode.split('\n').map((_, i) => i + 1).join('\n')

    if (loading) {
        return (
            <div className="flex flex-col items-center justify-center h-screen gap-4">
                <div className="spinner" />
                <p className="text-text-muted text-sm font-medium">Loading query...</p>
            </div>
        )
    }

    return (
        <div className="flex flex-col h-full bg-background-primary overflow-hidden">
            {/* Code Editor */}
            <div className="flex-1 flex overflow-hidden min-h-0">
                <div className="flex w-full h-full bg-[#0d0f17] font-mono text-sm leading-relaxed">
                    {/* Line numbers */}
                    <div
                        ref={lineNumRef}
                        className="w-14 py-6 bg-[#111420] border-r border-border-primary text-[#4b5563] text-right select-none overflow-hidden"
                    >
                        <pre className="m-0 px-4 font-inherit leading-relaxed">{lineNumbers}</pre>
                    </div>
                    
                    {/* Code editor */}
                    <div className="relative flex-1 overflow-hidden">
                        <textarea
                            ref={textareaRef}
                            value={pythonCode}
                            onChange={(e) => setPythonCode(e.target.value)}
                            onKeyDown={handleKeyDown}
                            onScroll={(e) => {
                                const { scrollTop, scrollLeft } = e.target
                                if (preRef.current) {
                                    preRef.current.scrollTop = scrollTop
                                    preRef.current.scrollLeft = scrollLeft
                                }
                                if (lineNumRef.current) {
                                    lineNumRef.current.scrollTop = scrollTop
                                }
                            }}
                            className="absolute inset-0 w-full h-full p-6 m-0 border-none bg-transparent resize-none outline-none z-10 overflow-auto scrollbar-thin scrollbar-thumb-border-primary scrollbar-track-transparent font-inherit leading-relaxed whitespace-pre"
                            style={{
                                color: 'transparent',
                                caretColor: '#818cf8',
                            }}
                            spellCheck="false"
                            autoComplete="off"
                            autoCorrect="off"
                            autoCapitalize="off"
                        />
                        <pre
                            ref={preRef}
                            className="absolute inset-0 p-6 m-0 z-0 pointer-events-none overflow-auto font-inherit leading-relaxed"
                            aria-hidden="true"
                        >
                            <code
                                className="language-python block pointer-events-none"
                                dangerouslySetInnerHTML={{ __html: highlightedCode }}
                            />
                        </pre>
                    </div>
                </div>
            </div>

            {/* Results Panel - Always visible */}
            <div className="h-80 border-t border-border-primary bg-background-secondary overflow-hidden flex flex-col">
                <div className="flex items-center justify-between px-6 py-3 border-b border-border-primary bg-background-tertiary/30">
                    <div className="flex items-center gap-3">
                        <h3 className="text-sm font-bold text-text-primary">
                            {executing ? 'Executing...' : error ? 'Error' : result ? `Results (${result?.table?.length || 0} rows)` : 'Query Results'}
                        </h3>
                        {executing && (
                            <span className="inline-block w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                        )}
                    </div>
                    {(result || error) && (
                        <button
                            onClick={() => { setResult(null); setError(null) }}
                            className="p-1.5 rounded-lg text-text-muted hover:text-text-primary hover:bg-background-hover transition-all"
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
                        <div className="flex items-center justify-center h-full text-text-muted">
                            <div className="text-center">
                                <div className="inline-block w-8 h-8 border-3 border-primary border-t-transparent rounded-full animate-spin mb-3" />
                                <p className="text-sm font-medium">Executing query...</p>
                            </div>
                        </div>
                    ) : error ? (
                        <div className="p-6">
                            <div className="bg-status-error/10 border border-status-error/30 rounded-lg p-4">
                                <div className="flex items-start gap-3">
                                    <svg className="w-5 h-5 text-status-error flex-shrink-0 mt-0.5" viewBox="0 0 20 20" fill="currentColor">
                                        <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" />
                                    </svg>
                                    <div className="flex-1">
                                        <h4 className="text-sm font-bold text-status-error mb-2">Query Execution Error</h4>
                                        <pre className="text-status-error/90 font-mono text-xs whitespace-pre-wrap break-words leading-relaxed">
                                            {error}
                                        </pre>
                                    </div>
                                </div>
                            </div>
                        </div>
                    ) : result?.table?.length > 0 ? (
                        <div className="p-6">
                            <div className="overflow-x-auto border border-border-primary rounded-lg">
                                <table className="w-full text-sm text-left">
                                    <thead className="text-xs uppercase bg-background-tertiary text-text-secondary sticky top-0">
                                        <tr>
                                            {Object.keys(result.table[0]).map(key => (
                                                <th key={key} className="px-4 py-3 font-semibold whitespace-nowrap border-b border-border-primary">{key}</th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {result.table.map((row, idx) => (
                                            <tr key={idx} className="border-b border-border-primary hover:bg-background-tertiary/30 transition-colors">
                                                {Object.values(row).map((val, i) => (
                                                    <td key={i} className="px-4 py-3 text-text-primary whitespace-nowrap">
                                                        {val === null ? (
                                                            <span className="text-text-muted italic">null</span>
                                                        ) : typeof val === 'number' ? (
                                                            <span className="text-accent-blue font-mono">{String(val)}</span>
                                                        ) : typeof val === 'boolean' ? (
                                                            <span className={val ? 'text-status-success' : 'text-status-error'}>{String(val)}</span>
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
                            <div className="mt-4 text-xs text-text-muted">
                                Showing {result.table.length} row{result.table.length !== 1 ? 's' : ''}
                            </div>
                        </div>
                    ) : (
                        <div className="flex items-center justify-center h-full text-text-muted">
                            <div className="text-center max-w-md">
                                <svg className="w-16 h-16 mx-auto mb-4 text-text-muted/50" viewBox="0 0 20 20" fill="currentColor">
                                    <path fillRule="evenodd" d="M3 3a1 1 0 000 2v8a2 2 0 002 2h2.586l-1.293 1.293a1 1 0 101.414 1.414L10 15.414l2.293 2.293a1 1 0 001.414-1.414L12.414 15H15a2 2 0 002-2V5a1 1 0 100-2H3zm11.707 4.707a1 1 0 00-1.414-1.414L10 9.586 8.707 8.293a1 1 0 00-1.414 0l-2 2a1 1 0 101.414 1.414L8 10.414l1.293 1.293a1 1 0 001.414 0l4-4z" />
                                </svg>
                                <p className="text-sm font-medium mb-2">No results yet</p>
                                <p className="text-xs">
                                    Click <span className="font-semibold text-primary">Test Run</span> or press <kbd className="px-2 py-1 bg-background-tertiary rounded text-xs font-mono">⌘ + Enter</kbd> to execute your query
                                </p>
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}
