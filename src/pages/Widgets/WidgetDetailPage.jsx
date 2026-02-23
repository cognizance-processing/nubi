import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import api from '../../lib/api'
import { useHeader } from '../../contexts/HeaderContext'
import CodeEditor from '../../components/CodeEditor'
import {
    ArrowLeft, Monitor, Tablet, Smartphone,
    Eye, Code2, Globe, Lock, Save, Copy, Check,
} from 'lucide-react'

const VIEWPORTS = {
    desktop: { label: 'Desktop', width: '100%', icon: Monitor },
    tablet: { label: 'Tablet', width: '768px', icon: Tablet },
    mobile: { label: 'Mobile', width: '375px', icon: Smartphone },
}

const DEFAULT_WIDGET_CODE = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Widget</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #0f172a;
      color: #f1f5f9;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 1.5rem;
    }
    .card {
      background: rgba(30, 41, 59, 0.8);
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 1rem;
      padding: 2rem;
      max-width: 400px;
      width: 100%;
      backdrop-filter: blur(12px);
    }
    .card h2 {
      font-size: 1.25rem;
      font-weight: 700;
      margin-bottom: 0.5rem;
    }
    .card p {
      color: #94a3b8;
      font-size: 0.875rem;
      line-height: 1.6;
    }
  </style>
</head>
<body>
  <div class="card">
    <h2>Hello Widget</h2>
    <p>Edit the code to build your embeddable widget. Use the preview to see changes in real time.</p>
  </div>
