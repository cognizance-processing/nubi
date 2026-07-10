/**
 * ProjectSettings — manage every project in the active organisation.
 *
 * One screen to:
 *   - See all projects in the org (with a "Current" badge on the active one).
 *   - Create a new project (inline form → ProjectContext.createProject).
 *   - Switch to / rename / delete ANY project — not just the active one.
 *   - Configure Git sync for the active project (embedded GitPanel).
 *
 * Dropdown sync
 * -------------
 * Every mutating action funnels through ProjectContext so the sidebar
 * WorkspaceSwitcher stays in lock-step:
 *   - Rename        → updateProjectSettings + refreshProjects() (re-derives the
 *                     active project from the fresh list, so renaming the
 *                     current project updates the dropdown label).
 *   - Create        → createProject() (refreshes the list and switches to it).
 *   - Delete active → re-select a remaining project + refreshProjects() so we
 *                     never leave zero active while others exist.
 *
 * Delete rules (unchanged)
 * ------------------------
 *   - Fetch GET /projects/{id}/deletion-impact first.
 *   - Show impact list (dashboards, queries, flows, connectors, secrets, …).
 *   - Require the user to type the exact project name to confirm.
 *   - An org must keep at least one project: the last project cannot be deleted
 *     (guarded in the UI and enforced by the backend, which returns a
 *     `last_project` blocker + 409).
 */

import { useEffect, useState, useCallback, useRef } from 'react'
import { Trash2, Folder, FolderGit2, Pencil, Plus, Check, X, Loader2 } from 'lucide-react'
import { useProject } from '../../../contexts/ProjectContext.jsx'
import { useCanWrite } from '../../../contexts/OrgContext.jsx'
import GitPanel from '../../../components/app/GitPanel.jsx'
import DangerDeleteDialog from '../../../components/app/DangerDeleteDialog.jsx'
import { updateProjectSettings, deleteProjectSettings, getProjectDeletionImpact } from '../../../lib/settings.js'
import { toast } from '../../../components/ui/Toast.jsx'
import {
  SettingsPageHeader,
  SettingsCard,
  PrimaryButton,
  ErrorText,
  inputCls,
} from './SettingsUI.jsx'

// ---------------------------------------------------------------------------
// Small ghost action button styling used in the project rows.
// ---------------------------------------------------------------------------

const ghostBtnCls =
  'inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium ' +
  'text-muted hover:text-fg border border-border hover:bg-surface-2 transition-colors ' +
  'disabled:opacity-40 disabled:cursor-not-allowed ' +
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'

// ---------------------------------------------------------------------------
// ProjectRow — one project in the "All projects" list.
//
// Handles its own inline rename state; switch/delete are lifted to the parent
// so the parent can drive the shared delete dialog and active-project logic.
// ---------------------------------------------------------------------------

