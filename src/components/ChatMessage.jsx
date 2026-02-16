import ReactMarkdown from 'react-markdown'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import remarkGfm from 'remark-gfm'
import { Loader2, Brain, ChevronDown, ChevronRight } from 'lucide-react'
import * as Diff from 'diff'
import { useState } from 'react'

export default function ChatMessage({ message, isStreaming = false }) {
    const { role, content, thinking, code_delta, test_result, needs_user_input } = message
    const [showTestDetails, setShowTestDetails] = useState(false)

    const isUser = role === 'user'

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
