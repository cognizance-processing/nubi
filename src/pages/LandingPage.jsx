import { useState, useEffect, useMemo, useRef } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import {
  Sparkles, Code2, Database, MessageSquare, BarChart3, Zap,
  ArrowRight, Github, CheckCircle2, Send, Layers, BookOpen,
  Menu, X, Shield, Globe, Play, Table2, PanelTop, Puzzle,
} from 'lucide-react'

/* ─────────────────────────────────────────────
   Animated background
   ───────────────────────────────────────────── */

function Background() {
  const dots = useMemo(
    () =>
      Array.from({ length: 40 }).map((_, i) => ({
        id: i,
        top: `${Math.random() * 100}%`,
        left: `${Math.random() * 100}%`,
        duration: `${6 + Math.random() * 10}s`,
        delay: `${Math.random() * 8}s`,
        ty: `${12 + Math.random() * 28}px`,
      })),
    []
  )

  return (
    <div className="fixed inset-0 -z-10 overflow-hidden bg-slate-950" aria-hidden="true">
      <div
        className="absolute inset-0"
        style={{
          backgroundImage:
            'linear-gradient(to right,rgba(255,255,255,0.025) 1px,transparent 1px),linear-gradient(to bottom,rgba(255,255,255,0.025) 1px,transparent 1px)',
          backgroundSize: '72px 72px',
        }}
      />
      <div className="absolute -top-48 -left-48 h-[560px] w-[560px] rounded-full bg-indigo-700/[0.12] blur-[160px]" />
      <div className="absolute top-1/3 -right-48 h-[500px] w-[500px] rounded-full bg-purple-600/[0.09] blur-[160px]" />
      <div className="absolute bottom-0 left-1/2 -translate-x-1/2 h-[400px] w-[700px] rounded-full bg-indigo-800/[0.07] blur-[160px]" />
      <div
        className="absolute inset-x-0 top-0 h-[600px]"
        style={{
          background:
            'radial-gradient(ellipse 80% 50% at 50% 0%,rgba(99,102,241,0.07) 0%,transparent 70%)',
        }}
      />
      <div className="absolute inset-0 pointer-events-none">
        {dots.map((d) => (
          <span
            key={d.id}
            className="absolute block h-px w-px rounded-full bg-indigo-300/25"
            style={{
              top: d.top,
              left: d.left,
              animation: `fdot ${d.duration} ease-in-out infinite`,
              animationDelay: d.delay,
              '--ty': d.ty,
            }}
          />
        ))}
      </div>
      <style>{`
        @keyframes fdot {
          0%,100%{transform:translateY(0);opacity:.15}
          50%{transform:translateY(var(--ty,20px));opacity:.55}
        }
      `}</style>
    </div>
  )
}

/* ─────────────────────────────────────────────
   Animated hero demo — shows the full flow
   ───────────────────────────────────────────── */

