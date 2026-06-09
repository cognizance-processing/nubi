/**
 * OnboardingPage — Supabase-style forced onboarding at /onboarding.
 *
 * Shown to authenticated users with ZERO org memberships (e.g. a brand-new
 * Google OAuth user). Rendered as a full-screen authenticated route OUTSIDE
 * the app shell (no OrgProvider/ProjectProvider), so it fetches GET /orgs and
 * GET /auth/me/invites itself on mount.
 *
 * Two paths out:
 *   1. Accept a pending invite → acceptInvite(token), persist the org id and
 *      hard-redirect to /home so OrgContext/ProjectContext refetch.
 *   2. Create a workspace → POST /orgs → POST /projects {name} → optional
 *      POST /orgs/{id}/demo-project → hard-redirect to /home.
 *
 * Users who already belong to an org are bounced straight to /home.
 */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Loader2,
  Building2,
  CheckCircle,
  Mail,
  AlertTriangle,
  LogOut,
} from 'lucide-react'
import { useAuth } from '../contexts/AuthContext.jsx'
import * as api from '../lib/api.js'
import { acceptInvite } from '../lib/members.js'
import Logo from '../components/Logo.jsx'

const ACTIVE_ORG_KEY = 'nubi-active-org-id'

/** Persist the active project id, keyed per org (same scheme as ProjectContext). */
function saveActiveProjectId(orgId, projectId) {
  try {
    localStorage.setItem(`nubi-active-project-id:${orgId}`, projectId)
  } catch {
    // Ignore (private mode etc.)
  }
}

function saveActiveOrgId(orgId) {
  try {
    localStorage.setItem(ACTIVE_ORG_KEY, orgId)
  } catch {
    // Ignore
  }
}