function ProjectRow({
  project,
  isActive,
  canWrite,
  isOnlyProject,
  deleteBusy,
  onSwitch,
  onRename,
  onDelete,
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(project.name)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const inputRef = useRef(null)

  useEffect(() => {
    if (editing) {
      setDraft(project.name)
      setError(null)
      const t = setTimeout(() => {
        inputRef.current?.focus()
        inputRef.current?.select()
      }, 0)
      return () => clearTimeout(t)
    }
  }, [editing, project.name])

  function startEdit() {
    setDraft(project.name)
    setEditing(true)
  }

  function cancelEdit() {
    setEditing(false)
    setError(null)
  }

  async function save() {
    const name = draft.trim()
    if (!name || name === project.name) {
      cancelEdit()
      return
    }
    setSaving(true)
    setError(null)
    try {
      await onRename(project.id, name)
      setEditing(false)
    } catch (err) {
      setError(err?.message ?? 'Could not rename project.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <li className="py-3.5 first:pt-0 last:pb-0">
      <div className="flex flex-col sm:flex-row sm:items-center gap-3">
        {/* Name / identity */}
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <span
            className={[
              'flex items-center justify-center w-8 h-8 rounded-lg shrink-0',
              isActive ? 'bg-primary/10 text-primary' : 'bg-surface-2 text-muted',
            ].join(' ')}
            aria-hidden="true"
          >
            {isActive ? <FolderGit2 size={15} /> : <Folder size={15} />}
          </span>

          {editing ? (
            <input
              ref={inputRef}
              type="text"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); save() }
                if (e.key === 'Escape') { e.preventDefault(); cancelEdit() }
              }}
              disabled={saving}
              placeholder="Project name"
              aria-label={`Rename ${project.name}`}
              className={inputCls + ' max-w-xs py-1.5'}
            />
          ) : (
            <span className="truncate text-sm font-medium text-fg" title={project.name}>
              {project.name}
            </span>
          )}

          {isActive && !editing && (
            <span className="shrink-0 inline-flex items-center rounded-full bg-primary/10 text-primary px-2 py-0.5 text-[11px] font-semibold">
              Current
            </span>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1.5 shrink-0 sm:justify-end">
          {editing ? (
            <>
              <PrimaryButton
                type="button"
                onClick={save}
                busy={saving}
                disabled={saving || !draft.trim() || draft.trim() === project.name}
                className="!px-3 !py-1.5 text-xs"
              >
                {!saving && <Check size={14} />}
                Save
              </PrimaryButton>
              <button type="button" onClick={cancelEdit} disabled={saving} className={ghostBtnCls}>
                <X size={14} />
                Cancel
              </button>
            </>
          ) : (
            <>
              {!isActive && (
                <button
                  type="button"
                  onClick={() => onSwitch(project.id)}
                  className={ghostBtnCls}
                  title={`Switch to ${project.name}`}
                >
                  Switch to
                </button>
              )}
              {canWrite && (
                <button
                  type="button"
                  onClick={startEdit}
                  className={ghostBtnCls}
                  title="Rename project"
                  aria-label={`Rename ${project.name}`}
                >
                  <Pencil size={14} />
                  <span className="hidden sm:inline">Rename</span>
                </button>
              )}
              {canWrite && (
                <button
                  type="button"
                  onClick={() => onDelete(project)}
                  disabled={isOnlyProject || deleteBusy}
                  title={
                    isOnlyProject
                      ? 'An organisation must keep at least one project.'
                      : 'Delete project'
                  }
                  aria-label={`Delete ${project.name}`}
                  className={[
                    'inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium',
                    'text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800/70',
                    'hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors',
                    'disabled:opacity-40 disabled:cursor-not-allowed',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400/50',
                  ].join(' ')}
                >
                  {deleteBusy ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                  <span className="hidden sm:inline">Delete</span>
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {error && <div className="mt-1.5 pl-11"><ErrorText>{error}</ErrorText></div>}
    </li>
  )
}

// ---------------------------------------------------------------------------
// ProjectSettings
// ---------------------------------------------------------------------------

export default function ProjectSettings() {
  const { projects, activeProject, setActiveProject, refreshProjects, createProject } = useProject()
  const canWrite = useCanWrite() // viewers are read-only (backend gates via require_writer)
  const isOnlyProject = projects.length <= 1

  // Create-project inline form
  const [creating, setCreating] = useState(false)
  const [createName, setCreateName] = useState('')
  const [createBusy, setCreateBusy] = useState(false)
  const [createError, setCreateError] = useState(null)
  const createInputRef = useRef(null)

  // Delete flow — targets any project in the list.
  const [deleteTarget, setDeleteTarget] = useState(null) // project object
  const [impact, setImpact] = useState(null) // { forId, data }
  const [impactBusyId, setImpactBusyId] = useState(null)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    if (creating) {
      const t = setTimeout(() => createInputRef.current?.focus(), 0)
      return () => clearTimeout(t)
    }
  }, [creating])

  // ------------------------------------------------------------------ create
  function openCreate() {
    setCreateName('')
    setCreateError(null)
    setCreating(true)
  }

  function cancelCreate() {
    setCreating(false)
    setCreateName('')
    setCreateError(null)
  }

  async function handleCreate(e) {
    e.preventDefault()
    const name = createName.trim()
    if (!name) {
      setCreateError('Project name is required.')
      return
    }
    setCreateBusy(true)
    setCreateError(null)
    try {
      // createProject refreshes the list AND switches the active project (so the
      // sidebar dropdown updates to the new project).
      await createProject(name)
      setCreating(false)
      setCreateName('')
      toast.success('Project created.')
    } catch (err) {
      setCreateError(err?.message ?? 'Could not create project.')
    } finally {
      setCreateBusy(false)
    }
  }

  // ------------------------------------------------------------------ rename
  // Returns a promise so the row can surface errors inline. refreshProjects()
  // re-derives the active project from the fresh list, so renaming the current
  // project updates the sidebar dropdown label automatically.
  const handleRename = useCallback(
    async (id, name) => {
      await updateProjectSettings(id, { name })
      await refreshProjects()
      toast.success('Project renamed.')
    },
    [refreshProjects],
  )

  // ------------------------------------------------------------------ delete
  const requestDelete = useCallback(async (project) => {
    setImpactBusyId(project.id)
    try {
      const data = await getProjectDeletionImpact(project.id)
      setImpact({ forId: project.id, data })
      setDeleteTarget(project)
    } catch (err) {
      toast.error(err?.message ?? 'Could not load deletion impact.')
    } finally {
      setImpactBusyId(null)
    }
  }, [])

  function cancelDelete() {
    setDeleteTarget(null)
    setImpact(null)
  }

  async function handleDelete() {
    if (!deleteTarget) return
    // DangerDeleteDialog only calls onConfirm when the typed name matches
    // exactly, so passing the known name as confirm_name is safe.
    const confirmName = impact?.data?.name ?? deleteTarget.name
    const targetId = deleteTarget.id
    const wasActive = targetId === activeProject?.id
    setDeleting(true)
    try {
      await deleteProjectSettings(targetId, confirmName)
      // If we deleted the active project, move to another one BEFORE refreshing
      // so the app (and the sidebar dropdown) never sits on a deleted project.
      if (wasActive) {
        const remaining = projects.filter((p) => p.id !== targetId)
        if (remaining.length > 0) setActiveProject(remaining[0].id)
      }
      await refreshProjects()
      setDeleteTarget(null)
      setImpact(null)
      toast.success('Project deleted.')
    } catch (err) {
      toast.error(err?.message ?? 'Failed to delete project.')
    } finally {
      setDeleting(false)
    }
  }

  const dialogImpact = impact?.forId === deleteTarget?.id ? impact?.data : null

  return (
    <div className="space-y-6">
      <SettingsPageHeader
        title="Projects"
        description="Create, switch between, rename, or delete the projects in this organization. Renaming or deleting the current project updates the sidebar switcher too."
      >
        {canWrite && !creating && (
          <PrimaryButton type="button" onClick={openCreate}>
            <Plus size={15} />
            New project
          </PrimaryButton>
        )}
      </SettingsPageHeader>

      {!canWrite && (
        <SettingsCard>
          <p className="text-sm text-muted">
            You have read-only access — projects can only be created, renamed, or deleted by
            members with write access.
          </p>
        </SettingsCard>
      )}

      {/* All projects */}
      <SettingsCard
        title="All projects"
        description="Every project in this organization. The current project is highlighted."
      >
        {canWrite && creating && (
          <form
            onSubmit={handleCreate}
            className="mb-4 rounded-xl border border-border bg-surface-2/40 p-3"
          >
            <div className="flex flex-col sm:flex-row sm:items-center gap-2.5">
              <input
                ref={createInputRef}
                type="text"
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Escape') cancelCreate() }}
                disabled={createBusy}
                placeholder="New project name"
                aria-label="New project name"
                className={inputCls + ' flex-1'}
              />
              <div className="flex items-center gap-2 shrink-0">
                <PrimaryButton type="submit" busy={createBusy} disabled={createBusy || !createName.trim()}>
                  Create
                </PrimaryButton>
                <button type="button" onClick={cancelCreate} disabled={createBusy} className={ghostBtnCls}>
                  Cancel
                </button>
              </div>
            </div>
            {createError && <div className="mt-2"><ErrorText>{createError}</ErrorText></div>}
          </form>
        )}

        {projects.length === 0 ? (
          <p className="text-sm text-muted">No projects yet.</p>
        ) : (
          <ul className="divide-y divide-border">
            {projects.map((p) => (
              <ProjectRow
                key={p.id}
                project={p}
                isActive={p.id === activeProject?.id}
                canWrite={canWrite}
                isOnlyProject={isOnlyProject}
                deleteBusy={impactBusyId === p.id}
                onSwitch={setActiveProject}
                onRename={handleRename}
                onDelete={requestDelete}
              />
            ))}
          </ul>
        )}
      </SettingsCard>

      {/* Git sync — configures the CURRENT project (GitPanel reads the active
          project from context). Hidden for read-only viewers. */}
      {canWrite && activeProject && (
        <div className="space-y-2">
          <p className="text-xs text-muted px-1">
            Git sync for the current project — <span className="font-medium text-fg">{activeProject.name}</span>.
          </p>
          <GitPanel />
        </div>
      )}

      {/* Confirm delete dialog — driven by the row that requested deletion. */}
      {deleteTarget && dialogImpact && (
        <DangerDeleteDialog
          resourceType="project"
          name={dialogImpact.name ?? deleteTarget.name}
          impact={dialogImpact}
          loading={deleting}
          onConfirm={handleDelete}
          onCancel={cancelDelete}
        />
      )}
    </div>
  )
}