function HeroDemo() {
  const [phase, setPhase] = useState(0)
  const [typedText, setTypedText] = useState('')
  const question = 'Show me top 10 products by revenue this quarter'
  const timerRef = useRef(null)

  useEffect(() => {
    const sequence = [
      { delay: 600, action: () => setPhase(1) },
    ]
    const timers = sequence.map(({ delay, action }) => setTimeout(action, delay))
    return () => timers.forEach(clearTimeout)
  }, [])

  useEffect(() => {
    if (phase !== 1) return
    let i = 0
    timerRef.current = setInterval(() => {
      i++
      setTypedText(question.slice(0, i))
      if (i >= question.length) {
        clearInterval(timerRef.current)
        setTimeout(() => setPhase(2), 400)
      }
    }, 30)
    return () => clearInterval(timerRef.current)
  }, [phase])

  useEffect(() => {
    if (phase !== 2) return
    const t = setTimeout(() => setPhase(3), 800)
    return () => clearTimeout(t)
  }, [phase])

  useEffect(() => {
    if (phase !== 3) return
    const t = setTimeout(() => setPhase(4), 1200)
    return () => clearTimeout(t)
  }, [phase])

  const bars = [35, 52, 44, 72, 58, 88, 65]

  return (
    <div className="relative">
      <div className="absolute -inset-px bg-gradient-to-b from-indigo-500/20 via-purple-500/10 to-transparent rounded-2xl" />
      <div className="relative bg-[#0a0f1e] border border-white/[0.06] rounded-2xl overflow-hidden shadow-2xl shadow-black/50">
        {/* Window chrome */}
        <div className="flex items-center gap-2 px-4 sm:px-5 py-3 border-b border-white/[0.06] bg-white/[0.02]">
          <div className="flex gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full bg-[#ff5f57]" />
            <div className="w-2.5 h-2.5 rounded-full bg-[#ffbd2e]" />
            <div className="w-2.5 h-2.5 rounded-full bg-[#28c840]" />
          </div>
          <div className="flex-1 text-center">
            <span className="text-xs text-slate-500 font-mono">nubi — query editor</span>
          </div>
        </div>

        <div className="p-4 sm:p-6 space-y-0">
          {/* Step 1 — User types a question */}
          <div className={`transition-all duration-500 ${phase >= 1 ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-2'}`}>
            <div className="flex items-center gap-2 mb-2">
              <MessageSquare className="h-3 w-3 text-indigo-400" />
              <span className="text-[10px] font-medium text-indigo-400 uppercase tracking-wider">You asked</span>
            </div>
            <div className="rounded-xl bg-indigo-600/10 border border-indigo-500/20 px-4 py-3 mb-1">
              <span className="text-sm text-indigo-200 font-medium">
                {typedText}
                {phase === 1 && <span className="inline-block w-0.5 h-4 bg-indigo-400 ml-0.5 animate-pulse align-middle" />}
              </span>
            </div>
          </div>

          {/* Step 2 — SQL generated */}
          <div className={`transition-all duration-500 delay-100 mt-4 ${phase >= 2 ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
            <div className="flex items-center gap-2 mb-2">
              <Code2 className="h-3 w-3 text-purple-400" />
              <span className="text-[10px] font-medium text-purple-400 uppercase tracking-wider">Generated SQL</span>
              {phase >= 2 && (
                <span className="ml-auto inline-flex items-center gap-1 text-[9px] text-emerald-400 font-medium">
                  <Zap className="h-2 w-2" />auto-generated
                </span>
              )}
            </div>
            <div className="rounded-xl bg-slate-900/80 border border-white/[0.06] p-4 font-mono text-[12px] sm:text-[13px] leading-relaxed">
              <div><span className="text-purple-400">SELECT</span> <span className="text-slate-200">product_name,</span></div>
              <div className="pl-6"><span className="text-amber-300">SUM</span><span className="text-slate-300">(revenue) </span><span className="text-purple-400">AS</span> <span className="text-slate-200">total_revenue</span></div>
              <div><span className="text-purple-400">FROM</span> <span className="text-cyan-300">sales</span></div>
              <div><span className="text-purple-400">WHERE</span> <span className="text-slate-300">quarter = </span><span className="text-emerald-300">'Q1 2026'</span></div>
              <div><span className="text-purple-400">GROUP BY</span> <span className="text-slate-200">product_name</span></div>
              <div><span className="text-purple-400">ORDER BY</span> <span className="text-slate-200">total_revenue</span> <span className="text-purple-400">DESC</span></div>
              <div><span className="text-purple-400">LIMIT</span> <span className="text-amber-300">10</span></div>
            </div>
          </div>

          {/* Step 3 — Results */}
          <div className={`transition-all duration-500 delay-200 mt-4 ${phase >= 3 ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
            <div className="flex items-center gap-2 mb-2">
              <Table2 className="h-3 w-3 text-emerald-400" />
              <span className="text-[10px] font-medium text-emerald-400 uppercase tracking-wider">Results</span>
              <span className="ml-auto text-[9px] text-slate-600">10 rows &middot; 84ms</span>
            </div>
            <div className="rounded-xl bg-slate-900/80 border border-white/[0.06] overflow-hidden">
              <div className="grid grid-cols-3 text-[11px] border-b border-white/[0.06]">
                <span className="px-3 py-2 text-slate-500 font-medium">Product</span>
                <span className="px-3 py-2 text-slate-500 font-medium">Revenue</span>
                <span className="px-3 py-2 text-slate-500 font-medium">Trend</span>
              </div>
              {[
                { p: 'Enterprise Suite', r: '$428K', pct: 92 },
                { p: 'Cloud Platform', r: '$312K', pct: 68 },
                { p: 'Analytics Pro', r: '$287K', pct: 62 },
              ].map(({ p, r, pct }) => (
                <div key={p} className="grid grid-cols-3 text-[11px] border-b border-white/[0.04] last:border-0">
                  <span className="px-3 py-2 text-slate-300">{p}</span>
                  <span className="px-3 py-2 text-emerald-400 font-medium">{r}</span>
                  <span className="px-3 py-2 flex items-center gap-1.5">
                    <div className="flex-1 h-1 rounded-full bg-slate-800 overflow-hidden">
                      <div className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-indigo-400" style={{ width: `${pct}%` }} />
                    </div>
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Step 4 — Pin to dashboard */}
          <div className={`transition-all duration-500 delay-300 mt-4 ${phase >= 4 ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
            <div className="flex items-center gap-2 mb-2">
              <PanelTop className="h-3 w-3 text-amber-400" />
              <span className="text-[10px] font-medium text-amber-400 uppercase tracking-wider">Dashboard</span>
            </div>
            <div className="rounded-xl bg-slate-900/80 border border-white/[0.06] p-3">
              <div className="grid grid-cols-3 gap-2 mb-3">
                {[
                  { v: '$2.4M', l: 'Total Revenue', c: 'text-emerald-400' },
                  { v: '1,847', l: 'Orders', c: 'text-indigo-400' },
                  { v: '+23.4%', l: 'Growth', c: 'text-amber-400' },
                ].map(({ v, l, c }) => (
                  <div key={l} className="rounded-lg bg-slate-800/80 border border-white/[0.05] px-2.5 py-2">
                    <div className={`text-xs font-bold ${c}`}>{v}</div>
                    <div className="text-[9px] text-slate-500">{l}</div>
                  </div>
                ))}
              </div>
              <div className="flex items-end gap-1 h-14">
                {bars.map((h, i) => (
                  <div
                    key={i}
                    className={`flex-1 rounded-t transition-all duration-700 ${i === 5 ? 'bg-gradient-to-t from-indigo-600 to-indigo-400' : 'bg-gradient-to-t from-indigo-900/60 to-indigo-700/40'}`}
                    style={{ height: phase >= 4 ? `${h}%` : '0%', transitionDelay: `${i * 80}ms` }}
                  />
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ─────────────────────────────────────────────
   Feature visuals — mini UI mockups
   ───────────────────────────────────────────── */

function QueryChatVisual() {
  return (
    <div className="rounded-xl bg-slate-900 border border-white/[0.07] overflow-hidden shadow-inner">
      <div className="flex items-center gap-2 px-3.5 py-2.5 bg-slate-800/60 border-b border-white/[0.06]">
        <img src="/nubi.png" alt="" className="h-4 w-4 rounded" />
        <span className="text-[11px] text-slate-300 font-medium">Query Editor</span>
        <div className="ml-auto flex items-center gap-1">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
          <span className="text-[9px] text-slate-500">ready</span>
        </div>
      </div>
      <div className="p-3 space-y-2.5">
        <div className="flex justify-end">
          <div className="rounded-2xl rounded-tr-sm bg-indigo-600 px-3 py-2 text-[11px] text-white max-w-[88%] leading-relaxed">
            Show me top 10 products by revenue this quarter
          </div>
        </div>
        <div className="flex justify-start">
          <span className="inline-flex items-center gap-1 rounded-full border border-indigo-500/20 bg-indigo-500/[0.08] px-2 py-0.5 text-[9px] text-indigo-400 font-mono">
            <Zap className="h-2 w-2" />generate_sql()
          </span>
        </div>
        <div className="flex gap-2 items-start">
          <img src="/nubi.png" alt="" className="h-5 w-5 rounded mt-0.5" />
          <div className="rounded-2xl rounded-tl-sm bg-slate-800 border border-white/[0.06] px-3 py-2 text-[11px] text-slate-200 max-w-[88%] leading-relaxed">
            <span className="text-purple-400 font-mono text-[10px]">SELECT</span>
            <span className="text-slate-300 font-mono text-[10px]"> product, </span>
            <span className="text-amber-300 font-mono text-[10px]">SUM</span>
            <span className="text-slate-300 font-mono text-[10px]">(revenue)</span>
            <span className="text-slate-500 font-mono text-[10px]"> ...</span>
            <div className="mt-1.5 flex items-center gap-1 text-emerald-400 text-[9px]">
              <CheckCircle2 className="h-2.5 w-2.5" />10 rows · 84ms
            </div>
          </div>
        </div>
      </div>
      <div className="px-3 pb-3">
        <div className="flex items-center gap-1.5 rounded-lg bg-slate-800 border border-white/[0.06] pl-3 pr-1.5 py-2">
          <span className="text-[10px] text-slate-600 flex-1">Ask a follow-up question...</span>
          <div className="h-5 w-5 rounded-md bg-indigo-600 flex items-center justify-center flex-shrink-0">
            <Send className="h-2.5 w-2.5 text-white" />
          </div>
        </div>
      </div>
    </div>
  )
}

function DashboardVisual() {
  const bars = [35, 52, 44, 72, 58, 88, 65]
  return (
    <div className="rounded-xl bg-slate-900 border border-white/[0.07] p-3">
      <div className="grid grid-cols-3 gap-1.5 mb-3">
        {[
          { v: '$2.4M', l: 'Revenue', c: 'text-emerald-400' },
          { v: '1,847', l: 'Orders', c: 'text-indigo-400' },
          { v: '23.4%', l: 'Growth', c: 'text-amber-400' },
        ].map(({ v, l, c }) => (
          <div key={l} className="rounded-lg bg-slate-800 border border-white/[0.05] px-2.5 py-2">
            <div className={`text-xs font-bold ${c}`}>{v}</div>
            <div className="text-[9px] text-slate-500">{l}</div>
          </div>
        ))}
      </div>
      <div className="flex items-end gap-1 h-16 mb-2">
        {bars.map((h, i) => (
          <div
            key={i}
            className={`flex-1 rounded-t-sm ${i === 5 ? 'bg-gradient-to-t from-indigo-600 to-indigo-400' : 'bg-gradient-to-t from-indigo-900/60 to-indigo-700/40'}`}
            style={{ height: `${h}%` }}
          />
        ))}
      </div>
      <div className="flex justify-between text-[9px] text-slate-600">
        {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d) => <span key={d}>{d}</span>)}
      </div>
    </div>
  )
}

function DataSourcesVisual() {
  return (
    <div className="rounded-xl bg-slate-900 border border-white/[0.07] overflow-hidden">
      <div className="p-3 space-y-2">
        {[
          { name: 'BigQuery', project: 'analytics-prod', status: 'Connected', color: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20', icon: '🔷' },
          { name: 'PostgreSQL', project: 'app-db-main', status: 'Connected', color: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20', icon: '🐘' },
          { name: 'CSV Upload', project: 'sales_q4.csv', status: 'Ready', color: 'text-amber-400 bg-amber-500/10 border-amber-500/20', icon: '📄' },
        ].map(({ name, project, status, color, icon }) => (
          <div key={name} className="flex items-center justify-between gap-2 rounded-lg bg-slate-800 border border-white/[0.05] px-2.5 py-2">
            <div className="flex items-center gap-2 min-w-0">
              <span className="text-sm flex-shrink-0">{icon}</span>
              <div className="min-w-0">
                <div className="text-[11px] text-slate-300 font-medium">{name}</div>
                <div className="text-[9px] text-slate-600 font-mono truncate">{project}</div>
              </div>
            </div>
            <span className={`text-[9px] font-medium px-1.5 py-0.5 rounded-full border flex-shrink-0 ${color}`}>
              {status}
            </span>
          </div>
        ))}
        <div className="rounded-lg border border-dashed border-indigo-500/20 bg-indigo-500/5 px-2.5 py-2 text-[10px] text-indigo-400 text-center">
          + Add data source
        </div>
      </div>
    </div>
  )
}

function SqlEditorVisual() {
  return (
    <div className="rounded-xl bg-slate-900 border border-white/[0.07] overflow-hidden">
      <div className="flex items-center gap-2 px-3.5 py-2.5 bg-slate-800/60 border-b border-white/[0.06]">
        <Code2 className="h-3.5 w-3.5 text-purple-400" />
        <span className="text-[11px] text-slate-300 font-medium font-mono">query.sql</span>
        <span className="ml-auto flex items-center gap-1.5">
          <span className="text-[9px] text-slate-600">Python</span>
          <span className="text-[9px] text-slate-700">|</span>
          <span className="text-[9px] text-indigo-400 font-medium">SQL</span>
        </span>
      </div>
      <div className="p-3 font-mono text-[10px] leading-relaxed space-y-0.5">
        <div className="flex">
          <span className="w-5 text-right text-slate-700 mr-2.5 select-none">1</span>
          <span><span className="text-purple-400">SELECT</span> <span className="text-slate-200">region,</span></span>
        </div>
        <div className="flex">
          <span className="w-5 text-right text-slate-700 mr-2.5 select-none">2</span>
          <span className="pl-4"><span className="text-amber-300">SUM</span><span className="text-slate-300">(revenue) </span><span className="text-purple-400">AS</span> <span className="text-slate-200">total</span></span>
        </div>
        <div className="flex">
          <span className="w-5 text-right text-slate-700 mr-2.5 select-none">3</span>
          <span><span className="text-purple-400">FROM</span> <span className="text-cyan-300">sales</span></span>
        </div>
        <div className="flex bg-indigo-500/[0.06] rounded">
          <span className="w-5 text-right text-indigo-500 mr-2.5 select-none">4</span>
          <span><span className="text-purple-400">WHERE</span> <span className="text-slate-300">date &gt;= </span><span className="text-emerald-300">'2024-10-01'</span></span>
        </div>
        <div className="flex">
          <span className="w-5 text-right text-slate-700 mr-2.5 select-none">5</span>
          <span><span className="text-purple-400">GROUP BY</span> <span className="text-slate-200">region</span></span>
        </div>
      </div>
      <div className="px-3 pb-2.5 flex items-center gap-2">
        <button className="flex items-center gap-1 rounded-md bg-emerald-600/20 border border-emerald-500/20 px-2 py-1 text-[9px] text-emerald-400 font-medium">
          <Play className="h-2 w-2" /> Run
        </button>
        <span className="text-[9px] text-slate-600">Ctrl+Enter</span>
      </div>
    </div>
  )
}

function PipelineVisual() {
  return (
    <div className="rounded-xl bg-slate-900 border border-white/[0.07] p-3 space-y-1.5">
      {[
        { step: 1, name: 'Raw Sales Data', type: 'SQL', color: 'border-purple-500/30 bg-purple-500/5', badge: 'text-purple-400 bg-purple-500/10 border-purple-500/20' },
        { step: 2, name: 'Clean & Transform', type: 'Python', color: 'border-amber-500/30 bg-amber-500/5', badge: 'text-amber-400 bg-amber-500/10 border-amber-500/20' },
        { step: 3, name: 'Final Report', type: 'SQL', color: 'border-emerald-500/30 bg-emerald-500/5', badge: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20' },
      ].map(({ step, name, type, color, badge }, i) => (
        <div key={step}>
          <div className={`flex items-center gap-2.5 rounded-lg border ${color} px-2.5 py-2`}>
            <div className="h-5 w-5 rounded-md bg-white/[0.05] flex items-center justify-center text-[9px] font-bold text-slate-400 flex-shrink-0">
              {step}
            </div>
            <span className="text-[11px] text-slate-300 flex-1">{name}</span>
            <span className={`text-[9px] font-medium px-1.5 py-0.5 rounded-full border ${badge}`}>{type}</span>
          </div>
          {i < 2 && (
            <div className="flex justify-center py-0.5">
              <div className="w-px h-2.5 bg-slate-700" />
            </div>
          )}
        </div>
      ))}
      <div className="rounded-lg bg-indigo-500/[0.06] border border-indigo-500/15 px-2.5 py-1.5 flex items-center gap-1.5">
        <Zap className="h-2.5 w-2.5 text-indigo-400" />
        <span className="text-[10px] text-indigo-300">Variables passed between steps</span>
      </div>
    </div>
  )
}

function AssistantVisual() {
  return (
    <div className="rounded-xl bg-slate-900 border border-white/[0.07] p-3 space-y-2.5">
      <div className="flex items-center gap-2 mb-1">
        <Sparkles className="h-3.5 w-3.5 text-indigo-400" />
        <span className="text-[11px] text-slate-300 font-medium">Gemini Assistant</span>
      </div>
      {[
        { q: 'Why did sales drop in March?', a: 'March saw a 23% decline driven by supply chain delays in the midwest region.' },
        { q: 'Can you break it down by category?', a: null },
      ].map(({ q, a }, i) => (
        <div key={i} className="space-y-2">
          <div className="flex justify-end">
            <div className="rounded-2xl rounded-tr-sm bg-indigo-600/80 px-3 py-1.5 text-[10px] text-white max-w-[85%]">{q}</div>
          </div>
          {a && (
            <div className="flex gap-2 items-start">
              <img src="/nubi.png" alt="" className="h-4 w-4 rounded mt-0.5" />
              <div className="rounded-2xl rounded-tl-sm bg-slate-800 border border-white/[0.06] px-3 py-1.5 text-[10px] text-slate-300 max-w-[85%] leading-relaxed">{a}</div>
            </div>
          )}
        </div>
      ))}
      <div className="flex items-center gap-1 text-[9px] text-slate-600">
        <span className="h-1 w-1 rounded-full bg-indigo-400 animate-pulse" />
        <span className="h-1 w-1 rounded-full bg-indigo-400 animate-pulse" style={{ animationDelay: '150ms' }} />
        <span className="h-1 w-1 rounded-full bg-indigo-400 animate-pulse" style={{ animationDelay: '300ms' }} />
        <span className="ml-1">Thinking...</span>
      </div>
    </div>
  )
}

function WidgetsVisual() {
  return (
    <div className="rounded-xl bg-slate-900 border border-white/[0.07] overflow-hidden">
      <div className="flex items-center gap-2 px-3.5 py-2.5 bg-slate-800/60 border-b border-white/[0.06]">
        <Puzzle className="h-3.5 w-3.5 text-indigo-400" />
        <span className="text-[11px] text-slate-300 font-medium">Widget Library</span>
        <span className="ml-auto text-[9px] text-slate-500">10+ templates</span>
      </div>
      <div className="p-2.5 grid grid-cols-2 gap-2">
        {[
          { icon: '📊', name: 'Bar Chart', color: 'border-blue-500/20 bg-blue-500/5' },
          { icon: '📈', name: 'Line Chart', color: 'border-indigo-500/20 bg-indigo-500/5' },
          { icon: '🍩', name: 'Donut Chart', color: 'border-purple-500/20 bg-purple-500/5' },
          { icon: '🔢', name: 'Stats Card', color: 'border-emerald-500/20 bg-emerald-500/5' },
          { icon: '📋', name: 'Data Table', color: 'border-teal-500/20 bg-teal-500/5' },
          { icon: '⏱️', name: 'Gauge', color: 'border-rose-500/20 bg-rose-500/5' },
        ].map(({ icon, name, color }) => (
          <div key={name} className={`flex items-center gap-2 rounded-lg border ${color} px-2.5 py-2`}>
            <span className="text-sm">{icon}</span>
            <span className="text-[10px] text-slate-300 font-medium">{name}</span>
          </div>
        ))}
      </div>
      <div className="px-3 pb-3 pt-1">
        <div className="flex items-center gap-1.5 px-2.5 py-2 rounded-lg bg-indigo-500/[0.06] border border-indigo-500/15">
          <Sparkles className="h-2.5 w-2.5 text-indigo-400" />
          <span className="text-[9px] text-indigo-300">Pick a template, customise data &amp; style, use on any board</span>
        </div>
      </div>
    </div>
  )
}

/* ─────────────────────────────────────────────
   Features config
   ───────────────────────────────────────────── */

const features = [
  {
    icon: MessageSquare,
    title: 'Chat-to-Query in Seconds',
    description: 'Type a question in plain English. Nubi translates it to optimised SQL instantly — you see the query, the results, and can refine with follow-ups. No SQL knowledge required.',
    badge: 'Core',
    span: 'sm:col-span-2',
    visual: <QueryChatVisual />,
  },
  {
    icon: BarChart3,
    title: 'Dashboards Built from Chat',
    description: 'Every query result can become a dashboard widget — bar chart, line chart, KPI card, or data table. Ask a question, pin the answer, build a complete dashboard without leaving the conversation.',
    badge: 'Visuals',
    span: 'sm:col-span-2',
    visual: <DashboardVisual />,
  },
  {
    icon: Puzzle,
    title: 'Pre-Built Widget Library',
    description: 'Start from 10+ ready-made widget templates — bar charts, line charts, donut charts, KPI stat cards, sortable data tables, gauges, and drill-down views. Pick one, customise the data and styling, and drop it onto any dashboard.',
    badge: 'Widgets',
    span: 'sm:col-span-2',
    visual: <WidgetsVisual />,
  },
  {
    icon: Database,
    title: 'Connect Any Data Source',
    description: 'BigQuery, PostgreSQL, or CSV uploads. One unified interface for all your data. Credentials encrypted at rest.',
    badge: 'Data',
    visual: <DataSourcesVisual />,
  },
  {
    icon: Code2,
    title: 'Full SQL & Python Editor',
    description: 'When you need precise control, drop into the code editor with syntax highlighting, autocomplete, and instant execution.',
    badge: 'Developer',
    visual: <SqlEditorVisual />,
  },
  {
    icon: Zap,
    title: 'Data Stitching Pipelines',
    description: 'Chain SQL and Python steps into multi-step workflows. Output from one step feeds into the next with template variables.',
    badge: 'Workflows',
    visual: <PipelineVisual />,
  },
  {
    icon: Sparkles,
    title: 'AI Assistant That Remembers',
    description: 'Ask follow-ups, request tweaks, dig into anomalies. The Gemini-powered assistant keeps your full context across the conversation.',
    badge: 'AI',
    visual: <AssistantVisual />,
  },
]

/* ─────────────────────────────────────────────
   Main component
   ───────────────────────────────────────────── */

export default function LandingPage() {
  const navigate = useNavigate()
  const { user } = useAuth()
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 20)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  const ctaAction = () => navigate(user ? '/portal' : '/login')
  const ctaLabel = user ? 'Go to Portal' : 'Get Started Free'

  return (
    <div className="relative min-h-screen text-white">
      <Background />

      {/* ── Nav ── */}
      <nav className={`fixed top-0 w-full z-50 transition-all duration-300 ${scrolled ? 'bg-slate-950/85 backdrop-blur-xl border-b border-white/[0.06] shadow-lg shadow-black/20' : 'bg-transparent'}`}>
        <div className="max-w-6xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-2.5">
            <img src="/nubi.png" alt="Nubi" className="w-8 h-8 rounded-lg shadow-lg shadow-indigo-500/20" />
            <span className="text-xl font-bold tracking-tight">Nubi</span>
          </Link>

          <div className="hidden md:flex items-center gap-1">
            <a href="#features" className="px-3 py-2 text-sm text-slate-400 hover:text-white transition-colors rounded-lg hover:bg-white/[0.04]">Features</a>
            <a href="#how-it-works" className="px-3 py-2 text-sm text-slate-400 hover:text-white transition-colors rounded-lg hover:bg-white/[0.04]">How it Works</a>
            <Link to="/docs" className="px-3 py-2 text-sm text-slate-400 hover:text-white transition-colors rounded-lg hover:bg-white/[0.04]">Docs</Link>
          </div>

          <div className="hidden md:flex items-center gap-3">
            {user ? (
              <button onClick={() => navigate('/portal')} className="px-5 py-2 text-sm font-medium bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-all hover:shadow-lg hover:shadow-indigo-500/25">
                Go to Portal
              </button>
            ) : (
              <>
                <button onClick={() => navigate('/login')} className="px-4 py-2 text-sm text-slate-300 hover:text-white transition-colors">Log in</button>
                <button onClick={() => navigate('/login')} className="px-5 py-2 text-sm font-medium bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-all hover:shadow-lg hover:shadow-indigo-500/25">
                  Get Started
                </button>
              </>
            )}
          </div>

          <button className="md:hidden p-2 text-slate-400 hover:text-white" onClick={() => setMobileMenuOpen(!mobileMenuOpen)}>
            {mobileMenuOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
          </button>
        </div>

        {mobileMenuOpen && (
          <div className="md:hidden bg-slate-950/95 backdrop-blur-xl border-b border-white/[0.06] px-6 pb-6 pt-2 space-y-1 animate-fade-in">
            <a href="#features" onClick={() => setMobileMenuOpen(false)} className="block px-3 py-2.5 text-sm text-slate-400 hover:text-white rounded-lg">Features</a>
            <a href="#how-it-works" onClick={() => setMobileMenuOpen(false)} className="block px-3 py-2.5 text-sm text-slate-400 hover:text-white rounded-lg">How it Works</a>
            <Link to="/docs" className="block px-3 py-2.5 text-sm text-slate-400 hover:text-white rounded-lg">Docs</Link>
            <div className="pt-3 border-t border-white/[0.06]">
              <button onClick={ctaAction} className="w-full px-5 py-2.5 text-sm font-medium bg-indigo-600 hover:bg-indigo-500 rounded-lg">{ctaLabel}</button>
            </div>
          </div>
        )}
      </nav>

      {/* ── Hero ── */}
      <section className="relative pt-24 sm:pt-28 pb-6 px-4 sm:px-6">
        <div className="max-w-4xl mx-auto text-center relative z-10">
          <div className="inline-flex items-center gap-2 px-4 py-1.5 bg-indigo-500/[0.08] border border-indigo-500/20 rounded-full text-indigo-300 text-xs font-medium tracking-wide mb-6 backdrop-blur-sm">
            <Sparkles className="w-3.5 h-3.5" />
            Open-Source Business Intelligence Platform
          </div>

          <h1 className="text-4xl sm:text-5xl lg:text-[4.25rem] font-extrabold leading-[1.1] tracking-tight mb-6">
            Chat with your data.
            <br />
            <span className="bg-gradient-to-r from-indigo-400 via-purple-400 to-pink-400 bg-clip-text text-transparent">
              Build dashboards instantly.
            </span>
          </h1>

          <p className="text-base sm:text-lg text-slate-400 max-w-2xl mx-auto mb-7 leading-relaxed">
            Ask questions in plain English, get SQL-powered answers in seconds, and build dashboards from a library of pre-configured widgets — charts, KPI cards, tables, and more. Connect BigQuery, PostgreSQL, or upload a CSV to start.
          </p>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-3 mb-4">
            <button
              onClick={ctaAction}
              className="group w-full sm:w-auto px-8 py-3.5 bg-indigo-600 hover:bg-indigo-500 rounded-xl font-semibold transition-all duration-200 flex items-center justify-center gap-2 hover:shadow-xl hover:shadow-indigo-500/25 hover:-translate-y-0.5 text-[0.95rem]"
            >
              {ctaLabel}
              <ArrowRight className="w-4 h-4 group-hover:translate-x-0.5 transition-transform" />
            </button>
            <Link
              to="/docs"
              className="w-full sm:w-auto px-8 py-3.5 bg-white/[0.04] hover:bg-white/[0.08] border border-white/[0.08] hover:border-white/[0.15] rounded-xl font-semibold text-[0.95rem] transition-all duration-200 flex items-center justify-center gap-2"
            >
              <BookOpen className="w-4 h-4" />
              Read the Docs
            </Link>
          </div>
          <p className="text-xs text-slate-600">Free &amp; open source &middot; Self-host in under 2 minutes &middot; No credit card</p>
        </div>
      </section>

      {/* ── Animated Demo ── */}
      <section className="relative z-10 px-4 sm:px-6 pb-14 sm:pb-16">
        <div className="max-w-3xl mx-auto">
          <HeroDemo />
        </div>
      </section>

      {/* ── Social proof bar ── */}
      <section className="relative z-10 border-y border-white/[0.04] bg-white/[0.01]">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-7 grid grid-cols-2 md:grid-cols-4 gap-6 sm:gap-8 text-center">
          {[
            { value: 'Open Source', label: 'Apache 2.0 licensed' },
            { value: '5+ Sources', label: 'BigQuery, Postgres, CSV...' },
            { value: '< 2 min', label: 'From install to first query' },
            { value: 'AI-Native', label: 'Gemini-powered assistant' },
          ].map(({ value, label }) => (
            <div key={label}>
              <div className="text-lg sm:text-xl font-bold text-white mb-0.5">{value}</div>
              <div className="text-xs sm:text-sm text-slate-500">{label}</div>
            </div>
          ))}
        </div>
      </section>

      {/* ── Features ── */}
      <section id="features" className="relative z-10 py-14 sm:py-18 px-4 sm:px-6">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-10 sm:mb-12">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-indigo-500/20 bg-indigo-500/10 px-3 py-1 text-xs font-medium text-indigo-400 mb-5">
              <Layers className="h-3 w-3" />
              Features
            </span>
            <h2 className="text-3xl font-extrabold tracking-tight text-white sm:text-4xl md:text-5xl mb-5">
              From question to dashboard
              <br />
              <span className="text-slate-500">in a single conversation</span>
            </h2>
            <p className="max-w-2xl mx-auto text-slate-400 text-base sm:text-lg leading-relaxed">
              Ask a question, get a SQL query, see results, and pin them as dashboard widgets — bar charts, KPI cards, data tables, gauges, and more. Natural language queries, Python scripting, data pipelines, and a full widget library in one platform.
            </p>
          </div>

          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {features.map(({ icon: Icon, title, description, badge, span, visual }) => (
              <div
                key={title}
                className={`group relative flex flex-col rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5 sm:p-6 transition-all duration-300 hover:border-indigo-500/25 hover:bg-indigo-500/[0.02] ${span ?? ''}`}
              >
                <span className="mb-4 inline-flex w-fit items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-2.5 py-0.5 text-[10px] font-medium text-slate-400">
                  <Icon className="h-2.5 w-2.5" />
                  {badge}
                </span>

                <div className={span ? 'sm:flex sm:gap-8 sm:items-start' : ''}>
                  <div className={span ? 'sm:flex-1' : ''}>
                    <h3 className="text-base font-bold text-white mb-2">{title}</h3>
                    <p className="text-sm text-slate-500 leading-relaxed mb-5">{description}</p>
                  </div>
                  <div className={span ? 'sm:flex-1' : ''}>
                    {visual}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── How it works ── */}
      <section id="how-it-works" className="relative z-10 py-14 sm:py-18 px-4 sm:px-6">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-10 sm:mb-12">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-indigo-500/20 bg-indigo-500/10 px-3 py-1 text-xs font-medium text-indigo-400 mb-5">
              <Zap className="h-3 w-3" />
              How it Works
            </span>
            <h2 className="text-3xl font-extrabold tracking-tight text-white sm:text-4xl mb-5">
              Three steps to your first dashboard
            </h2>
            <p className="max-w-lg mx-auto text-slate-400 text-base sm:text-lg leading-relaxed">
              Connect a database, ask a question, and pin the answer. That's it.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {[
              { step: '01', icon: Database, title: 'Connect your data', desc: 'Link BigQuery, PostgreSQL, or upload a CSV. Nubi introspects your schema automatically — no configuration needed.' },
              { step: '02', icon: MessageSquare, title: 'Ask in plain English', desc: 'Type "What were our top products last quarter?" and Nubi generates optimised SQL, runs it, and returns results in under a second.' },
              { step: '03', icon: BarChart3, title: 'Pin to your dashboard', desc: 'One click turns any result into a live dashboard widget — bar chart, line chart, KPI card, or data table. Share it with your team.' },
            ].map(({ step, icon: Icon, title, desc }) => (
              <div key={step} className="relative rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5 sm:p-6">
                <div className="flex items-center gap-3 mb-4">
                  <div className="w-10 h-10 rounded-xl bg-indigo-500/10 border border-indigo-500/15 flex items-center justify-center">
                    <Icon className="w-5 h-5 text-indigo-400" />
                  </div>
                  <span className="text-xs font-mono text-indigo-400/60 tracking-widest">STEP {step}</span>
                </div>
                <h3 className="text-lg font-bold text-white mb-2">{title}</h3>
                <p className="text-sm text-slate-500 leading-relaxed">{desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Why Nubi ── */}
      <section className="relative z-10 py-14 sm:py-18 px-4 sm:px-6">
        <div className="max-w-6xl mx-auto">
          <div className="grid lg:grid-cols-2 gap-10 lg:gap-14 items-center">
            <div>
              <span className="inline-flex items-center gap-1.5 rounded-full border border-indigo-500/20 bg-indigo-500/10 px-3 py-1 text-xs font-medium text-indigo-400 mb-6">
                <Shield className="h-3 w-3" />
                Why Nubi
              </span>
              <h2 className="text-3xl font-extrabold tracking-tight text-white sm:text-4xl mb-5">
                Conversational BI that actually
                <br />
                <span className="text-slate-500">replaces your SQL client.</span>
              </h2>
              <p className="text-base sm:text-lg text-slate-400 mb-6 leading-relaxed">
                Most BI tools make you choose between ease-of-use and power. Nubi starts with chat — ask anything in plain English — then lets you drop into SQL or Python whenever you need full control.
              </p>

              <div className="space-y-3.5">
                {[
                  { t: 'Chat-first interface', d: 'Ask questions the way you think. The AI handles schema awareness, joins, and aggregations.' },
                  { t: 'SQL & Python when you need it', d: 'Full code editors with autocomplete. Edit generated queries or write from scratch.' },
                  { t: 'One-click dashboards', d: 'Pin any chat result as a live widget. Build a complete dashboard from a conversation.' },
                  { t: 'Widget template library', d: 'Pick from charts, KPI cards, tables, gauges and more. Customise and reuse across any board.' },
                  { t: 'Self-hosted & private', d: 'Your data never leaves your infrastructure. Apache 2.0 licensed. Deploy in 2 minutes.' },
                ].map(({ t, d }) => (
                  <div key={t} className="flex items-start gap-3">
                    <CheckCircle2 className="w-5 h-5 text-emerald-400 flex-shrink-0 mt-0.5" />
                    <div>
                      <div className="text-white font-medium text-sm mb-0.5">{t}</div>
                      <div className="text-slate-500 text-sm leading-relaxed">{d}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="relative">
              <div className="absolute inset-0 bg-gradient-to-r from-indigo-500/10 to-purple-500/10 rounded-3xl blur-3xl" />
              <div className="relative grid grid-cols-2 gap-3">
                {[
                  { icon: Zap, label: 'Instant Setup', desc: 'First query in under 2 minutes', color: 'border-indigo-500/15 bg-gradient-to-br from-indigo-500/10 to-indigo-600/5' },
                  { icon: Globe, label: 'Any Database', desc: 'BigQuery, Postgres, CSV & more', color: 'border-purple-500/15 bg-gradient-to-br from-purple-500/10 to-purple-600/5' },
                  { icon: Shield, label: 'Self-Hosted', desc: 'Your data stays on your servers', color: 'border-emerald-500/15 bg-gradient-to-br from-emerald-500/10 to-emerald-600/5' },
                  { icon: Puzzle, label: '10+ Widgets', desc: 'Charts, cards, tables & gauges', color: 'border-amber-500/15 bg-gradient-to-br from-amber-500/10 to-amber-600/5' },
                ].map(({ icon: Icon, label, desc, color }) => (
                  <div key={label} className={`border rounded-2xl p-5 ${color}`}>
                    <Icon className="w-5 h-5 text-white/50 mb-3" />
                    <div className="text-white font-medium text-sm mb-0.5">{label}</div>
                    <div className="text-slate-500 text-xs leading-relaxed">{desc}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── CTA ── */}
      <section className="relative z-10 py-14 sm:py-18 px-4 sm:px-6">
        <div className="max-w-3xl mx-auto">
          <div className="relative rounded-3xl overflow-hidden">
            <div className="absolute inset-0 bg-gradient-to-br from-indigo-600/30 via-purple-600/20 to-pink-600/10" />
            <div className="absolute inset-px rounded-3xl bg-slate-950/90" />
            <div className="relative px-6 py-12 sm:px-16 sm:py-14 text-center">
              <h2 className="text-3xl sm:text-4xl font-extrabold text-white mb-5">
                Stop writing SQL by hand.<br />Start asking questions.
              </h2>
              <p className="text-base sm:text-lg text-slate-400 mb-7 max-w-md mx-auto leading-relaxed">
                Connect your database, ask a question in plain English, and pin the answer to a dashboard. It takes 2 minutes.
              </p>
              <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
                <button
                  onClick={ctaAction}
                  className="group w-full sm:w-auto px-8 py-3.5 bg-white text-slate-900 rounded-xl font-semibold text-[0.95rem] transition-all duration-200 flex items-center justify-center gap-2 hover:shadow-xl hover:shadow-white/10 hover:-translate-y-0.5"
                >
                  {ctaLabel}
                  <ArrowRight className="w-4 h-4 group-hover:translate-x-0.5 transition-transform" />
                </button>
                <Link
                  to="/docs"
                  className="w-full sm:w-auto px-8 py-3.5 bg-white/[0.06] hover:bg-white/[0.1] border border-white/[0.08] rounded-xl font-semibold text-[0.95rem] transition-all flex items-center justify-center gap-2"
                >
                  <BookOpen className="w-4 h-4" />
                  Read the Docs
                </Link>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer className="relative z-10 border-t border-white/[0.04] bg-slate-950/80">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-10 sm:py-12">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-8 sm:gap-10 mb-10 sm:mb-12">
            <div className="col-span-2 md:col-span-1">
              <div className="flex items-center gap-2.5 mb-4">
                <img src="/nubi.png" alt="Nubi" className="w-7 h-7 rounded-lg" />
                <span className="text-lg font-bold">Nubi</span>
              </div>
              <p className="text-sm text-slate-500 leading-relaxed">
                Open-source, AI-powered business intelligence. Chat with your data and build dashboards from pre-configured widgets.
              </p>
            </div>

            <div>
              <h4 className="text-xs font-semibold text-slate-400 tracking-wider mb-4">PRODUCT</h4>
              <ul className="space-y-2.5">
                <li><a href="#features" className="text-sm text-slate-500 hover:text-white transition-colors">Features</a></li>
                <li><a href="#how-it-works" className="text-sm text-slate-500 hover:text-white transition-colors">How it Works</a></li>
                <li><Link to="/docs" className="text-sm text-slate-500 hover:text-white transition-colors">Documentation</Link></li>
              </ul>
            </div>

            <div>
              <h4 className="text-xs font-semibold text-slate-400 tracking-wider mb-4">RESOURCES</h4>
              <ul className="space-y-2.5">
                <li><Link to="/docs/getting-started" className="text-sm text-slate-500 hover:text-white transition-colors">Quick Start</Link></li>
                <li><Link to="/docs/data-sources" className="text-sm text-slate-500 hover:text-white transition-colors">Data Sources</Link></li>
                <li><Link to="/docs/queries" className="text-sm text-slate-500 hover:text-white transition-colors">Writing Queries</Link></li>
                <li><Link to="/docs/widgets" className="text-sm text-slate-500 hover:text-white transition-colors">Widgets</Link></li>
              </ul>
            </div>

            <div>
              <h4 className="text-xs font-semibold text-slate-400 tracking-wider mb-4">LEGAL</h4>
              <ul className="space-y-2.5">
                <li><Link to="/docs/privacy" className="text-sm text-slate-500 hover:text-white transition-colors">Privacy Policy</Link></li>
                <li><Link to="/docs/terms" className="text-sm text-slate-500 hover:text-white transition-colors">Terms of Service</Link></li>
                <li><Link to="/docs/popia" className="text-sm text-slate-500 hover:text-white transition-colors">POPIA</Link></li>
              </ul>
            </div>
          </div>

          <div className="flex flex-col sm:flex-row items-center justify-between gap-4 pt-8 border-t border-white/[0.04]">
            <span className="text-sm text-slate-600">&copy; {new Date().getFullYear()} Cognizance Processing (Pty) Ltd. All rights reserved.</span>
          </div>
        </div>
      </footer>
    </div>
  )
}
