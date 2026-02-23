import ReactMarkdown from 'react-markdown'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import remarkGfm from 'remark-gfm'
import { ChevronDown, ChevronRight, Database, PlayCircle, CheckCircle, XCircle, Code, Trash2, Search, FileCode, Pencil } from 'lucide-react'
import * as Diff from 'diff'
import { useState } from 'react'

function CollapsibleCode({ language, children }) {
    const [expanded, setExpanded] = useState(false)
    const code = String(children).replace(/\n$/, '')
    const lineCount = code.split('\n').length
    const isLong = lineCount > 8

    return (
        <div className="relative group">
            <div
                className={`overflow-hidden transition-[max-height] duration-300 ease-in-out ${isLong && !expanded ? 'max-h-[12rem]' : ''}`}
            >
                <SyntaxHighlighter
                    style={vscDarkPlus}
                    language={language || 'python'}
                    PreTag="div"
                    customStyle={{
                        margin: 0,
                        borderRadius: '0.5rem',
                        fontSize: '0.75rem',
                        background: '#0f1219',
                        border: '1px solid rgba(255,255,255,0.06)',
                    }}
                >
                    {code}
                </SyntaxHighlighter>
            </div>
            {isLong && (
                <>
                    {!expanded && (
                        <div className="absolute bottom-0 left-0 right-0 h-12 bg-gradient-to-t from-[#0f1219] to-transparent rounded-b-lg pointer-events-none" />
                    )}
                    <button
                        onClick={() => setExpanded(!expanded)}
                        className="absolute bottom-1.5 left-1/2 -translate-x-1/2 flex items-center gap-1 px-2.5 py-1 rounded-md bg-slate-800/90 border border-white/[0.08] text-[10px] font-medium text-slate-400 hover:text-white hover:bg-slate-700/90 transition z-10"
                    >
                        {expanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                        {expanded ? 'Collapse' : `${lineCount} lines`}
                    </button>
                </>
            )}
        </div>
    )
}

function ProgressLog({ lines, isStreaming }) {
    const [expanded, setExpanded] = useState(false)

    if (!lines || lines.length === 0) return null

    if (isStreaming) {
        const visible = lines.slice(-2)
        const hidden = lines.length - visible.length
        return (
            <div className="mt-1.5 space-y-0.5">
                {hidden > 0 && (
                    <button
                        onClick={() => setExpanded(!expanded)}
                        className="text-[10px] text-slate-600 hover:text-slate-400 transition"
                    >
                        {expanded ? 'Hide' : `${hidden} more step${hidden > 1 ? 's' : ''}...`}
                    </button>
                )}
                {expanded && lines.slice(0, -2).map((line, i) => (
                    <div key={i} className="text-[11px] text-slate-600 pl-2 border-l border-white/[0.04] truncate">{line}</div>
                ))}
                {visible.map((line, i) => (
                    <div key={`v-${i}`} className="text-[11px] text-slate-500 pl-2 border-l border-indigo-500/30 truncate">{line}</div>
                ))}
            </div>
        )
    }

    if (lines.length <= 2) {
        return (
            <div className="mt-1.5 space-y-0.5">
                {lines.map((line, i) => (
                    <div key={i} className="text-[11px] text-slate-600 pl-2 border-l border-white/[0.04] truncate">{line}</div>
                ))}
            </div>
        )
    }

    return (
        <div className="mt-1.5">
            <button
                onClick={() => setExpanded(!expanded)}
                className="flex items-center gap-1 text-[10px] text-slate-600 hover:text-slate-400 transition"
            >
                <ChevronRight size={10} className={`transition-transform ${expanded ? 'rotate-90' : ''}`} />
                {lines.length} steps
            </button>
            {expanded && (
                <div className="mt-1 space-y-0.5">
                    {lines.map((line, i) => (
                        <div key={i} className="text-[11px] text-slate-600 pl-2 border-l border-white/[0.04] truncate">{line}</div>
                    ))}
                </div>
            )}
        </div>
    )
}

export default function ChatMessage({ message, isStreaming = false }) {
    const { role, content, thinking, code_delta, test_result, needs_user_input, tool_calls = [], progress = [] } = message
    const [showTestError, setShowTestError] = useState(false)
    const [toolsExpanded, setToolsExpanded] = useState(false)
    const [expandedToolIdx, setExpandedToolIdx] = useState(null)

    const isUser = role === 'user'

    const getToolIcon = (toolName) => {
        switch(toolName) {
            case 'get_datastore_schema': return <Database size={12} />
            case 'list_datastores': return <Database size={12} />
            case 'manage_datastore': return <Database size={12} />
            case 'run_query': return <PlayCircle size={12} />
            case 'execute_query_direct': return <PlayCircle size={12} />
            case 'create_or_update_query': return <Code size={12} />
            case 'get_code': return <FileCode size={12} />
            case 'get_query_code': return <FileCode size={12} />
            case 'get_board_code': return <FileCode size={12} />
            case 'search_code': return <Search size={12} />
            case 'search_query_code': return <Search size={12} />
            case 'search_board_code': return <Search size={12} />
            case 'edit_code': return <Pencil size={12} />
            case 'list_board_queries': return <Code size={12} />
            case 'delete_query': return <Trash2 size={12} />
            default: return <Code size={12} />
        }
    }

    const getToolLabel = (toolName) => {
        switch(toolName) {
            case 'get_datastore_schema': return 'Analyze Schema'
            case 'run_query': return 'Run Query'
            case 'create_or_update_query': return 'Save Query'
            case 'delete_query': return 'Delete Query'
            case 'list_datastores': return 'List Datastores'
            case 'manage_datastore': return 'Manage Datastore'
            case 'get_code': return 'Read Code'
            case 'get_board_code': return 'Read Board Code'
            case 'search_code': return 'Search Code'
            case 'search_board_code': return 'Search Board Code'
            case 'edit_code': return 'Edit Code'
            case 'list_board_queries': return 'List Queries'
            case 'get_query_code': return 'Read Query Code'
            case 'search_query_code': return 'Search Query Code'
            case 'execute_query_direct': return 'Execute SQL'
            default: return toolName.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())
        }
    }

    if (isUser) {
        return (
            <div className="flex justify-end animate-fade-in">
                <div className="max-w-[80%]">
                    {message.files?.length > 0 && (
                        <div className="flex flex-wrap gap-1 mb-1 justify-end">
                            {message.files.map((f, i) => (
                                <span key={i} className="inline-flex items-center gap-1 px-2 py-0.5 bg-indigo-500/20 rounded text-[10px] text-indigo-300">
                                    <svg width="10" height="10" viewBox="0 0 20 20" fill="currentColor"><path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" /></svg>
                                    {f}
                                </span>
                            ))}
                        </div>
                    )}
                    <div className="rounded-2xl rounded-tr-sm bg-indigo-600 text-white shadow-lg px-4 py-2.5 text-[13px] leading-relaxed">
                        {content}
                    </div>
                </div>
            </div>
        )
    }

    const completedTools = tool_calls.filter(t => t.status === 'success' || t.status === 'error')
    const pendingTools = tool_calls.filter(t => t.status === 'started')
    const hasErrors = tool_calls.some(t => t.status === 'error')

    const getToolSummary = (tool) => {
        if (tool.status === 'error') return tool.error ? tool.error.slice(0, 60) + (tool.error.length > 60 ? '...' : '') : 'failed'
        const r = tool.result
        if (!r) return ''
        switch (tool.tool) {
            case 'get_datastore_schema': {
                const s = r.schema || {}
                if (s.datasets) return `${s.datasets.length} dataset${s.datasets.length !== 1 ? 's' : ''}`
                if (s.schemas) return `${s.schemas.length} schema${s.schemas.length !== 1 ? 's' : ''}`
                if (s.tables) return `${s.tables.length} table${s.tables.length !== 1 ? 's' : ''}`
                if (s.columns) return `${s.columns.length} column${s.columns.length !== 1 ? 's' : ''}`
                return ''
            }
            case 'execute_query_direct': return r.success ? `${r.returned_rows || 0} rows` : ''
            case 'run_query': return r.success ? `${r.row_count || 0} rows` : ''
            case 'list_datastores': return `${(r.datastores || []).length} found`
            case 'list_board_queries': return `${r.count ?? (r.queries || []).length} queries`
            case 'get_code':
            case 'get_query_code':
            case 'get_board_code': return r.total_lines ? `${r.total_lines} lines` : ''
            case 'search_code':
            case 'search_board_code':
            case 'search_query_code': return r.matches ? `${r.matches.length} match${r.matches.length !== 1 ? 'es' : ''}` : ''
            case 'edit_code': return r.edits_applied ? `${r.edits_applied} edit${r.edits_applied !== 1 ? 's' : ''}` : ''
            case 'create_or_update_query': return r.success ? (r.name || 'saved') : ''
            case 'delete_query': return r.success ? 'deleted' : ''
            default: return r.message ? r.message.slice(0, 40) + (r.message.length > 40 ? '...' : '') : ''
        }
    }

    return (
        <div className="animate-fade-in w-full">
            {/* Thinking — subtle inline text */}
            {thinking && (
                <div className="text-[11px] text-slate-500 italic mb-1.5">{thinking}</div>
            )}

            {/* Main content — skip if content is raw HTML (should have gone to code editor) */}
            {content && !content.includes('<!DOCTYPE') && !content.match(/^<html[\s>]/i) && (
                <div className="prose prose-sm prose-invert max-w-none text-[13px] text-slate-200 leading-relaxed [&_p]:mb-2 [&_p:last-child]:mb-0 [&_ul]:my-2 [&_ol]:my-2 [&_li]:leading-relaxed [&_a]:text-indigo-400 [&_a:hover]:underline">
                    <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                            code({ node, inline, className, children, ...props }) {
                                const match = /language-(\w+)/.exec(className || '')
                                const language = match ? match[1] : ''
                                return !inline ? (
                                    <CollapsibleCode language={language}>{children}</CollapsibleCode>
                                ) : (
                                    <code className="bg-white/[0.06] px-1.5 py-0.5 rounded text-[0.85em] text-indigo-300" {...props}>
                                        {children}
                                    </code>
                                )
                            },
                            pre: ({ children }) => <div className="my-2">{children}</div>,
                        }}
                    >
                        {content}
                    </ReactMarkdown>
                </div>
            )}

            {/* Progress log */}
            <ProgressLog lines={progress} isStreaming={isStreaming} />

            {/* Tool calls — grouped compact display */}
            {tool_calls.length > 0 && (
                <div className="mt-2">
                    {/* During streaming: show current tool inline */}
                    {isStreaming && pendingTools.length > 0 && (
                        <div className="flex items-center gap-1.5 text-[11px] text-slate-400 mb-1">
                            <span className="flex gap-0.5">
                                <span className="w-1 h-1 rounded-full bg-indigo-400 animate-bounce [animation-delay:0ms]" />
                                <span className="w-1 h-1 rounded-full bg-indigo-400 animate-bounce [animation-delay:150ms]" />
                                <span className="w-1 h-1 rounded-full bg-indigo-400 animate-bounce [animation-delay:300ms]" />
                            </span>
                            <span className="text-slate-500">{getToolLabel(pendingTools[pendingTools.length - 1].tool)}</span>
                        </div>
                    )}

                    {/* Collapsible group header */}
                    {completedTools.length > 0 && (
                        <div>
                            <button
                                onClick={() => setToolsExpanded(!toolsExpanded)}
                                className="flex items-center gap-1.5 text-[11px] text-slate-500 hover:text-slate-300 transition"
                            >
                                <ChevronRight size={10} className={`transition-transform ${toolsExpanded ? 'rotate-90' : ''}`} />
                                <span>
                                    Used {completedTools.length} tool{completedTools.length > 1 ? 's' : ''}
                                </span>
                                {hasErrors && <span className="w-1.5 h-1.5 rounded-full bg-red-400" />}
                                {!hasErrors && <span className="w-1.5 h-1.5 rounded-full bg-green-400/60" />}
                            </button>

                            {toolsExpanded && (
                                <div className="mt-1 ml-1 border-l border-white/[0.05] pl-2 space-y-0.5">
                                    {completedTools.map((tool, index) => {
                                        const isErr = tool.status === 'error'
                                        const isDetailOpen = expandedToolIdx === index
                                        const summary = getToolSummary(tool)
                                        return (
                                            <div key={index}>
                                                <button
                                                    onClick={() => setExpandedToolIdx(isDetailOpen ? null : index)}
                                                    className="flex items-center gap-1.5 w-full text-left text-[11px] py-0.5 hover:text-slate-300 transition"
                                                >
                                                    <span className={isErr ? 'text-red-400' : 'text-slate-500'}>{getToolIcon(tool.tool)}</span>
                                                    <span className={isErr ? 'text-red-400' : 'text-slate-400'}>{getToolLabel(tool.tool)}</span>
                                                    {summary && <span className="text-slate-600 truncate max-w-[160px]">· {summary}</span>}
                                                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${isErr ? 'bg-red-400' : 'bg-green-400/60'}`} />
                                                    <ChevronRight size={9} className={`text-slate-600 ml-auto shrink-0 transition-transform ${isDetailOpen ? 'rotate-90' : ''}`} />
                                                </button>
                                                {isDetailOpen && (
                                                    <ToolDetail tool={tool} />
                                                )}
                                            </div>
                                        )
                                    })}
                                </div>
                            )}
                        </div>
                    )}
                </div>
            )}

            {/* Code diff — starts collapsed */}
            {code_delta && (
                <div className="mt-2">
                    <CodeDiff oldCode={code_delta.old_code} newCode={code_delta.new_code} />
                </div>
            )}

            {/* Test result — compact pill */}
            {test_result && (
                <div className="mt-2">
                    {test_result.success ? (
                        <div className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md bg-green-500/10 text-[11px] text-green-400">
                            <CheckCircle size={11} />
                            <span>Passed — {test_result.row_count || 0} rows</span>
                        </div>
                    ) : (
                        <div>
                            <button
                                onClick={() => setShowTestError(!showTestError)}
                                className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md bg-red-500/10 text-[11px] text-red-400 hover:bg-red-500/15 transition"
                            >
                                <XCircle size={11} />
                                <span>Failed</span>
                                <ChevronRight size={9} className={`transition-transform ${showTestError ? 'rotate-90' : ''}`} />
                            </button>
                            {showTestError && test_result.error && (
                                <div className="mt-1 px-2 py-1.5 rounded-md bg-red-500/5 border border-red-500/10 font-mono text-[10px] text-red-300/80 whitespace-pre-wrap break-words max-h-32 overflow-y-auto">
                                    {test_result.error}
                                </div>
                            )}
                        </div>
                    )}
                </div>
            )}

            {/* User input needed */}
            {needs_user_input && (
                <div className="mt-2 flex items-start gap-2 rounded-lg bg-amber-500/10 border border-amber-500/20 px-3 py-2 text-xs text-amber-200/90">
                    <svg className="w-3.5 h-3.5 text-amber-400 mt-0.5 shrink-0" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" />
                    </svg>
                    <span>Needs your input — reply with more details to continue.</span>
                </div>
            )}

            {/* Streaming indicator — animated dots */}
            {isStreaming && !pendingTools.length && (
                <div className="flex items-center gap-1.5 mt-2">
                    <span className="flex gap-0.5">
                        <span className="w-1 h-1 rounded-full bg-slate-500 animate-bounce [animation-delay:0ms]" />
                        <span className="w-1 h-1 rounded-full bg-slate-500 animate-bounce [animation-delay:150ms]" />
                        <span className="w-1 h-1 rounded-full bg-slate-500 animate-bounce [animation-delay:300ms]" />
                    </span>
                </div>
            )}
        </div>
    )
}

function CodeDiff({ oldCode, newCode }) {
    const [expanded, setExpanded] = useState(false)
    const diff = Diff.diffLines(oldCode || '', newCode || '')
    const hasChanges = diff.some(part => part.added || part.removed)

    if (!hasChanges) {
        return <div className="text-[10px] text-slate-600 italic">No changes</div>
    }

    const added = diff.filter(p => p.added).reduce((s, p) => s + (p.count || 0), 0)
    const removed = diff.filter(p => p.removed).reduce((s, p) => s + (p.count || 0), 0)

    return (
        <div>
            <button
                onClick={() => setExpanded(!expanded)}
                className="flex items-center gap-2 text-[11px] hover:text-slate-300 transition"
            >
                <ChevronRight size={10} className={`text-slate-600 transition-transform ${expanded ? 'rotate-90' : ''}`} />
                <span className="text-slate-500">Code changes</span>
                <span className="text-green-400/70">+{added}</span>
                <span className="text-red-400/70">-{removed}</span>
            </button>

            {expanded && (
                <div className="mt-1 bg-[#0a0d14] rounded-lg overflow-hidden border border-white/[0.06] max-h-72 overflow-y-auto">
                    <div className="font-mono text-[0.7rem] leading-relaxed">
                        {diff.map((part, index) => {
                            if (part.added) {
                                return (
                                    <div key={index} className="bg-green-500/8 text-green-400/90 border-l-2 border-green-500/60">
                                        {part.value.split('\n').map((line, i) => (
                                            line && <div key={i} className="px-3 py-0.5">+ {line}</div>
                                        ))}
                                    </div>
                                )
                            }
                            if (part.removed) {
                                return (
                                    <div key={index} className="bg-red-500/8 text-red-400/90 border-l-2 border-red-500/60">
                                        {part.value.split('\n').map((line, i) => (
                                            line && <div key={i} className="px-3 py-0.5">- {line}</div>
                                        ))}
                                    </div>
                                )
                            }
                            const lines = part.value.split('\n').filter(l => l)
                            if (lines.length > 6) {
                                return (
                                    <div key={index} className="text-slate-600">
                                        {lines.slice(0, 2).map((line, i) => (
                                            <div key={i} className="px-3 py-0.5">  {line}</div>
                                        ))}
                                        <div className="px-3 py-0.5 text-slate-700 text-[10px]">  ··· {lines.length - 4} unchanged lines ···</div>
                                        {lines.slice(-2).map((line, i) => (
                                            <div key={`end-${i}`} className="px-3 py-0.5">  {line}</div>
                                        ))}
                                    </div>
                                )
                            }
                            return (
                                <div key={index} className="text-slate-600">
                                    {lines.map((line, i) => (
                                        <div key={i} className="px-3 py-0.5">  {line}</div>
                                    ))}
                                </div>
                            )
                        })}
                    </div>
                </div>
            )}
        </div>
    )
}

function ToolDetail({ tool }) {
    return (
        <div className="ml-5 mt-0.5 mb-1 space-y-1">
            {tool.args && Object.keys(tool.args).length > 0 && (
                <div className="bg-slate-950/50 rounded px-2 py-1">
                    <div className="font-mono text-[10px] text-slate-600 space-y-0.5">
                        {Object.entries(tool.args).map(([key, value]) => (
                            <div key={key} className="flex gap-1.5">
                                <span className="text-indigo-400/60 shrink-0">{key}:</span>
                                <span className="text-slate-600 truncate">{
                                    typeof value === 'string' && value.length > 80
                                        ? value.substring(0, 80) + '...'
                                        : JSON.stringify(value)
                                }</span>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {tool.status === 'success' && tool.result && (
                <div className="bg-slate-950/50 rounded px-2 py-1">
                    {tool.tool === 'get_datastore_schema' ? (
                        <SchemaDisplay result={tool.result} />
                    ) : tool.tool === 'run_query' ? (
                        <TestQueryDisplay result={tool.result} />
                    ) : tool.tool === 'list_datastores' ? (
                        <div className="font-mono text-[10px] text-slate-400 space-y-0.5">
                            {(tool.result.datastores || []).map((ds, i) => (
                                <div key={i} className="flex gap-2"><span className="text-indigo-400">{ds.name}</span><span className="text-slate-600">{ds.type}</span></div>
                            ))}
                        </div>
                    ) : tool.tool === 'list_board_queries' ? (
                        <div className="font-mono text-[10px] text-slate-400 space-y-0.5">
                            {(tool.result.queries || []).map((q, i) => (
                                <div key={i} className="flex gap-2"><span className="text-indigo-400">{q.name}</span>{q.description && <span className="text-slate-600 truncate">{q.description}</span>}</div>
                            ))}
                            {tool.result.count === 0 && <div className="text-slate-600">No queries found</div>}
                        </div>
                    ) : (tool.tool === 'get_query_code' || tool.tool === 'get_code') ? (
                        <div className="font-mono text-[10px] text-slate-400">
                            {tool.result.name && <div className="text-indigo-400 mb-1">{tool.result.name} ({tool.result.total_lines || '?'} lines)</div>}
                            <pre className="text-slate-600 max-h-20 overflow-y-auto whitespace-pre-wrap break-words">{(tool.result.code || tool.result.python_code || '').slice(0, 500)}</pre>
                        </div>
                    ) : tool.tool === 'edit_code' ? (
                        <div className="font-mono text-[10px] text-green-400/80">
                            {tool.result.edits_applied} edit{tool.result.edits_applied !== 1 ? 's' : ''} applied
                            {tool.result.edits_failed > 0 && <span className="text-red-400/80"> ({tool.result.edits_failed} failed)</span>}
                        </div>
                    ) : (
                        <div className="font-mono text-[10px] text-green-400/80 max-h-20 overflow-y-auto">
                            {tool.result.message || JSON.stringify(tool.result, null, 2)}
                        </div>
                    )}
                </div>
            )}

            {tool.status === 'error' && tool.error && (
                <div className="bg-red-500/5 rounded px-2 py-1 border border-red-500/10">
                    <div className="font-mono text-[10px] text-red-300/80 whitespace-pre-wrap break-words">
                        {tool.error}
                    </div>
                </div>
            )}
        </div>
    )
}

function SchemaDisplay({ result }) {
    const [expandedItems, setExpandedItems] = useState({})
    const schema = result.schema || {}

    const toggleExpand = (key) => {
        setExpandedItems(prev => ({ ...prev, [key]: !prev[key] }))
    }

    const datasets = schema.datasets || []
    const schemas = schema.schemas || []
    const tables = schema.tables || []
    const columns = schema.columns || schema.schema || []

    const topLevel = datasets.length > 0 ? datasets : schemas
    const isTopLevel = topLevel.length > 0
    const isTableList = !isTopLevel && tables.length > 0
    const isColumnList = !isTopLevel && !isTableList && columns.length > 0

    return (
        <div className="text-[10px] space-y-1.5">
            <div className="flex items-center gap-1.5 text-slate-400">
                <Database size={10} />
                <span className="font-medium">{result.datastore_name || 'Datastore'}</span>
                <span className="text-slate-600">· {result.type || result.datastore_type || 'unknown'}</span>
                {schema.dataset && <span className="text-indigo-400/80">/ {schema.dataset}</span>}
                {schema.table && <span className="text-indigo-400/80">/ {schema.table}</span>}
            </div>

            {isTopLevel && (
                <div className="space-y-0.5 max-h-48 overflow-y-auto">
                    {topLevel.map((item, idx) => (
                        <div key={idx}>
                            <button
                                onClick={() => toggleExpand(`ds_${idx}`)}
                                className="flex items-center gap-1 text-left w-full"
                            >
                                <ChevronRight size={9} className={`text-slate-600 transition-transform ${expandedItems[`ds_${idx}`] ? 'rotate-90' : ''}`} />
                                <span className="text-indigo-400">{item.name}</span>
                                {item.table_count !== undefined && <span className="text-slate-600">({item.table_count})</span>}
                            </button>
                            {expandedItems[`ds_${idx}`] && item.tables?.length > 0 && (
                                <div className="ml-3 mt-0.5 space-y-0 text-slate-500">
                                    {item.tables.map((table, tidx) => (
                                        <div key={tidx} className="pl-1.5 border-l border-white/[0.04]">{table.name}</div>
                                    ))}
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            )}

            {isTableList && (
                <div className="space-y-0 max-h-48 overflow-y-auto text-slate-400">
                    {tables.map((table, idx) => (
                        <div key={idx} className="py-0.5">{table.name} {table.column_count && <span className="text-slate-600">({table.column_count} cols)</span>}</div>
                    ))}
                </div>
            )}

            {isColumnList && (
                <div className="max-h-48 overflow-y-auto">
                    <table className="w-full text-[9px]">
                        <thead>
                            <tr className="border-b border-white/[0.05]">
                                <th className="px-1 py-0.5 text-left text-slate-500 font-medium">Column</th>
                                <th className="px-1 py-0.5 text-left text-slate-500 font-medium">Type</th>
                            </tr>
                        </thead>
                        <tbody>
                            {columns.map((col, idx) => (
                                <tr key={idx} className="border-b border-white/[0.02]">
                                    <td className="px-1 py-0.5 text-indigo-400/80">{col.name}</td>
                                    <td className="px-1 py-0.5 text-slate-600">{col.type || col.field_type || ''}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            {!isTopLevel && !isTableList && !isColumnList && (
                <div className="text-slate-600">No schema data</div>
            )}
        </div>
    )
}

function TestQueryDisplay({ result }) {
    return (
        <div className="space-y-1 text-[10px]">
            <div className="flex items-center gap-1.5 text-green-400">
                <CheckCircle size={10} />
                <span>{result.row_count || 0} rows</span>
            </div>
            {result.columns?.length > 0 && (
                <div className="text-slate-600">Columns: {result.columns.join(', ')}</div>
            )}
            {result.sample_rows?.length > 0 && (
                <div className="overflow-x-auto max-h-24">
                    <table className="w-full text-[9px]">
                        <thead className="bg-slate-950 sticky top-0">
                            <tr>
                                {result.columns.map(col => (
                                    <th key={col} className="px-1.5 py-0.5 text-left text-slate-500 font-medium border-b border-white/[0.05]">{col}</th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {result.sample_rows.map((row, idx) => (
                                <tr key={idx} className="border-b border-white/[0.02]">
                                    {result.columns.map(col => (
                                        <td key={col} className="px-1.5 py-0.5 text-slate-600">
                                            {row[col] !== null && row[col] !== undefined ? String(row[col]) : '—'}
                                        </td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    )
}
