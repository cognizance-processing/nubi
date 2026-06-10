/**
 * BlendBuilder — combine 2–3 data sources into ONE materialized dataset
 * a dashboard can cheaply query.
 *
 * Concept (cost/wedge-first, materialize-then-serve)
 * --------------------------------------------------
 * A "blend" is a SCHEDULED flow that:
 *   1. runs N (2–3) source queries against their connectors,
 *   2. combines them in DuckDB via a single `combine_sql`,
 *   3. materializes ONE dataset dashboards read between refreshes.
 * This is NOT live federation — reads are cheap because they hit the
 * materialized table, and the heavy combine only runs on the schedule.
 *
 * Submits:  POST /flows/blend
 *   {
 *     name, sources: [{ key, query_id|sql, datastore_id }, ...],
 *     combine_sql, schedule?, rls_keys?
 *   }
 *   → { flow, materialized: { datastore_id, query_id } }
 * The returned `materialized.query_id` is what a dashboard widget binds to.
 *
 * Owns: this file (+ an entry point on QueriesPage and a route in App.jsx).
 * Uses the existing get/post api helpers directly.
 */

import { useState, useEffect, useCallback, useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  Combine,
  Layers,
  GitMerge,
  Plus,
  Trash2,
  FileCode2,
  Code2,
  Database,
  CalendarClock,
  Clock,
  Shield,
  X,
  Copy,
  Check,
  CheckCircle2,
  AlertCircle,
  Loader2,
  ArrowLeft,
  ExternalLink,
} from 'lucide-react'

import { get, post } from '../../lib/api.js'
import { useCanWrite } from '../../contexts/OrgContext.jsx'
import SqlEditor from '../../components/SqlEditor.jsx'

// ---------------------------------------------------------------------------
// Source limits
// ---------------------------------------------------------------------------

const MIN_SOURCES = 2
const MAX_SOURCES = 4

let _srcSeq = 0
function newSource(defaultKey) {
  _srcSeq += 1
  return {
    _id: `src-${Date.now()}-${_srcSeq}`,
    key: defaultKey ?? '',
    mode: 'query',          // 'query' | 'sql'
    query_id: '',
    sql: '',
    datastore_id: '',
  }
}

// ---------------------------------------------------------------------------
// Schedule helpers (mirrors the interval/cron picker used elsewhere)
// ---------------------------------------------------------------------------

const INTERVAL_UNITS = [
  { value: 'm', label: 'minutes' },
  { value: 'h', label: 'hours' },
  { value: 'd', label: 'days' },
]

function buildIntervalString(n, unit) {
  const count = Math.max(1, Math.floor(Number(n) || 1))
  if (unit === 'd') return `interval:${count * 24}h`
  return `interval:${count}${unit}`
}

function describeSchedule(mode, schedule) {
  if (!schedule) return null
  if (mode === 'interval') {
    const m = /^interval:(\d+)([mh])$/.exec(schedule)
    if (!m) return null
    const n = Number(m[1])
    if (m[2] === 'm') return `Materializes every ${n} minute${n !== 1 ? 's' : ''}.`
    if (n % 24 === 0) {
      const days = n / 24
      return `Materializes every ${days} day${days !== 1 ? 's' : ''}.`
    }
    return `Materializes every ${n} hour${n !== 1 ? 's' : ''}.`
  }
  const parts = schedule.trim().split(/\s+/)
  if (parts.length === 5) return 'Valid 5-field cron expression.'
  return null
}

// ---------------------------------------------------------------------------
// RLS keys — tiny tags input
// ---------------------------------------------------------------------------

