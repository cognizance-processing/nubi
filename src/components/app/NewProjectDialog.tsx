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
import { Loader2, X } from 'lucide-react'
import Button from '../ui/Button.jsx'
import '../../shell/shell.css'

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
        // demo connector into the (now active) new project.
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
        className="fixed inset-0 z-[60] bg-[var(--sh-scrim)]"
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
          className="sh-pop sh-pop-in pointer-events-auto w-full max-w-md flex flex-col"
          onClick={e => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-start gap-3 px-6 pt-5 pb-4 border-b-2 border-[var(--ink)]">
            <div className="flex-1 min-w-0">
              <p className="t-label m-0 mb-1">Project</p>
              <h2 id="new-project-dialog-title" className="m-0 text-[17px] font-semibold tracking-[-0.01em] text-fg">
                New project
              </h2>
              <p className="text-[13px] text-muted mt-1 mb-0">
                Projects group your queries, dashboards and connectors.
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              disabled={busy}
              aria-label="Close"
              className="sh-icon-btn shrink-0 disabled:opacity-50"
            >
              <X size={16} strokeWidth={1.5} aria-hidden="true" />
            </button>
          </div>

          {/* Body */}
          <form onSubmit={handleSubmit} className="px-6 py-5 space-y-4" noValidate>
            {error && (
              <div
                role="alert"
                className="border-l-2 border-[var(--danger)] bg-danger-bg text-danger-fg px-3 py-2.5 text-[13px]"
              >
                {error}
              </div>
            )}

            <div>
              <label htmlFor="np-name" className="block text-[13px] font-medium text-fg mb-1.5">
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
                className="w-full h-9 px-3 bg-surface border border-[var(--frame-line)] rounded-sm text-sm text-fg placeholder:text-subtle hover:border-[var(--ink)] focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus:border-[var(--ink)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              />
            </div>

            <label
              htmlFor="np-demo-data"
              className="flex items-start gap-3 rounded-sm border border-[var(--frame-line)] px-3 py-3 cursor-pointer hover:border-[var(--ink)] transition-colors"
            >
              <input
                id="np-demo-data"
                type="checkbox"
                checked={seedDemo}
                onChange={(e) => setSeedDemo(e.target.checked)}
                disabled={busy}
                className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--ink)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              <span>
                <span className="block text-[13px] font-medium text-fg">Seed with demo data</span>
                <span className="block mt-0.5 text-xs text-muted">
                  Adds sample dashboards, queries and a demo connector — removable anytime.
                </span>
              </span>
            </label>

            {/* Footer */}
            <div className="flex items-center justify-end gap-2 pt-1">
              <Button type="button" variant="secondary" onClick={onClose} disabled={busy}>
                Cancel
              </Button>
              <Button type="submit" loading={busy}>
                {phase === 'seeding'
                  ? 'Adding demo data…'
                  : phase === 'creating'
                    ? 'Creating…'
                    : 'Create project'}
              </Button>
            </div>
          </form>
        </div>
      </div>
    </>
  )
}
