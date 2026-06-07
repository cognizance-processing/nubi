/**
 * ProjectContext — manages the active org's projects (org → project → resources).
 *
 * Mirrors OrgContext, one level deeper:
 *   - Loads GET /api/v1/projects for the active org (scoped by X-Org-Id, which
 *     OrgContext has already configured on the api client).
 *   - Re-loads whenever the active ORG changes (reads useOrg()).
 *   - Persists the active project id in localStorage, keyed per org, so each
 *     org remembers its own last-selected project.
 *   - Defaults to the org's first/default project when nothing is saved.
 *   - Calls api.setActiveProjectId(id) whenever the active project changes so
 *     the api fetch wrapper attaches the X-Project-Id header.
 *
 * Project shape: { id, name, slug, org_id, ... }
 *
 * Exposes:
 *   projects        {Array<Project>}
 *   activeProject   {Project|null}
 *   setActiveProject(id) — switch active project, persists per-org
 *   refreshProjects()    — re-fetch the project list for the active org
 *   createProject(name)  — create a project, refresh, and switch to it
 *   loading         {boolean}
 */

import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
} from 'react'
import * as api from '../lib/api.js'
import { useOrg } from './OrgContext.jsx'

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const ProjectContext = createContext(null)

/** localStorage key for a given org's active project id. */
function activeProjectKey(orgId) {
  return `nubi-active-project-id:${orgId ?? 'default'}`
}

function getSavedProjectId(orgId) {
  try {
    return localStorage.getItem(activeProjectKey(orgId)) ?? null
  } catch {
    return null
  }
}

function saveProjectId(orgId, id) {
  try {
    localStorage.setItem(activeProjectKey(orgId), id)
  } catch {
    // Ignore (private mode etc.)
  }
}

/** Update the api client's active project id so fetches send X-Project-Id. */
function _applyActiveProject(project) {
  api.setActiveProjectId(project ? project.id : null)
}

export function ProjectProvider({ children }) {
  const { activeOrg } = useOrg()
  const orgId = activeOrg?.id ?? null

  const [projects, setProjects] = useState([])
  const [activeProject, setActiveProjectState] = useState(null)
  const [loading, setLoading] = useState(true)

  // Load (and reload) projects whenever the active org changes.
  useEffect(() => {
    let cancelled = false

    async function fetchProjects() {
      setLoading(true)
      // listProjects() degrades to [] on failure.
      const list = await api.listProjects()
      if (cancelled) return

      setProjects(list)

      // Restore the saved selection for this org, defaulting to the first
      // (the backend lists the org's default project first).
      const savedId = getSavedProjectId(orgId)
      const next = list.find(p => p.id === savedId) ?? list[0] ?? null
      setActiveProjectState(next)
      _applyActiveProject(next)
      setLoading(false)
    }

    fetchProjects()
    return () => { cancelled = true }
  }, [orgId])

  const setActiveProject = useCallback(
    (id) => {
      const project = projects.find(p => p.id === id)
      if (!project) return
      setActiveProjectState(project)
      _applyActiveProject(project)
      saveProjectId(orgId, id)
    },
    [projects, orgId],
  )

  const refreshProjects = useCallback(async () => {
    const list = await api.listProjects()
    setProjects(list)
    // Keep the current selection if it still exists, otherwise fall back.
    setActiveProjectState(prev => {
      const next = list.find(p => p.id === prev?.id) ?? list[0] ?? null
      _applyActiveProject(next)
      return next
    })
    return list
  }, [])

  const createProject = useCallback(
    async (name) => {
      const created = await api.createProject(name)
      // Refresh the list and switch to the new project.
      const list = await api.listProjects()
      setProjects(list)
      const next = list.find(p => p.id === created?.id) ?? created
      setActiveProjectState(next)
      _applyActiveProject(next)
      if (next?.id) saveProjectId(orgId, next.id)
      return created
    },
    [orgId],
  )

  return (
    <ProjectContext.Provider
      value={{ projects, activeProject, setActiveProject, refreshProjects, createProject, loading }}
    >
      {children}
    </ProjectContext.Provider>
  )
}

/**
 * @returns {{
 *   projects: Array<{id:string,name:string,slug?:string,org_id?:string}>,
 *   activeProject: Object|null,
 *   setActiveProject: (id: string) => void,
 *   refreshProjects: () => Promise<Array>,
 *   createProject: (name: string) => Promise<Object>,
 *   loading: boolean,
 * }}
 */
export function useProject() {
  const ctx = useContext(ProjectContext)
  if (!ctx) throw new Error('useProject must be used inside <ProjectProvider>')
  return ctx
}
