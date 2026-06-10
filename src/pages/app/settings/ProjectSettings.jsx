/**
 * ProjectSettings — project "General" section.
 *
 * Pick which project to configure, rename it, configure git sync, and
 * optionally delete it.  Git settings (GitPanel) are embedded here so all
 * project configuration lives in one place.
 *
 * Delete rules (unchanged):
 *   - Fetch GET /projects/{id}/deletion-impact first.
 *   - Show impact list (dashboards, queries, flows, connectors, secrets, …).
 *   - Require the user to type the exact project name to confirm.
 */

import { useEffect, useState, useCallback } from 'react'
import { Trash2, Folder } from 'lucide-react'
import { useProject } from '../../../contexts/ProjectContext.jsx'
import { useCanWrite } from '../../../contexts/OrgContext.jsx'
import GitPanel from '../../../components/app/GitPanel.jsx'
import DangerDeleteDialog from '../../../components/app/DangerDeleteDialog.jsx'
import { updateProjectSettings, deleteProjectSettings, getProjectDeletionImpact } from '../../../lib/settings.js'
import {
  SettingsPageHeader,
  SettingsCard,
  Field,
  PrimaryButton,
  SavedBadge,
  ErrorText,
  DangerZone,
  DangerRow,
  DangerButton,
  inputCls,
} from './SettingsUI.jsx'

export default function ProjectSettings() {
  const { activeProject, refreshProjects, setActiveProject, projects } = useProject()
  const canWrite = useCanWrite()  // viewers are read-only (backend gates via require_writer)
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
      <div>
        <SettingsPageHeader
          title="General"
          description="Rename the project, connect it to a Git repository, or delete it."
        />
        <SettingsCard>
          <p className="text-sm text-muted">Select a project to view its settings.</p>
        </SettingsCard>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <SettingsPageHeader
        title="General"
        description="Rename the project, connect it to a Git repository, or delete it."
      >
        {/* Project picker — switch which project you are configuring */}
        {projects.length > 1 && (
          <label className="flex items-center gap-2 text-xs text-muted">
            <Folder size={13} className="shrink-0" />
            <select
              value={projectId}
              onChange={(e) => setActiveProject(e.target.value)}
              className="px-2.5 py-1.5 rounded-xl bg-bg border border-border text-sm text-fg focus:outline-none focus:border-primary max-w-[220px]"
              aria-label="Switch project"
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </label>
        )}
      </SettingsPageHeader>

      {!canWrite && (
        <SettingsCard>
          <p className="text-sm text-muted">
            You have read-only access — project settings can only be changed by members with
            write access.
          </p>
        </SettingsCard>
      )}

      {/* Rename card */}
      <form onSubmit={handleSave}>
        <SettingsCard
          title="Project name"
          description="Shown in the sidebar project picker and across the app."
          footer={
            <>
              <PrimaryButton type="submit" busy={saving} disabled={saving || !canWrite}>
                Save changes
              </PrimaryButton>
              <SavedBadge show={saved} />
              <ErrorText>{saveError}</ErrorText>
            </>
          }
        >
          <div className="max-w-md">
            <Field htmlFor="project-name">
              <input
                id="project-name"
                type="text"
                value={projectName}
                onChange={(e) => setProjectName(e.target.value)}
                placeholder="My Project"
                className={inputCls}
                disabled={!canWrite}
              />
            </Field>
          </div>
        </SettingsCard>
      </form>

      {/* Git sync — embedded (write actions; hidden for read-only viewers).
          GitPanel is a self-contained card, so it is rendered bare. */}
      {canWrite && <GitPanel />}

      {/* Danger zone — hidden for read-only viewers */}
      {canWrite && (
        <DangerZone>
          <DangerRow
            title="Delete this project"
            description="Permanently deletes the project and all dashboards, queries, flows, and automations inside it. This cannot be undone."
            extra={
              <>
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
              </>
            }
          >
            <DangerButton
              onClick={() => setDialogOpen(true)}
              disabled={impactLoading}
              busy={impactLoading}
            >
              {!impactLoading && <Trash2 size={15} />}
              Delete project
            </DangerButton>
          </DangerRow>
        </DangerZone>
      )}

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
