/**
 * bootstrapPrime — the saved org/project hints and the bootstrap payload that
 * POST /auth/refresh?bootstrap=1 delivered, waiting for the shell's first
 * loadBootstrap (see bootstrap.ts). Kept free of the API client so it can be
 * unit-tested on its own.
 */

export interface BootstrapData {
  orgs: any[]
  active_org_id: string | null
  projects: any[]
  active_project_id: string | null
  environments: any[] | null
  features: string[]
}

/** localStorage key of the last-selected org (shared with OrgContext). */
export const ACTIVE_ORG_KEY = 'nubi-active-org-id'

/** localStorage key for a given org's active project id (shared with ProjectContext). */
export function activeProjectKey(orgId: string | null): string {
  return `nubi-active-project-id:${orgId ?? 'default'}`
}

export function getSavedOrgId(): string | null {
  try {
    return localStorage.getItem(ACTIVE_ORG_KEY) ?? null
  } catch {
    return null
  }
}

export function getSavedProjectId(orgId: string | null): string | null {
  try {
    return localStorage.getItem(activeProjectKey(orgId)) ?? null
  } catch {
    return null
  }
}

/** The hints OrgProvider will pass: the saved org and that org's saved project. */
function currentHints(): { org: string | null; project: string | null } {
  const org = getSavedOrgId()
  return { org, project: org ? getSavedProjectId(org) : null }
}

/** Query string for POST /auth/refresh that asks it to carry the bootstrap. */
export function bootstrapRefreshQuery(): string {
  const { org, project } = currentHints()
  const qs = new URLSearchParams({ bootstrap: '1' })
  if (org) qs.set('org_id', org)
  if (project) qs.set('project_id', project)
  return qs.toString()
}

let _primed: { org: string | null; project: string | null; data: BootstrapData } | null = null

/** Keep a refresh-delivered payload for the current hints; returns it, or null if malformed. */
export function stashPrimedBootstrap(data: any): BootstrapData | null {
  if (!data || !Array.isArray(data.orgs)) return null
  const { org, project } = currentHints()
  _primed = { org, project, data: data as BootstrapData }
  return _primed.data
}

/** The stashed payload, once, if it was computed for exactly these hints. Always clears it. */
export function takePrimedBootstrap(org: string | null, project: string | null): BootstrapData | null {
  const primed = _primed
  _primed = null
  return primed && primed.org === org && primed.project === project ? primed.data : null
}

/** Drop an unconsumed refresh-delivered bootstrap (sign-out, another sign-in). */
export function clearPrimedBootstrap(): void {
  _primed = null
}