function RlsKeysInput({ keys, onChange }) {
  const [draft, setDraft] = useState('')

  const add = useCallback((raw) => {
    const v = String(raw || '').trim().replace(/,$/, '')
    if (!v) return
    if (keys.includes(v)) { setDraft(''); return }
    onChange([...keys, v])
    setDraft('')
  }, [keys, onChange])

  const remove = useCallback((k) => {
    onChange(keys.filter(x => x !== k))
  }, [keys, onChange])

  return (
    <div className="flex flex-wrap items-center gap-1.5 min-h-8 px-2 py-1.5 rounded-lg border border-border bg-surface focus-within:ring-1 focus-within:ring-ring">
      {keys.map(k => (
        <span
          key={k}
          className="inline-flex items-center gap-1 pl-2 pr-1 h-6 rounded-md bg-primary/10 text-primary text-[11px] font-mono"
        >
          {k}
          <button
            type="button"
            onClick={() => remove(k)}
            className="flex items-center justify-center w-4 h-4 rounded hover:bg-primary/20"
            aria-label={`Remove ${k}`}
          >
            <X size={10} />
          </button>
        </span>
      ))}
      <input
        type="text"
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(draft) }
          else if (e.key === 'Backspace' && !draft && keys.length) { remove(keys[keys.length - 1]) }
        }}
        onBlur={() => add(draft)}
        placeholder={keys.length ? 'Add another…' : 'tenant_id, org_id…'}
        className="flex-1 min-w-[8rem] h-6 px-1 bg-transparent text-xs text-fg placeholder:text-muted/50 focus:outline-none"
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// SourceRow
// ---------------------------------------------------------------------------

