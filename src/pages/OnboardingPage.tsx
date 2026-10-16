/**
 * OnboardingPage — /onboarding.
 *
 * Shown to authenticated users with zero org memberships (for example a brand
 * new Google sign-in). Two ways out:
 *   1. Accept a pending invite
 *   2. Create a workspace
 *
 * A stepper makes the way ahead visible: this page is step 01 of four
 * (workspace, connect, query, board). The rest happens from Home.
 */

import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { LogOut } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext.jsx'
import * as api from '../lib/api.js'
import { acceptInvite } from '../lib/members.js'
import Logo from '../components/Logo.jsx'
import MarketingStyles from '../components/marketing/MarketingStyles.jsx'
import Figure from '../components/marketing/Figure.jsx'
import { Tag } from '../components/marketing/kit.jsx'
import { TextField, Checkbox, Banner, SubmitLabel, check, validators, focusFirst } from '../components/marketing/AuthFields.jsx'

const ACTIVE_ORG_KEY = 'nubi-active-org-id'

function saveActiveProjectId(orgId: string, projectId: string) {
  try { localStorage.setItem(`nubi-active-project-id:${orgId}`, projectId) } catch { /* ignore */ }
}
function saveActiveOrgId(orgId: string) {
  try { localStorage.setItem(ACTIVE_ORG_KEY, orgId) } catch { /* ignore */ }
}

const STEPS = [
  { n: '01', t: 'Workspace', d: 'Name your organization' },
  { n: '02', t: 'Connect', d: 'Point at a warehouse' },
  { n: '03', t: 'Query', d: 'Write your first SQL' },
  { n: '04', t: 'Board', d: 'Publish a dashboard' },
]

const CSS = `
.ob { min-height: 100vh; display: flex; flex-direction: column; background: var(--surface); color: var(--text); }
.ob-top { height: 56px; border-bottom: 1px solid var(--border); }
.ob-top .mk-wrap { height: 100%; display: flex; align-items: center; justify-content: space-between; }
.ob-main { flex: 1; padding-block: 40px 72px; display: grid; gap: 40px; align-content: start; }
@media (min-width: 1000px) { .ob-main { grid-template-columns: minmax(0, 5fr) minmax(0, 6fr); gap: 80px; padding-block: 64px 96px; } }
.ob-steps { list-style: none; margin: 28px 0 0; padding: 0; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 4px; }
.ob-steps li { padding-top: 10px; border-top: 3px solid var(--border); }
.ob-steps li[aria-current="step"] { border-top-color: var(--ink); }
.ob-steps b { display: block; font: 500 11px var(--font-mono); letter-spacing: .08em; color: var(--text-muted); }
.ob-steps li[aria-current="step"] b { color: var(--text); }
.ob-steps span { display: block; margin-top: 2px; font-size: 14px; font-weight: 600; }
.ob-steps small { display: none; font-size: 12.5px; color: var(--text-muted); line-height: 1.4; margin-top: 2px; }
@media (min-width: 640px) { .ob-steps small { display: block; } }
.ob-card { border: 1px solid var(--border); border-top: 2px solid var(--ink); padding: 24px 20px 26px; }
@media (min-width: 640px) { .ob-card { padding: 28px 28px 32px; } }
.ob-card + .ob-card { margin-top: 20px; }
.ob-card h2 { margin: 0; font-size: 20px; font-weight: 600; letter-spacing: -.02em; }
.ob-inv { list-style: none; margin: 16px 0 0; padding: 0; border-top: 1px solid var(--border); }
.ob-inv li { display: flex; align-items: center; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--border); }
.ob-inv b { display: block; font-size: 15px; font-weight: 600; }
.ob-inv small { font: 12px var(--font-mono); color: var(--text-muted); text-transform: capitalize; }
`

