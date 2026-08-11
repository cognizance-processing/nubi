/**
 * Login — email + password sign-in form.
 *
 * Polished: uses nubi-input / nubi-btn / nubi-auth-* design system classes,
 * password visibility toggle, inline error state on fields, loading skeleton
 * on submit, dark mode parity, full a11y (focus rings, aria-invalid, aria-live).
 */

import { useState, useEffect } from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'
import { Eye, EyeOff, AlertCircle } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext.jsx'
import { googleStartUrl, fetchAuthConfig } from '../lib/api.js'
import AuthLayout from '../components/AuthLayout.jsx'

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const from = location.state?.from?.pathname ?? '/dashboard'

  const [email, setEmail]       = useState('')
  const [password, setPassword] = useState('')
  const [showPw, setShowPw]     = useState(false)
  const [error, setError]       = useState(null)
  const [pending, setPending]   = useState(false)
  const [googleEnabled, setGoogleEnabled] = useState(false)

  useEffect(() => {
    let alive = true
    fetchAuthConfig().then(c => { if (alive) setGoogleEnabled(!!c.google_oauth) })
    return () => { alive = false }
  }, [])

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    setPending(true)
    try {
      await login({ email, password })
      navigate(from, { replace: true })
    } catch (err) {
      setError(err.message)
    } finally {
      setPending(false)
    }
  }

  return (
    <AuthLayout
      title="Welcome back"
      subtitle="Sign in to your Nubi workspace"
      artTagline="Your data story, beautifully told"
      footer={
        <>
          Don&apos;t have an account?{' '}
          <Link to="/register" className="font-semibold text-primary hover:opacity-80 transition-opacity nubi-focus-ring rounded">
            Create one free
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
          <div className="nubi-auth-divider">or sign in with email</div>
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
        {/* Email */}
        <div className="nubi-field">
          <label htmlFor="login-email" className="nubi-label">Email address</label>
          <input
            id="login-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={e => setEmail(e.target.value)}
            className={`nubi-input nubi-input-lg${error ? ' nubi-input-error' : ''}`}
            placeholder="you@example.com"
            disabled={pending}
            aria-invalid={error ? 'true' : undefined}
            aria-describedby={error ? 'login-err' : undefined}
          />
        </div>

        {/* Password */}
        <div className="nubi-field">
          <div className="flex items-center justify-between mb-1.5">
            <label htmlFor="login-password" className="nubi-label" style={{ marginBottom: 0 }}>
              Password
            </label>
            <Link
              to="/forgot-password"
              className="text-xs text-muted hover:text-primary transition-colors nubi-focus-ring rounded"
              tabIndex={0}
            >
              Forgot password?
            </Link>
          </div>
          <div className="nubi-input-icon-wrap">
            <input
              id="login-password"
              type={showPw ? 'text' : 'password'}
              autoComplete="current-password"
              required
              value={password}
              onChange={e => setPassword(e.target.value)}
              className={`nubi-input nubi-input-lg nubi-input-has-right${error ? ' nubi-input-error' : ''}`}
              placeholder="••••••••"
              disabled={pending}
              aria-invalid={error ? 'true' : undefined}
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
              Signing in…
            </>
          ) : (
            'Sign in'
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