function SourceRow({
  source, index, canRemove, canWrite, registeredQueries, datastores,
  duplicateKey, onChange, onRemove,
}) {
  const set = (patch) => onChange({ ...source, ...patch })

  return (
    <div className="rounded-xl border border-border bg-surface p-3 flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <span className="flex items-center justify-center w-6 h-6 rounded-md bg-surface-2 text-[11px] font-semibold text-muted shrink-0">
          {index + 1}
        </span>

        {/* Key / alias */}
        <div className="flex flex-col gap-0.5 flex-1 min-w-0">
          <input
            type="text"
            value={source.key}
            onChange={e => set({ key: e.target.value.replace(/\s+/g, '_') })}
            placeholder="alias (used as table name)"
            className={[
              'h-8 px-2.5 text-xs font-mono bg-surface border rounded-lg text-fg placeholder:text-muted/40 focus:outline-none focus:ring-1 focus:ring-ring',
              duplicateKey ? 'border-rose-500/60' : 'border-border',
            ].join(' ')}
          />
        </div>

        {/* Source type toggle */}
        <div className="flex items-center rounded-lg border border-border overflow-hidden shrink-0">
          {[
            { v: 'query', l: 'Query', icon: FileCode2 },
            { v: 'sql', l: 'SQL', icon: Code2 },
          ].map(opt => {
            const Icon = opt.icon
            return (
              <button
                key={opt.v}
                type="button"
                onClick={() => set({ mode: opt.v })}
                className={[
                  'h-8 px-2.5 text-[11px] font-medium inline-flex items-center gap-1 transition-colors',
                  source.mode === opt.v
                    ? 'bg-primary/10 text-primary'
                    : 'bg-surface text-muted hover:text-fg hover:bg-surface-2',
                  opt.v === 'sql' ? 'border-l border-border' : '',
                ].join(' ')}
              >
                <Icon size={11} />
                {opt.l}
              </button>
            )
          })}
        </div>

        {canWrite && (
          <button
            type="button"
            onClick={onRemove}
            disabled={!canRemove}
            title={canRemove ? 'Remove source' : `At least ${MIN_SOURCES} sources required`}
            className="flex items-center justify-center w-8 h-8 rounded-lg border border-border text-muted hover:text-rose-500 hover:border-rose-500/40 disabled:opacity-30 disabled:hover:text-muted disabled:hover:border-border transition-colors shrink-0"
          >
            <Trash2 size={13} />
          </button>
        )}
      </div>

      {duplicateKey && (
        <p className="text-[10px] text-rose-500 flex items-center gap-1 -mt-1">
          <AlertCircle size={10} /> Aliases must be unique — referenced as table names in the combine SQL.
        </p>
      )}

      {/* Source picker */}
      {source.mode === 'query' ? (
        <select
          value={source.query_id}
          onChange={e => set({ query_id: e.target.value })}
          className="h-8 px-2 text-xs bg-surface border border-border rounded-lg text-fg focus:outline-none focus:ring-1 focus:ring-ring cursor-pointer"
        >
          <option value="">Select a registered query…</option>
          {registeredQueries.map(q => (
            <option key={q.id} value={q.id}>{q.name ?? q.id} ({q.id})</option>
          ))}
        </select>
      ) : (
        <SqlEditor
          value={source.sql}
          onChange={v => set({ sql: v })}
          height="120px"
          toolbar={false}
          dialect="duckdb"
        />
      )}

      {/* Connector */}
      <div className="flex items-center gap-2">
        <label className="inline-flex items-center gap-1 text-[11px] font-medium text-muted shrink-0">
          <Database size={11} className="text-primary/70" />
          Connector
        </label>
        <select
          value={source.datastore_id}
          onChange={e => set({ datastore_id: e.target.value })}
          className="h-8 px-2 text-xs bg-surface border border-border rounded-lg text-fg focus:outline-none focus:ring-1 focus:ring-ring cursor-pointer flex-1"
        >
          <option value="">Select a connector…</option>
          {datastores.map(d => (
            <option key={d.id} value={d.id}>{d.name ?? d.id}</option>
          ))}
        </select>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// BlendBuilder
// ---------------------------------------------------------------------------

export default function BlendBuilder() {
  const navigate = useNavigate()
  const canWrite = useCanWrite()

  // Reference data
  const [registeredQueries, setRegisteredQueries] = useState([])
  const [datastores, setDatastores] = useState([])

  // Form state
  const [name, setName] = useState('')
  const [sources, setSources] = useState(() => [newSource('orders'), newSource('analytics')])
  const [combineSql, setCombineSql] = useState(
    'SELECT *\nFROM orders o\nJOIN analytics a USING (id)'
  )

  // Schedule
  const [scheduleEnabled, setScheduleEnabled] = useState(true)
  const [scheduleMode, setScheduleMode] = useState('interval')
  const [intervalN, setIntervalN] = useState('1')
  const [intervalUnit, setIntervalUnit] = useState('h')
  const [cron, setCron] = useState('0 9 * * *')

  // RLS
  const [rlsKeys, setRlsKeys] = useState([])

  // Submission
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)   // { flow, materialized: { datastore_id, query_id } }
  const [copied, setCopied] = useState(false)

  // ── Load reference data (degrades gracefully) ──────────────────────────
  useEffect(() => {
    let alive = true
    Promise.allSettled([get('/query/registry'), get('/datastores')]).then(([rq, ds]) => {
      if (!alive) return
      if (rq.status === 'fulfilled') {
        const d = rq.value
        setRegisteredQueries(Array.isArray(d) ? d : (d?.queries ?? []))
      }
      if (ds.status === 'fulfilled') {
        const d = ds.value
        setDatastores(Array.isArray(d) ? d : (d?.datastores ?? d?.items ?? []))
      }
    })
    return () => { alive = false }
  }, [])

  // ── Derived ────────────────────────────────────────────────────────────
  const schedule = scheduleMode === 'interval'
    ? buildIntervalString(intervalN, intervalUnit)
    : cron.trim()
  const schedulePreview = describeSchedule(scheduleMode, schedule)
  const cronInvalid = scheduleEnabled && scheduleMode === 'cron'
    && cron.trim().split(/\s+/).length !== 5

  // Duplicate-key detection
  const dupKeys = useMemo(() => {
    const seen = new Map()
    const dups = new Set()
    for (const s of sources) {
      const k = s.key.trim()
      if (!k) continue
      if (seen.has(k)) dups.add(k)
      seen.set(k, true)
    }
    return dups
  }, [sources])

  const availableKeys = sources.map(s => s.key.trim()).filter(Boolean)

  // Validation
  const sourcesValid = sources.every(s => {
    if (!s.key.trim()) return false
    if (!s.datastore_id) return false
    return s.mode === 'query' ? Boolean(s.query_id) : Boolean(s.sql.trim())
  })
  const canSubmit =
    name.trim() &&
    sources.length >= MIN_SOURCES &&
    sourcesValid &&
    dupKeys.size === 0 &&
    combineSql.trim() &&
    !cronInvalid &&
    !submitting

  // ── Actions ────────────────────────────────────────────────────────────
  const updateSource = useCallback((id, next) => {
    setSources(prev => prev.map(s => (s._id === id ? next : s)))
  }, [])

  const addSource = useCallback(() => {
    setSources(prev => (prev.length >= MAX_SOURCES ? prev : [...prev, newSource('')]))
  }, [])

  const removeSource = useCallback((id) => {
    setSources(prev => (prev.length <= MIN_SOURCES ? prev : prev.filter(s => s._id !== id)))
  }, [])

  const buildBody = () => {
    const body = {
      name: name.trim(),
      sources: sources.map(s => {
        const src = { key: s.key.trim(), datastore_id: s.datastore_id }
        if (s.mode === 'query') src.query_id = s.query_id
        else src.sql = s.sql.trim()
        return src
      }),
      combine_sql: combineSql.trim(),
    }
    if (scheduleEnabled && schedule) body.schedule = schedule
    if (rlsKeys.length) body.rls_keys = rlsKeys
    return body
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      const res = await post('/flows/blend', buildBody())
      setResult(res)
    } catch (err) {
      setError(err?.message ?? 'Failed to create blend.')
    } finally {
      setSubmitting(false)
    }
  }

  const materializedQueryId =
    result?.materialized?.query_id ?? result?.materialized?.queryId ?? null

  const copyQueryId = useCallback(() => {
    if (!materializedQueryId) return
    navigator.clipboard?.writeText(materializedQueryId).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    })
  }, [materializedQueryId])

  // ── Success view ─────────────────────────────────────────────────────────
  if (result) {
    return (
      <div className="flex flex-col h-full bg-bg overflow-y-auto">
        <div className="px-6 py-6 max-w-2xl w-full mx-auto">
          <div className="rounded-2xl border border-border bg-surface p-6 flex flex-col gap-4">
            <div className="flex items-center gap-2 text-emerald-600 dark:text-emerald-400">
              <CheckCircle2 size={20} />
              <h1 className="font-display font-semibold text-lg text-fg">Blend created</h1>
            </div>
            <p className="text-sm text-muted">
              <span className="font-medium text-fg">{name.trim()}</span> will materialize{' '}
              {scheduleEnabled && schedulePreview
                ? schedulePreview.toLowerCase().replace('materializes ', 'on its schedule (')
                : 'on demand'}
              {scheduleEnabled && schedulePreview ? ')' : ''}. Reads in between hit the
              cached dataset — cheap and fast.
            </p>

            {materializedQueryId && (
              <div className="flex flex-col gap-1.5">
                <label className="text-[11px] font-semibold text-muted uppercase tracking-wide">
                  Materialized query id
                </label>
                <div className="flex items-center gap-2">
                  <code className="flex-1 h-9 px-3 flex items-center text-xs font-mono bg-surface-2 border border-border rounded-lg text-fg truncate">
                    {materializedQueryId}
                  </code>
                  <button
                    type="button"
                    onClick={copyQueryId}
                    className="flex items-center gap-1.5 h-9 px-3 text-xs font-medium border border-border rounded-lg bg-surface hover:bg-surface-2 text-fg transition-colors"
                  >
                    {copied ? <Check size={13} className="text-emerald-500" /> : <Copy size={13} />}
                    {copied ? 'Copied' : 'Copy'}
                  </button>
                </div>
              </div>
            )}

            <div className="rounded-lg bg-surface-2/60 border border-border p-3 text-[12px] text-muted leading-relaxed">
              <p className="font-medium text-fg mb-1">Next steps</p>
              Bind a dashboard widget to this query, or manage the refresh schedule in{' '}
              <Link to="/automations" className="text-primary hover:underline">Automations</Link>.
            </div>

            <div className="flex items-center gap-2 justify-end pt-1">
              <Link
                to="/automations"
                className="inline-flex items-center gap-1.5 h-9 px-3 text-xs font-medium border border-border rounded-lg bg-surface hover:bg-surface-2 text-fg transition-colors"
              >
                <ExternalLink size={13} /> Open Automations
              </Link>
              <button
                type="button"
                onClick={() => { setResult(null); setError(null) }}
                className="inline-flex items-center gap-1.5 h-9 px-4 text-xs font-semibold bg-primary text-primary-fg rounded-lg hover:opacity-90 transition-opacity"
              >
                <Plus size={13} /> New blend
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  // ── Builder form ─────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-full bg-bg overflow-y-auto">
      {/* Header */}
      <div className="flex items-center gap-3 px-6 pt-6 pb-4 border-b border-border bg-surface">
        <button
          type="button"
          onClick={() => navigate('/queries')}
          title="Back to queries"
          className="flex items-center justify-center w-9 h-9 rounded-xl border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors shrink-0"
        >
          <ArrowLeft size={15} />
        </button>
        <div>
          <h1 className="font-display font-semibold text-2xl text-fg flex items-center gap-2">
            <Combine size={22} className="text-primary" />
            Blend sources
          </h1>
          <p className="text-sm text-muted mt-0.5">
            Combine 2–3 sources into one materialized dataset your dashboards can query cheaply.
          </p>
        </div>
      </div>

      <form onSubmit={handleSubmit} className="flex-1 px-6 py-6 max-w-3xl w-full mx-auto flex flex-col gap-7">
        {/* Name */}
        <div className="flex flex-col gap-1.5">
          <label className="text-[11px] font-semibold text-muted uppercase tracking-wide">Name</label>
          <input
            type="text"
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="Orders + Analytics"
            className="h-9 px-3 text-sm bg-surface border border-border rounded-lg text-fg placeholder:text-muted/40 focus:outline-none focus:ring-1 focus:ring-ring"
          />
        </div>

        {/* Sources */}
        <section className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-fg flex items-center gap-1.5">
              <Layers size={15} className="text-primary" />
              Sources
              <span className="text-[11px] font-normal text-muted">({sources.length}/{MAX_SOURCES})</span>
            </h2>
            {canWrite && (
              <button
                type="button"
                onClick={addSource}
                disabled={sources.length >= MAX_SOURCES}
                className="inline-flex items-center gap-1.5 h-8 px-2.5 text-xs font-medium rounded-lg border border-dashed border-border text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40 transition-colors"
              >
                <Plus size={13} /> Add source
              </button>
            )}
          </div>
          <p className="text-[11px] text-muted -mt-1">
            Each source has a unique <span className="font-mono text-fg/80">alias</span> (its DuckDB
            table name in the combine SQL), a registered query or ad-hoc SQL, and a connector.
          </p>

          {sources.map((s, i) => (
            <SourceRow
              key={s._id}
              source={s}
              index={i}
              canRemove={sources.length > MIN_SOURCES}
              canWrite={canWrite}
              registeredQueries={registeredQueries}
              datastores={datastores}
              duplicateKey={dupKeys.has(s.key.trim()) && Boolean(s.key.trim())}
              onChange={next => updateSource(s._id, next)}
              onRemove={() => removeSource(s._id)}
            />
          ))}
        </section>

        {/* Combine SQL */}
        <section className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold text-fg flex items-center gap-1.5">
            <GitMerge size={15} className="text-primary" />
            Combine SQL
          </h2>
          <p className="text-[11px] text-muted">
            DuckDB SQL that joins/unions your sources. Reference each source by its alias.
          </p>
          {availableKeys.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[10px] text-muted">Available tables:</span>
              {availableKeys.map(k => (
                <code key={k} className="px-1.5 py-0.5 rounded bg-surface-2 border border-border/60 font-mono text-[10px] text-primary/90">
                  {k}
                </code>
              ))}
            </div>
          )}
          <SqlEditor
            value={combineSql}
            onChange={setCombineSql}
            height="160px"
            toolbar={false}
            dialect="duckdb"
          />
        </section>

        {/* Schedule */}
        <section className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-fg flex items-center gap-1.5">
              <CalendarClock size={15} className="text-primary" />
              Schedule
              <span className="text-[11px] font-normal text-muted">(optional)</span>
            </h2>
            <label className="inline-flex items-center gap-2 text-xs text-muted cursor-pointer select-none">
              <input
                type="checkbox"
                checked={scheduleEnabled}
                onChange={e => setScheduleEnabled(e.target.checked)}
                className="accent-[var(--color-primary,#2456a6)]"
              />
              Refresh on a schedule
            </label>
          </div>
          <p className="text-[11px] text-muted">
            The dataset materializes on this schedule; dashboard reads in between hit the cached
            result (cheap). Leave off to materialize once now.
          </p>

          {scheduleEnabled && (
            <div className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-3">
              <div className="flex items-center rounded-lg border border-border overflow-hidden w-fit">
                {[
                  { v: 'interval', l: 'Interval' },
                  { v: 'cron', l: 'Cron' },
                ].map(opt => (
                  <button
                    key={opt.v}
                    type="button"
                    onClick={() => setScheduleMode(opt.v)}
                    className={[
                      'h-7 px-3 text-[11px] font-medium transition-colors',
                      scheduleMode === opt.v
                        ? 'bg-primary/10 text-primary'
                        : 'bg-surface text-muted hover:text-fg hover:bg-surface-2',
                      opt.v === 'cron' ? 'border-l border-border' : '',
                    ].join(' ')}
                  >
                    {opt.l}
                  </button>
                ))}
              </div>

              {scheduleMode === 'interval' ? (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted">Every</span>
                  <input
                    type="number"
                    min="1"
                    value={intervalN}
                    onChange={e => setIntervalN(e.target.value)}
                    className="h-8 w-16 px-2 text-xs bg-surface border border-border rounded-lg text-fg focus:outline-none focus:ring-1 focus:ring-ring"
                  />
                  <select
                    value={intervalUnit}
                    onChange={e => setIntervalUnit(e.target.value)}
                    className="h-8 px-2 text-xs bg-surface border border-border rounded-lg text-fg focus:outline-none focus:ring-1 focus:ring-ring cursor-pointer"
                  >
                    {INTERVAL_UNITS.map(u => (
                      <option key={u.value} value={u.value}>{u.label}</option>
                    ))}
                  </select>
                  <span className="text-[10px] font-mono text-muted/70">→ {schedule}</span>
                </div>
              ) : (
                <div className="flex flex-col gap-1">
                  <input
                    type="text"
                    value={cron}
                    onChange={e => setCron(e.target.value)}
                    placeholder="0 9 * * *"
                    className={[
                      'h-8 px-2.5 text-xs font-mono bg-surface border rounded-lg text-fg placeholder:text-muted/40 focus:outline-none focus:ring-1 focus:ring-ring',
                      cronInvalid ? 'border-rose-500/50' : 'border-border',
                    ].join(' ')}
                  />
                  <p className="text-[10px] text-muted/70">
                    Standard 5-field cron: <span className="font-mono">min hour day month weekday</span>
                  </p>
                </div>
              )}

              {schedulePreview && !cronInvalid && (
                <p className="text-[11px] text-muted flex items-center gap-1.5">
                  <Clock size={11} className="text-primary/70" />
                  {schedulePreview}
                </p>
              )}
              {cronInvalid && (
                <p className="text-[11px] text-rose-500 flex items-center gap-1">
                  <AlertCircle size={10} /> Cron expression must have 5 fields.
                </p>
              )}
            </div>
          )}
        </section>

        {/* RLS keys */}
        <section className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold text-fg flex items-center gap-1.5">
            <Shield size={15} className="text-primary" />
            RLS keys
            <span className="text-[11px] font-normal text-muted">(optional)</span>
          </h2>
          <p className="text-[11px] text-muted">
            Columns to keep in the materialized dataset so per-tenant row-level security can filter
            reads (e.g. <span className="font-mono text-fg/80">tenant_id</span>). Press Enter or comma to add.
          </p>
          <RlsKeysInput keys={rlsKeys} onChange={setRlsKeys} />
        </section>

        {/* Error */}
        {error && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-rose-500/5 border border-rose-500/20 text-xs text-rose-600 dark:text-rose-400">
            <AlertCircle size={14} /> {error}
          </div>
        )}

        {/* Submit */}
        <div className="flex items-center justify-end gap-2 border-t border-border pt-4">
          <button
            type="button"
            onClick={() => navigate('/queries')}
            className="h-9 px-4 text-xs font-medium text-muted hover:text-fg border border-border rounded-lg bg-surface hover:bg-surface-2 transition-colors"
          >
            Cancel
          </button>
          {canWrite ? (
            <button
              type="submit"
              disabled={!canSubmit}
              className="inline-flex items-center gap-2 h-9 px-5 text-sm font-semibold bg-primary text-primary-fg rounded-lg hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity"
            >
              {submitting ? <Loader2 size={15} className="animate-spin" /> : <Combine size={15} />}
              {submitting ? 'Creating…' : 'Create blend'}
            </button>
          ) : (
            <span className="text-xs font-medium text-muted/70 select-none">
              Read-only — you don’t have permission to create blends.
            </span>
          )}
        </div>
      </form>
    </div>
  )
}
