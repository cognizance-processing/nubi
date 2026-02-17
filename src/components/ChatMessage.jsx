import ReactMarkdown from 'react-markdown'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import remarkGfm from 'remark-gfm'
import { Loader2, Brain, ChevronDown, ChevronRight, Database, PlayCircle, CheckCircle, XCircle, Code, Trash2 } from 'lucide-react'
import * as Diff from 'diff'
import { useState } from 'react'

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
            case 'get_datastore_schema': return <Database size={14} />
            case 'test_query': return <PlayCircle size={14} />
            case 'create_or_update_query': return <Code size={14} />
            case 'delete_query': return <Trash2 size={14} />
            default: return <Code size={14} />
        }
    }

    const getToolLabel = (toolName) => {
        switch(toolName) {
            case 'get_datastore_schema': return 'Get Database Schema'
            case 'test_query': return 'Test Query'
            case 'create_or_update_query': return 'Save Query'
            case 'delete_query': return 'Delete Query'
            case 'list_datastores': return 'List Datastores'
            case 'get_board_code': return 'Get Board Code'
            case 'list_board_queries': return 'List Queries'
            default: return toolName.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())
        }
    }

    return (
        <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} animate-fade-in`}>
            <div className={`max-w-[85%] rounded-2xl text-sm ${
                isUser
                    ? 'bg-accent-gradient text-white shadow-lg rounded-tr-none px-4 py-3'
                    : 'bg-background-tertiary border border-border-primary text-text-primary rounded-tl-none shadow-sm'
            }`}>
                {/* Thinking section (AI only) */}
                {!isUser && thinking && (
                    <div className="flex items-start gap-2 mb-3 pb-3 border-b border-border-primary/30">
                        <Brain size={14} className="mt-0.5 text-accent-primary flex-shrink-0 animate-pulse" />
                        <div className="text-xs text-text-muted italic leading-relaxed">
                            {thinking}
                        </div>
                    </div>
                )}

                {/* Main content with markdown */}
                <div className={`prose prose-sm max-w-none ${isUser ? 'prose-invert' : 'prose-neutral'}`}>
                    <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                            code({ node, inline, className, children, ...props }) {
                                const match = /language-(\w+)/.exec(className || '')
                                const language = match ? match[1] : ''
                                
                                return !inline ? (
                                    <SyntaxHighlighter
                                        style={vscDarkPlus}
                                        language={language || 'python'}
                                        PreTag="div"
                                        customStyle={{
                                            margin: 0,
                                            borderRadius: '0.5rem',
                                            fontSize: '0.8rem',
                                            background: '#1e1e1e'
                                        }}
                                        {...props}
                                    >
                                        {String(children).replace(/\n$/, '')}
                                    </SyntaxHighlighter>
                                ) : (
                                    <code className={`${isUser ? 'bg-white/20' : 'bg-background-hover'} px-1.5 py-0.5 rounded text-[0.85em]`} {...props}>
                                        {children}
                                    </code>
                                )
                            },
                            pre: ({ children }) => <div className="my-2">{children}</div>,
                            p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
                            ul: ({ children }) => <ul className="my-2 space-y-1">{children}</ul>,
                            ol: ({ children }) => <ol className="my-2 space-y-1">{children}</ol>,
                            li: ({ children }) => <li className="leading-relaxed">{children}</li>,
                            a: ({ href, children }) => (
                                <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent-primary hover:underline">
                                    {children}
                                </a>
                            ),
                        }}
                    >
                        {content}
                    </ReactMarkdown>
                </div>

                {/* Tool calls section */}
                {!isUser && tool_calls && tool_calls.length > 0 && (
                    <div className="mt-3 space-y-2">
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
                {!isUser && code_delta && (
                    <div className="mt-3 pt-3 border-t border-border-primary/30">
                        <CodeDiff oldCode={code_delta.old_code} newCode={code_delta.new_code} />
                    </div>
                )}

                {/* Test result collapsible section */}
                {!isUser && test_result && (
                    <div className="mt-3 pt-3 border-t border-border-primary/30">
                        <button
                            onClick={() => setShowTestDetails(!showTestDetails)}
                            className={`w-full flex items-center justify-between gap-2 text-xs px-3 py-2 rounded-lg transition-colors ${
                                test_result.success 
                                    ? 'bg-green-500/10 hover:bg-green-500/20 text-green-400' 
                                    : 'bg-red-500/10 hover:bg-red-500/20 text-red-400'
                            }`}
                        >
                            <div className="flex items-center gap-2">
                                <span className={`w-2 h-2 rounded-full ${
                                    test_result.success ? 'bg-green-400' : 'bg-red-400'
                                }`} />
                                <span className="font-medium">
                                    {test_result.success ? 'Query Execution Successful' : 'Query Execution Failed'}
                                </span>
                                {test_result.success && test_result.row_count !== undefined && (
                                    <span className="text-text-muted">• {test_result.row_count} rows returned</span>
                                )}
                            </div>
                            {showTestDetails ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                        </button>
                        
                        {showTestDetails && (
                            <div className="mt-2 px-3 py-2 rounded-lg bg-background-hover text-xs text-text-secondary">
                                {test_result.success ? (
                                    <div>
                                        <div className="font-medium text-green-400 mb-1">✓ Test Passed</div>
                                        <div className="text-text-muted">
                                            The query executed successfully and returned {test_result.row_count || 0} row{test_result.row_count !== 1 ? 's' : ''}.
                                        </div>
                                    </div>
                                ) : (
                                    <div>
                                        <div className="font-medium text-red-400 mb-1">✗ Test Failed</div>
                                        <div className="text-text-muted mb-2">The query encountered an error:</div>
                                        <div className="bg-background-primary rounded px-2 py-1 text-red-300 font-mono text-[11px] whitespace-pre-wrap break-words">
                                            {test_result.error}
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                )}

                {/* User input needed indicator */}
                {!isUser && needs_user_input && (
                    <div className="mt-3 pt-3 border-t border-border-primary/30">
                        <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3">
                            <div className="flex items-start gap-2">
                                <svg className="w-4 h-4 text-amber-400 mt-0.5 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                                    <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" />
                                </svg>
                                <div className="flex-1 text-xs text-amber-200 leading-relaxed">
                                    <strong className="font-semibold">Needs your input</strong>
                                    <p className="mt-1 text-amber-200/90">The code is ready but needs refinement. Reply with more details or instructions to continue.</p>
                                </div>
                            </div>
                        </div>
                    </div>
                )}

                {/* Streaming indicator */}
                {isStreaming && !isUser && (
                    <div className="flex items-center gap-2 mt-2 text-text-muted text-xs">
                        <Loader2 size={12} className="animate-spin" />
                        <span>Thinking...</span>
                    </div>
                )}
            </div>
        </div>
    )
}

function CodeDiff({ oldCode, newCode }) {
    const diff = Diff.diffLines(oldCode || '', newCode || '')
    const hasChanges = diff.some(part => part.added || part.removed)

    if (!hasChanges) {
        return (
            <div className="text-xs text-text-muted italic">
                No changes to code
            </div>
        )
    }

    return (
        <div className="space-y-1">
            <div className="text-xs font-semibold text-text-secondary mb-2">Code changes:</div>
            <div className="bg-background-primary rounded-lg overflow-hidden border border-border-primary max-h-60 overflow-y-auto">
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
                                <div key={index} className="text-text-muted">
                                    {lines.slice(0, 2).map((line, i) => (
                                        <div key={i} className="px-3 py-0.5">  {line}</div>
                                    ))}
                                    <div className="px-3 py-0.5 text-text-muted/50">... {lines.length - 4} unchanged lines ...</div>
                                    {lines.slice(-2).map((line, i) => (
                                        <div key={`end-${i}`} className="px-3 py-0.5">  {line}</div>
                                    ))}
                                </div>
                            )
                        }
                        return (
                            <div key={index} className="text-text-muted">
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
            isSuccess ? 'border-green-500/30 bg-green-500/5' : 
            isError ? 'border-red-500/30 bg-red-500/5' : 
            'border-border-primary bg-background-hover'
        }`}>
            <button
                onClick={onToggle}
                className="w-full flex items-center justify-between gap-3 px-3 py-2.5 text-xs hover:bg-background-hover/50 transition-colors rounded-lg"
            >
                <div className="flex items-center gap-2 flex-1">
                    <div className={`p-1.5 rounded-md ${
                        isSuccess ? 'bg-green-500/20 text-green-400' :
                        isError ? 'bg-red-500/20 text-red-400' :
                        'bg-accent-primary/20 text-accent-primary'
                    }`}>
                        {getIcon(tool.tool)}
                    </div>
                    <div className="flex items-center gap-2 text-left">
                        <span className="font-medium text-text-primary">{getLabel(tool.tool)}</span>
                        {isSuccess && <CheckCircle size={14} className="text-green-400" />}
                        {isError && <XCircle size={14} className="text-red-400" />}
                    </div>
                </div>
                {expanded ? <ChevronDown size={14} className="text-text-muted" /> : <ChevronRight size={14} className="text-text-muted" />}
            </button>
            
            {expanded && (
                <div className="px-3 pb-3 space-y-2">
                    {/* Arguments */}
                    {tool.args && Object.keys(tool.args).length > 0 && (
                        <div className="bg-background-primary rounded-md p-2">
                            <div className="text-[10px] uppercase font-semibold text-text-muted mb-1">Arguments</div>
                            <div className="font-mono text-[11px] text-text-secondary space-y-0.5">
                                {Object.entries(tool.args).map(([key, value]) => (
                                    <div key={key} className="flex gap-2">
                                        <span className="text-accent-primary">{key}:</span>
                                        <span className="text-text-muted truncate">{
                                            typeof value === 'string' && value.length > 80 
                                                ? value.substring(0, 80) + '...' 
                                                : JSON.stringify(value)
                                        }</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                    
                    {/* Result */}
                    {isSuccess && tool.result && (
                        <div className="bg-background-primary rounded-md p-2">
                            <div className="text-[10px] uppercase font-semibold text-text-muted mb-1">Result</div>
                            {tool.tool === 'get_datastore_schema' ? (
                                <SchemaDisplay result={tool.result} />
                            ) : tool.tool === 'test_query' ? (
                                <TestQueryDisplay result={tool.result} />
                            ) : (
                                <div className="font-mono text-[11px] text-green-400">
                                    {tool.result.message || JSON.stringify(tool.result, null, 2)}
                                </div>
                            )}
                        </div>
                    )}
                    
                    {/* Error */}
                    {isError && tool.error && (
                        <div className="bg-red-500/10 rounded-md p-2 border border-red-500/30">
                            <div className="text-[10px] uppercase font-semibold text-red-400 mb-1">Error</div>
                            <div className="font-mono text-[11px] text-red-300 whitespace-pre-wrap break-words">
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
            <div className="flex items-center gap-2 text-text-secondary">
                <Database size={12} />
                <span className="font-medium">{result.datastore_name || 'Datastore'}</span>
                <span className="text-text-muted">• {result.type || result.datastore_type || 'unknown'}</span>
                {schema.dataset && <span className="text-accent-primary">/ {schema.dataset}</span>}
                {schema.table && <span className="text-accent-primary">/ {schema.table}</span>}
            </div>
            
            {/* Top-level: Datasets/Schemas with tables */}
            {isTopLevel && (
                <div className="space-y-1 max-h-72 overflow-y-auto">
                    {topLevel.map((item, idx) => (
                        <div key={idx} className="bg-background-hover rounded px-2 py-1.5">
                            <button 
                                onClick={() => toggleExpand(`ds_${idx}`)}
                                className="w-full flex items-center justify-between text-left"
                            >
                                <div className="flex items-center gap-1.5">
                                    {expandedItems[`ds_${idx}`] ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                                    <span className="font-semibold text-accent-primary">{item.name}</span>
                                    {item.table_count !== undefined && (
                                        <span className="text-text-muted text-[10px]">({item.table_count} tables)</span>
                                    )}
                                </div>
                            </button>
                            {expandedItems[`ds_${idx}`] && item.tables && item.tables.length > 0 && (
                                <div className="ml-4 mt-1 space-y-0.5 text-text-muted">
                                    {item.tables.map((table, tidx) => (
                                        <div key={tidx} className="flex items-baseline gap-1.5">
                                            <span className="text-text-muted/50 flex-shrink-0 text-[10px]">├</span>
                                            <span className="text-text-secondary">{table.name}</span>
                                            {table.column_count && <span className="text-text-muted text-[10px]">({table.column_count} cols)</span>}
                                            {table.row_count !== undefined && <span className="text-text-muted text-[10px]">~{table.row_count?.toLocaleString()} rows</span>}
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
                    <div className="text-text-muted mb-1 font-medium">
                        {schema.dataset && `Dataset: ${schema.dataset}`}
                        {schema.schema && `Schema: ${schema.schema}`}
                        {` (${tables.length} tables)`}
                    </div>
                    {tables.map((table, idx) => (
                        <div key={idx} className="flex items-baseline gap-1.5 px-2 py-0.5 bg-background-hover rounded">
                            <span className="text-text-secondary font-medium">{table.name}</span>
                            {table.type && <span className="text-text-muted text-[10px]">({table.type})</span>}
                            {table.column_count && <span className="text-text-muted text-[10px]">{table.column_count} cols</span>}
                            {table.row_count !== undefined && <span className="text-text-muted text-[10px]">~{table.row_count?.toLocaleString()} rows</span>}
                        </div>
                    ))}
                </div>
            )}
            
            {/* Columns list (from table drill-down) */}
            {isColumnList && (
                <div className="space-y-0.5 max-h-60 overflow-y-auto">
                    <div className="text-text-muted mb-1 font-medium">
                        {schema.table && `Table: ${schema.dataset ? schema.dataset + '.' : ''}${schema.table}`}
                        {schema.row_count !== undefined && ` (~${schema.row_count?.toLocaleString()} rows)`}
                        {` — ${columns.length} columns`}
                    </div>
                    <div className="bg-background-hover rounded overflow-hidden">
                        <table className="w-full text-[10px]">
                            <thead>
                                <tr className="border-b border-border-primary">
                                    <th className="px-2 py-1 text-left text-text-secondary font-semibold">Column</th>
                                    <th className="px-2 py-1 text-left text-text-secondary font-semibold">Type</th>
                                    <th className="px-2 py-1 text-left text-text-secondary font-semibold">Info</th>
                                </tr>
                            </thead>
                            <tbody>
                                {columns.map((col, idx) => (
                                    <tr key={idx} className="border-b border-border-primary/20">
                                        <td className="px-2 py-0.5 text-accent-primary font-medium">{col.name}</td>
                                        <td className="px-2 py-0.5 text-text-muted">{col.type || col.field_type || ''}</td>
                                        <td className="px-2 py-0.5 text-text-muted/70">
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
                <div className="text-text-muted text-center py-2">No schema information available</div>
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
                    <div className="text-text-muted mb-1">Columns: {result.columns.join(', ')}</div>
                </div>
            )}
            
            {result.sample_rows && result.sample_rows.length > 0 && (
                <div className="bg-background-hover rounded overflow-hidden">
                    <div className="overflow-x-auto max-h-32">
                        <table className="w-full text-[10px]">
                            <thead className="bg-background-primary sticky top-0">
                                <tr>
                                    {result.columns.map(col => (
                                        <th key={col} className="px-2 py-1 text-left text-text-secondary font-semibold border-b border-border-primary">
                                            {col}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {result.sample_rows.map((row, idx) => (
                                    <tr key={idx} className="border-b border-border-primary/30 hover:bg-background-primary/50">
                                        {result.columns.map(col => (
                                            <td key={col} className="px-2 py-1 text-text-muted">
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
