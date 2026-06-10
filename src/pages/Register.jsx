/**
 * Register page — name + email + password form.
 *
 * On success: navigates to /dashboard.
 *
 * Provides a "Sign up with Google" button that redirects to the backend OAuth
 * start endpoint (PKCE flow handled entirely server-side).
 *
 * Renders inside <AuthLayout> — a standalone full-viewport split-screen
 * (no Navbar, no Footer).
 */

import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext.jsx'
import { googleStartUrl } from '../lib/api.js'
import AuthLayout from '../components/AuthLayout.jsx'

export default function Register() {
  const { register } = useAuth()
  const navigate = useNavigate()

  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [orgName, setOrgName] = useState('')
  // First project defaults to "Default" (Supabase-style) but stays editable.
  const [projectName, setProjectName] = useState('Default')
  const [demoProject, setDemoProject] = useState(true)
  const [error, setError] = useState(null)
  const [pending, setPending] = useState(false)

  // Suggested examples shown as placeholders (both fields are required).
  const firstName = name.trim().split(/\s+/)[0]
  const orgPlaceholder = firstName ? `${firstName}'s Org` : 'My Org'
  const projectPlaceholder = 'Default'

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    const org_name = orgName.trim()
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

  function handleGoogle() {
    window.location.href = googleStartUrl()
  }

  return (
    <AuthLayout
      title="Create your account"
      subtitle="Set up your workspace and start querying your data with Nubi — free forever"
      artTagline="Transform your data into insight"
      footer={
        <>
          Already have an account?{' '}
          <Link
            to="/login"
            className="font-semibold text-primary hover:opacity-80 transition-opacity"
          >
            Sign in
          </Link>
        </>
      }
    >
      {/* Google sign-up */}
      <button
        type="button"
        onClick={handleGoogle}
        className="w-full flex items-center justify-center gap-3 px-4 py-3 border border-border rounded-xl text-sm font-medium text-fg bg-surface hover:bg-surface-2 transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1 min-h-[48px]"
      >
        <GoogleIcon />
        Continue with Google
      </button>

      {/* Divider */}
      <div className="relative my-5">
        <div className="absolute inset-0 flex items-center">
          <div className="w-full border-t border-border" />
        </div>
        <div className="relative flex justify-center">
          <span className="bg-bg px-3 text-xs text-muted">or sign up with email</span>
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div
          role="alert"
          className="mb-5 rounded-xl border px-4 py-3 text-sm"
          style={{
            background: 'color-mix(in srgb, #ef4444 8%, transparent)',
            borderColor: 'color-mix(in srgb, #ef4444 28%, transparent)',
            color: '#ef4444',
          }}
        >
          {error}
        </div>
      )}

      {/* Form */}
      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <div>
          <label htmlFor="name" className="block text-sm font-medium text-fg mb-1.5">
            Full name
          </label>
          <input
            id="name"
            type="text"
            autoComplete="name"
            required
            value={name}
            onChange={e => setName(e.target.value)}
            className="w-full px-4 py-3 bg-surface border border-border rounded-xl text-sm text-fg placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            placeholder="Jane Smith"
            disabled={pending}
          />
        </div>

        <div>
          <label htmlFor="orgName" className="block text-sm font-medium text-fg mb-1.5">
            Organization name
          </label>
          <input
            id="orgName"
            type="text"
            autoComplete="organization"
            required
            value={orgName}
            onChange={e => setOrgName(e.target.value)}
            className="w-full px-4 py-3 bg-surface border border-border rounded-xl text-sm text-fg placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            placeholder={orgPlaceholder}
            disabled={pending}
          />
          <p className="mt-1.5 text-xs text-muted">
            Your workspace name. You can rename or add more later.
          </p>
        </div>

        <div>
          <label htmlFor="projectName" className="block text-sm font-medium text-fg mb-1.5">
            First project name
          </label>
          <input
            id="projectName"
            type="text"
            required
            value={projectName}
            onChange={e => setProjectName(e.target.value)}
            className="w-full px-4 py-3 bg-surface border border-border rounded-xl text-sm text-fg placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            placeholder={projectPlaceholder}
            disabled={pending}
          />
          <p className="mt-1.5 text-xs text-muted">
            We&apos;ll create this project to get you started. You can add more anytime.
          </p>
        </div>

        <label
          htmlFor="demoProject"
          className="flex items-start gap-3 rounded-xl border border-border bg-surface px-4 py-3 cursor-pointer hover:bg-surface-2 transition-colors"
        >
          <input
            id="demoProject"
            type="checkbox"
            checked={demoProject}
            onChange={e => setDemoProject(e.target.checked)}
            disabled={pending}
            className="mt-0.5 h-4 w-4 shrink-0 rounded border-border text-primary accent-[var(--color-primary,#2456a6)] focus:outline-none focus:ring-2 focus:ring-ring"
          />
          <span>
            <span className="block text-sm font-medium text-fg">Include demo project</span>
            <span className="block mt-0.5 text-xs text-muted">
              Add a Demo project with sample dashboards &amp; data — you can delete it anytime.
            </span>
          </span>
        </label>

        <div>
          <label htmlFor="email" className="block text-sm font-medium text-fg mb-1.5">
            Email address
          </label>
          <input
            id="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={e => setEmail(e.target.value)}
            className="w-full px-4 py-3 bg-surface border border-border rounded-xl text-sm text-fg placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            placeholder="you@example.com"
            disabled={pending}
          />
        </div>

        <div>
          <label htmlFor="password" className="block text-sm font-medium text-fg mb-1.5">
            Password
          </label>
          <input
            id="password"
            type="password"
            autoComplete="new-password"
            required
            value={password}
            onChange={e => setPassword(e.target.value)}
            className="w-full px-4 py-3 bg-surface border border-border rounded-xl text-sm text-fg placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            placeholder="••••••••"
            disabled={pending}
          />
        </div>

        <button
          type="submit"
          disabled={pending}
          className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-primary text-primary-fg text-sm font-semibold rounded-xl hover:opacity-90 disabled:opacity-60 disabled:cursor-not-allowed transition-opacity focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1 min-h-[48px]"
        >
          {pending ? (
            <>
              <span className="h-4 w-4 rounded-full border-2 border-current border-t-transparent animate-spin" />
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

// ---------------------------------------------------------------------------
// Inline Google SVG icon
// ---------------------------------------------------------------------------

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
      <path
        d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.716v2.259h2.908c1.702-1.567 2.684-3.875 2.684-6.615Z"
        fill="#4285F4"
      />
      <path
        d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18Z"
        fill="#34A853"
      />
      <path
        d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332Z"
        fill="#FBBC05"
      />
      <path
        d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58Z"
        fill="#EA4335"
      />
    </svg>
  )
}
