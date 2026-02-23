import { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import api, { invokeBoardHelper } from '../lib/api'
import { useHeader } from '../contexts/HeaderContext'
import { useChat } from '../contexts/ChatContext'
import { useOrg } from '../contexts/OrgContext'
import CodeEditor from './CodeEditor'
import widgetTemplates from '../pages/Widgets/widgetTemplates'
import {
    ArrowLeft, Monitor, Tablet, Smartphone,
    Eye, Code2, Database, Plus, Save, X,
    Puzzle, LayoutGrid, Search, Pencil, Check,
} from 'lucide-react'


// ... existing COMPONENT_TEMPLATES and DEFAULT_HTML_TEMPLATE ...
const COMPONENT_TEMPLATES = [
    {
        id: 'kpi-card',
        name: 'KPI Card',
        type: 'metric',
        template: `<div class="widget" data-type="kpi" x-data="canvasWidget()" x-init="initWidget($el)">
  <div class="kpi-card" x-data="{ value: 1234, label: 'Total Sales', trend: '+12%' }">
    <div class="kpi-label" x-text="label"></div>
    <div class="kpi-value" x-text="value.toLocaleString()"></div>
    <div class="kpi-trend positive" x-text="trend"></div>
  </div>
</div>`
    },
    {
        id: 'bar-chart',
        name: 'Bar Chart',
        type: 'chart',
        template: `<div class="widget" data-type="chart" x-data="canvasWidget()" x-init="initWidget($el)">
  <div class="chart-container" x-data="barChart()">
    <canvas x-ref="chart"></canvas>
  </div>
</div>

<script>
function barChart() {
  return {
    init() {
      new Chart(this.$refs.chart, {
        type: 'bar',
        data: {
          labels: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun'],
          datasets: [{
            label: 'Sales',
            data: [12, 19, 3, 5, 2, 3],
            backgroundColor: 'rgba(99, 102, 241, 0.8)'
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false
        }
      });
    }
  }
}
</script>`
    },
    {
        id: 'line-chart',
        name: 'Line Chart',
        type: 'chart',
        template: `<div class="widget" data-type="chart" x-data="canvasWidget()" x-init="initWidget($el)">
  <div class="chart-container" x-data="lineChart()">
    <canvas x-ref="chart"></canvas>
  </div>
</div>

<script>
function lineChart() {
  return {
    init() {
      new Chart(this.$refs.chart, {
        type: 'line',
        data: {
          labels: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun'],
          datasets: [{
            label: 'Revenue',
            data: [30, 45, 35, 50, 40, 60],
            borderColor: 'rgb(99, 102, 241)',
            tension: 0.4
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false
        }
      });
    }
  }
}
</script>`
    },
    {
        id: 'pie-chart',
        name: 'Pie Chart',
        type: 'chart',
        template: `<div class="widget" data-type="chart" x-data="canvasWidget()" x-init="initWidget($el)">
  <div class="chart-container" x-data="pieChart()">
    <canvas x-ref="chart"></canvas>
  </div>
</div>

<script>
function pieChart() {
  return {
    init() {
      new Chart(this.$refs.chart, {
        type: 'pie',
        data: {
          labels: ['Product A', 'Product B', 'Product C'],
          datasets: [{
            data: [300, 50, 100],
            backgroundColor: [
              'rgba(99, 102, 241, 0.8)',
              'rgba(139, 92, 246, 0.8)',
              'rgba(236, 72, 153, 0.8)'
            ]
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false
        }
      });
    }
  }
}
</script>`
    }
]

const DEFAULT_HTML_TEMPLATE = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Board</title>
  <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/interactjs/dist/interact.min.js"></script>
  <style>
    :root {
      --grid-gap: 1.5rem;
      --primary: #6366f1;
    }
    
    * { margin: 0; padding: 0; box-sizing: border-box; }
    
    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: #0a0e1a;
      color: #f9fafb;
      padding: 2rem;
      min-height: 100vh;
      overflow-x: hidden;
    }
    
    .board-canvas {
      position: relative;
      width: 100%;
      height: 2000px;
      max-width: 1400px;
      margin: 0 auto;
    }
    
    .widget {
      position: absolute;
      touch-action: none;
      user-select: none;
      min-width: 150px;
      min-height: 80px;
      cursor: grab;
      transition: outline 0.2s ease, transform 0.1s ease;
      z-index: 1;
    }
    
    .widget:active { cursor: grabbing; z-index: 10; }
    .widget.interacting { outline: 2px solid var(--primary); }
    
    .widget::after {
      content: '';
      position: absolute;
      right: 4px;
      bottom: 4px;
      width: 12px;
      height: 12px;
      border-right: 2px solid rgba(255, 255, 255, 0.3);
      border-bottom: 2px solid rgba(255, 255, 255, 0.3);
      cursor: nwse-resize;
    }
    
    .kpi-card, .chart-container {
      background: rgba(17, 24, 39, 0.7);
      backdrop-filter: blur(12px);
      border: 1px solid #1f2937;
      border-radius: 0.75rem;
      padding: 1.5rem;
      height: 100%;
      width: 100%;
    }
    
    .kpi-card { display: flex; flex-direction: column; justify-content: center; }
    .kpi-label { color: #9ca3af; font-size: 0.875rem; margin-bottom: 0.5rem; }
    .kpi-value { font-size: 2rem; font-weight: 700; margin-bottom: 0.5rem; }
    .kpi-trend { font-size: 0.875rem; font-weight: 600; }
    .kpi-trend.positive { color: #10b981; }
    .kpi-trend.negative { color: #ef4444; }
    
    .chart-container canvas { max-height: 100%; width: 100% !important; }

    @media (max-width: 480px) {
      body { padding: 1rem; }
      .widget { min-width: 120px; min-height: 60px; }
    }
  </style>
</head>
<body x-data="boardManager()" @resize.window="detectViewport()">
  <div class="board-canvas">
    <!-- Add your components here -->
    
  </div>

  <script>
    function boardManager() {
      return {
        viewport: 'lg',
        init() {
          this.detectViewport();
          window.addEventListener('message', (e) => {
            if (e.data.type === 'GET_HTML') {
              // Prepare clean HTML for export
              const canvas = document.querySelector('.board-canvas').cloneNode(true);
              // Clean up any interaction classes
              canvas.querySelectorAll('.interacting').forEach(el => el.classList.remove('interacting'));
              
              const fullHTML = document.documentElement.outerHTML;
              window.parent.postMessage({ type: 'SYNC_HTML', html: fullHTML }, '*');
            }
          });
        },
        detectViewport() {
          const w = window.innerWidth;
          let newV = 'lg';
          if (w <= 375) newV = 'sm';
          else if (w <= 768) newV = 'md';
          
          if (newV !== this.viewport) {
            this.viewport = newV;
            document.body.setAttribute('data-viewport', newV);
            this.applyLayout();
          }
        },
        applyLayout() {
          document.querySelectorAll('.widget').forEach(el => {
            const v = this.viewport;
            const x = el.getAttribute('data-' + v + '-x') || '0';
            const y = el.getAttribute('data-' + v + '-y') || '0';
            const w = el.getAttribute('data-' + v + '-w') || '300';
            const h = el.getAttribute('data-' + v + '-h') || '200';
            
            el.style.transform = 'translate(' + x + 'px, ' + y + 'px)';
            el.style.width = w.includes('%') ? w : w + 'px';
            el.style.height = h + 'px';
          });
        }
      }
    }

    function canvasWidget() {
      return {
        initWidget(el) {
          const getV = () => document.body.getAttribute('data-viewport') || 'lg';
          
          interact(el)
            .draggable({
              inertia: true,
              modifiers: [
                interact.modifiers.restrictRect({ restriction: 'parent' })
              ],
              listeners: {
                start: (e) => e.target.classList.add('interacting'),
                end: (e) => e.target.classList.remove('interacting'),
                move: (event) => {
                  const target = event.target;
                  const v = getV();
                  const x = (parseFloat(target.getAttribute('data-' + v + '-x')) || 0) + event.dx;
                  const y = (parseFloat(target.getAttribute('data-' + v + '-y')) || 0) + event.dy;

                  target.style.transform = 'translate(' + x + 'px, ' + y + 'px)';
                  target.setAttribute('data-' + v + '-x', x);
                  target.setAttribute('data-' + v + '-y', y);
                }
              }
            })
            .resizable({
              edges: { left: false, right: true, bottom: true, top: false },
              modifiers: [
                interact.modifiers.restrictSize({ min: { width: 120, height: 60 } })
              ],
              listeners: {
                start: (e) => e.target.classList.add('interacting'),
                end: (e) => e.target.classList.remove('interacting'),
                move: (event) => {
                  const target = event.target;
                  const v = getV();
                  
                  target.style.width = event.rect.width + 'px';
                  target.style.height = event.rect.height + 'px';
                  
                  target.setAttribute('data-' + v + '-w', event.rect.width);
                  target.setAttribute('data-' + v + '-h', event.rect.height);
                }
              }
            })
        }
      }
    }
  </script>
</body>
</html>`

const VIEWPORTS = {
    lg: { name: 'Desktop', width: '100%', icon: Monitor },
    md: { name: 'Tablet', width: '768px', icon: Tablet },
    sm: { name: 'Mobile', width: '375px', icon: Smartphone },
}

export default function BoardEditor() {
    const { boardId } = useParams()
    const navigate = useNavigate()
    const { setHeaderContent } = useHeader()
    const { currentOrg } = useOrg()
    const { openChatFor, setOnSubmitCallback, chatMessages, setChatMessages, setChatLoading, ensureCurrentChat, appendMessage, selectedModel, setPageContext } = useChat()
    const [board, setBoard] = useState(null)
    const [code, setCode] = useState(DEFAULT_HTML_TEMPLATE)
    const [activeTab, setActiveTab] = useState('preview')
    const [viewport, setViewport] = useState('lg')
    const [showTemplates, setShowTemplates] = useState(false)
    const [queries, setQueries] = useState([])
    const [loadingQueries, setLoadingQueries] = useState(false)
    const [showCreateQuery, setShowCreateQuery] = useState(false)
    const [createQueryName, setCreateQueryName] = useState('')
    const [createQueryDescription, setCreateQueryDescription] = useState('')
    const [creatingQuery, setCreatingQuery] = useState(false)
    const [deleteConfirm, setDeleteConfirm] = useState(null)
    const [deleting, setDeleting] = useState(false)
    const [userWidgets, setUserWidgets] = useState([])
    const [pickerTab, setPickerTab] = useState('templates')
    const [pickerSearch, setPickerSearch] = useState('')
    const [editingName, setEditingName] = useState(false)
    const [nameValue, setNameValue] = useState('')
    const nameInputRef = useRef(null)
    const iframeRef = useRef(null)

    const handleSaveShortcut = useCallback(() => { saveCode() }, [code])

    const loadBoard = async () => {
        try {
            const data = await api.boards.get(boardId)
            setBoard(data)
        } catch (error) {
            console.error('Error loading board:', error)
        }
    }

    const loadUserWidgets = async () => {
        try {
            const data = await api.widgets.list(currentOrg?.id)
            setUserWidgets(data || [])
        } catch (error) {
            console.error('Error loading user widgets:', error)
        }
    }

    const startEditingName = () => {
        setNameValue(board?.name || '')
        setEditingName(true)
        setTimeout(() => nameInputRef.current?.select(), 0)
    }

    const saveBoardName = async () => {
        const trimmed = nameValue.trim()
        if (!trimmed || trimmed === board?.name) {
            setEditingName(false)
            return
        }
        try {
            const updated = await api.boards.update(boardId, { name: trimmed })
            setBoard(updated)
        } catch (error) {
            console.error('Error updating board name:', error)
        }
        setEditingName(false)
    }

    const loadBoardCode = async () => {
        try {
            const data = await api.boards.getCode(boardId)
            if (data && data.code) {
                setCode(data.code)
            }
        } catch (error) {
            console.error('Error loading board code:', error)
        }
    }

    const loadQueries = useCallback(async () => {
        setLoadingQueries(true)
        try {
            const data = await api.boards.listQueries(boardId)
            setQueries(data || [])
        } catch (error) {
            console.error('Error loading queries:', error)
        } finally {
            setLoadingQueries(false)
        }
    }, [boardId])

    useEffect(() => {
        loadBoard()
        loadBoardCode()
        loadQueries()
        loadUserWidgets()
        openChatFor(boardId)
        setPageContext({ type: 'board', boardId })
        return () => setPageContext({ type: 'general' })
    }, [boardId, openChatFor, loadQueries, setPageContext])
    
    // Listen for /templates command
    useEffect(() => {
        const checkTemplateCommand = setInterval(() => {
            if (window._chatCommands?.showTemplates) {
                setShowTemplates(true)
                window._chatCommands.showTemplates = false
            }
        }, 100)
        
        return () => clearInterval(checkTemplateCommand)
    }, [])

    // Set up the submit callback for board-helper
    useEffect(() => {
        const handleBoardChatSubmit = async (chatId, prompt, messages, mentionedContext = []) => {
            const codeToSend = code || DEFAULT_HTML_TEMPLATE

            // Build context string from mentions
            let contextString = ''
            if (mentionedContext.length > 0) {
                contextString = '\n\nReferenced content:\n'
                mentionedContext.forEach(ctx => {
                    contextString += `\n--- ${ctx.type}: ${ctx.name} ---\n`
                    if (ctx.description) contextString += `Description: ${ctx.description}\n`
                    contextString += `${ctx.content}\n`
                })
            }

            // Use streaming for better UX
            const backendUrl = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000'
            
            // Add user message to UI immediately
            await appendMessage(chatId, 'user', prompt)
            
            const token = localStorage.getItem('nubi_token')
            const streamHeaders = { 'Content-Type': 'application/json' }
            if (token) streamHeaders['Authorization'] = `Bearer ${token}`
            const response = await fetch(`${backendUrl}/board-helper-stream`, {
                method: 'POST',
                headers: streamHeaders,
                body: JSON.stringify({
                    code: codeToSend,
                    user_prompt: prompt + contextString,
                    chat: messages.filter((m) => m.role === 'user' || m.role === 'assistant').map((m) => ({ role: m.role, content: m.content })),
                    context: 'board',
                    board_id: boardId,
                    model: selectedModel,
                    chat_id: chatId,
                    organization_id: currentOrg?.id,
                })
            })

            if (!response.ok) {
                throw new Error('Stream request failed')
            }

            // Create streaming message that updates in real-time
            const streamingMessage = {
                role: 'assistant',
                content: '',
                thinking: null,
                code_delta: null,
                needs_user_input: null,
                tool_calls: []
            }
            setChatMessages(prev => [...prev, streamingMessage])

            const reader = response.body.getReader()
            const decoder = new TextDecoder()
            let buffer = ''
            let finalCode = null
            let progressLines = []
            let finalSummary = '' // Clean summary for DB storage
            const toolCallsMap = new Map() // Track tool calls by name

            try {
                while (true) {
                    const { done, value } = await reader.read()
                    if (done) break

                    buffer += decoder.decode(value, { stream: true })
                    const lines = buffer.split('\n')
                    buffer = lines.pop() || ''

                    for (const line of lines) {
                        if (!line.startsWith('data: ')) continue
                        const data = JSON.parse(line.slice(6))

                        if (data.type === 'thinking') {
                            streamingMessage.thinking = data.content
                            setChatMessages(prev => [...prev.slice(0, -1), { ...streamingMessage }])
                        } else if (data.type === 'tool_call') {
                            // Track tool call started
                            const toolKey = `${data.tool}_${toolCallsMap.size}`
                            toolCallsMap.set(toolKey, {
                                tool: data.tool,
                                status: data.status,
                                args: data.args
                            })
                            streamingMessage.tool_calls = Array.from(toolCallsMap.values())
                            setChatMessages(prev => [...prev.slice(0, -1), { ...streamingMessage }])
                        } else if (data.type === 'tool_result') {
                            // Update the most recent tool call of this type that doesn't have a result yet
                            const matchingKeys = Array.from(toolCallsMap.keys()).filter(k => k.startsWith(data.tool + '_'))
                            const pendingKey = matchingKeys.reverse().find(k => {
                                const entry = toolCallsMap.get(k)
                                return entry && entry.status === 'started'
                            })
                            const toolKey = pendingKey || matchingKeys[matchingKeys.length - 1]
                            if (toolKey) {
                                const existing = toolCallsMap.get(toolKey)
                                toolCallsMap.set(toolKey, {
                                    ...existing,
                                    status: data.status,
                                    result: data.result,
                                    error: data.error
                                })
                            } else {
                                // Fallback: create new entry
                                const newKey = `${data.tool}_${toolCallsMap.size}`
                                toolCallsMap.set(newKey, {
                                    tool: data.tool,
                                    status: data.status,
                                    result: data.result,
                                    error: data.error
                                })
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
                        } else if (data.type === 'needs_user_input') {
                            streamingMessage.needs_user_input = { message: data.message, error: data.error }
                            setChatMessages(prev => [...prev.slice(0, -1), { ...streamingMessage }])
                        } else if (data.type === 'final') {
                            finalCode = data.code
                            finalSummary = data.message // Clean summary for DB
                            streamingMessage.content = data.message + '\n\n' + progressLines.join('\n')
                            setChatMessages(prev => [...prev.slice(0, -1), { ...streamingMessage }])
                            
                            // Update the code editor and iframe
                            if (finalCode) {
                                setCode(finalCode)
                                if (iframeRef.current) {
                                    iframeRef.current.srcdoc = finalCode
                                }
                            }
                        } else if (data.type === 'error') {
                            streamingMessage.content = `❌ Error: ${data.content}`
                            setChatMessages(prev => [...prev.slice(0, -1), { ...streamingMessage }])
                        }
                    }
                }

                // Save a clean summary to DB (not progress logs) for chat continuation
                const messageToSave = finalSummary || streamingMessage.content
                await appendMessage(chatId, 'assistant', messageToSave)
                
                // Reload queries in case any were created/updated/deleted
                await loadQueries()
                
            } catch (error) {
                streamingMessage.content = `Error during streaming: ${error.message}`
                setChatMessages(prev => [...prev.slice(0, -1), { ...streamingMessage }])
                throw error
            }
        }

        setOnSubmitCallback(handleBoardChatSubmit)

        return () => setOnSubmitCallback(null)
    }, [code, setOnSubmitCallback, setChatMessages, appendMessage, loadQueries, currentOrg?.id])


    const handleCreateQuery = async (e) => {
        e.preventDefault()
        if (!createQueryName.trim() || creatingQuery) return
        setCreatingQuery(true)

        try {
            const data = await api.boards.createQuery(boardId, {
                name: createQueryName.trim(),
                description: createQueryDescription.trim() || null,
                python_code: '# Write your query code here\n# Use @node comments to define data queries\n#\n# Example:\n# @node: my_query\n# @type: query\n# @datastore: your_datastore_id\n# @query: SELECT * FROM dataset.your_table LIMIT 100\n\n# The query result will be available as query_result\n# args dict is available for runtime parameters (e.g. args.get("filter_value", "default"))\nresult = query_result\n',
                ui_map: {}
            })

            await loadQueries()
            setShowCreateQuery(false)
            setCreateQueryName('')
            setCreateQueryDescription('')
            console.log('Query created:', data)
        } catch (error) {
            console.error('Error creating query:', error)
            alert('Failed to create query: ' + error.message)
        } finally {
            setCreatingQuery(false)
        }
    }

    const deleteQuery = async (queryId) => {
        setDeleting(true)
        try {
            await api.queries.delete(queryId)
            setDeleteConfirm(null)
            await loadQueries()
        } catch (error) {
            console.error('Error deleting query:', error)
        } finally {
            setDeleting(false)
        }
    }

    const saveCode = async () => {
        try {
            let finalCode = code;
            if (activeTab === 'preview' && iframeRef.current) {
                finalCode = await new Promise((resolve) => {
                    const handler = (event) => {
                        if (event.data?.type === 'SYNC_HTML') {
                            window.removeEventListener('message', handler);
                            resolve(event.data.html);
                        }
                    };
                    window.addEventListener('message', handler);
                    iframeRef.current.contentWindow.postMessage({ type: 'GET_HTML' }, '*');
                    setTimeout(() => resolve(code), 1000);
                });
                setCode(finalCode);
            }

            await api.boards.saveCode(boardId, finalCode)

            console.log('Code saved successfully!')
        } catch (error) {
            console.error('Error saving code:', error)
        }
    }

    const insertTemplate = (template) => {
        const widgetCount = (code.match(/class="widget"/g) || []).length
        const offset = widgetCount * 40
        const id = `widget-${Date.now()}`

        const positionedTemplate = template.replace(
            'class="widget"',
            `class="widget" id="${id}" 
                data-lg-x="${offset}" data-lg-y="${offset}" data-lg-w="300" data-lg-h="220"
                data-md-x="${offset}" data-md-y="${offset}" data-md-w="280" data-md-h="200"
                data-sm-x="${offset}" data-sm-y="${offset}" data-sm-w="250" data-sm-h="180"
                style="transform: translate(${offset}px, ${offset}px); width: 300px; height: 220px;"`
        )

        const boardEndIndex = code.indexOf('<!-- Add your components here -->')
        if (boardEndIndex !== -1) {
            const before = code.substring(0, boardEndIndex)
            const after = code.substring(boardEndIndex)
            setCode(before + positionedTemplate + '\n    ' + after)
        }
        setShowTemplates(false)
    }

    const insertFullWidget = (htmlCode, name) => {
        const widgetCount = (code.match(/class="widget"/g) || []).length
        const offset = widgetCount * 40
        const id = `widget-${Date.now()}`
        const escaped = htmlCode.replace(/"/g, '&quot;')

        const snippet = `<div class="widget" id="${id}"
            data-lg-x="${offset}" data-lg-y="${offset}" data-lg-w="420" data-lg-h="320"
            data-md-x="${offset}" data-md-y="${offset}" data-md-w="350" data-md-h="280"
            data-sm-x="${offset}" data-sm-y="${offset}" data-sm-w="280" data-sm-h="220"
            style="transform: translate(${offset}px, ${offset}px); width: 420px; height: 320px;"
            x-data="canvasWidget()" x-init="initWidget($el)">
  <iframe srcdoc="${escaped}" style="width:100%;height:100%;border:none;border-radius:0.75rem;background:#0f172a;pointer-events:none;" title="${name}"></iframe>
</div>`

        const boardEndIndex = code.indexOf('<!-- Add your components here -->')
        if (boardEndIndex !== -1) {
            const before = code.substring(0, boardEndIndex)
            const after = code.substring(boardEndIndex)
            setCode(before + snippet + '\n    ' + after)
        }
        setShowTemplates(false)
    }

    useEffect(() => {
        setHeaderContent(
            <div className="flex items-center gap-1.5 sm:gap-2 flex-1 min-w-0">
                <button
                    onClick={() => navigate('/boards')}
                    className="flex items-center gap-1.5 px-2 py-1 rounded-md text-slate-400 hover:text-white hover:bg-white/[0.05] transition text-xs font-medium shrink-0"
                >
                    <ArrowLeft className="h-3.5 w-3.5" />
                    <span className="hidden sm:inline">Boards</span>
                </button>

                <div className="h-4 w-px bg-white/[0.07] shrink-0 hidden sm:block" />

                {board && (
                    editingName ? (
                        <form
                            className="hidden sm:flex items-center gap-1 min-w-0 flex-1 max-w-xs"
                            onSubmit={(e) => { e.preventDefault(); saveBoardName() }}
                        >
                            <input
                                ref={nameInputRef}
                                value={nameValue}
                                onChange={(e) => setNameValue(e.target.value)}
                                onBlur={saveBoardName}
                                onKeyDown={(e) => e.key === 'Escape' && setEditingName(false)}
                                className="flex-1 min-w-0 px-2 py-0.5 rounded-md bg-white/[0.06] border border-white/[0.12] text-sm font-semibold text-white focus:border-indigo-500/50 focus:outline-none transition"
                                autoFocus
                            />
                            <button type="submit" className="p-1 rounded-md text-emerald-400 hover:bg-white/[0.05] transition shrink-0">
                                <Check className="h-3.5 w-3.5" />
                            </button>
                        </form>
                    ) : (
                        <button
                            onClick={startEditingName}
                            className="hidden sm:flex group items-center gap-1.5 min-w-0 max-w-xs hover:bg-white/[0.04] rounded-md px-1.5 py-0.5 transition"
                            title="Click to rename"
                        >
                            <span className="text-sm font-semibold text-white truncate">{board.name}</span>
                            <Pencil className="h-3 w-3 text-slate-600 group-hover:text-slate-400 transition shrink-0" />
                        </button>
                    )
                )}

                <div className="h-4 w-px bg-white/[0.07] shrink-0 hidden sm:block" />

                {/* Tab switcher */}
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
                    <button
                        onClick={() => setActiveTab('data')}
                        className={`flex items-center gap-1.5 px-2 sm:px-2.5 py-1 rounded-md text-[11px] font-medium transition-all ${
                            activeTab === 'data'
                                ? 'bg-slate-800 text-indigo-400 shadow-sm'
                                : 'text-slate-500 hover:text-white'
                        }`}
                    >
                        <Database className="h-3.5 w-3.5" />
                        <span className="hidden sm:inline">Data</span>
                    </button>
                </div>

                <div className="flex-1" />

                <button
                    onClick={() => setShowTemplates(!showTemplates)}
                    className="hidden sm:flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-white/[0.08] bg-white/[0.02] text-slate-400 hover:bg-white/[0.05] hover:text-white transition-all"
                >
                    <Plus className="h-3.5 w-3.5" />
                    <span className="hidden sm:inline">Widget</span>
                </button>
                <button className="btn btn-primary text-xs shrink-0" onClick={saveCode}>
                    <Save className="h-3.5 w-3.5" />
                    <span className="hidden sm:inline">Save</span>
                </button>
            </div>
        )
        return () => setHeaderContent(null)
    }, [board, activeTab, showTemplates, code, editingName, nameValue])

    const filteredTemplates = widgetTemplates.filter(t =>
        t.name.toLowerCase().includes(pickerSearch.toLowerCase()) ||
        t.description?.toLowerCase().includes(pickerSearch.toLowerCase())
    )
    const filteredUserWidgets = userWidgets.filter(w =>
        w.name?.toLowerCase().includes(pickerSearch.toLowerCase())
    )

    return (
        <div className="flex flex-col h-screen bg-slate-950 overflow-hidden relative">
            {/* Widget Picker Modal */}
            {showTemplates && (
                <div className="fixed inset-0 z-[100] flex items-center justify-center p-3 sm:p-6" onClick={() => { setShowTemplates(false); setPickerSearch('') }}>
                    <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
                    <div
                        className="relative w-full max-w-3xl max-h-[85vh] rounded-2xl border border-white/[0.08] bg-slate-900 shadow-2xl shadow-black/50 flex flex-col animate-fade-in overflow-hidden"
                        onClick={(e) => e.stopPropagation()}
                    >
                        {/* Modal header */}
                        <div className="flex items-center justify-between gap-3 px-5 py-4 border-b border-white/[0.07] shrink-0">
                            <div className="flex items-center gap-2.5">
                                <div className="w-8 h-8 rounded-lg bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center">
                                    <Puzzle className="h-4 w-4 text-indigo-400" />
                                </div>
                                <div>
                                    <h2 className="text-sm font-semibold text-white">Add Widget</h2>
                                    <p className="text-[11px] text-slate-500">Choose a template or pick from your widgets</p>
                                </div>
                            </div>
                            <button
                                onClick={() => { setShowTemplates(false); setPickerSearch('') }}
                                className="p-1.5 rounded-lg text-slate-500 hover:text-white hover:bg-white/[0.05] transition"
                            >
                                <X className="h-4 w-4" />
                            </button>
                        </div>

                        {/* Tabs + Search */}
                        <div className="flex items-center gap-3 px-5 py-3 border-b border-white/[0.06] shrink-0">
                            <div className="flex items-center bg-white/[0.03] p-0.5 rounded-lg border border-white/[0.07]">
                                <button
                                    onClick={() => setPickerTab('templates')}
                                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                                        pickerTab === 'templates'
                                            ? 'bg-slate-800 text-indigo-400 shadow-sm'
                                            : 'text-slate-500 hover:text-white'
                                    }`}
                                >
                                    <LayoutGrid className="h-3.5 w-3.5" />
                                    Templates
                                </button>
                                <button
                                    onClick={() => setPickerTab('my')}
                                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                                        pickerTab === 'my'
                                            ? 'bg-slate-800 text-indigo-400 shadow-sm'
                                            : 'text-slate-500 hover:text-white'
                                    }`}
                                >
                                    <Puzzle className="h-3.5 w-3.5" />
                                    My Widgets
                                    {userWidgets.length > 0 && (
                                        <span className="ml-1 text-[10px] px-1.5 py-0.5 rounded-full bg-white/[0.06] text-slate-400">{userWidgets.length}</span>
                                    )}
                                </button>
                            </div>
                            <div className="flex-1" />
                            <div className="relative">
                                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-slate-500" />
                                <input
                                    type="text"
                                    placeholder="Search..."
                                    value={pickerSearch}
                                    onChange={(e) => setPickerSearch(e.target.value)}
                                    className="pl-8 pr-3 py-1.5 rounded-lg border border-white/[0.08] bg-white/[0.02] text-xs text-slate-200 placeholder:text-slate-600 focus:border-indigo-500/50 focus:outline-none transition w-40"
                                />
                            </div>
                        </div>

                        {/* Content */}
                        <div className="flex-1 overflow-y-auto p-5">
                            {pickerTab === 'templates' && (
                                <div>
                                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5">
                                        {filteredTemplates.map((tmpl) => (
                                            <button
                                                key={tmpl.id}
                                                onClick={() => insertFullWidget(tmpl.code, tmpl.name)}
                                                className="group text-left rounded-xl border border-white/[0.06] bg-white/[0.015] p-3 transition-all relative overflow-hidden hover:border-indigo-500/30 hover:bg-indigo-500/[0.04]"
                                            >
                                                <div className="w-full h-20 rounded-lg overflow-hidden mb-2.5 bg-[#0f172a] border border-white/[0.04]">
                                                    <iframe
                                                        srcDoc={tmpl.code}
                                                        className="w-[400%] h-[400%] border-none pointer-events-none"
                                                        style={{ transform: 'scale(0.25)', transformOrigin: 'top left' }}
                                                        title={tmpl.name}
                                                        sandbox="allow-scripts"
                                                        tabIndex={-1}
                                                    />
                                                </div>
                                                <div className="flex items-center gap-2">
                                                    <div className={`w-6 h-6 rounded-md border flex items-center justify-center text-xs shrink-0 ${tmpl.color}`}>
                                                        {tmpl.icon}
                                                    </div>
                                                    <div className="min-w-0">
                                                        <h4 className="text-xs font-semibold text-white truncate leading-tight">{tmpl.name}</h4>
                                                        <p className="text-[10px] text-slate-500 truncate">{tmpl.description}</p>
                                                    </div>
                                                </div>
                                            </button>
                                        ))}
                                        {filteredTemplates.length === 0 && (
                                            <div className="col-span-full text-center py-8 text-slate-500 text-sm">No templates match your search.</div>
                                        )}
                                    </div>
                                </div>
                            )}

                            {pickerTab === 'my' && (
                                <div>
                                    {filteredUserWidgets.length === 0 ? (
                                        <div className="flex flex-col items-center justify-center py-12 text-center">
                                            <div className="w-14 h-14 rounded-2xl border border-white/[0.07] bg-white/[0.02] flex items-center justify-center mb-4">
                                                <Puzzle className="h-6 w-6 text-slate-600" />
                                            </div>
                                            <h3 className="text-sm font-semibold text-white mb-1">
                                                {pickerSearch ? 'No widgets match your search' : 'No widgets yet'}
                                            </h3>
                                            <p className="text-xs text-slate-500 mb-4 max-w-xs">
                                                {pickerSearch ? 'Try a different search term.' : 'Create widgets in the Widgets section, then use them on any board.'}
                                            </p>
                                            {!pickerSearch && (
                                                <button onClick={() => navigate('/widgets')} className="btn btn-secondary text-xs">
                                                    Go to Widgets
                                                </button>
                                            )}
                                        </div>
                                    ) : (
                                        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5">
                                            {filteredUserWidgets.map((widget) => (
                                                <button
                                                    key={widget.id}
                                                    onClick={() => widget.html_code && insertFullWidget(widget.html_code, widget.name)}
                                                    disabled={!widget.html_code}
                                                    className="group text-left rounded-xl border border-white/[0.06] bg-white/[0.015] p-3 transition-all relative overflow-hidden hover:border-indigo-500/30 hover:bg-indigo-500/[0.04] disabled:opacity-40 disabled:cursor-not-allowed"
                                                >
                                                    <div className="w-full h-20 rounded-lg overflow-hidden mb-2.5 bg-[#0f172a] border border-white/[0.04]">
                                                        {widget.html_code ? (
                                                            <iframe
                                                                srcDoc={widget.html_code}
                                                                className="w-[400%] h-[400%] border-none pointer-events-none"
                                                                style={{ transform: 'scale(0.25)', transformOrigin: 'top left' }}
                                                                title={widget.name}
                                                                sandbox="allow-scripts"
                                                                tabIndex={-1}
                                                            />
                                                        ) : (
                                                            <div className="w-full h-full flex items-center justify-center text-slate-600">
                                                                <Puzzle className="h-5 w-5" />
                                                            </div>
                                                        )}
                                                    </div>
                                                    <div className="flex items-center gap-2">
                                                        <div className="w-6 h-6 rounded-md bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center shrink-0">
                                                            <Puzzle className="h-3 w-3 text-indigo-400" />
                                                        </div>
                                                        <h4 className="text-xs font-semibold text-white truncate leading-tight">{widget.name}</h4>
                                                    </div>
                                                </button>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}

            {/* Main Editor Area */}
            <div className="flex-1 flex relative overflow-hidden">
                <div className="flex-1 relative overflow-hidden">
                    {activeTab === 'preview' ? (
                        <div className="h-full flex flex-col bg-[#0d0f17]">
                            {/* Viewport switcher bar */}
                            <div className="flex items-center justify-center gap-1 px-3 py-1.5 bg-slate-950/80 backdrop-blur-md border-b border-white/[0.07] shrink-0">
                                {Object.entries(VIEWPORTS).map(([key, { name, icon: Icon }]) => (
                                    <button
                                        key={key}
                                        onClick={() => setViewport(key)}
                                        className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium transition-all ${
                                            viewport === key
                                                ? 'bg-indigo-600 text-white shadow-sm shadow-indigo-900/40'
                                                : 'text-slate-500 hover:text-white hover:bg-white/[0.05]'
                                        }`}
                                        title={name}
                                    >
                                        <Icon className="h-3.5 w-3.5" />
                                        <span className="hidden sm:inline">{name}</span>
                                    </button>
                                ))}
                            </div>
                            <div className="flex-1 overflow-hidden relative">
                                <div className="h-full border-x border-white/[0.07] shadow-2xl transition-all duration-500 mx-auto" style={{ width: VIEWPORTS[viewport].width }}>
                                    <iframe
                                        ref={iframeRef}
                                        className="w-full h-full border-none bg-[#0a0e1a]"
                                        title="Board Preview"
                                        srcDoc={code}
                                    />
                                </div>
                            </div>
                        </div>
                    ) : activeTab === 'code' ? (
                        <CodeEditor
                            value={code}
                            onChange={setCode}
                            language="html"
                            onSave={handleSaveShortcut}
                        />
                    ) : (
                        <div className="h-full overflow-y-auto p-5">
                            <div className="max-w-6xl mx-auto">
                                <div className="mb-5 flex items-center justify-between">
                                    <div>
                                        <h2 className="text-base font-semibold text-white">Data Queries</h2>
                                        <p className="text-slate-400 text-xs mt-0.5">Manage Python queries that fetch and transform data from your datastores.</p>
                                    </div>
                                    <button 
                                        onClick={() => setShowCreateQuery(true)}
                                        className="btn btn-primary"
                                    >
                                        <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
                                            <path fillRule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" />
                                        </svg>
                                        New Query
                                    </button>
                                </div>
                                
                                {loadingQueries ? (
                                    <div className="flex flex-col items-center justify-center py-32 gap-4">
                                        <div className="spinner" />
                                        <p className="text-slate-500 text-sm font-medium">Loading queries...</p>
                                    </div>
                                ) : queries.length === 0 ? (
                                    <div className="flex items-center justify-center py-24 text-center">
                                        <div className="max-w-md">
                                            <div className="w-20 h-20 rounded-2xl bg-slate-800 border border-white/[0.07] flex items-center justify-center text-slate-500 mb-6 mx-auto">
                                                <svg width="36" height="36" viewBox="0 0 20 20" fill="currentColor">
                                                    <path d="M3 12v3c0 1.657 3.134 3 7 3s7-1.343 7-3v-3c0 1.657-3.134 3-7 3s-7-1.343-7-3z" />
                                                    <path d="M3 7v3c0 1.657 3.134 3 7 3s7-1.343 7-3V7c0 1.657-3.134 3-7 3S3 8.657 3 7z" />
                                                    <path d="M17 5c0 1.657-3.134 3-7 3S3 6.657 3 5s3.134-3 7-3 7 1.343 7 3z" />
                                                </svg>
                                            </div>
                                            <h3 className="text-xl font-semibold mb-2 text-white">No queries yet</h3>
                                            <p className="text-slate-400 mb-8 text-sm leading-relaxed">
                                                Queries let you fetch and transform data from your datastores. Create your first query to get started.
                                            </p>
                                            <button 
                                                onClick={() => setShowCreateQuery(true)}
                                                className="btn btn-primary"
                                            >
                                                <svg width="18" height="18" viewBox="0 0 20 20" fill="currentColor">
                                                    <path fillRule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" />
                                                </svg>
                                                Create Your First Query
                                            </button>
                                        </div>
                                    </div>
                                ) : (
                                    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5 animate-fade-in">
                                        {queries.map(query => (
                                            <div
                                                key={query.id}
                                                className="group relative bg-slate-900/70 backdrop-blur-sm border border-white/[0.07] rounded-xl p-5 cursor-pointer transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lg hover:border-white/[0.12] hover:bg-slate-900"
                                                onClick={() => navigate(`/board/${boardId}/query/${query.id}`)}
                                            >
                                                <div className="flex items-start gap-3.5 mb-3">
                                                    <div className="w-10 h-10 flex-shrink-0 flex items-center justify-center bg-indigo-600/10 border border-indigo-500/20 rounded-lg text-indigo-400 group-hover:bg-indigo-600/15 transition-colors">
                                                        <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
                                                            <path d="M3 12v3c0 1.657 3.134 3 7 3s7-1.343 7-3v-3c0 1.657-3.134 3-7 3s-7-1.343-7-3z" />
                                                            <path d="M3 7v3c0 1.657 3.134 3 7 3s7-1.343 7-3V7c0 1.657-3.134 3-7 3S3 8.657 3 7z" />
                                                            <path d="M17 5c0 1.657-3.134 3-7 3S3 6.657 3 5s3.134-3 7-3 7 1.343 7 3z" />
                                                        </svg>
                                                    </div>
                                                    <div className="flex-1 min-w-0">
                                                        <h3 className="text-base font-semibold text-white truncate leading-tight mb-0.5">
                                                            {query.name}
                                                        </h3>
                                                        <p className="text-slate-400 text-xs leading-relaxed line-clamp-2">
                                                            {query.description || 'No description'}
                                                        </p>
                                                    </div>
                                                </div>

                                                <div className="flex items-center justify-between pt-3 border-t border-white/[0.07]/60">
                                                    <div className="flex items-center gap-3">
                                                        <span className="flex items-center gap-1.5 text-[11px] text-slate-500">
                                                            <svg width="12" height="12" viewBox="0 0 20 20" fill="currentColor">
                                                                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-12a1 1 0 10-2 0v4a1 1 0 00.293.707l2.828 2.829a1 1 0 101.415-1.415L11 9.586V6z" />
                                                            </svg>
                                                            {new Date(query.updated_at).toLocaleDateString()}
                                                        </span>
                                                    </div>
                                                    <div className="flex items-center gap-2">
                                                        <button
                                                            onClick={(e) => { e.stopPropagation(); setDeleteConfirm(query.id) }}
                                                            className="p-1.5 rounded-md text-slate-500 opacity-0 group-hover:opacity-100 hover:bg-red-500/10 hover:text-red-400 transition-all"
                                                            title="Delete"
                                                        >
                                                            <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
                                                                <path fillRule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" />
                                                            </svg>
                                                        </button>
                                                        <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor" className="text-slate-500 group-hover:text-indigo-400 group-hover:translate-x-0.5 transition-all">
                                                            <path fillRule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" />
                                                        </svg>
                                                    </div>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            </div>

            {/* Create Query Modal */}
            {showCreateQuery && (
                <div className="modal-overlay" onClick={() => setShowCreateQuery(false)}>
                    <div className="modal-container max-w-md" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-between mb-6">
                            <h2 className="text-lg font-bold text-white">New Query</h2>
                            <button
                                onClick={() => setShowCreateQuery(false)}
                                className="p-1.5 rounded-lg text-slate-500 hover:text-white hover:bg-white/[0.05] transition-all"
                            >
                                <svg width="18" height="18" viewBox="0 0 20 20" fill="currentColor">
                                    <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" />
                                </svg>
                            </button>
                        </div>

                        <form onSubmit={handleCreateQuery} className="space-y-4">
                            <div className="form-group">
                                <label className="form-label">Name</label>
                                <input
                                    autoFocus
                                    className="form-input"
                                    placeholder="e.g. Sales by Region"
                                    value={createQueryName}
                                    onChange={(e) => setCreateQueryName(e.target.value)}
                                    required
                                />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Description <span className="text-slate-500 font-normal">(optional)</span></label>
                                <textarea
                                    className="form-input resize-none"
                                    rows={3}
                                    placeholder="What does this query do?"
                                    value={createQueryDescription}
                                    onChange={(e) => setCreateQueryDescription(e.target.value)}
                                />
                            </div>

                            <div className="flex items-center gap-3 pt-2">
                                <button
                                    type="button"
                                    onClick={() => setShowCreateQuery(false)}
                                    className="btn btn-secondary flex-1 py-2.5 h-auto text-sm"
                                >
                                    Cancel
                                </button>
                                <button
                                    type="submit"
                                    disabled={!createQueryName.trim() || creatingQuery}
                                    className="btn btn-primary flex-1 py-2.5 h-auto text-sm"
                                >
                                    {creatingQuery ? (
                                        <span className="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                                    ) : (
                                        <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor">
                                            <path fillRule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" />
                                        </svg>
                                    )}
                                    Create
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}

            {/* Delete Query Confirmation */}
            {deleteConfirm && (
                <div className="modal-overlay" onClick={() => !deleting && setDeleteConfirm(null)}>
                    <div className="modal-container max-w-sm" onClick={(e) => e.stopPropagation()}>
                        <div className="flex flex-col items-center text-center pt-2 pb-1">
                            <div className="w-12 h-12 rounded-full bg-red-500/10 flex items-center justify-center mb-4">
                                <svg width="24" height="24" viewBox="0 0 20 20" fill="currentColor" className="text-red-400">
                                    <path fillRule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" />
                                </svg>
                            </div>
                            <h3 className="text-base font-semibold text-white mb-1.5">Delete Query</h3>
                            <p className="text-sm text-slate-400 leading-relaxed">
                                This will permanently remove the query and its data. This action cannot be undone.
                            </p>
                        </div>
                        <div className="flex items-center gap-3 pt-5">
                            <button
                                onClick={() => setDeleteConfirm(null)}
                                disabled={deleting}
                                className="btn btn-secondary flex-1 py-2.5 h-auto text-sm"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={() => deleteQuery(deleteConfirm)}
                                disabled={deleting}
                                className="flex-1 py-2.5 h-auto text-sm font-medium rounded-lg bg-red-500 hover:bg-red-500/90 text-white transition-colors flex items-center justify-center gap-2 disabled:opacity-50"
                            >
                                {deleting ? (
                                    <span className="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                                ) : (
                                    <svg width="15" height="15" viewBox="0 0 20 20" fill="currentColor">
                                        <path fillRule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" />
                                    </svg>
                                )}
                                Delete
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}
