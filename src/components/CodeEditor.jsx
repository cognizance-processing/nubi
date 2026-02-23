import { useRef, useCallback, useEffect } from 'react'
import Editor from '@monaco-editor/react'

const DARK_THEME = {
    base: 'vs-dark',
    inherit: true,
    rules: [
        { token: 'comment', foreground: '6b7280', fontStyle: 'italic' },
        { token: 'keyword', foreground: 'c084fc' },
        { token: 'string', foreground: '34d399' },
        { token: 'number', foreground: '60a5fa' },
        { token: 'type', foreground: '38bdf8' },
        { token: 'function', foreground: 'fbbf24' },
        { token: 'variable', foreground: 'e2e8f0' },
        { token: 'operator', foreground: 'f472b6' },
        { token: 'tag', foreground: 'f87171' },
        { token: 'attribute.name', foreground: 'fbbf24' },
        { token: 'attribute.value', foreground: '34d399' },
        { token: 'delimiter', foreground: '94a3b8' },
        { token: 'metatag', foreground: 'f87171' },
    ],
    colors: {
        'editor.background': '#0c0e16',
        'editor.foreground': '#e2e8f0',
        'editor.lineHighlightBackground': '#1e1b4b15',
        'editor.selectionBackground': '#6366f140',
        'editor.inactiveSelectionBackground': '#6366f120',
        'editorLineNumber.foreground': '#334155',
        'editorLineNumber.activeForeground': '#818cf8',
        'editorCursor.foreground': '#818cf8',
        'editorIndentGuide.background': '#1e293b',
        'editorIndentGuide.activeBackground': '#334155',
        'editor.selectionHighlightBackground': '#6366f120',
        'editorBracketMatch.background': '#6366f130',
        'editorBracketMatch.border': '#6366f160',
        'scrollbar.shadow': '#00000000',
        'scrollbarSlider.background': '#ffffff08',
        'scrollbarSlider.hoverBackground': '#ffffff15',
        'scrollbarSlider.activeBackground': '#ffffff20',
        'editorOverviewRuler.border': '#00000000',
        'editorGutter.background': '#0c0e16',
        'editorWidget.background': '#151823',
        'editorWidget.border': '#ffffff15',
        'editorSuggestWidget.background': '#151823',
        'editorSuggestWidget.border': '#ffffff10',
        'editorSuggestWidget.selectedBackground': '#6366f125',
        'editorHoverWidget.background': '#151823',
        'editorHoverWidget.border': '#ffffff10',
        'minimap.background': '#0c0e16',
    },
}

const LANG_MAP = {
    html: 'html',
    python: 'python',
    sql: 'sql',
    javascript: 'javascript',
    css: 'css',
    json: 'json',
}

export default function CodeEditor({
    value,
    onChange,
    language = 'html',
    onSave,
    onRun,
    readOnly = false,
    className = '',
    minimap = false,
}) {
    const editorRef = useRef(null)
    const monacoRef = useRef(null)

    const handleBeforeMount = useCallback((monaco) => {
        monacoRef.current = monaco
        monaco.editor.defineTheme('nubi-dark', DARK_THEME)
    }, [])

    const handleMount = useCallback((editor, monaco) => {
        editorRef.current = editor
        monaco.editor.setTheme('nubi-dark')

        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
            onSave?.()
        })
        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => {
            onRun?.()
        })
    }, [onSave, onRun])

    useEffect(() => {
        const editor = editorRef.current
        if (!editor) return

        const model = editor.getModel()
        if (!model) return

        const monacoLang = LANG_MAP[language] || 'plaintext'
        const currentLang = model.getLanguageId()
        if (currentLang !== monacoLang) {
            monacoRef.current?.editor.setModelLanguage(model, monacoLang)
        }
    }, [language])

    return (
        <div className={`w-full h-full bg-[#0c0e16] ${className}`}>
            <Editor
                defaultLanguage={LANG_MAP[language] || 'plaintext'}
                value={value}
                onChange={(val) => onChange(val || '')}
                beforeMount={handleBeforeMount}
                onMount={handleMount}
                theme="nubi-dark"
                options={{
                    fontSize: 12,
                    fontFamily: "'JetBrains Mono', 'Fira Code', 'SF Mono', Menlo, Monaco, 'Courier New', monospace",
                    fontLigatures: true,
                    lineHeight: 20,
                    tabSize: language === 'python' ? 4 : 2,
                    insertSpaces: true,
                    minimap: { enabled: minimap },
                    scrollBeyondLastLine: false,
                    renderLineHighlight: 'line',
                    cursorBlinking: 'smooth',
                    cursorSmoothCaretAnimation: 'on',
                    smoothScrolling: true,
                    padding: { top: 16, bottom: 16 },
                    lineNumbers: 'on',
                    lineNumbersMinChars: 4,
                    glyphMargin: false,
                    folding: true,
                    foldingHighlight: true,
                    bracketPairColorization: { enabled: true },
                    autoClosingBrackets: 'always',
                    autoClosingQuotes: 'always',
                    autoIndent: 'full',
                    formatOnPaste: true,
                    suggestOnTriggerCharacters: true,
                    quickSuggestions: true,
                    wordWrap: 'off',
                    readOnly,
                    domReadOnly: readOnly,
                    overviewRulerBorder: false,
                    overviewRulerLanes: 0,
                    hideCursorInOverviewRuler: true,
                    scrollbar: {
                        verticalScrollbarSize: 6,
                        horizontalScrollbarSize: 6,
                        useShadows: false,
                    },
                    renderWhitespace: 'none',
                    contextmenu: true,
                    mouseWheelZoom: true,
                }}
                loading={
                    <div className="flex items-center justify-center h-full bg-[#0c0e16]">
                        <div className="flex items-center gap-3 text-slate-500 text-sm">
                            <span className="inline-block w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                            Loading editor...
                        </div>
                    </div>
                }
            />
        </div>
    )
}
