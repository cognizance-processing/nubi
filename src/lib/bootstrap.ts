/**
 * Shell bootstrap — orgs, projects, environments and feature flags in ONE
 * request (GET /bootstrap), instead of /orgs → /projects → /environments in
 * series plus /features.
 *
 * OrgProvider starts it; ProjectProvider and EnvProvider each take their slice
 * once, for the org / project it was computed for. Anything later (switching
 * org or project, refreshes) uses the regular endpoints. If the call fails,
 * every provider falls back to its regular endpoint, so an older backend
 * without /bootstrap keeps working.
 */
import { get } from './api.js'
import { awaitFeaturePrime, setEnabledFeatures } from './features.js'

export interface BootstrapData {
  orgs: any[]
  active_org_id: string | null
  projects: any[]
  active_project_id: string | null
  environments: any[] | null
  features: string[]
}

/** localStorage key for a given org's active project id (shared with ProjectContext). */
export function activeProjectKey(orgId: string | null): string {
  return `nubi-active-project-id:${orgId ?? 'default'}`
}

export function getSavedProjectId(orgId: string | null): string | null {
  try {
    return localStorage.getItem(activeProjectKey(orgId)) ?? null
  } catch {
    return null
  }
}

let _inflight: Promise<BootstrapData | null> | null = null
let _result: BootstrapData | null = null
let _projectsTaken = false
let _envsTaken = false

/**
 * Load the bootstrap for the given hints (the last-selected org and project).
 * Concurrent callers share one request; a call after it settled starts a new
 * one (a fresh provider mount, e.g. after signing in again).
 */
export function loadBootstrap(
  orgHint: string | null,
  projectHint: string | null,
): Promise<BootstrapData | null> {
  if (_inflight) return _inflight
  _result = null
  _projectsTaken = false
  _envsTaken = false
  const qs = new URLSearchParams()
  if (orgHint) qs.set('org_id', orgHint)
  if (projectHint) qs.set('project_id', projectHint)
  const query = qs.toString()
  const p: Promise<BootstrapData | null> = get(`/bootstrap${query ? `?${query}` : ''}`)
    .then((data: any) => {
      if (!data || !Array.isArray(data.orgs)) return null
      _result = data as BootstrapData
      if (Array.isArray(data.features)) setEnabledFeatures(data.features)
      return _result
    })
    .catch(() => null)
    .finally(() => {
      _inflight = null
    })
  _inflight = p
  // The feature store waits for this instead of racing it with GET /features.
  awaitFeaturePrime(p)
  return p
}

/** The bootstrap's project list, once, if it was computed for *orgId*. */
export function takeBootstrapProjects(orgId: string | null): any[] | null {
  if (!_result || _projectsTaken || !orgId || _result.active_org_id !== orgId) return null
  _projectsTaken = true
  return Array.isArray(_result.projects) ? _result.projects : null
}

/** The bootstrap's environment list, once, if it was computed for *projectId*. */
export function takeBootstrapEnvironments(projectId: string | null): any[] | null {
  if (!_result || _envsTaken || !projectId || _result.active_project_id !== projectId) return null
  _envsTaken = true
  return Array.isArray(_result.environments) ? _result.environments : null
}