export default function OnboardingPage() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  const [checking, setChecking] = useState(true)
  const [invites, setInvites] = useState([])

  // Invite-accept state
  const [acceptingId, setAcceptingId] = useState(null)
  const [inviteError, setInviteError] = useState(null)

  // Create-workspace form state
  const [orgName, setOrgName] = useState('')
  const [projectName, setProjectName] = useState('')
  const [demoProject, setDemoProject] = useState(true)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState(null)

  // On mount: bounce users who already have an org; load pending invites.
  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const data = await api.get('/orgs')
        const list = Array.isArray(data?.orgs) ? data.orgs : []
        if (cancelled) return
        if (list.length > 0) {
          navigate('/home', { replace: true })
          return
        }
      } catch {
        // Transport error — stay on the page; creating an org will surface
        // any real problem to the user.
      }
      const pending = await api.getMyInvites()
      if (!cancelled) {
        setInvites(pending)
        setChecking(false)
      }
    }

    load()
    return () => { cancelled = true }
  }, [navigate])

  async function handleAccept(invite) {
    setAcceptingId(invite.id)
    setInviteError(null)
    try {
      const org = await acceptInvite(invite.token)
      saveActiveOrgId(org?.id ?? invite.org_id)
      // Hard navigation so OrgProvider/ProjectProvider refetch and select the new org.
      window.location.assign('/home')
    } catch (e) {
      setInviteError(e?.message ?? 'Could not accept this invite.')
      setAcceptingId(null)
    }
  }

  async function handleCreate(e) {
    e.preventDefault()
    setCreateError(null)
    const org_name = orgName.trim()
    const project_name = projectName.trim()
    if (!org_name || !project_name) {
      setCreateError('Organization name and project name are required.')
      return
    }
    setCreating(true)
    try {
      const org = await api.createOrg(org_name)
      saveActiveOrgId(org.id)
      // Scope subsequent calls (projects, demo seed) to the new org.
      api.setActiveOrgId(org.id)

      const project = await api.createProject(project_name)
      if (project?.id) saveActiveProjectId(org.id, project.id)

      if (demoProject) {
        await api.createDemoProject(org.id)
      }

      // Hard navigation so OrgContext/ProjectContext refetch from scratch.
      window.location.assign('/home')
    } catch (err) {
      setCreateError(err?.message ?? 'Could not create your workspace.')
      setCreating(false)
    }
  }

  async function handleSignOut() {
    await logout()
    navigate('/login', { replace: true })
  }

  const inputClass =
    'w-full px-4 py-3 bg-surface border border-border rounded-xl text-sm text-fg placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent disabled:opacity-50 disabled:cursor-not-allowed transition-colors'

  return (
    <div className="min-h-screen bg-bg text-fg flex flex-col">
      {/* Top bar */}
      <header className="flex items-center justify-between px-6 py-4">
        <Logo size={32} showName />
        <button
          type="button"
          onClick={handleSignOut}
          className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg transition-colors"
        >
          <LogOut size={14} />
          Sign out
        </button>
      </header>

      {/* Centered content */}
      <main className="flex-1 flex items-start sm:items-center justify-center px-5 py-8">
        <div className="w-full max-w-lg">
          {checking ? (
            <div className="flex items-center justify-center gap-2 text-sm text-muted py-16">
              <Loader2 size={16} className="animate-spin" /> Setting things up…
            </div>
          ) : (
            <>
              {/* Heading */}
              <div className="text-center mb-8">
                <div
                  className="mx-auto flex items-center justify-center w-14 h-14 rounded-2xl mb-4"
                  style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
                >
                  <Building2 size={26} className="text-white" />
                </div>
                <h1 className="font-display font-bold text-2xl sm:text-3xl text-fg leading-tight">
                  Welcome to Nubi{user?.name ? `, ${user.name.split(/\s+/)[0]}` : ''}
                </h1>
                <p className="mt-2 text-sm text-muted">
                  You need an organization to get started — join one you&apos;ve been
                  invited to, or create your own.
                </p>
              </div>

              {/* ── Pending invites ─────────────────────────────────────── */}
              {invites.length > 0 && (
                <section className="mb-6 rounded-2xl border border-border bg-surface shadow-sm p-6">
                  <h2 className="font-display font-semibold text-base text-fg mb-1">
                    Join an existing organization
                  </h2>
                  <p className="text-xs text-muted mb-4">
                    You have {invites.length === 1 ? 'a pending invitation' : `${invites.length} pending invitations`}.
                  </p>

                  {inviteError && (
                    <p className="flex items-center gap-1.5 text-xs text-red-600 dark:text-red-400 mb-3">
                      <AlertTriangle size={13} /> {inviteError}
                    </p>
                  )}

                  <ul className="space-y-3">
                    {invites.map((invite) => (
                      <li
                        key={invite.id}
                        className="flex items-center gap-3 rounded-xl border border-border bg-bg px-4 py-3"
                      >
                        <span className="flex items-center justify-center w-9 h-9 rounded-lg bg-surface-2 text-muted shrink-0">
                          <Mail size={16} />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-sm font-medium text-fg truncate">
                            {invite.org_name}
                          </span>
                          <span className="block text-xs text-muted">as {invite.role}</span>
                        </span>
                        <button
                          type="button"
                          onClick={() => handleAccept(invite)}
                          disabled={acceptingId !== null}
                          className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-semibold text-white disabled:opacity-50 shrink-0"
                          style={{ background: 'linear-gradient(135deg, #2456a6, #17b3a3)' }}
                        >
                          {acceptingId === invite.id ? (
                            <Loader2 size={13} className="animate-spin" />
                          ) : (
                            <CheckCircle size={13} />
                          )}
                          Accept
                        </button>
                      </li>
                    ))}
                  </ul>

                  {/* Divider between the two paths */}
                  <div className="relative mt-6">
                    <div className="absolute inset-0 flex items-center">
                      <div className="w-full border-t border-border" />
                    </div>
                    <div className="relative flex justify-center">
                      <span className="bg-surface px-3 text-xs text-muted">or</span>
                    </div>
                  </div>
                </section>
              )}

              {/* ── Create a new organization ───────────────────────────── */}
              <section className="rounded-2xl border border-border bg-surface shadow-sm p-6">
                <h2 className="font-display font-semibold text-base text-fg mb-1">
                  Create a new organization
                </h2>
                <p className="text-xs text-muted mb-4">
                  Your workspace for projects, dashboards and teammates.
                </p>

                {createError && (
                  <div
                    role="alert"
                    className="mb-4 rounded-xl border px-4 py-3 text-sm"
                    style={{
                      background: 'color-mix(in srgb, #ef4444 8%, transparent)',
                      borderColor: 'color-mix(in srgb, #ef4444 28%, transparent)',
                      color: '#ef4444',
                    }}
                  >
                    {createError}
                  </div>
                )}

                <form onSubmit={handleCreate} className="space-y-4" noValidate>
                  <div>
                    <label htmlFor="ob-org-name" className="block text-sm font-medium text-fg mb-1.5">
                      Organization name
                    </label>
                    <input
                      id="ob-org-name"
                      type="text"
                      autoComplete="organization"
                      required
                      value={orgName}
                      onChange={(e) => setOrgName(e.target.value)}
                      className={inputClass}
                      placeholder="Acme Inc"
                      disabled={creating}
                    />
                  </div>

                  <div>
                    <label htmlFor="ob-project-name" className="block text-sm font-medium text-fg mb-1.5">
                      First project name
                    </label>
                    <input
                      id="ob-project-name"
                      type="text"
                      required
                      value={projectName}
                      onChange={(e) => setProjectName(e.target.value)}
                      className={inputClass}
                      placeholder="Default"
                      disabled={creating}
                    />
                  </div>

                  <label
                    htmlFor="ob-demo-project"
                    className="flex items-start gap-3 rounded-xl border border-border bg-bg px-4 py-3 cursor-pointer hover:bg-surface-2 transition-colors"
                  >
                    <input
                      id="ob-demo-project"
                      type="checkbox"
                      checked={demoProject}
                      onChange={(e) => setDemoProject(e.target.checked)}
                      disabled={creating}
                      className="mt-0.5 h-4 w-4 shrink-0 rounded border-border text-primary accent-[var(--color-primary,#2456a6)] focus:outline-none focus:ring-2 focus:ring-ring"
                    />
                    <span>
                      <span className="block text-sm font-medium text-fg">Include demo project</span>
                      <span className="block mt-0.5 text-xs text-muted">
                        Add a Demo project with sample dashboards &amp; data — you can delete it anytime.
                      </span>
                    </span>
                  </label>

                  <button
                    type="submit"
                    disabled={creating}
                    className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-primary text-primary-fg text-sm font-semibold rounded-xl hover:opacity-90 disabled:opacity-60 disabled:cursor-not-allowed transition-opacity focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1 min-h-[48px]"
                  >
                    {creating ? (
                      <>
                        <span className="h-4 w-4 rounded-full border-2 border-current border-t-transparent animate-spin" />
                        Creating workspace…
                      </>
                    ) : (
                      'Create organization'
                    )}
                  </button>
                </form>
              </section>
            </>
          )}
        </div>
      </main>
    </div>
  )
}
