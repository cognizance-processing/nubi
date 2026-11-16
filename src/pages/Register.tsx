/**
 * Register — create an account and a first workspace.
 *
 * Account first (name, email, password with a strength meter), then the
 * workspace (organization, first project, demo data). Inline validation,
 * a banner for server errors, Google SSO when configured.
 */

import { useState, useEffect, useRef } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext.jsx'
import { googleStartUrl, fetchAuthConfig } from '../lib/api.js'
import AuthLayout from '../components/AuthLayout.jsx'
import MarketingStyles from '../components/marketing/MarketingStyles.jsx'
import {
  TextField, PasswordField, SsoButton, OrDivider, Banner, Checkbox, SubmitLabel, check, validators, focusFirst,
} from '../components/marketing/AuthFields.jsx'

type Touched = Partial<Record<'name' | 'email' | 'password' | 'org' | 'project', boolean>>

export default function Register() {
  const { register, user } = useAuth()
  const navigate = useNavigate()

  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [orgName, setOrgName] = useState('')
  const [projectName, setProjectName] = useState('Default')
  const [demoProject, setDemoProject] = useState(true)
  const [touched, setTouched] = useState<Touched>({})
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [googleEnabled, setGoogleEnabled] = useState(false)
  const banner = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let alive = true
    fetchAuthConfig().then((c: any) => { if (alive) setGoogleEnabled(!!c.google_oauth) })
    return () => { alive = false }
  }, [])

  const firstName = name.trim().split(/\s+/)[0]
  const orgPlaceholder = firstName ? `${firstName}'s team` : 'Acme Inc'

  const errs = {
    name: check(name, validators.required('your name')),
    email: check(email, validators.email),
    password: check(password, v => (v ? null : 'Choose a password.'), validators.minLength(8, 'Your password')),
    org: check(orgName, validators.required('an organization name')),
    project: check(projectName, validators.required('a project name')),
  }
  const show = (k: keyof Touched) => (touched[k] ? errs[k] : null)
  const blur = (k: keyof Touched) => () => setTouched(t => ({ ...t, [k]: true }))

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setTouched({ name: true, email: true, password: true, org: true, project: true })
    const order: [keyof typeof errs, string][] = [['name', 'reg-name'], ['email', 'reg-email'], ['password', 'reg-password'], ['org', 'reg-org'], ['project', 'reg-project']]
    const bad = order.filter(([k]) => errs[k]).map(([, id]) => id)
    if (bad.length) { focusFirst(bad); return }
    setPending(true)
    try {
      await register({ name: name.trim(), email: email.trim(), password, org_name: orgName.trim(), project_name: projectName.trim(), demo_project: demoProject })
      navigate('/dashboard', { replace: true })
    } catch (err: any) {
      setError(err?.message ?? 'Could not create your account. Try again.')
      setPending(false)
      requestAnimationFrame(() => banner.current?.scrollIntoView({ block: 'nearest' }))
    }
  }

  // Already signed in (the session was restored): skip the form.
  if (user && !pending) return <Navigate to="/dashboard" replace />

  return (
    <AuthLayout
      title="Create your account"
      subtitle="Set up a workspace and connect your first warehouse. Free, with unlimited viewers."
      eyebrow="CREATE ACCOUNT"
      artTagline="One query, any number of viewers, each seeing only their rows."
      footer={<>Already have an account?{' '}<Link to="/login" className="mk-link">Sign in</Link></>}
    >
      <MarketingStyles />
      <div className="mk-form">
        {googleEnabled && (
          <>
            <SsoButton disabled={pending} onClick={() => { window.location.href = googleStartUrl() }}>Continue with Google</SsoButton>
            <OrDivider>or use email</OrDivider>
          </>
        )}

        {error && <div ref={banner}><Banner>{error}</Banner></div>}

        <form onSubmit={handleSubmit} className="mk-form" noValidate>
          <TextField id="reg-name" label="Full name" autoComplete="name" placeholder="Jane Smith"
            value={name} onChange={setName} disabled={pending} error={show('name')} onBlurValue={blur('name')} />
          <TextField id="reg-email" label="Work email" type="email" autoComplete="email" inputMode="email" placeholder="name@company.com"
            value={email} onChange={setEmail} disabled={pending} error={show('email')} onBlurValue={blur('email')} />
          <PasswordField id="reg-password" value={password} onChange={setPassword} autoComplete="new-password" disabled={pending}
            placeholder="At least 8 characters" meter error={show('password')} onBlurValue={blur('password')} />

          <div className="mk-idx" style={{ marginTop: 6, fontSize: 11 }}><b>WORKSPACE</b><span>YOU CAN RENAME BOTH LATER</span></div>
          <div className="mk-pair">
            <TextField id="reg-org" label="Organization" autoComplete="organization" placeholder={orgPlaceholder}
              value={orgName} onChange={setOrgName} disabled={pending} error={show('org')} onBlurValue={blur('org')} />
            <TextField id="reg-project" label="First project" placeholder="Default"
              value={projectName} onChange={setProjectName} disabled={pending} error={show('project')} onBlurValue={blur('project')} />
          </div>
          <Checkbox id="reg-demo" checked={demoProject} onChange={setDemoProject} disabled={pending} title="Add demo data">
            Sample dashboards and queries to explore. Remove them any time.
          </Checkbox>

          <button type="submit" disabled={pending} className="nubi-btn nubi-btn-primary nubi-btn-xl mk-submit" aria-busy={pending}>
            <SubmitLabel pending={pending} idle="Create account" busy="Creating account…" />
          </button>
        </form>
      </div>
    </AuthLayout>
  )
}
