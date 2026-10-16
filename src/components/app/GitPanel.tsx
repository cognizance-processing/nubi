/**
 * GitPanel — bind the active project to a GitHub / GitLab remote and
 * push/pull its resources (dashboards, queries, flows, automations).
 *
 * The DB stays canonical; git is the mirror. Connectors are NEVER serialized.
 *
 * API (src/lib/api.js):
 *   GET  /git/status?project_id=...
 *   POST /git/connect { project_id, provider, repo_url, branch, base_path, token }
 *   POST /git/push    { project_id, message?, open_pr? }
 *   POST /git/pull    { project_id }
 *
 * The token is sent once on connect and stored server-side in the secret
 * store; it is never returned and never displayed after save.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  Github,
  GitBranch,
  Upload,
  Download,
  Link2,
  Loader2,
  CheckCircle,
  AlertTriangle,
  ShieldCheck,
  RefreshCw,
} from 'lucide-react'
import * as api from '../../lib/api.js'
import { useProject } from '../../contexts/ProjectContext.jsx'
import GitGraphDialog from './GitGraphDialog.jsx'

const PROVIDERS = [
  { id: 'github', label: 'GitHub' },
  { id: 'gitlab', label: 'GitLab' },
]

export default function GitPanel() {
  const { activeProject } = useProject()
  const projectId = activeProject?.id ?? null

  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState(null) // { connected, binding, last_sync }
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null) // 'connect' | 'push' | 'pull' | null
  const [result, setResult] = useState(null) // { kind, text }
  const [graphOpen, setGraphOpen] = useState(false) // env branch graph dialog

  // Connect form
  const [form, setForm] = useState({
    provider: 'github',
    repo_url: '',
    branch: 'main',
    base_path: '',
    token: '',
  })
  const [editing, setEditing] = useState(false)

  const loadStatus = useCallback(async () => {
    if (!projectId) return
    setLoading(true)
    setError(null)
    try {
      const res = await api.get(`/git/status?project_id=${encodeURIComponent(projectId)}`)
      setStatus(res)
      if (res?.binding) {
        setForm((f) => ({
          ...f,
          provider: res.binding.provider || 'github',
          repo_url: res.binding.repo_url || '',
          branch: res.binding.branch || 'main',
          base_path: res.binding.base_path || '',
          token: '',
        }))
      }
    } catch (err) {
      setError(err?.message || 'Failed to load git status.')
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    setStatus(null)
    setResult(null)
    setEditing(false)
    loadStatus()
  }, [loadStatus])

  const update = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }))

  async function handleConnect(e) {
    e?.preventDefault?.()
    if (!projectId) return
    setBusy('connect')
    setError(null)
    setResult(null)
    try {
      await api.post('/git/connect', {
        project_id: projectId,
        provider: form.provider,
        repo_url: form.repo_url.trim(),
        branch: form.branch.trim() || 'main',
        base_path: form.base_path.trim(),
        token: form.token,
      })
      setForm((f) => ({ ...f, token: '' }))
      setEditing(false)
      setResult({ kind: 'ok', text: 'Repository connected.' })
      await loadStatus()
    } catch (err) {
      setError(err?.message || 'Failed to connect repository.')
    } finally {
      setBusy(null)
    }
  }

  async function handlePush() {
    if (!projectId) return
    setBusy('push')
    setError(null)
    setResult(null)
    try {
      const res = await api.post('/git/push', {
        project_id: projectId,
        message: 'chore: sync nubi resources',
      })
      if (res?.committed) {
        setResult({
          kind: 'ok',
          text: `Pushed ${res.files} file(s) — ${(res.sha || '').slice(0, 7)}`,
        })
      } else {
        setResult({ kind: 'ok', text: 'Already up to date — nothing to push.' })
      }
      await loadStatus()
    } catch (err) {
      setError(err?.message || 'Push failed.')
    } finally {
      setBusy(null)
    }
  }

  async function handlePull() {
    if (!projectId) return
    setBusy('pull')
    setError(null)
    setResult(null)
    try {
      const res = await api.post('/git/pull', { project_id: projectId })
      setResult({ kind: 'ok', text: `Imported ${res?.imported ?? 0} resource(s) from remote.` })
      await loadStatus()
    } catch (err) {
      setError(err?.message || 'Pull failed.')
    } finally {
      setBusy(null)
    }
  }

  if (!projectId) {
    return (
      <div className="ap-panel mb-6 p-5">
        <p className="text-sm text-muted">
          Select a project to configure git sync.
        </p>
      </div>
    )
  }

  const connected = !!status?.connected
  const binding = status?.binding
  const showForm = !connected || editing

  return (
    <section className="ap-panel mb-6">
      {/* Header — same ruled card header as every other settings card */}
      <div className="ap-panel-head flex items-center gap-3" style={{ paddingInline: 20 }}>
        <div className="min-w-0 flex-1">
          <h2 className="ap-panel-title">Git sync</h2>
          <p className="ap-panel-sub truncate" style={{ lineHeight: 1.5 }}>
            Mirror dashboards, queries, flows &amp; automations to a repo. Connectors are never synced.
          </p>
        </div>
        <button
          type="button"
          onClick={loadStatus}
          disabled={loading}
          aria-label="Refresh git status"
          className="shrink-0 p-2 rounded-[var(--radius-control)] text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          title="Refresh status"
        >
          <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className="px-5 py-5 space-y-5">
        {/* Connected summary */}
        {connected && binding && (
          <div className="flex items-start gap-3 px-4 py-3 rounded-[var(--radius-control)] bg-bg border border-border">
            <CheckCircle size={18} className="text-success shrink-0 mt-0.5" />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 text-sm font-medium text-fg">
                <span className="capitalize">{binding.provider}</span>
                <span className="text-muted">·</span>
                <span className="inline-flex items-center gap-1 text-muted">
                  <GitBranch size={13} /> {binding.branch}
                </span>
              </div>
              <p className="text-xs text-muted truncate mt-0.5">{binding.repo_url}</p>
              {binding.base_path && (
                <p className="text-xs text-muted mt-0.5">path: {binding.base_path}</p>
              )}
              {status?.last_sync && (
                <p className="text-xs text-muted mt-1">
                  Last sync: {(status.last_sync.sha || '').slice(0, 7)} —{' '}
                  {status.last_sync.message}
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={() => setEditing((v) => !v)}
              className="text-xs text-primary hover:underline shrink-0"
            >
              {editing ? 'Cancel' : 'Edit'}
            </button>
          </div>
        )}

        {/* Push / Pull actions */}
        {connected && !editing && (
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={handlePush}
              disabled={busy !== null}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-[var(--radius-control)] text-sm font-medium bg-[var(--ink)] text-[var(--ink-fg)] hover:bg-[var(--ink-hover)] transition-colors disabled:opacity-50"
            >
              {busy === 'push' ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Upload size={16} />
              )}
              Push
            </button>
            <button
              type="button"
              onClick={handlePull}
              disabled={busy !== null}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-[var(--radius-control)] text-sm font-medium text-fg bg-bg border border-border hover:border-fg transition-colors disabled:opacity-50"
            >
              {busy === 'pull' ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Download size={16} />
              )}
              Pull
            </button>
            {/* Link-out: per-environment branch graph (push/pull per env lives there) */}
            <button
              type="button"
              onClick={() => setGraphOpen(true)}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-[var(--radius-control)] text-sm font-medium text-fg bg-bg border border-border hover:border-fg transition-colors"
            >
              <GitBranch size={16} />
              Branch graph
            </button>
          </div>
        )}

        {/* Connect / edit form */}
        {showForm && (
          <form onSubmit={handleConnect} className="space-y-4">
            <div className="nubi-field">
              <label className="nubi-label">Provider</label>
              <div className="flex gap-2">
                {PROVIDERS.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => setForm((f) => ({ ...f, provider: p.id }))}
                    className={`inline-flex items-center gap-2 px-3 py-2 rounded-[var(--radius-control)] text-sm border transition-colors ${
                      form.provider === p.id
                        ? 'border-[var(--ink)] text-fg bg-surface-2'
                        : 'border-border text-muted hover:text-fg'
                    }`}
                  >
                    {p.id === 'gitlab' ? <GitBranch size={15} /> : <Github size={15} />}
                    {p.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="nubi-field">
              <label className="nubi-label">Repository URL</label>
              <input
                type="url"
                required
                value={form.repo_url}
                onChange={update('repo_url')}
                placeholder="https://github.com/owner/repo.git"
                className="nubi-input nubi-input-md"
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="nubi-field">
                <label className="nubi-label">Branch</label>
                <input
                  type="text"
                  value={form.branch}
                  onChange={update('branch')}
                  placeholder="main"
                  className="nubi-input nubi-input-md"
                />
              </div>
              <div className="nubi-field">
                <label className="nubi-label">
                  Base path <span className="text-muted font-normal">(optional)</span>
                </label>
                <input
                  type="text"
                  value={form.base_path}
                  onChange={update('base_path')}
                  placeholder="nubi/"
                  className="nubi-input nubi-input-md"
                />
              </div>
            </div>

            <div className="nubi-field">
              <label className="nubi-label">
                Access token {connected && <span className="text-muted font-normal">(leave blank to keep current)</span>}
              </label>
              <input
                type="password"
                required={!connected}
                value={form.token}
                onChange={update('token')}
                placeholder={form.provider === 'gitlab' ? 'glpat-…' : 'ghp_… / github_pat_…'}
                autoComplete="off"
                className="nubi-input nubi-input-md font-mono"
              />
            </div>

            <div className="flex items-start gap-2 px-3 py-2.5 rounded-[var(--radius-control)] bg-bg border border-border text-xs text-muted">
              <ShieldCheck size={15} className="shrink-0 mt-0.5 text-success" />
              <span>
                The token is stored encrypted server-side and is never written to the
                repository or shown again. Connectors and their secrets are never synced.
              </span>
            </div>

            <button
              type="submit"
              disabled={busy !== null}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-[var(--radius-control)] text-sm font-medium bg-[var(--ink)] text-[var(--ink-fg)] hover:bg-[var(--ink-hover)] transition-colors disabled:opacity-50"
            >
              {busy === 'connect' ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Link2 size={16} />
              )}
              {connected ? 'Update connection' : 'Connect repository'}
            </button>
          </form>
        )}

        {/* Feedback */}
        {result && (
          <div className="flex items-center gap-2 px-3 py-2.5 rounded-[var(--radius-control)] nubi-notice nubi-notice-success text-xs">
            <CheckCircle size={15} className="shrink-0" />
            {result.text}
          </div>
        )}
        {error && (
          <div className="flex items-start gap-2 px-3 py-2.5 rounded-[var(--radius-control)] nubi-notice nubi-notice-error text-xs">
            <AlertTriangle size={15} className="shrink-0 mt-0.5" />
            {error}
          </div>
        )}
      </div>

      {/* Per-environment commit graph + branch sync actions */}
      <GitGraphDialog open={graphOpen} onClose={() => setGraphOpen(false)} />
    </section>
  )
}
