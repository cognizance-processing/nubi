/**
 * Login — email and password sign-in.
 *
 * Inline validation after a field is left or the form is submitted, a password
 * visibility toggle, a banner for server errors (including the auth rate
 * limit), and Google SSO when the backend has it configured.
 */

import { useState, useEffect, useRef } from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext.jsx'
import { googleStartUrl, fetchAuthConfig } from '../lib/api.js'
import AuthLayout from '../components/AuthLayout.jsx'
import { rememberOAuthReturn } from '../lib/oauthConnect.js'
import MarketingStyles from '../components/marketing/MarketingStyles.jsx'
import {
  TextField, PasswordField, SsoButton, OrDivider, Banner, SubmitLabel, check, validators, focusFirst,
} from '../components/marketing/AuthFields.jsx'

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  // Keep the query string: the AI-app consent page (/oauth/consent?request=…)
  // is useless without it.
  const fromLoc = location.state?.from
  const from = fromLoc?.pathname ? fromLoc.pathname + (fromLoc.search ?? '') : '/dashboard'

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [touched, setTouched] = useState<{ email?: boolean; password?: boolean }>({})
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [googleEnabled, setGoogleEnabled] = useState(false)
  const banner = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let alive = true
    fetchAuthConfig().then((c: any) => { if (alive) setGoogleEnabled(!!c.google_oauth) })
    return () => { alive = false }
  }, [])

  const emailErr = touched.email ? check(email, validators.email) : null
  const pwErr = touched.password ? check(password, validators.required('your password')) : null

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    const eErr = check(email, validators.email)
    const pErr = check(password, validators.required('your password'))
    setTouched({ email: true, password: true })
    if (eErr || pErr) { focusFirst([eErr ? 'login-email' : '', pErr ? 'login-password' : ''].filter(Boolean)); return }
    setPending(true)
    try {
      await login({ email: email.trim(), password })
      navigate(from, { replace: true })
    } catch (err: any) {
      setError(err?.message ?? 'Could not sign in. Try again.')
      setPending(false)
      requestAnimationFrame(() => banner.current?.scrollIntoView({ block: 'nearest' }))
    }
  }

  return (
    <AuthLayout
      title="Welcome back"
      subtitle="Sign in to your workspace."
      eyebrow="SIGN IN"
      artTagline="Your data, computed where it is read."
      footer={
        <>
          New to Nubi?{' '}
          <Link to="/register" className="mk-link">Start free</Link>
        </>
      }
    >
      <MarketingStyles />
      <div className="mk-form">
        {googleEnabled && (
          <>
            <SsoButton
              disabled={pending}
              onClick={() => {
                // Google sign-in leaves the SPA and lands back on '/', so park a
                // pending AI-app approval for <OAuthReturn> to resume.
                if (from.startsWith('/oauth/consent')) rememberOAuthReturn(from)
                window.location.href = googleStartUrl()
              }}
            >
              Continue with Google
            </SsoButton>
            <OrDivider>or use email</OrDivider>
          </>
        )}

        {error && <div ref={banner}><Banner>{error}</Banner></div>}

        <form onSubmit={handleSubmit} className="mk-form" noValidate>
          <TextField
            id="login-email" label="Email" type="email" autoComplete="email" inputMode="email"
            placeholder="name@company.com" value={email} onChange={setEmail} disabled={pending}
            error={emailErr} onBlurValue={() => setTouched(t => ({ ...t, email: true }))}
          />
          <PasswordField
            id="login-password" value={password} onChange={setPassword} autoComplete="current-password" disabled={pending}
            placeholder="Your password" error={pwErr} onBlurValue={() => setTouched(t => ({ ...t, password: true }))}
            aside={<Link to="/forgot-password" className="mk-link" style={{ fontSize: 13, fontWeight: 500 }}>Forgot password?</Link>}
          />
          <button type="submit" disabled={pending} className="nubi-btn nubi-btn-primary nubi-btn-xl mk-submit" aria-busy={pending}>
            <SubmitLabel pending={pending} idle="Sign in" busy="Signing in…" />
          </button>
        </form>

        <ul className="mk-meta" aria-label="About Nubi">
          <li><b>Open source</b><span>Apache-2.0, self-host or cloud</span></li>
          <li><b>Unlimited viewers</b><span>on every plan, free included</span></li>
        </ul>
      </div>
    </AuthLayout>
  )
}
