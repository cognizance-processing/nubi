/**
 * Register — create account form.
 *
 * Polished: nubi design system classes, password show/hide + strength meter,
 * inline field error states, loading states, dark mode, a11y.
 */

import { useState, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Eye, EyeOff, AlertCircle, Building2, FolderOpen } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext.jsx'
import { googleStartUrl, fetchAuthConfig } from '../lib/api.js'
import AuthLayout from '../components/AuthLayout.jsx'

// ── Password strength ─────────────────────────────────────────────────────────

function pwStrength(pw) {
  if (!pw) return 0
  let s = 0
  if (pw.length >= 8)  s++
  if (/[A-Z]/.test(pw)) s++
  if (/[0-9]/.test(pw)) s++
  if (/[^A-Za-z0-9]/.test(pw)) s++
  return s
}

const PW_LABELS = ['', 'Weak', 'Fair', 'Good', 'Strong']

function PasswordStrength({ password }) {
  const score = pwStrength(password)
  if (!password) return null
  return (
    <div>
      <div className="nubi-pw-strength" aria-hidden="true">
        {[1, 2, 3, 4].map(i => (
          <div
            key={i}
            className={`nubi-pw-strength-seg ${score >= i ? `filled-${score}` : ''}`}
          />
        ))}
      </div>
      <p className={`text-[11px] mt-1 font-medium ${
        score === 1 ? 'text-red-500' :
        score === 2 ? 'text-orange-500' :
        score === 3 ? 'text-yellow-500' :
        score === 4 ? 'text-green-500' : 'text-muted'
      }`}>
        {PW_LABELS[score]}
      </p>
    </div>
  )
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function Register() {
  const { register } = useAuth()
  const navigate = useNavigate()

  const [name, setName]               = useState('')
  const [email, setEmail]             = useState('')
  const [password, setPassword]       = useState('')
  const [orgName, setOrgName]         = useState('')
  const [projectName, setProjectName] = useState('Default')
  const [demoProject, setDemoProject] = useState(true)
  const [showPw, setShowPw]           = useState(false)
  const [error, setError]             = useState(null)
  const [pending, setPending]         = useState(false)
  const [googleEnabled, setGoogleEnabled] = useState(false)

  useEffect(() => {
    let alive = true
    fetchAuthConfig().then(c => { if (alive) setGoogleEnabled(!!c.google_oauth) })
    return () => { alive = false }
  }, [])

  const firstName      = name.trim().split(/\s+/)[0]
  const orgPlaceholder = firstName ? `${firstName}'s Org` : 'My Org'

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    const org_name     = orgName.trim()
    const project_name = projectName.trim()
    if (!org_name || !project_name) {
      setError('Organization name and project name are required.')
      return
    }
    setPending(true)
    try {
      await register({ name, email, password, org_name, project_name, demo_project: demoProject })
      navigate('/dashboard', { replace: true })
    } catch (err) {
      setError(err.message)
    } finally {
      setPending(false)
    }
  }

  return (
    <AuthLayout
      title="Create your account"
      subtitle="Set up your workspace and start querying data — free forever"
      artTagline="Transform your data into insight"
      footer={
        <>
          Already have an account?{' '}
          <Link to="/login" className="font-semibold text-primary hover:opacity-80 transition-opacity nubi-focus-ring rounded">
            Sign in
          </Link>
        </>
      }
    >
      {/* Google OAuth — only when the backend has it configured */}
      {googleEnabled && (
        <>
          <button
            type="button"
            onClick={() => { window.location.href = googleStartUrl() }}
            disabled={pending}
            className="nubi-auth-social-btn"
            aria-label="Continue with Google"
          >
            <GoogleIcon />
            Continue with Google
          </button>

          {/* Divider */}
          <div className="nubi-auth-divider">or sign up with email</div>
        </>
      )}

      {/* Error banner */}
      {error && (
        <div role="alert" aria-live="assertive" className="nubi-auth-error">
          <AlertCircle size={15} className="shrink-0 mt-0.5" aria-hidden="true" />
          {error}
        </div>
      )}

      {/* Form */}
      <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        {/* Full name */}
        <div className="nubi-field">
          <label htmlFor="reg-name" className="nubi-label">Full name</label>
          <input
            id="reg-name"
            type="text"
            autoComplete="name"
            required
            value={name}
            onChange={e => setName(e.target.value)}
            className="nubi-input nubi-input-lg"
            placeholder="Jane Smith"
            disabled={pending}
          />
        </div>

        {/* Org + Project row */}
        <div className="grid grid-cols-2 gap-3">
          <div className="nubi-field">
            <label htmlFor="reg-org" className="nubi-label">
              <span className="inline-flex items-center gap-1.5">
                <Building2 size={12} aria-hidden="true" />
                Organization
              </span>
            </label>
            <input
              id="reg-org"
              type="text"
              autoComplete="organization"
              required
              value={orgName}
              onChange={e => setOrgName(e.target.value)}
              className="nubi-input"
              placeholder={orgPlaceholder}
              disabled={pending}
            />
          </div>
          <div className="nubi-field">
            <label htmlFor="reg-project" className="nubi-label">
              <span className="inline-flex items-center gap-1.5">
                <FolderOpen size={12} aria-hidden="true" />
                First project
              </span>
            </label>
            <input
              id="reg-project"
              type="text"
              required
              value={projectName}
              onChange={e => setProjectName(e.target.value)}
              className="nubi-input"
              placeholder="Default"
              disabled={pending}
            />
          </div>
        </div>
        <p className="nubi-hint -mt-2.5">
          Your workspace and first project. You can rename or add more later.
        </p>

        {/* Demo data checkbox */}
        <label htmlFor="reg-demo" className="nubi-auth-checkbox-card">
          <input
            id="reg-demo"
            type="checkbox"
            checked={demoProject}
            onChange={e => setDemoProject(e.target.checked)}
            disabled={pending}
            className="mt-0.5 h-4 w-4 shrink-0 rounded border-border accent-[var(--primary)] focus:outline-none focus:ring-2 focus:ring-ring"
          />
          <span>
            <span className="block text-sm font-medium text-fg">Seed with demo data</span>
            <span className="block mt-0.5 text-xs text-muted">
              Sample dashboards &amp; queries to explore — removable anytime.
            </span>
          </span>
        </label>

        {/* Email */}
        <div className="nubi-field">
          <label htmlFor="reg-email" className="nubi-label">Email address</label>
          <input
            id="reg-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={e => setEmail(e.target.value)}
            className="nubi-input nubi-input-lg"
            placeholder="you@example.com"
            disabled={pending}
          />
        </div>

        {/* Password + strength */}
        <div className="nubi-field">
          <label htmlFor="reg-password" className="nubi-label">Password</label>
          <div className="nubi-input-icon-wrap">
            <input
              id="reg-password"
              type={showPw ? 'text' : 'password'}
              autoComplete="new-password"
              required
              value={password}
              onChange={e => setPassword(e.target.value)}
              className="nubi-input nubi-input-lg nubi-input-has-right"
              placeholder="min. 8 characters"
              disabled={pending}
            />
            <button
              type="button"
              className="nubi-input-icon-right"
              onClick={() => setShowPw(v => !v)}
              aria-label={showPw ? 'Hide password' : 'Show password'}
              tabIndex={-1}
            >
              {showPw
                ? <EyeOff size={15} aria-hidden="true" />
                : <Eye size={15} aria-hidden="true" />
              }
            </button>
          </div>
          <PasswordStrength password={password} />
        </div>

        {/* Submit */}
        <button
          type="submit"
          disabled={pending}
          className="nubi-btn nubi-btn-primary w-full justify-center"
          style={{
            minHeight: '48px',
            fontSize: '0.9375rem',
            borderRadius: '0.875rem',
            background: pending
              ? undefined
              : 'linear-gradient(135deg, #1b2363 0%, #2456a6 50%, #17b3a3 100%)',
            boxShadow: pending ? undefined : '0 8px 28px -8px rgba(23,179,163,0.5), 0 4px 14px rgba(36,86,166,0.4)',
          }}
        >
          {pending ? (
            <>
              <span className="nubi-spinner nubi-spinner-sm border-white/40 border-t-white" aria-hidden="true" />
              Creating account…
            </>
          ) : (
            'Create account'
          )}
        </button>
      </form>
    </AuthLayout>
  )
}

// ── Google icon ───────────────────────────────────────────────────────────────

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
      <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.716v2.259h2.908c1.702-1.567 2.684-3.875 2.684-6.615Z" fill="#4285F4" />
      <path d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18Z" fill="#34A853" />
      <path d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332Z" fill="#FBBC05" />
      <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58Z" fill="#EA4335" />
    </svg>
  )
}
