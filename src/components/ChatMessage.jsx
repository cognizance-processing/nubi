import ReactMarkdown from 'react-markdown'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import remarkGfm from 'remark-gfm'
import { Loader2, Brain, ChevronDown, ChevronRight, Database, PlayCircle, CheckCircle, XCircle, Code, Trash2 } from 'lucide-react'
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

export default function ChatMessage({ message, isStreaming = false }) {
    const { role, content, thinking, code_delta, test_result, needs_user_input, tool_calls = [] } = message
    const [showTestDetails, setShowTestDetails] = useState(false)
    const [expandedTools, setExpandedTools] = useState({})

    const isUser = role === 'user'

    const toggleToolExpand = (index) => {
        setExpandedTools(prev => ({ ...prev, [index]: !prev[index] }))
    }

    const getToolIcon = (toolName) => {
        switch(toolName) {
            case 'get_datastore_schema': return <Database size={13} />
            case 'list_datastores': return <Database size={13} />
            case 'create_or_update_datastore': return <Database size={13} />
            case 'test_datastore': return <Database size={13} />
            case 'run_query': return <PlayCircle size={13} />
            case 'execute_query_direct': return <PlayCircle size={13} />
            case 'create_or_update_query': return <Code size={13} />
            case 'get_query_code': return <Code size={13} />
            case 'search_query_code': return <Code size={13} />
            case 'list_board_queries': return <Code size={13} />
            case 'get_board_code': return <Code size={13} />
            case 'search_board_code': return <Code size={13} />
            case 'delete_query': return <Trash2 size={13} />
            case 'save_keyfile': return <Database size={13} />
            default: return <Code size={13} />
        }
    }

    const getToolLabel = (toolName) => {
        switch(toolName) {
            case 'get_datastore_schema': return 'Analyze Schema'
            case 'run_query': return 'Run Query'
            case 'create_or_update_query': return 'Save Query'
            case 'delete_query': return 'Delete Query'
            case 'list_datastores': return 'List Datastores'
            case 'list_boards': return 'List Boards'
            case 'get_board_code': return 'Get Board Code'
            case 'search_board_code': return 'Search Board Code'
            case 'list_board_queries': return 'List Queries'
            case 'get_query_code': return 'Read Query Code'
            case 'search_query_code': return 'Search Query Code'
            case 'execute_query_direct': return 'Execute Query'
            case 'create_or_update_datastore': return 'Save Datastore'
            case 'test_datastore': return 'Test Connection'
            case 'save_keyfile': return 'Save Keyfile'
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

    return (
        <div className="animate-fade-in w-full">
            {/* Thinking section */}
            {thinking && (
                <div className="flex items-center gap-2 mb-2">
                    <Brain size={12} className="text-indigo-400 animate-pulse" />
                    <span className="text-[11px] text-slate-500 italic">{thinking}</span>
                </div>
            )}

            {/* Main content with markdown */}
            {content && (
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

            {/* Tool calls section */}
            {tool_calls && tool_calls.length > 0 && (
                <div className="mt-3 space-y-1.5">
                    {tool_calls.map((tool, index) => (
                        <ToolCallDisplay 
                            key={index} 
                            tool={tool} 
                            expanded={expandedTools[index]} 
                            onToggle={() => toggleToolExpand(index)}
                            getIcon={getToolIcon}
                            getLabel={getToolLabel}
                        />
                    ))}
                </div>
            )}

            {/* Code diff visualization */}
            {code_delta && (
                <div className="mt-3">
                    <CodeDiff oldCode={code_delta.old_code} newCode={code_delta.new_code} />
                </div>
            )}

            {/* Test result */}
            {test_result && (
                <div className="mt-3">
                    <button
                        onClick={() => setShowTestDetails(!showTestDetails)}
                        className={`w-full flex items-center justify-between gap-2 text-xs px-3 py-2 rounded-lg transition-colors ${
                            test_result.success 
                                ? 'bg-green-500/10 hover:bg-green-500/15 text-green-400' 
                                : 'bg-red-500/10 hover:bg-red-500/15 text-red-400'
                        }`}
                    >
                        <div className="flex items-center gap-2">
                            {test_result.success ? <CheckCircle size={13} /> : <XCircle size={13} />}
                            <span className="font-medium">
                                {test_result.success ? `Query passed — ${test_result.row_count || 0} rows` : 'Query failed'}
                            </span>
                        </div>
                        {showTestDetails ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                    </button>
                    
                    {showTestDetails && (
                        <div className="mt-1.5 px-3 py-2 rounded-lg bg-white/[0.03] text-xs text-slate-400">
                            {test_result.success ? (
                                <div className="text-slate-500">
                                    Returned {test_result.row_count || 0} row{test_result.row_count !== 1 ? 's' : ''}.
                                </div>
                            ) : (
                                <div className="bg-red-500/5 rounded px-2 py-1.5 text-red-300 font-mono text-[11px] whitespace-pre-wrap break-words">
                                    {test_result.error}
                                </div>
                            )}
                        </div>
                    )}
                </div>
            )}

            {/* User input needed */}
            {needs_user_input && (
                <div className="mt-3 flex items-start gap-2 rounded-lg bg-amber-500/10 border border-amber-500/20 px-3 py-2.5 text-xs text-amber-200/90">
                    <svg className="w-3.5 h-3.5 text-amber-400 mt-0.5 shrink-0" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" />
                    </svg>
                    <span>Needs your input — reply with more details to continue.</span>
                </div>
            )}

            {/* Streaming indicator */}
            {isStreaming && (
                <div className="flex items-center gap-2 mt-2 text-indigo-400/70 text-xs">
                    <Loader2 size={12} className="animate-spin" />
                    <span>Thinking...</span>
                </div>
            )}
        </div>
    )
}

function CodeDiff({ oldCode, newCode }) {
    const diff = Diff.diffLines(oldCode || '', newCode || '')
    const hasChanges = diff.some(part => part.added || part.removed)

    if (!hasChanges) {
        return (
            <div className="text-xs text-slate-500 italic">
                No changes to code
            </div>
        )
    }

    return (
        <div className="space-y-1">
            <div className="text-xs font-semibold text-slate-400 mb-2">Code changes:</div>
            <div className="bg-slate-950 rounded-lg overflow-hidden border border-white/[0.07] max-h-60 overflow-y-auto">
                <div className="font-mono text-[0.7rem] leading-relaxed">
                    {diff.map((part, index) => {
                        if (part.added) {
                            return (
                                <div key={index} className="bg-green-500/10 text-green-400 border-l-2 border-green-500">
                                    {part.value.split('\n').map((line, i) => (
                                        line && <div key={i} className="px-3 py-0.5">+ {line}</div>
                                    ))}
                                </div>
                            )
                        }
                        if (part.removed) {
                            return (
                                <div key={index} className="bg-red-500/10 text-red-400 border-l-2 border-red-500">
                                    {part.value.split('\n').map((line, i) => (
                                        line && <div key={i} className="px-3 py-0.5">- {line}</div>
                                    ))}
                                </div>
                            )
                        }
                        // Show max 3 context lines
                        const lines = part.value.split('\n').filter(l => l)
                        if (lines.length > 6) {
                            return (
                                <div key={index} className="text-slate-500">
                                    {lines.slice(0, 2).map((line, i) => (
                                        <div key={i} className="px-3 py-0.5">  {line}</div>
                                    ))}
                                    <div className="px-3 py-0.5 text-slate-600">... {lines.length - 4} unchanged lines ...</div>
                                    {lines.slice(-2).map((line, i) => (
                                        <div key={`end-${i}`} className="px-3 py-0.5">  {line}</div>
                                    ))}
                                </div>
                            )
                        }
                        return (
                            <div key={index} className="text-slate-500">
                                {lines.map((line, i) => (
                                    <div key={i} className="px-3 py-0.5">  {line}</div>
                                ))}
                            </div>
                        )
                    })}
                </div>
            </div>
        </div>
    )
}

function ToolCallDisplay({ tool, expanded, onToggle, getIcon, getLabel }) {
    const isSuccess = tool.status === 'success'
    const isError = tool.status === 'error'
    
    return (
        <div className={`rounded-lg border ${
            isSuccess ? 'border-green-500/15 bg-green-500/[0.03]' : 
            isError ? 'border-red-500/15 bg-red-500/[0.03]' : 
            'border-white/[0.06] bg-white/[0.02]'
        }`}>
            <button
                onClick={onToggle}
                className="w-full flex items-center justify-between gap-2 px-2.5 py-2 text-[11px] hover:bg-white/[0.02] transition-colors rounded-lg"
            >
                <div className="flex items-center gap-2 min-w-0">
                    <div className={`p-1 rounded ${
                        isSuccess ? 'text-green-400' :
                        isError ? 'text-red-400' :
                        'text-indigo-400'
                    }`}>
                        {getIcon(tool.tool)}
                    </div>
                    <span className="font-medium text-slate-300 truncate">{getLabel(tool.tool)}</span>
                    {isSuccess && <CheckCircle size={12} className="text-green-400/60 shrink-0" />}
                    {isError && <XCircle size={12} className="text-red-400/60 shrink-0" />}
                </div>
                <ChevronRight size={12} className={`text-slate-600 shrink-0 transition-transform ${expanded ? 'rotate-90' : ''}`} />
            </button>
            
            {expanded && (
                <div className="px-2.5 pb-2.5 space-y-1.5">
                    {tool.args && Object.keys(tool.args).length > 0 && (
                        <div className="bg-slate-950/50 rounded-md px-2 py-1.5">
                            <div className="font-mono text-[10px] text-slate-500 space-y-0.5">
                                {Object.entries(tool.args).map(([key, value]) => (
                                    <div key={key} className="flex gap-1.5">
                                        <span className="text-indigo-400/80 shrink-0">{key}:</span>
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
                    
                    {isSuccess && tool.result && (
                        <div className="bg-slate-950/50 rounded-md px-2 py-1.5">
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
                            ) : tool.tool === 'get_query_code' ? (
                                <div className="font-mono text-[10px] text-slate-400">
                                    <div className="text-indigo-400 mb-1">{tool.result.name}</div>
                                    <pre className="text-slate-600 max-h-20 overflow-y-auto whitespace-pre-wrap break-words">{tool.result.code || tool.result.python_code}</pre>
                                </div>
                            ) : (
                                <div className="font-mono text-[10px] text-green-400/80">
                                    {tool.result.message || JSON.stringify(tool.result, null, 2)}
                                </div>
                            )}
                        </div>
                    )}
                    
                    {isError && tool.error && (
                        <div className="bg-red-500/5 rounded-md px-2 py-1.5 border border-red-500/15">
                            <div className="font-mono text-[10px] text-red-300/80 whitespace-pre-wrap break-words">
                                {tool.error}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}

function SchemaDisplay({ result }) {
    const [expandedItems, setExpandedItems] = useState({})
    const schema = result.schema || {}
    const schemaType = schema.type || 'unknown'
    
    const toggleExpand = (key) => {
        setExpandedItems(prev => ({ ...prev, [key]: !prev[key] }))
    }
    
    // Datasets view (top-level BigQuery)
    const datasets = schema.datasets || []
    // Schemas view (top-level PostgreSQL)
    const schemas = schema.schemas || []
    // Tables view (dataset drill-down)
    const tables = schema.tables || []
    // Columns view (table drill-down)
    const columns = schema.columns || schema.schema || []
    
    const topLevel = datasets.length > 0 ? datasets : schemas
    const isTopLevel = topLevel.length > 0
    const isTableList = !isTopLevel && tables.length > 0
    const isColumnList = !isTopLevel && !isTableList && columns.length > 0
    
    return (
        <div className="text-[11px] space-y-2">
            {/* Header */}
            <div className="flex items-center gap-2 text-slate-400">
                <Database size={12} />
                <span className="font-medium">{result.datastore_name || 'Datastore'}</span>
                <span className="text-slate-500">• {result.type || result.datastore_type || 'unknown'}</span>
                {schema.dataset && <span className="text-indigo-400">/ {schema.dataset}</span>}
                {schema.table && <span className="text-indigo-400">/ {schema.table}</span>}
            </div>
            
            {/* Top-level: Datasets/Schemas with tables */}
            {isTopLevel && (
                <div className="space-y-1 max-h-72 overflow-y-auto">
                    {topLevel.map((item, idx) => (
                        <div key={idx} className="bg-white/[0.05] rounded px-2 py-1.5">
                            <button 
                                onClick={() => toggleExpand(`ds_${idx}`)}
                                className="w-full flex items-center justify-between text-left"
                            >
                                <div className="flex items-center gap-1.5">
                                    {expandedItems[`ds_${idx}`] ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                                    <span className="font-semibold text-indigo-400">{item.name}</span>
                                    {item.table_count !== undefined && (
                                        <span className="text-slate-500 text-[10px]">({item.table_count} tables)</span>
                                    )}
                                </div>
                            </button>
                            {expandedItems[`ds_${idx}`] && item.tables && item.tables.length > 0 && (
                                <div className="ml-4 mt-1 space-y-0.5 text-slate-500">
                                    {item.tables.map((table, tidx) => (
                                        <div key={tidx} className="flex items-baseline gap-1.5">
                                            <span className="text-slate-600 flex-shrink-0 text-[10px]">├</span>
                                            <span className="text-slate-400">{table.name}</span>
                                            {table.column_count && <span className="text-slate-500 text-[10px]">({table.column_count} cols)</span>}
                                            {table.row_count !== undefined && <span className="text-slate-500 text-[10px]">~{table.row_count?.toLocaleString()} rows</span>}
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            )}
            
            {/* Tables list (from dataset drill-down) */}
            {isTableList && (
                <div className="space-y-0.5 max-h-60 overflow-y-auto">
                    <div className="text-slate-500 mb-1 font-medium">
                        {schema.dataset && `Dataset: ${schema.dataset}`}
                        {schema.schema && `Schema: ${schema.schema}`}
                        {` (${tables.length} tables)`}
                    </div>
                    {tables.map((table, idx) => (
                        <div key={idx} className="flex items-baseline gap-1.5 px-2 py-0.5 bg-white/[0.05] rounded">
                            <span className="text-slate-400 font-medium">{table.name}</span>
                            {table.type && <span className="text-slate-500 text-[10px]">({table.type})</span>}
                            {table.column_count && <span className="text-slate-500 text-[10px]">{table.column_count} cols</span>}
                            {table.row_count !== undefined && <span className="text-slate-500 text-[10px]">~{table.row_count?.toLocaleString()} rows</span>}
                        </div>
                    ))}
                </div>
            )}
            
            {/* Columns list (from table drill-down) */}
            {isColumnList && (
                <div className="space-y-0.5 max-h-60 overflow-y-auto">
                    <div className="text-slate-500 mb-1 font-medium">
                        {schema.table && `Table: ${schema.dataset ? schema.dataset + '.' : ''}${schema.table}`}
                        {schema.row_count !== undefined && ` (~${schema.row_count?.toLocaleString()} rows)`}
                        {` — ${columns.length} columns`}
                    </div>
                    <div className="bg-white/[0.05] rounded overflow-hidden">
                        <table className="w-full text-[10px]">
                            <thead>
                                <tr className="border-b border-white/[0.07]">
                                    <th className="px-2 py-1 text-left text-slate-400 font-semibold">Column</th>
                                    <th className="px-2 py-1 text-left text-slate-400 font-semibold">Type</th>
                                    <th className="px-2 py-1 text-left text-slate-400 font-semibold">Info</th>
                                </tr>
                            </thead>
                            <tbody>
                                {columns.map((col, idx) => (
                                    <tr key={idx} className="border-b border-white/[0.03]">
                                        <td className="px-2 py-0.5 text-indigo-400 font-medium">{col.name}</td>
                                        <td className="px-2 py-0.5 text-slate-500">{col.type || col.field_type || ''}</td>
                                        <td className="px-2 py-0.5 text-slate-500/70">
                                            {col.mode && col.mode !== 'NULLABLE' && <span className="mr-1">{col.mode}</span>}
                                            {col.nullable === false && <span className="mr-1">NOT NULL</span>}
                                            {col.description && <span>{col.description}</span>}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
            
            {/* Empty state */}
            {!isTopLevel && !isTableList && !isColumnList && (
                <div className="text-slate-500 text-center py-2">No schema information available</div>
            )}
        </div>
    )
}

function TestQueryDisplay({ result }) {
    return (
        <div className="space-y-2 text-[11px]">
            <div className="flex items-center gap-2 text-green-400">
                <CheckCircle size={12} />
                <span className="font-medium">{result.row_count || 0} rows returned</span>
            </div>
            
            {result.columns && result.columns.length > 0 && (
                <div>
                    <div className="text-slate-500 mb-1">Columns: {result.columns.join(', ')}</div>
                </div>
            )}
            
            {result.sample_rows && result.sample_rows.length > 0 && (
                <div className="bg-white/[0.05] rounded overflow-hidden">
                    <div className="overflow-x-auto max-h-32">
                        <table className="w-full text-[10px]">
                            <thead className="bg-slate-950 sticky top-0">
                                <tr>
                                    {result.columns.map(col => (
                                        <th key={col} className="px-2 py-1 text-left text-slate-400 font-semibold border-b border-white/[0.07]">
                                            {col}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {result.sample_rows.map((row, idx) => (
                                    <tr key={idx} className="border-b border-white/[0.04] hover:bg-slate-950/50">
                                        {result.columns.map(col => (
                                            <td key={col} className="px-2 py-1 text-slate-500">
                                                {row[col] !== null && row[col] !== undefined ? String(row[col]) : '—'}
                                            </td>
                                        ))}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    )
}
