/**
 * ResetPassword — choose a new password with the link from the reset email.
 *
 * The token arrives in the URL fragment (/reset-password#token=…), which the
 * browser never sends to a server. It is moved into state and stripped from
 * the address bar and history right away.
 *
 * A reset ends every session of the account, so success signs this browser out
 * too; otherwise /login would bounce a still-signed-in user back into the app
 * on a dead session.
 */

import { useState, useEffect, useRef } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext.jsx'
import { resetPassword } from '../lib/api.js'
import AuthLayout from '../components/AuthLayout.jsx'
import MarketingStyles from '../components/marketing/MarketingStyles.jsx'
import {
  PasswordField, Banner, SubmitLabel, check, validators, focusFirst,
} from '../components/marketing/AuthFields.jsx'

const tokenFromHash = (hash: string) => new URLSearchParams(hash.replace(/^#/, '')).get('token')

export default function ResetPassword() {
  const { logout } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()

  const [token, setToken] = useState<string | null>(() => tokenFromHash(location.hash))
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [touched, setTouched] = useState<{ password?: boolean; confirm?: boolean }>({})
  const [error, setError] = useState<string | null>(null)
  const [linkDead, setLinkDead] = useState(false)
  const [pending, setPending] = useState(false)
  const [done, setDone] = useState(false)
  const banner = useRef<HTMLDivElement>(null)

  // Also runs when another reset link is opened in a tab already on this page.
  useEffect(() => {
    const fromUrl = tokenFromHash(location.hash)
    if (!fromUrl) return
    setToken(fromUrl)
    setLinkDead(false)
    setError(null)
    setDone(false)
    navigate(location.pathname, { replace: true })
  }, [location.hash, location.pathname, navigate])

  const errs = {
    password: check(password, v => (v ? null : 'Choose a password.'), validators.minLength(8, 'Your password')),
    confirm: confirm === password ? null : 'The passwords do not match.',
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setTouched({ password: true, confirm: true })
    if (errs.password || errs.confirm) {
      focusFirst([errs.password ? 'reset-password' : '', errs.confirm ? 'reset-confirm' : ''].filter(Boolean))
      return
    }
    if (!token) return
    setPending(true)
    try {
      await resetPassword({ token, password })
      setToken(null)
      setPassword('')
      setConfirm('')
      await logout()
      setDone(true)
    } catch (err: any) {
      if (err?.payload?.error?.code === 'invalid_reset_token') setLinkDead(true)
      setError(err?.message ?? 'Could not reset your password. Try again.')
    } finally {
      setPending(false)
      requestAnimationFrame(() => banner.current?.scrollIntoView({ block: 'nearest' }))
    }
  }

  const unusable = linkDead || (!token && !done && !pending)

  return (
    <AuthLayout
      title={done ? 'Password changed' : 'Choose a new password'}
      subtitle={done ? undefined : 'You will be signed out everywhere, and your API keys and connected apps will stop working.'}
      eyebrow="RESET PASSWORD"
      artTagline="Your data, computed where it is read."
      footer={<>Back to{' '}<Link to="/login" className="mk-link">sign in</Link></>}
    >
      <MarketingStyles />
      <div className="mk-form">
        {done ? (
          <>
            <div ref={banner}><Banner kind="ok">Your password has been changed. Sign in with the new one.</Banner></div>
            <Link to="/login" className="nubi-btn nubi-btn-primary nubi-btn-xl mk-submit">Sign in</Link>
          </>
        ) : unusable ? (
          <>
            <div ref={banner}><Banner>{error ?? 'This page needs the link from your reset email. Request a new one.'}</Banner></div>
            <Link to="/forgot-password" className="nubi-btn nubi-btn-primary nubi-btn-xl mk-submit">Request a new link</Link>
          </>
        ) : (
          <>
            {error && <div ref={banner}><Banner>{error}</Banner></div>}
            <form onSubmit={handleSubmit} className="mk-form" noValidate>
              <PasswordField
                id="reset-password" label="New password" value={password} onChange={setPassword} autoComplete="new-password"
                disabled={pending} placeholder="At least 8 characters" meter error={touched.password ? errs.password : null}
                onBlurValue={() => setTouched(t => ({ ...t, password: true }))}
              />
              <PasswordField
                id="reset-confirm" label="Confirm new password" value={confirm} onChange={setConfirm} autoComplete="new-password"
                disabled={pending} placeholder="Type it again" error={touched.confirm ? errs.confirm : null}
                onBlurValue={() => setTouched(t => ({ ...t, confirm: true }))}
              />
              <button type="submit" disabled={pending} className="nubi-btn nubi-btn-primary nubi-btn-xl mk-submit" aria-busy={pending}>
                <SubmitLabel pending={pending} idle="Change password" busy="Changing…" />
              </button>
            </form>
          </>
        )}
      </div>
    </AuthLayout>
  )
}