</body>
</html>`

export default function WidgetDetailPage() {
    const { widgetId } = useParams()
    const navigate = useNavigate()
    const { setHeaderContent } = useHeader()

    const [widget, setWidget] = useState(null)
    const [code, setCode] = useState(DEFAULT_WIDGET_CODE)
    const [loading, setLoading] = useState(true)
    const [saving, setSaving] = useState(false)
    const [activeTab, setActiveTab] = useState('preview')
    const [viewport, setViewport] = useState('desktop')
    const [isPublic, setIsPublic] = useState(false)
    const [copied, setCopied] = useState(false)
    const [dirty, setDirty] = useState(false)

    const iframeRef = useRef(null)

    const fetchWidget = useCallback(async () => {
        setLoading(true)
        try {
            const data = await api.widgets.get(widgetId)
            setWidget(data)
            if (data.html_code) setCode(data.html_code)
            setIsPublic(!!data.is_public)
        } catch (err) {
            console.error('Error loading widget:', err)
        } finally {
            setLoading(false)
        }
    }, [widgetId])

    useEffect(() => { fetchWidget() }, [fetchWidget])

    const handleCodeChange = (newCode) => {
        setCode(newCode)
        setDirty(true)
    }

    const saveWidget = async () => {
        setSaving(true)
        try {
            await api.widgets.update(widgetId, { html_code: code, is_public: isPublic })
            setDirty(false)
        } catch (err) {
            console.error('Error saving widget:', err)
        } finally {
            setSaving(false)
        }
    }

    const togglePublic = async () => {
        const next = !isPublic
        setIsPublic(next)
        try {
            await api.widgets.update(widgetId, { is_public: next })
        } catch (err) {
            console.error('Error updating visibility:', err)
            setIsPublic(!next)
        }
    }

    const copyEmbedCode = () => {
        const embedUrl = `${window.location.origin}/widgets/${widgetId}/embed`
        const snippet = `<iframe src="${embedUrl}" width="100%" height="400" frameborder="0" style="border:none;border-radius:12px;"></iframe>`
        navigator.clipboard.writeText(snippet)
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
    }


    // Header content
    useEffect(() => {
        setHeaderContent(
            <div className="flex items-center gap-1.5 sm:gap-2 flex-1 min-w-0">
                <button
                    onClick={() => navigate('/widgets')}
                    className="flex items-center gap-1.5 px-2 py-1 rounded-md text-slate-400 hover:text-white hover:bg-white/[0.05] transition text-xs font-medium shrink-0"
                >
                    <ArrowLeft className="h-3.5 w-3.5" />
                    <span className="hidden sm:inline">Widgets</span>
                </button>

                <div className="h-4 w-px bg-white/[0.07] shrink-0 hidden sm:block" />

                {widget && (
                    <span className="hidden sm:inline text-sm font-semibold text-white truncate">{widget.name}</span>
                )}

                <div className="h-4 w-px bg-white/[0.07] shrink-0 hidden sm:block" />

                {/* Preview / Code switcher */}
                <div className="flex items-center bg-white/[0.03] p-0.5 rounded-lg border border-white/[0.07]">
                    <button
                        onClick={() => setActiveTab('preview')}
                        className={`flex items-center gap-1.5 px-2 sm:px-2.5 py-1 rounded-md text-[11px] font-medium transition-all ${
                            activeTab === 'preview'
                                ? 'bg-slate-800 text-indigo-400 shadow-sm'
                                : 'text-slate-500 hover:text-white'
                        }`}
                    >
                        <Eye className="h-3.5 w-3.5" />
                        <span className="hidden sm:inline">Preview</span>
                    </button>
                    <button
                        onClick={() => setActiveTab('code')}
                        className={`flex items-center gap-1.5 px-2 sm:px-2.5 py-1 rounded-md text-[11px] font-medium transition-all ${
                            activeTab === 'code'
                                ? 'bg-slate-800 text-indigo-400 shadow-sm'
                                : 'text-slate-500 hover:text-white'
                        }`}
                    >
                        <Code2 className="h-3.5 w-3.5" />
                        <span className="hidden sm:inline">Code</span>
                    </button>
                </div>

                <div className="flex-1" />

                {/* Public toggle */}
                <button
                    onClick={togglePublic}
                    className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-all ${
                        isPublic
                            ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-400 hover:bg-emerald-500/15'
                            : 'bg-white/[0.02] border-white/[0.08] text-slate-400 hover:bg-white/[0.05] hover:text-white'
                    }`}
                    title={isPublic ? 'Widget is public' : 'Widget is private'}
                >
                    {isPublic ? <Globe className="h-3.5 w-3.5" /> : <Lock className="h-3.5 w-3.5" />}
                    <span className="hidden lg:inline">{isPublic ? 'Public' : 'Private'}</span>
                </button>

                {/* Embed code — hide on mobile */}
                {isPublic && (
                    <button
                        onClick={copyEmbedCode}
                        className="hidden sm:flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-white/[0.08] bg-white/[0.02] text-slate-400 hover:bg-white/[0.05] hover:text-white transition-all"
                        title="Copy embed code"
                    >
                        {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
                        <span className="hidden lg:inline">{copied ? 'Copied' : 'Embed'}</span>
                    </button>
                )}

                {/* Save */}
                <button
                    onClick={saveWidget}
                    disabled={saving || !dirty}
                    className="btn btn-primary text-xs shrink-0"
                >
                    {saving ? (
                        <span className="inline-block w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    ) : (
                        <Save className="h-3.5 w-3.5" />
                    )}
                    <span className="hidden sm:inline">Save</span>
                </button>
            </div>
        )
        return () => setHeaderContent(null)
    }, [widget, activeTab, isPublic, saving, dirty, copied])

    if (loading) {
        return (
            <div className="flex flex-col items-center justify-center min-h-[60vh] gap-3">
                <div className="spinner" />
                <p className="text-slate-500 text-sm">Loading widget...</p>
            </div>
        )
    }

    return (
        <div className="flex flex-col h-full bg-slate-950 overflow-hidden">
            {/* Main content */}
            <div className="flex-1 overflow-hidden relative">
                {activeTab === 'preview' ? (
                    <div className="h-full flex flex-col bg-[#080b14]">
                        {/* Viewport switcher bar */}
                        <div className="flex items-center justify-center gap-1 px-3 py-1.5 bg-slate-950/80 backdrop-blur-md border-b border-white/[0.07] shrink-0">
                            {Object.entries(VIEWPORTS).map(([key, { label, icon: Icon }]) => (
                                <button
                                    key={key}
                                    onClick={() => setViewport(key)}
                                    className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium transition-all ${
                                        viewport === key
                                            ? 'bg-indigo-600 text-white shadow-sm shadow-indigo-900/40'
                                            : 'text-slate-500 hover:text-white hover:bg-white/[0.05]'
                                    }`}
                                    title={label}
                                >
                                    <Icon className="h-3.5 w-3.5" />
                                    <span className="hidden sm:inline">{label}</span>
                                </button>
                            ))}
                        </div>
                        {/* Iframe container */}
                        <div
                            className="flex-1 w-full overflow-auto flex justify-center"
                            style={{ padding: viewport === 'desktop' ? 0 : '1.5rem 1.5rem' }}
                        >
                            <div
                                className="h-full transition-all duration-300 ease-out relative"
                                style={{
                                    width: VIEWPORTS[viewport].width,
                                    maxWidth: '100%',
                                    ...(viewport !== 'desktop' && {
                                        borderRadius: '12px',
                                        border: '1px solid rgba(255,255,255,0.06)',
                                        boxShadow: '0 25px 50px -12px rgba(0,0,0,0.5)',
                                        overflow: 'hidden',
                                    }),
                                }}
                            >
                                <iframe
                                    ref={iframeRef}
                                    className="w-full h-full border-none bg-[#0f172a]"
                                    title="Widget Preview"
                                    srcDoc={code}
                                    sandbox="allow-scripts allow-same-origin"
                                />
                            </div>
                        </div>
                    </div>
                ) : (
                    <CodeEditor
                        value={code}
                        onChange={handleCodeChange}
                        language="html"
                        onSave={saveWidget}
                    />
                )}
            </div>

            {/* Bottom status bar */}
            <div className="flex items-center justify-between px-4 py-1.5 border-t border-white/[0.07] bg-slate-950/80 text-[10px] text-slate-600">
                <div className="flex items-center gap-3">
                    <span>{code.split('\n').length} lines</span>
                    <span>{code.length.toLocaleString()} chars</span>
                </div>
                <div className="flex items-center gap-3">
                    {dirty && <span className="text-amber-500/80">Unsaved changes</span>}
                    <span className="uppercase tracking-wider">{VIEWPORTS[viewport].label}</span>
                </div>
            </div>
        </div>
    )
}
