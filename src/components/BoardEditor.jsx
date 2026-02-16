import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { supabase, invokeBoardHelper } from '../lib/supabase'
import { useHeader } from '../contexts/HeaderContext'
import { useChat } from '../contexts/ChatContext'
import Prism from 'prismjs'
import 'prismjs/themes/prism-tomorrow.css'
import 'prismjs/components/prism-markup'
import 'prismjs/components/prism-javascript'
import 'prismjs/components/prism-css'


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

    /* Hide resize handles when in specific fixed layouts if needed */
    [data-viewport="sm"] .widget::after { display: none; }
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
                  
                  // Update current style for immediate feedback
                  target.style.transform = 'translate(' + x + 'px, ' + y + 'px)';
                }
              }
            })
            .resizable({
              edges: { left: false, right: true, bottom: true, top: false },
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
    lg: { name: 'Desktop', width: '100%', icon: '💻' },
    md: { name: 'Tablet', width: '768px', icon: '📱' },
    sm: { name: 'Mobile', width: '375px', icon: '📱' }
}

export default function BoardEditor() {
    const { boardId } = useParams()
    const navigate = useNavigate()
    const { setHeaderContent } = useHeader()
    const { openChatFor, setOnSubmitCallback, chatMessages, setChatMessages, setChatLoading, ensureCurrentChat, appendMessage } = useChat()
    const [code, setCode] = useState(DEFAULT_HTML_TEMPLATE)
    const [activeTab, setActiveTab] = useState('preview') // 'preview', 'code', or 'data'
    const [viewport, setViewport] = useState('lg')
    const [showTemplates, setShowTemplates] = useState(false)
    const [queries, setQueries] = useState([])
    const [loadingQueries, setLoadingQueries] = useState(false)
    const [showCreateQuery, setShowCreateQuery] = useState(false)
    const [createQueryName, setCreateQueryName] = useState('')
    const [createQueryDescription, setCreateQueryDescription] = useState('')
    const [creatingQuery, setCreatingQuery] = useState(false)
    const iframeRef = useRef(null)
    const codeRef = useRef(null)
    const textareaRef = useRef(null)

    // Sync scroll between textarea, highlight, and line numbers
    const handleScroll = (e) => {
        const { scrollTop, scrollLeft } = e.target;
        if (codeRef.current) {
            codeRef.current.scrollTop = scrollTop;
            codeRef.current.scrollLeft = scrollLeft;
        }
        if (lineNumbersRef.current) {
            lineNumbersRef.current.scrollTop = scrollTop;
        }
    };

    const handleKeyDown = (e) => {
        if (e.key === 'Tab') {
            e.preventDefault();
            const start = e.target.selectionStart;
            const end = e.target.selectionEnd;

            // Set textarea value to: text before caret + tab + text after caret
            const newCode = code.substring(0, start) + '  ' + code.substring(end);
            setCode(newCode);

            // Put caret at right position again
            setTimeout(() => {
                e.target.selectionStart = e.target.selectionEnd = start + 2;
            }, 0);
        }
    };

    useEffect(() => {
        loadBoardCode()
        loadQueries()
        openChatFor(boardId)
    }, [boardId, openChatFor])
    
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
            
            // Get settings from localStorage
            const settingsStr = localStorage.getItem('nubi_settings')
            const settings = settingsStr ? JSON.parse(settingsStr) : {}
            
            // Add user message to UI immediately
            await appendMessage(chatId, 'user', prompt)
            
            const response = await fetch(`${backendUrl}/board-helper-stream`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    code: codeToSend,
                    user_prompt: prompt + contextString,
                    chat: messages.filter((m) => m.role === 'user' || m.role === 'assistant').map((m) => ({ role: m.role, content: m.content })),
                    context: 'board',
                    // Pass settings to backend
                    max_tool_iterations: settings.maxToolIterations || 10,
                    temperature: settings.temperature || 0.3,
                    max_output_tokens: settings.maxOutputTokens || 8192,
                    ...(import.meta.env?.VITE_GEMINI_API_KEY && { gemini_api_key: import.meta.env.VITE_GEMINI_API_KEY }),
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
                needs_user_input: null
            }
            setChatMessages(prev => [...prev, streamingMessage])

            const reader = response.body.getReader()
            const decoder = new TextDecoder()
            let buffer = ''
            let finalCode = null
            let progressLines = []

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

                // Save assistant message to DB
                await appendMessage(chatId, 'assistant', streamingMessage.content)
                
            } catch (error) {
                streamingMessage.content = `Error during streaming: ${error.message}`
                setChatMessages(prev => [...prev.slice(0, -1), { ...streamingMessage }])
                throw error
            }
        }

        setOnSubmitCallback(() => handleBoardChatSubmit)

        return () => setOnSubmitCallback(null)
    }, [code, setOnSubmitCallback, setChatMessages, appendMessage])

    useEffect(() => {
        if (activeTab === 'code' && codeRef.current) {
            const codeElement = codeRef.current.querySelector('code');
            if (codeElement) {
                Prism.highlightElement(codeElement);
            }
        }
    }, [code, activeTab]);

    const lineNumbersRef = useRef(null);
    const lineNumbers = code.split('\n').map((_, i) => i + 1).join('\n');

    const loadBoardCode = async () => {
        try {
            const { data, error } = await supabase
                .from('board_code')
                .select('code')
                .eq('board_id', boardId)
                .order('version', { ascending: false })
                .limit(1)
                .maybeSingle()

            if (error) throw error
            if (data) {
                setCode(data.code)
            }
        } catch (error) {
            console.error('Error loading board code:', error)
        }
    }

    const loadQueries = async () => {
        setLoadingQueries(true)
        try {
            const { data, error } = await supabase
                .from('board_queries')
                .select('*')
                .eq('board_id', boardId)
                .order('updated_at', { ascending: false })

            if (error) throw error
            setQueries(data || [])
        } catch (error) {
            console.error('Error loading queries:', error)
        } finally {
            setLoadingQueries(false)
        }
    }

    const handleCreateQuery = async (e) => {
        e.preventDefault()
        if (!createQueryName.trim() || creatingQuery) return
        setCreatingQuery(true)

        try {
            const { data, error } = await supabase
                .from('board_queries')
                .insert([{
                    board_id: boardId,
                    name: createQueryName.trim(),
                    description: createQueryDescription.trim() || null,
                    python_code: '# Write your query code here\n# Use @node comments to define data queries\n#\n# Example:\n# @node: my_query\n# @type: query\n# @connector: your_datastore_id\n# @query: SELECT * FROM your_table LIMIT 100\n\n# The query result will be available as query_result\nresult = query_result\n',
                    ui_map: {}
                }])
                .select()
                .single()

            if (error) throw error
            
            // Reload queries
            await loadQueries()
            
            // Close modal
            setShowCreateQuery(false)
            setCreateQueryName('')
            setCreateQueryDescription('')
            
            // TODO: Navigate to query editor
            console.log('Query created:', data)
        } catch (error) {
            console.error('Error creating query:', error)
            alert('Failed to create query: ' + error.message)
        } finally {
            setCreatingQuery(false)
        }
    }

    const deleteQuery = async (queryId, e) => {
        e.stopPropagation()
        if (!confirm('Are you sure you want to delete this query?')) return

        try {
            const { error } = await supabase
                .from('board_queries')
                .delete()
                .eq('id', queryId)

            if (error) throw error
            await loadQueries()
        } catch (error) {
            console.error('Error deleting query:', error)
            alert('Failed to delete query: ' + error.message)
        }
    }

    const saveCode = async () => {
        try {
            // Request latest HTML from iframe if in preview mode
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
                    setTimeout(() => resolve(code), 1000); // Timeout fallback
                });
                setCode(finalCode);
            }

            const { data: latestVersion } = await supabase
                .from('board_code')
                .select('version')
                .eq('board_id', boardId)
                .order('version', { ascending: false })
                .limit(1)
                .maybeSingle()

            const newVersion = latestVersion ? latestVersion.version + 1 : 1

            const { error } = await supabase
                .from('board_code')
                .insert([
                    {
                        board_id: boardId,
                        version: newVersion,
                        code: code,
                    },
                ])

            if (error) throw error
            console.log('Code saved successfully!')
        } catch (error) {
            console.error('Error saving code:', error)
        }
    }

    const insertTemplate = (template) => {
        // Find existing widgets to determine next position
        const widgetCount = (code.match(/class="widget"/g) || []).length
        const offset = widgetCount * 40
        const id = `widget-${Date.now()}`

        // Add default layout for all breakpoints
        const positionedTemplate = template.replace(
            'class="widget"',
            `class="widget" id="${id}" 
                data-lg-x="${offset}" data-lg-y="${offset}" data-lg-w="300" data-lg-h="220"
                data-md-x="${offset}" data-md-y="${offset}" data-md-w="280" data-md-h="200"
                data-sm-x="0" data-sm-y="0" data-sm-w="100%" data-sm-h="150"
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

    // Modern Header Injection
    useEffect(() => {
        setHeaderContent(
            <div className="flex items-center gap-6 flex-1 px-4">
                <button
                    className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-text-secondary hover:text-text-primary hover:bg-background-tertiary transition-all font-medium text-sm"
                    onClick={() => navigate('/')}
                >
                    <svg width="18" height="18" viewBox="0 0 20 20" fill="currentColor">
                        <path fillRule="evenodd" d="M9.707 16.707a1 1 0 01-1.414 0l-6-6a1 1 0 010-1.414l6-6a1 1 0 011.414 1.414L5.414 9H17a1 1 0 110 2H5.414l4.293 4.293a1 1 0 010 1.414z" />
                    </svg>
                    <span>Back</span>
                </button>

                <div className="h-6 w-px bg-border-primary mx-2" />

                <div className="flex bg-background-tertiary p-1 rounded-xl shadow-inner border border-border-primary/50">
                    {Object.entries(VIEWPORTS).map(([key, value]) => (
                        <button
                            key={key}
                            className={`flex items-center gap-2 px-4 py-1.5 rounded-lg text-xs font-bold transition-all duration-200 uppercase tracking-wider ${viewport === key
                                ? 'bg-accent-primary text-white shadow-lg'
                                : 'text-text-secondary hover:text-text-primary hover:bg-white/5'
                                }`}
                            onClick={() => setViewport(key)}
                            title={value.name}
                        >
                            <span>{value.icon}</span>
                            <span className="hidden md:inline">{key}</span>
                        </button>
                    ))}
                </div>

                <div className="flex bg-background-tertiary p-1 rounded-xl mx-4 shadow-inner border border-border-primary/50">
                    <button
                        className={`flex items-center gap-2 px-5 py-1.5 rounded-lg text-sm font-bold transition-all duration-200 ${activeTab === 'preview'
                            ? 'bg-background-secondary text-accent-primary shadow-sm border border-border-primary'
                            : 'text-text-secondary hover:text-text-primary'
                            }`}
                        onClick={() => setActiveTab('preview')}
                    >
                        <svg width="18" height="18" viewBox="0 0 20 20" fill="currentColor">
                            <path d="M10 12a2 2 0 100-4 2 2 0 000 4z" />
                            <path fillRule="evenodd" d="M.458 10C1.732 5.943 5.522 3 10 3s8.268 2.943 9.542 7c-1.274 4.057-5.064 7-9.542 7S1.732 14.057.458 10zM14 10a4 4 0 11-8 0 4 4 0 018 0z" />
                        </svg>
                        <span className="hidden lg:inline">Preview</span>
                    </button>
                    <button
                        className={`flex items-center gap-2 px-5 py-1.5 rounded-lg text-sm font-bold transition-all duration-200 ${activeTab === 'code'
                            ? 'bg-background-secondary text-accent-primary shadow-sm border border-border-primary'
                            : 'text-text-secondary hover:text-text-primary'
                            }`}
                        onClick={() => setActiveTab('code')}
                    >
                        <svg width="18" height="18" viewBox="0 0 20 20" fill="currentColor">
                            <path fillRule="evenodd" d="M12.316 3.051a1 1 0 01.633 1.265l-4 12a1 1 0 11-1.898-.632l4-12a1 1 0 011.265-.633zM5.707 6.293a1 1 0 010 1.414L3.414 10l2.293 2.293a1 1 0 11-1.414 1.414l-3-3a1 1 0 010-1.414l3-3a1 1 0 011.414 0zm8.586 0a1 1 0 011.414 0l3 3a1 1 0 010 1.414l-3 3a1 1 0 11-1.414-1.414L16.586 10l-2.293-2.293a1 1 0 010-1.414z" />
                        </svg>
                        <span className="hidden lg:inline">Code</span>
                    </button>
                    <button
                        className={`flex items-center gap-2 px-5 py-1.5 rounded-lg text-sm font-bold transition-all duration-200 ${activeTab === 'data'
                            ? 'bg-background-secondary text-accent-primary shadow-sm border border-border-primary'
                            : 'text-text-secondary hover:text-text-primary'
                            }`}
                        onClick={() => setActiveTab('data')}
                    >
                        <svg width="18" height="18" viewBox="0 0 20 20" fill="currentColor">
                            <path d="M3 12v3c0 1.657 3.134 3 7 3s7-1.343 7-3v-3c0 1.657-3.134 3-7 3s-7-1.343-7-3z" />
                            <path d="M3 7v3c0 1.657 3.134 3 7 3s7-1.343 7-3V7c0 1.657-3.134 3-7 3S3 8.657 3 7z" />
                            <path d="M17 5c0 1.657-3.134 3-7 3S3 6.657 3 5s3.134-3 7-3 7 1.343 7 3z" />
                        </svg>
                        <span className="hidden lg:inline">Data</span>
                    </button>
                </div>

                <div className="ml-auto flex items-center gap-3">
                    <button className="btn btn-secondary py-1.5 h-auto text-xs" onClick={() => setShowTemplates(!showTemplates)}>
                        <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor">
                            <path fillRule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" />
                        </svg>
                        <span className="hidden sm:inline">Add Component</span>
                    </button>
                    <button className="btn btn-primary py-1.5 h-auto text-xs" onClick={saveCode}>
                        <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor">
                            <path d="M7.707 10.293a1 1 0 10-1.414 1.414l3 3a1 1 0 001.414 0l3-3a1 1 0 00-1.414-1.414L11 11.586V6h5a2 2 0 012 2v7a2 2 0 01-2 2H4a2 2 0 01-2-2V8a2 2 0 012-2h5v5.586l-1.293-1.293zM9 4a1 1 0 012 0v2H9V4z" />
                        </svg>
                        Save
                    </button>
                </div>
            </div>
        )
        // Cleanup when leaving the editor
        return () => setHeaderContent(null)
    }, [activeTab, showTemplates, code, viewport]) // Update when dependencies change

    return (
        <div className="flex flex-col h-screen bg-background-primary overflow-hidden relative">
            {/* Component Templates Dropdown */}
            {showTemplates && (
                <div className="fixed top-[72px] right-8 w-96 p-6 rounded-2xl z-[100] animate-fade-in glass-effect shadow-2xl border border-border-primary/50">
                    <h3 className="text-base font-bold text-text-primary mb-6 flex items-center gap-2">
                        <div className="w-1 bg-accent-primary h-4 rounded-full" />
                        Component Templates
                    </h3>
                    <div className="grid grid-cols-2 gap-4">
                        {COMPONENT_TEMPLATES.map((template) => (
                            <button
                                key={template.id}
                                className="bg-background-tertiary border border-border-primary rounded-xl p-6 flex flex-col items-center gap-3 transition-all duration-300 hover:bg-background-secondary hover:border-accent-primary hover:-translate-y-1 group"
                                onClick={() => insertTemplate(template.template)}
                            >
                                <div className="text-3xl filter grayscale group-hover:grayscale-0 transition-all duration-300">
                                    {template.type === 'chart' ? '📊' : '📈'}
                                </div>
                                <div className="text-xs font-bold text-text-primary uppercase tracking-wider">{template.name}</div>
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {/* Main Editor Area */}
            <div className="flex-1 flex relative overflow-hidden">
                <div className="flex-1 relative overflow-hidden">
                    {activeTab === 'preview' ? (
                        <div className="h-full flex flex-col bg-[#0d0f17] relative border-x border-border-primary shadow-2xl transition-all duration-500 mx-auto" style={{ width: VIEWPORTS[viewport].width }}>
                            <div className="text-[10px] font-bold text-text-muted/50 px-4 py-2 bg-background-primary/80 backdrop-blur-md uppercase tracking-[0.2em] border-b border-border-primary flex items-center justify-between">
                                <span>{VIEWPORTS[viewport].name} Environment</span>
                                <span>{VIEWPORTS[viewport].width === '100%' ? 'Adaptive Width' : VIEWPORTS[viewport].width}</span>
                            </div>
                            <iframe
                                ref={iframeRef}
                                className="w-full flex-1 border-none bg-[#0a0e1a]"
                                title="Board Preview"
                                srcDoc={code}
                            />
                        </div>
                    ) : activeTab === 'code' ? (
                        <div className="flex w-full h-full bg-[#0d0f17] font-mono text-sm leading-relaxed overflow-hidden">
                            <div className="w-14 py-6 bg-[#111420] border-r border-border-primary text-[#4b5563] text-right select-none overflow-hidden" ref={lineNumbersRef}>
                                <pre className="m-0 px-4 font-inherit line-around leading-relaxed">{lineNumbers}</pre>
                            </div>
                            <div className="relative flex-1 overflow-hidden">
                                <textarea
                                    ref={textareaRef}
                                    value={code}
                                    onChange={(e) => setCode(e.target.value)}
                                    onScroll={handleScroll}
                                    onKeyDown={handleKeyDown}
                                    className="absolute inset-0 w-full h-full p-6 m-0 border-none bg-transparent text-transparent caret-indigo-500 resize-none outline-none z-10 overflow-auto scrollbar-thin scrollbar-thumb-border-primary scrollbar-track-transparent"
                                    spellCheck="false"
                                />
                                <pre className="absolute inset-0 p-6 m-0 z-0 pointer-events-none overflow-hidden" ref={codeRef}>
                                    <code className="language-markup block pointer-events-none">
                                        {code + (code.endsWith('\n') ? ' ' : '')}
                                    </code>
                                </pre>
                            </div>
                        </div>
                    ) : (
                        // Data Tab - Queries for this board
                        <div className="h-full overflow-y-auto p-8">
                            <div className="max-w-7xl mx-auto">
                                <div className="mb-8 flex items-end justify-between">
                                    <div>
                                        <h2 className="text-2xl font-bold text-text-primary mb-2">Data Queries</h2>
                                        <p className="text-text-secondary">Manage Python queries for this board. Queries can fetch data from your datastores and transform it.</p>
                                    </div>
                                    <button 
                                        onClick={() => setShowCreateQuery(true)}
                                        className="btn btn-primary py-2 h-auto text-sm"
                                    >
                                        <svg width="18" height="18" viewBox="0 0 20 20" fill="currentColor">
                                            <path fillRule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" />
                                        </svg>
                                        New Query
                                    </button>
                                </div>
                                
                                {loadingQueries ? (
                                    <div className="flex flex-col items-center justify-center py-32 gap-4">
                                        <div className="spinner" />
                                        <p className="text-text-muted text-sm font-medium">Loading queries...</p>
                                    </div>
                                ) : queries.length === 0 ? (
                                    <div className="flex items-center justify-center py-24 text-center">
                                        <div className="max-w-md">
                                            <div className="w-20 h-20 rounded-2xl bg-background-tertiary border border-border-primary flex items-center justify-center text-text-muted mb-6 mx-auto">
                                                <svg width="36" height="36" viewBox="0 0 20 20" fill="currentColor">
                                                    <path d="M3 12v3c0 1.657 3.134 3 7 3s7-1.343 7-3v-3c0 1.657-3.134 3-7 3s-7-1.343-7-3z" />
                                                    <path d="M3 7v3c0 1.657 3.134 3 7 3s7-1.343 7-3V7c0 1.657-3.134 3-7 3S3 8.657 3 7z" />
                                                    <path d="M17 5c0 1.657-3.134 3-7 3S3 6.657 3 5s3.134-3 7-3 7 1.343 7 3z" />
                                                </svg>
                                            </div>
                                            <h3 className="text-xl font-semibold mb-2 text-text-primary">No queries yet</h3>
                                            <p className="text-text-secondary mb-8 text-sm leading-relaxed">
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
                                                className="group relative bg-background-secondary/70 backdrop-blur-sm border border-border-primary rounded-xl p-5 cursor-pointer transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lg hover:border-border-secondary hover:bg-background-secondary"
                                                onClick={() => navigate(`/board/${boardId}/query/${query.id}`)}
                                            >
                                                <div className="flex items-start gap-3.5 mb-3">
                                                    <div className="w-10 h-10 flex-shrink-0 flex items-center justify-center bg-accent-primary/10 border border-accent-primary/20 rounded-lg text-accent-primary group-hover:bg-accent-primary/15 transition-colors">
                                                        <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
                                                            <path d="M3 12v3c0 1.657 3.134 3 7 3s7-1.343 7-3v-3c0 1.657-3.134 3-7 3s-7-1.343-7-3z" />
                                                            <path d="M3 7v3c0 1.657 3.134 3 7 3s7-1.343 7-3V7c0 1.657-3.134 3-7 3S3 8.657 3 7z" />
                                                            <path d="M17 5c0 1.657-3.134 3-7 3S3 6.657 3 5s3.134-3 7-3 7 1.343 7 3z" />
                                                        </svg>
                                                    </div>
                                                    <div className="flex-1 min-w-0">
                                                        <h3 className="text-base font-semibold text-text-primary truncate leading-tight mb-0.5">
                                                            {query.name}
                                                        </h3>
                                                        <p className="text-text-secondary text-xs leading-relaxed line-clamp-2">
                                                            {query.description || 'No description'}
                                                        </p>
                                                    </div>
                                                </div>

                                                <div className="flex items-center justify-between pt-3 border-t border-border-primary/60">
                                                    <div className="flex items-center gap-3">
                                                        <span className="flex items-center gap-1.5 text-[11px] text-text-muted">
                                                            <svg width="12" height="12" viewBox="0 0 20 20" fill="currentColor">
                                                                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-12a1 1 0 10-2 0v4a1 1 0 00.293.707l2.828 2.829a1 1 0 101.415-1.415L11 9.586V6z" />
                                                            </svg>
                                                            {new Date(query.updated_at).toLocaleDateString()}
                                                        </span>
                                                    </div>
                                                    <div className="flex items-center gap-2">
                                                        <button
                                                            onClick={(e) => deleteQuery(query.id, e)}
                                                            className="p-1.5 rounded-md text-text-muted opacity-0 group-hover:opacity-100 hover:bg-status-error/10 hover:text-status-error transition-all"
                                                            title="Delete"
                                                        >
                                                            <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
                                                                <path fillRule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" />
                                                            </svg>
                                                        </button>
                                                        <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor" className="text-text-muted group-hover:text-accent-primary group-hover:translate-x-0.5 transition-all">
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
                            <h2 className="text-lg font-bold text-text-primary">New Query</h2>
                            <button
                                onClick={() => setShowCreateQuery(false)}
                                className="p-1.5 rounded-lg text-text-muted hover:text-text-primary hover:bg-background-hover transition-all"
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
                                <label className="form-label">Description <span className="text-text-muted font-normal">(optional)</span></label>
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
        </div>
    )
}
