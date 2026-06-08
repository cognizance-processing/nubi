/**
 * ProjectSettings — rename the active project, configure git sync, and
 * optionally delete the project.
 *
 * Git settings (GitPanel) are embedded here so all project configuration
 * lives in one place.
 *
 * Delete rules:
 *   - Fetch GET /projects/{id}/deletion-impact first.
 *   - Show impact list (dashboards, queries, flows, connectors, secrets, …).
 *   - Require the user to type the exact project name to confirm.
 */

import { useEffect, useState, useCallback } from 'react'
import { FolderGit2, Loader2, CheckCircle, Trash2 } from 'lucide-react'
import { useProject } from '../../../contexts/ProjectContext.jsx'
import GitPanel from '../../../components/app/GitPanel.jsx'
import DangerDeleteDialog from '../../../components/app/DangerDeleteDialog.jsx'
import { updateProjectSettings, deleteProjectSettings, getProjectDeletionImpact } from '../../../lib/settings.js'

export default function ProjectSettings() {
  const { activeProject, refreshProjects, setActiveProject, projects } = useProject()
  const projectId = activeProject?.id ?? null

  const [projectName, setProjectName] = useState(activeProject?.name ?? '')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [saveError, setSaveError] = useState(null)

  // Impact state
  const [impact, setImpact] = useState(null)
  const [impactLoading, setImpactLoading] = useState(false)
  const [impactError, setImpactError] = useState(null)

  // Dialog
  const [dialogOpen, setDialogOpen] = useState(false)

  // Sync local state when active project changes
  useEffect(() => {
    setProjectName(activeProject?.name ?? '')
    setImpact(null)
    setImpactError(null)
  }, [projectId, activeProject?.name])

  const loadImpact = useCallback(async () => {
    if (!projectId) return
    setImpactLoading(true)
    setImpactError(null)
    try {
      const data = await getProjectDeletionImpact(projectId)
      setImpact(data)
    } catch (err) {
      setImpactError(err?.message ?? 'Could not load deletion impact.')
    } finally {
      setImpactLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    loadImpact()
  }, [loadImpact])

  async function handleSave(e) {
    e.preventDefault()
    if (!projectId) return
    setSaving(true)
    setSaved(false)
    setSaveError(null)
    try {
      await updateProjectSettings(projectId, {
        name: projectName.trim() || undefined,
      })
      setSaved(true)
      await refreshProjects()
      setTimeout(() => setSaved(false), 3000)
    } catch (err) {
      setSaveError(err?.message ?? 'Failed to save project settings.')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete() {
    // DangerDeleteDialog only calls onConfirm when the typed name matches exactly,
    // so we can safely pass the known name as confirm_name to the API.
    const confirmName = impact?.name ?? activeProject?.name ?? ''
    await deleteProjectSettings(projectId, confirmName)
    const remaining = projects.filter((p) => p.id !== projectId)
    setDialogOpen(false)
    if (remaining.length > 0) {
      setActiveProject(remaining[0].id)
    }
    await refreshProjects()
  }

  if (!projectId) {
    return (
      <div className="rounded-2xl border border-border bg-surface p-6">
        <p className="text-sm text-muted">Select a project to view its settings.</p>
      </div>
    )
  }

  return (
    <div className="space-y-8">
      {/* Section header */}
      <div className="flex items-start gap-4 pb-5 border-b border-border">
        <div
          className="flex items-center justify-center w-10 h-10 rounded-xl shrink-0"
          style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
        >
          <FolderGit2 size={18} className="text-white" />
        </div>
        <div>
          <h2 className="font-display font-semibold text-base text-fg">Project settings</h2>
          <p className="text-sm text-muted mt-0.5">
            Rename the project, connect it to a Git repository, or delete it.
          </p>
        </div>
      </div>

      {/* Rename form */}
      <form onSubmit={handleSave} className="space-y-5 max-w-md">
        <div className="space-y-1.5">
          <label className="block text-xs font-medium text-muted" htmlFor="project-name">
            Project name
          </label>
          <input
            id="project-name"
            type="text"
            value={projectName}
            onChange={(e) => setProjectName(e.target.value)}
            placeholder="My Project"
            className="w-full px-3 py-2 rounded-xl bg-bg border border-border text-sm text-fg placeholder:text-muted focus:outline-none focus:border-primary"
          />
        </div>

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={saving}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium text-white transition-opacity disabled:opacity-50"
            style={{ background: 'linear-gradient(135deg, #2456a6, #17b3a3)' }}
          >
            {saving ? <Loader2 size={15} className="animate-spin" /> : null}
            Save changes
          </button>
          {saved && (
            <span className="inline-flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400">
              <CheckCircle size={15} />
              Saved
            </span>
          )}
        </div>

        {saveError && (
          <p className="text-sm text-red-600 dark:text-red-400">{saveError}</p>
        )}
      </form>

      {/* Git sync — embedded */}
      <div className="space-y-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted/70">Git sync</h3>
        <GitPanel />
      </div>

      {/* Danger zone */}
      <div className="rounded-2xl border border-red-200 dark:border-red-900 overflow-hidden">
        <div className="px-5 py-4 bg-red-50 dark:bg-red-950/30 border-b border-red-200 dark:border-red-900">
          <h3 className="font-semibold text-sm text-red-700 dark:text-red-400">Danger zone</h3>
        </div>

        <div className="px-5 py-4">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="text-sm font-medium text-fg">Delete this project</p>
              <p className="text-xs text-muted mt-0.5">
                Permanently deletes the project and all dashboards, queries, flows, and
                automations inside it. This cannot be undone.
              </p>
              {impact && impact.deletes?.length > 0 && (
                <ul className="mt-2 space-y-0.5">
                  {impact.deletes.map((d) => (
                    <li key={d.type} className="text-xs text-muted">
                      — {d.count} {d.type}
                    </li>
                  ))}
                </ul>
              )}
              {impactError && (
                <p className="mt-2 text-xs text-red-600 dark:text-red-400">{impactError}</p>
              )}
            </div>

            <button
              type="button"
              onClick={() => setDialogOpen(true)}
              disabled={impactLoading}
              className="inline-flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
            >
              {impactLoading ? (
                <Loader2 size={15} className="animate-spin" />
              ) : (
                <Trash2 size={15} />
              )}
              Delete project
            </button>
          </div>
        </div>
      </div>

      {/* Confirm dialog */}
      {dialogOpen && impact && (
        <DangerDeleteDialog
          resourceType="project"
          name={impact.name ?? activeProject?.name ?? ''}
          impact={impact}
          onConfirm={handleDelete}
          onCancel={() => setDialogOpen(false)}
        />
      )}
    </div>
  )
}
