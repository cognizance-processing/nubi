/**
 * NewProjectDialog — modal for creating a project from the workspace switcher.
 *
 * Replaces the old window.prompt flow with a proper dialog:
 *   - Project name input (required).
 *   - "Seed with demo data" checkbox (default OFF) — when checked, the new
 *     project is seeded with the removable demo bundle via
 *     POST /projects/sample/restore, exactly like the onboarding checkbox.
 *     ProjectContext.createProject() switches the active project (and the
 *     api client's X-Project-Id header) BEFORE the restore call, so the seed
 *     is scoped to the project just created.
 *
 * On success the user lands on /home in the new project (mirrors onboarding).
 * Errors are surfaced inline; the dialog stays open so the user can retry.
 *
 * Props:
 *   open    {boolean}
 *   onClose {() => void}
 */

import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FolderPlus, Loader2, X } from 'lucide-react'

import { useProject } from '../../contexts/ProjectContext.jsx'
import * as api from '../../lib/api.js'

export default function NewProjectDialog({ open, onClose }) {
  const { createProject } = useProject()
  const navigate = useNavigate()

  const [name, setName] = useState('')
  const [seedDemo, setSeedDemo] = useState(false)
  // null | 'creating' | 'seeding'
  const [phase, setPhase] = useState(null)
  const [error, setError] = useState(null)
  const inputRef = useRef(null)

  const busy = phase !== null

  // Reset the form every time the dialog opens, then focus the name input.
  // rAF keeps the setState calls out of the synchronous effect body.
  useEffect(() => {
    if (!open) return
    const raf = requestAnimationFrame(() => {
      setName('')
      setSeedDemo(false)
      setPhase(null)
      setError(null)
      inputRef.current?.focus()
    })
    return () => cancelAnimationFrame(raf)
  }, [open])

  // ESC to close (unless mid-create).
  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape' && !busy) onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, busy, onClose])

  if (!open) return null

  async function handleSubmit(e) {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) {
      setError('Project name is required.')
      return
    }
    setError(null)
    setPhase('creating')
    try {
      // Creates the project AND switches the active project — api.js sends
      // X-Project-Id for the new project from here on.
      await createProject(trimmed)
      if (seedDemo) {
        setPhase('seeding')
        // Same endpoint onboarding uses — seeds dashboards, queries and the
        // demo lakehouse connector into the (now active) new project.
        await api.restoreSample()
      }
      onClose()
      navigate('/home')
    } catch (err) {
      setError(err?.message ?? 'Could not create the project.')
      setPhase(null)
    }
  }

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-[60] bg-black/50 backdrop-blur-sm"
        onClick={busy ? undefined : onClose}
        aria-hidden="true"
      />

      {/* Dialog */}
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-project-dialog-title"
        className="fixed inset-0 z-[60] flex items-center justify-center p-4 pointer-events-none"
      >
        <div
          className="pointer-events-auto w-full max-w-md bg-surface rounded-2xl border border-border shadow-2xl flex flex-col"
          onClick={e => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-start gap-3 px-6 pt-6 pb-4 border-b border-border">
            <div className="shrink-0 w-10 h-10 rounded-xl flex items-center justify-center bg-primary/10">
              <FolderPlus size={18} className="text-primary" />
            </div>
            <div className="flex-1 min-w-0">
              <h2 id="new-project-dialog-title" className="font-display font-semibold text-base text-fg">
                New project
              </h2>
              <p className="text-sm text-muted mt-0.5">
                Projects group your queries, dashboards and connectors.
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              disabled={busy}
              aria-label="Close"
              className="shrink-0 p-1.5 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-50"
            >
              <X size={16} />
            </button>
          </div>

          {/* Body */}
          <form onSubmit={handleSubmit} className="px-6 py-5 space-y-4" noValidate>
            {error && (
              <div
                role="alert"
                className="rounded-xl border px-4 py-3 text-sm"
                style={{
                  background: 'color-mix(in srgb, #ef4444 8%, transparent)',
                  borderColor: 'color-mix(in srgb, #ef4444 28%, transparent)',
                  color: '#ef4444',
                }}
              >
                {error}
              </div>
            )}

            <div>
              <label htmlFor="np-name" className="block text-sm font-medium text-fg mb-1.5">
                Project name
              </label>
              <input
                id="np-name"
                ref={inputRef}
                type="text"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={busy}
                placeholder="Analytics"
                className="w-full px-3.5 py-2.5 bg-surface border border-border rounded-xl text-sm text-fg placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              />
            </div>

            <label
              htmlFor="np-demo-data"
              className="flex items-start gap-3 rounded-xl border border-border bg-bg px-4 py-3 cursor-pointer hover:bg-surface-2 transition-colors"
            >
              <input
                id="np-demo-data"
                type="checkbox"
                checked={seedDemo}
                onChange={(e) => setSeedDemo(e.target.checked)}
                disabled={busy}
                className="mt-0.5 h-4 w-4 shrink-0 rounded border-border text-primary accent-[var(--color-primary,#2456a6)] focus:outline-none focus:ring-2 focus:ring-ring"
              />
              <span>
                <span className="block text-sm font-medium text-fg">Seed with demo data</span>
                <span className="block mt-0.5 text-xs text-muted">
                  Adds sample dashboards, queries and a demo lakehouse connector — removable anytime.
                </span>
              </span>
            </label>

            {/* Footer */}
            <div className="flex items-center justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={onClose}
                disabled={busy}
                className="px-3.5 py-2 rounded-xl text-sm font-medium text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={busy}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold bg-primary text-primary-fg hover:opacity-90 disabled:opacity-60 disabled:cursor-not-allowed transition-opacity focus:outline-none focus:ring-2 focus:ring-ring"
              >
                {busy && <Loader2 size={14} className="animate-spin" />}
                {phase === 'seeding'
                  ? 'Adding demo data…'
                  : phase === 'creating'
                    ? 'Creating…'
                    : 'Create project'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </>
  )
}