export default function OnboardingPage() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  const [checking, setChecking] = useState(true)
  const [invites, setInvites] = useState<any[]>([])
  const [acceptingId, setAcceptingId] = useState<string | null>(null)
  const [inviteError, setInviteError] = useState<string | null>(null)
  const [orgName, setOrgName] = useState('')
  const [projectName, setProjectName] = useState('Default')
  const [demoProject, setDemoProject] = useState(true)
  const [touched, setTouched] = useState<{ org?: boolean; project?: boolean }>({})
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    async function load() {
      try {
        const data = await api.get('/orgs')
        const list = Array.isArray(data?.orgs) ? data.orgs : []
        if (!alive.current) return
        if (list.length > 0) { navigate('/home', { replace: true }); return }
      } catch { /* Transport error: stay on the page */ }
      const pending = await api.getMyInvites()
      if (alive.current) { setInvites(pending); setChecking(false) }
    }
    load()
    return () => { alive.current = false }
  }, [navigate])

  async function handleAccept(invite: any) {
    setAcceptingId(invite.id)
    setInviteError(null)
    try {
      const org = await acceptInvite(invite.token)
      saveActiveOrgId(org?.id ?? invite.org_id)
      window.location.assign('/home')
    } catch (e: any) {
      setInviteError(e?.message ?? 'Could not accept this invite.')
      setAcceptingId(null)
    }
  }

  const orgErr = check(orgName, validators.required('an organization name'))
  const projErr = check(projectName, validators.required('a project name'))

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    setCreateError(null)
    setTouched({ org: true, project: true })
    if (orgErr || projErr) { focusFirst([orgErr ? 'ob-org-name' : '', projErr ? 'ob-project-name' : ''].filter(Boolean)); return }
    setCreating(true)
    try {
      const org = await api.createOrg(orgName.trim())
      saveActiveOrgId(org.id)
      api.setActiveOrgId(org.id)
      const project = await api.createProject(projectName.trim())
      if (project?.id) {
        saveActiveProjectId(org.id, project.id)
        api.setActiveProjectId(project.id)
      }
      if (demoProject) await api.restoreSample()
      window.location.assign('/home')
    } catch (err: any) {
      setCreateError(err?.message ?? 'Could not create your workspace.')
      setCreating(false)
    }
  }

  async function handleSignOut() {
    await logout()
    navigate('/login', { replace: true })
  }

  const first = user?.name ? user.name.split(/\s+/)[0] : ''

  return (
    <div className="mk ob">
      <MarketingStyles />
      <style href="nubi-onboarding" precedence="default">{CSS}</style>

      <header className="ob-top">
        <div className="mk-wrap">
          <Logo size={26} showName />
          <button type="button" onClick={handleSignOut} className="nubi-btn nubi-btn-ghost nubi-btn-sm">
            <LogOut size={14} strokeWidth={1.75} aria-hidden="true" />Sign out
          </button>
        </div>
      </header>

      <main id="main" className="mk-wrap ob-main">
        <section aria-labelledby="ob-h" style={{ display: 'grid', gap: 20, alignContent: 'start' }}>
          <div className="mk-idx"><b>SETUP</b><span>STEP 01 OF 04</span></div>
          <h1 id="ob-h" className="mk-h1" style={{ fontSize: 'clamp(36px, 5vw, 64px)' }}>
            Welcome{first ? <>, {first}</> : ''}. <em>Set up your workspace.</em>
          </h1>
          <p className="mk-lead">Join an organization you were invited to, or create your own. Then connect a warehouse and build your first board from Home.</p>

          <ol className="ob-steps" aria-label="Setup steps">
            {STEPS.map((s, i) => (
              <li key={s.n} aria-current={i === 0 ? 'step' : undefined}>
                <b>{s.n}</b><span>{s.t}</span><small>{s.d}</small>
              </li>
            ))}
          </ol>

          <Figure name="OnboardingSteps" step={1} title="Three steps after setup: connect data, write a query, build a board" className="w-full h-auto" />
        </section>

        <div>
          {checking ? (
            <p className="mk-p" role="status" style={{ display: 'flex', alignItems: 'center', gap: 10 }}><span className="mk-spin" aria-hidden="true" />Setting things up…</p>
          ) : (
            <>
              {invites.length > 0 && (
                <section className="ob-card" aria-labelledby="ob-inv-h">
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                    <h2 id="ob-inv-h">Pending invitations</h2>
                    <Tag tone="accent" dot>{invites.length}</Tag>
                  </div>
                  <p className="mk-small" style={{ marginTop: 6 }}>You&rsquo;ve been invited to {invites.length === 1 ? 'an organization' : 'organizations'}.</p>
                  {inviteError && <div style={{ marginTop: 14 }}><Banner>{inviteError}</Banner></div>}
                  <ul className="ob-inv">
                    {invites.map(inv => (
                      <li key={inv.id}>
                        <span style={{ flex: 1, minWidth: 0 }}><b>{inv.org_name}</b><small>{inv.role}</small></span>
                        <button type="button" onClick={() => handleAccept(inv)} disabled={acceptingId !== null} className="nubi-btn nubi-btn-primary nubi-btn-md" aria-busy={acceptingId === inv.id}>
                          <SubmitLabel pending={acceptingId === inv.id} idle="Accept" busy="Joining…" />
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              <section className="ob-card" aria-labelledby="ob-new-h">
                <h2 id="ob-new-h">{invites.length > 0 ? 'Or create a new organization' : 'Create your workspace'}</h2>
                <p className="mk-small" style={{ marginTop: 6, marginBottom: 20 }}>Your home for projects, dashboards and teammates.</p>
                {createError && <div style={{ marginBottom: 16 }}><Banner>{createError}</Banner></div>}
                <form onSubmit={handleCreate} className="mk-form" noValidate>
                  <div className="mk-pair">
                    <TextField id="ob-org-name" label="Organization" autoComplete="organization" placeholder="Acme Inc"
                      value={orgName} onChange={setOrgName} disabled={creating}
                      error={touched.org ? orgErr : null} onBlurValue={() => setTouched(t => ({ ...t, org: true }))} />
                    <TextField id="ob-project-name" label="First project" placeholder="Default"
                      value={projectName} onChange={setProjectName} disabled={creating}
                      error={touched.project ? projErr : null} onBlurValue={() => setTouched(t => ({ ...t, project: true }))} />
                  </div>
                  <Checkbox id="ob-demo-data" checked={demoProject} onChange={setDemoProject} disabled={creating} title="Add demo data">
                    Sample dashboards, queries and a demo connector. Remove them any time.
                  </Checkbox>
                  <button type="submit" disabled={creating} className="nubi-btn nubi-btn-primary nubi-btn-xl mk-submit" aria-busy={creating}>
                    <SubmitLabel pending={creating} idle="Create workspace" busy="Creating workspace…" />
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
