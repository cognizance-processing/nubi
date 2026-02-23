import { useState, useEffect } from 'react'
import api from '../lib/api'
import { BarChart3, Zap, ArrowDownUp, Clock, ChevronDown } from 'lucide-react'

const PERIOD_OPTIONS = [
    { label: 'Today', days: 1 },
    { label: '7 days', days: 7 },
    { label: '30 days', days: 30 },
    { label: 'All time', days: 3650 },
]

function formatNumber(n) {
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
    if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K'
    return n.toLocaleString()
}

const PROVIDER_COLORS = {
    gemini: 'bg-blue-500/15 text-blue-400 border-blue-500/20',
    anthropic: 'bg-amber-500/15 text-amber-400 border-amber-500/20',
    openai: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/20',
    deepseek: 'bg-purple-500/15 text-purple-400 border-purple-500/20',
}

const PROVIDER_BAR_COLORS = {
    gemini: 'bg-blue-500',
    anthropic: 'bg-amber-500',
    openai: 'bg-emerald-500',
    deepseek: 'bg-purple-500',
}

export default function UsagePage() {
    const [days, setDays] = useState(30)
    const [summary, setSummary] = useState(null)
    const [details, setDetails] = useState([])
    const [loading, setLoading] = useState(true)

    useEffect(() => {
        fetchUsage()
    }, [days])

    const fetchUsage = async () => {
        setLoading(true)
        try {
            const [summaryData, detailsData] = await Promise.all([
                api.usage.summary(days),
                api.usage.details(Math.min(days, 7), 50),
            ])
            setSummary(summaryData)
            setDetails(detailsData || [])
        } catch (err) {
            console.error('Error fetching usage:', err)
        } finally {
            setLoading(false)
        }
    }

    const maxTokens = summary?.models?.reduce((max, m) => Math.max(max, m.total_input_tokens + m.total_output_tokens), 0) || 1

    if (loading && !summary) {
        return (
            <div className="flex flex-col items-center justify-center min-h-[60vh] gap-3">
                <div className="spinner" />
                <p className="text-slate-500 text-sm">Loading usage data...</p>
            </div>
        )
    }

    return (
        <div className="p-6 lg:p-8 max-w-5xl mx-auto w-full">
            {/* Header */}
            <div className="flex items-center justify-between mb-8">
                <div>
                    <h1 className="text-2xl font-bold text-white mb-1">AI Usage</h1>
                    <p className="text-slate-500 text-sm">Token consumption across models</p>
                </div>
                <div className="flex items-center gap-1 rounded-lg border border-white/[0.08] bg-white/[0.02] p-0.5">
                    {PERIOD_OPTIONS.map(opt => (
                        <button
                            key={opt.days}
                            onClick={() => setDays(opt.days)}
                            className={`px-3 py-1.5 rounded-md text-xs font-medium transition ${
                                days === opt.days
                                    ? 'bg-indigo-600/20 text-indigo-400'
                                    : 'text-slate-500 hover:text-white hover:bg-white/[0.04]'
                            }`}
                        >
                            {opt.label}
                        </button>
                    ))}
                </div>
            </div>

            {/* Summary cards */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-8">
                <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-5">
                    <div className="flex items-center gap-2 mb-3">
                        <div className="w-8 h-8 rounded-lg bg-indigo-500/10 border border-indigo-500/15 flex items-center justify-center">
                            <Zap className="h-4 w-4 text-indigo-400" />
                        </div>
                        <span className="text-xs text-slate-500 font-medium">Total Requests</span>
                    </div>
                    <p className="text-2xl font-bold text-white">{formatNumber(summary?.total_requests || 0)}</p>
                </div>
                <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-5">
                    <div className="flex items-center gap-2 mb-3">
                        <div className="w-8 h-8 rounded-lg bg-emerald-500/10 border border-emerald-500/15 flex items-center justify-center">
                            <ArrowDownUp className="h-4 w-4 text-emerald-400" />
                        </div>
                        <span className="text-xs text-slate-500 font-medium">Input Tokens</span>
                    </div>
                    <p className="text-2xl font-bold text-white">{formatNumber(summary?.total_input_tokens || 0)}</p>
                </div>
                <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-5">
                    <div className="flex items-center gap-2 mb-3">
                        <div className="w-8 h-8 rounded-lg bg-purple-500/10 border border-purple-500/15 flex items-center justify-center">
                            <BarChart3 className="h-4 w-4 text-purple-400" />
                        </div>
                        <span className="text-xs text-slate-500 font-medium">Output Tokens</span>
                    </div>
                    <p className="text-2xl font-bold text-white">{formatNumber(summary?.total_output_tokens || 0)}</p>
                </div>
            </div>

            {/* Per-model breakdown */}
            <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] mb-8">
                <div className="px-5 py-4 border-b border-white/[0.07]">
                    <h2 className="text-sm font-semibold text-white">Usage by Model</h2>
                </div>
                {(!summary?.models || summary.models.length === 0) ? (
                    <div className="px-5 py-10 text-center">
                        <p className="text-slate-500 text-sm">No usage data yet. Start a chat to see usage here.</p>
                    </div>
                ) : (
                    <div className="divide-y divide-white/[0.05]">
                        {summary.models.map((m) => {
                            const total = m.total_input_tokens + m.total_output_tokens
                            const pct = maxTokens > 0 ? (total / maxTokens) * 100 : 0
                            const providerColor = PROVIDER_COLORS[m.provider] || 'bg-slate-500/15 text-slate-400 border-slate-500/20'
                            const barColor = PROVIDER_BAR_COLORS[m.provider] || 'bg-slate-500'

                            return (
                                <div key={m.model} className="px-5 py-4">
                                    <div className="flex items-center justify-between mb-2">
                                        <div className="flex items-center gap-2.5">
                                            <span className="text-sm font-medium text-white">{m.model}</span>
                                            <span className={`text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded border ${providerColor}`}>
                                                {m.provider}
                                            </span>
                                        </div>
                                        <div className="text-xs text-slate-500">
                                            {formatNumber(m.request_count)} requests
                                        </div>
                                    </div>
                                    <div className="h-2 rounded-full bg-white/[0.04] overflow-hidden mb-2">
                                        <div
                                            className={`h-full rounded-full ${barColor} transition-all duration-500`}
                                            style={{ width: `${Math.max(pct, 1)}%` }}
                                        />
                                    </div>
                                    <div className="flex items-center gap-4 text-[11px] text-slate-500">
                                        <span>In: <span className="text-slate-300 font-medium">{formatNumber(m.total_input_tokens)}</span></span>
                                        <span>Out: <span className="text-slate-300 font-medium">{formatNumber(m.total_output_tokens)}</span></span>
                                        <span>Total: <span className="text-slate-300 font-medium">{formatNumber(total)}</span></span>
                                    </div>
                                </div>
                            )
                        })}
                    </div>
                )}
            </div>

            {/* Recent requests */}
            <div className="rounded-xl border border-white/[0.07] bg-white/[0.02]">
                <div className="px-5 py-4 border-b border-white/[0.07]">
                    <h2 className="text-sm font-semibold text-white flex items-center gap-2">
                        <Clock className="h-3.5 w-3.5 text-slate-500" />
                        Recent Requests
                    </h2>
                </div>
                {details.length === 0 ? (
                    <div className="px-5 py-10 text-center">
                        <p className="text-slate-500 text-sm">No recent requests.</p>
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                            <thead>
                                <tr className="border-b border-white/[0.05]">
                                    <th className="text-left px-5 py-3 text-slate-500 font-medium">Model</th>
                                    <th className="text-left px-4 py-3 text-slate-500 font-medium">Provider</th>
                                    <th className="text-right px-4 py-3 text-slate-500 font-medium">Input</th>
                                    <th className="text-right px-4 py-3 text-slate-500 font-medium">Output</th>
                                    <th className="text-right px-5 py-3 text-slate-500 font-medium">Time</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-white/[0.04]">
                                {details.map((d) => (
                                    <tr key={d.id} className="hover:bg-white/[0.02] transition">
                                        <td className="px-5 py-2.5 font-medium text-slate-300">{d.model}</td>
                                        <td className="px-4 py-2.5">
                                            <span className={`text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded border ${PROVIDER_COLORS[d.provider] || ''}`}>
                                                {d.provider}
                                            </span>
                                        </td>
                                        <td className="px-4 py-2.5 text-right text-slate-400">{formatNumber(d.input_tokens)}</td>
                                        <td className="px-4 py-2.5 text-right text-slate-400">{formatNumber(d.output_tokens)}</td>
                                        <td className="px-5 py-2.5 text-right text-slate-600">
                                            {new Date(d.created_at).toLocaleString(undefined, {
                                                month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
                                            })}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    )
}
