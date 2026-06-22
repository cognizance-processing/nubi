/**
 * OnboardingPage — Supabase-style forced onboarding at /onboarding.
 *
 * Shown to authenticated users with ZERO org memberships (e.g. brand-new
 * Google OAuth user). Two paths out:
 *   1. Accept a pending invite
 *   2. Create a workspace
 *
 * Polished: entrance animation, nubi design system inputs/buttons,
 * refined error states, dark mode parity, a11y.
 */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2, Building2, CheckCircle, Mail, AlertTriangle, LogOut, ArrowRight } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext.jsx'
import * as api from '../lib/api.js'
import { acceptInvite } from '../lib/members.js'
import Logo from '../components/Logo.jsx'

const ACTIVE_ORG_KEY = 'nubi-active-org-id'

function saveActiveProjectId(orgId, projectId) {
  try { localStorage.setItem(`nubi-active-project-id:${orgId}`, projectId) } catch { /* ignore */ }
}
function saveActiveOrgId(orgId) {
  try { localStorage.setItem(ACTIVE_ORG_KEY, orgId) } catch { /* ignore */ }
}

export default function OnboardingPage() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  const [checking, setChecking]       = useState(true)
  const [invites, setInvites]         = useState([])
  const [acceptingId, setAcceptingId] = useState(null)
  const [inviteError, setInviteError] = useState(null)
  const [orgName, setOrgName]         = useState('')
  const [projectName, setProjectName] = useState('Default')
  const [demoProject, setDemoProject] = useState(true)
  const [creating, setCreating]       = useState(false)
  const [createError, setCreateError] = useState(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const data = await api.get('/orgs')
        const list = Array.isArray(data?.orgs) ? data.orgs : []
        if (cancelled) return
        if (list.length > 0) { navigate('/home', { replace: true }); return }
      } catch { /* Transport error — stay on the page */ }
      const pending = await api.getMyInvites()
      if (!cancelled) { setInvites(pending); setChecking(false) }
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
      window.location.assign('/home')
    } catch (e) {
      setInviteError(e?.message ?? 'Could not accept this invite.')
      setAcceptingId(null)
    }
  }

  async function handleCreate(e) {
    e.preventDefault()
    setCreateError(null)
    const org_name     = orgName.trim()
    const project_name = projectName.trim()
    if (!org_name || !project_name) {
      setCreateError('Organization name and project name are required.')
      return
    }
    setCreating(true)
    try {
      const org = await api.createOrg(org_name)
      saveActiveOrgId(org.id)
      api.setActiveOrgId(org.id)
      const project = await api.createProject(project_name)
      if (project?.id) {
        saveActiveProjectId(org.id, project.id)
        api.setActiveProjectId(project.id)
      }
      if (demoProject) await api.restoreSample()
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

  return (
    <div className="min-h-screen bg-bg text-fg flex flex-col">
      {/* Subtle gradient background */}
      <div
        className="fixed inset-0 pointer-events-none"
        style={{
          background:
            'radial-gradient(ellipse 65% 45% at 20% 0%, rgba(36,86,166,0.07) 0%, transparent 65%), radial-gradient(ellipse 50% 40% at 85% 85%, rgba(23,179,163,0.07) 0%, transparent 65%)',
        }}
        aria-hidden="true"
      />

      {/* Top bar */}
      <header className="relative flex items-center justify-between px-6 py-4 border-b border-border bg-surface/80 backdrop-blur-sm">
        <Logo size={30} showName />
        <button
          type="button"
          onClick={handleSignOut}
          className="nubi-btn nubi-btn-ghost nubi-btn-sm text-muted"
          aria-label="Sign out"
        >
          <LogOut size={14} aria-hidden="true" />
          Sign out
        </button>
      </header>

      {/* Centered content */}
      <main className="relative flex-1 flex items-start sm:items-center justify-center px-5 py-10">
        <div className="w-full max-w-lg nubi-animate-slide-up">
          {checking ? (
            <div className="flex items-center justify-center gap-2 text-sm text-muted py-20">
              <Loader2 size={16} className="animate-spin" aria-hidden="true" />
              Setting things up…
            </div>
          ) : (
            <>
              {/* Hero heading */}
              <div className="text-center mb-8">
                <div
                  className="mx-auto flex items-center justify-center w-14 h-14 rounded-2xl mb-5 shadow-lg"
                  style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
                >
                  <Building2 size={24} className="text-white" aria-hidden="true" />
                </div>
                <h1 className="font-display font-bold text-2xl sm:text-3xl text-fg leading-tight tracking-tight">
                  Welcome to Nubi{user?.name ? `, ${user.name.split(/\s+/)[0]}` : ''}
                </h1>
                <p className="mt-2.5 text-sm text-muted leading-relaxed max-w-sm mx-auto">
                  Join an organization you&apos;ve been invited to, or create your own workspace to get started.
                </p>
              </div>

              {/* ── Pending invites ──────────────────────────────────────── */}
              {invites.length > 0 && (
                <section className="mb-5 nubi-card p-6">
                  <div className="flex items-start justify-between mb-1">
                    <h2 className="font-display font-semibold text-base text-fg">
                      Pending invitations
                    </h2>
                    <span className="nubi-badge-primary text-[11px]">
                      {invites.length}
                    </span>
                  </div>
                  <p className="nubi-hint mb-4">
                    You&apos;ve been invited to {invites.length === 1 ? 'an organization' : 'organizations'}.
                  </p>

                  {inviteError && (
                    <div className="nubi-auth-error mb-3">
                      <AlertTriangle size={13} aria-hidden="true" />
                      {inviteError}
                    </div>
                  )}

                  <ul className="flex flex-col gap-2.5">
                    {invites.map((invite) => (
                      <li
                        key={invite.id}
                        className="flex items-center gap-3 rounded-xl border border-border bg-bg px-4 py-3"
                      >
                        <span className="flex items-center justify-center w-9 h-9 rounded-lg bg-surface-2 text-muted shrink-0">
                          <Mail size={15} aria-hidden="true" />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-sm font-semibold text-fg truncate">{invite.org_name}</span>
                          <span className="block text-xs text-muted capitalize">{invite.role}</span>
                        </span>
                        <button
                          type="button"
                          onClick={() => handleAccept(invite)}
                          disabled={acceptingId !== null}
                          className="nubi-btn nubi-btn-sm text-white disabled:opacity-50 shrink-0"
                          style={{ background: 'linear-gradient(135deg, #2456a6, #17b3a3)' }}
                        >
                          {acceptingId === invite.id ? (
                            <Loader2 size={12} className="animate-spin" aria-hidden="true" />
                          ) : (
                            <CheckCircle size={12} aria-hidden="true" />
                          )}
                          Accept
                        </button>
                      </li>
                    ))}
                  </ul>

                  <div className="nubi-auth-divider mt-6">or create a new workspace</div>
                </section>
              )}

              {/* ── Create organization ──────────────────────────────────── */}
              <section className="nubi-card-raised p-6 sm:p-7">
                <h2 className="font-display font-semibold text-base text-fg mb-1">
                  {invites.length > 0 ? 'Create a new organization' : 'Create your workspace'}
                </h2>
                <p className="nubi-hint mb-5">
                  Your workspace for projects, dashboards, and teammates.
                </p>

                {createError && (
                  <div role="alert" aria-live="assertive" className="nubi-auth-error">
                    <AlertTriangle size={14} className="shrink-0 mt-0.5" aria-hidden="true" />
                    {createError}
                  </div>
                )}

                <form onSubmit={handleCreate} className="flex flex-col gap-4" noValidate>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="nubi-field">
                      <label htmlFor="ob-org-name" className="nubi-label">Organization</label>
                      <input
                        id="ob-org-name"
                        type="text"
                        autoComplete="organization"
                        required
                        value={orgName}
                        onChange={(e) => setOrgName(e.target.value)}
                        className="nubi-input"
                        placeholder="Acme Inc"
                        disabled={creating}
                      />
                    </div>
                    <div className="nubi-field">
                      <label htmlFor="ob-project-name" className="nubi-label">First project</label>
                      <input
                        id="ob-project-name"
                        type="text"
                        required
                        value={projectName}
                        onChange={(e) => setProjectName(e.target.value)}
                        className="nubi-input"
                        placeholder="Default"
                        disabled={creating}
                      />
                    </div>
                  </div>

                  <label htmlFor="ob-demo-data" className="nubi-auth-checkbox-card">
                    <input
                      id="ob-demo-data"
                      type="checkbox"
                      checked={demoProject}
                      onChange={(e) => setDemoProject(e.target.checked)}
                      disabled={creating}
                      className="mt-0.5 h-4 w-4 shrink-0 rounded border-border accent-[var(--primary)] focus:outline-none focus:ring-2 focus:ring-ring"
                    />
                    <span>
                      <span className="block text-sm font-medium text-fg">Seed with demo data</span>
                      <span className="block mt-0.5 text-xs text-muted">
                        Sample dashboards, queries &amp; a demo lakehouse — removable anytime.
                      </span>
                    </span>
                  </label>

                  <button
                    type="submit"
                    disabled={creating}
                    className="nubi-btn nubi-btn-primary w-full justify-center"
                    style={{
                      minHeight: '48px',
                      fontSize: '0.9375rem',
                      borderRadius: '0.875rem',
                      background: creating
                        ? undefined
                        : 'linear-gradient(135deg, #1b2363 0%, #2456a6 50%, #17b3a3 100%)',
                      boxShadow: creating ? undefined : '0 8px 28px -8px rgba(23,179,163,0.5), 0 4px 14px rgba(36,86,166,0.4)',
                    }}
                  >
                    {creating ? (
                      <>
                        <span className="nubi-spinner nubi-spinner-sm border-white/40 border-t-white" aria-hidden="true" />
                        Creating workspace…
                      </>
                    ) : (
                      <>
                        Create organization
                        <ArrowRight size={14} aria-hidden="true" />
                      </>
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
