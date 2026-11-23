/**
 * ResetPassword — choose a new password from an emailed link.
 *
 * The token arrives in the URL fragment (`/reset-password#token=…`), which
 * browsers never send to a server. It is held in memory and the fragment is
 * dropped from the address bar and history as soon as it has been read.
 */

import { useState, useEffect, useRef } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext.jsx'
import { postResetPassword } from '../lib/api.js'
import AuthLayout from '../components/AuthLayout.jsx'
import MarketingStyles from '../components/marketing/MarketingStyles.jsx'
import {
  PasswordField, Banner, SubmitLabel, check, validators, focusFirst,
} from '../components/marketing/AuthFields.jsx'

const tokenFromHash = (hash: string) => new URLSearchParams(hash.replace(/^#/, '')).get('token') ?? ''

export default function ResetPassword() {
  const { logout } = useAuth()
  const navigate = useNavigate()
  const { hash } = useLocation()

  const [token, setToken] = useState(() => tokenFromHash(hash))
  const [password, setPassword] = useState('')
  const [touched, setTouched] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [done, setDone] = useState(false)
  const banner = useRef<HTMLDivElement>(null)

  // Also covers a second link pasted into this tab, which does not remount.
  useEffect(() => {
    const t = tokenFromHash(hash)
    if (!t) return
    setToken(t)
    setError(null)
    setDone(false)
    navigate('/reset-password', { replace: true })
  }, [hash, navigate])

  const validate = () => check(password, v => (v ? null : 'Choose a password.'), validators.minLength(8, 'Your password'))
  const pwErr = touched ? validate() : null

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setTouched(true)
    if (validate()) { focusFirst(['reset-password']); return }
    setPending(true)
    try {
      await postResetPassword({ token, password })
      // The server has revoked every session; drop this browser's too.
      await logout()
      setToken('')
      setPassword('')
      setTouched(false)
      setDone(true)
    } catch (err: any) {
      setError(err?.message ?? 'Could not reset your password. Try again.')
      requestAnimationFrame(() => banner.current?.scrollIntoView({ block: 'nearest' }))
    } finally {
      setPending(false)
    }
  }

  return (
    <AuthLayout
      title={done ? 'Password changed' : 'Choose a new password'}
      subtitle={done
        ? 'You were signed out everywhere, and your API keys and connected apps were revoked.'
        : "You'll be signed out everywhere, and your API keys and connected apps will be revoked."}
      eyebrow="RESET PASSWORD"
      artTagline="Your data, computed where it is read."
      footer={!done && <>Link expired?{' '}<Link to="/forgot-password" className="mk-link">Request a new one</Link></>}
    >
      <MarketingStyles />
      <div className="mk-form">
        {done ? (
          <>
            <Banner kind="ok">Sign in with your new password.</Banner>
            <Link to="/login" className="nubi-btn nubi-btn-primary nubi-btn-xl mk-submit">Sign in</Link>
          </>
        ) : !token ? (
          <Banner>This reset link is incomplete. Open the link from your email again, or request a new one.</Banner>
        ) : (
          <>
            {error && <div ref={banner}><Banner>{error}</Banner></div>}
            <form onSubmit={handleSubmit} className="mk-form" noValidate>
              <PasswordField
                id="reset-password" label="New password" value={password} onChange={setPassword} autoComplete="new-password"
                disabled={pending} placeholder="At least 8 characters" meter error={pwErr} onBlurValue={() => setTouched(true)}
              />
              <button type="submit" disabled={pending} className="nubi-btn nubi-btn-primary nubi-btn-xl mk-submit" aria-busy={pending}>
                <SubmitLabel pending={pending} idle="Change password" busy="Changing password…" />
              </button>
            </form>
          </>
        )}
      </div>
    </AuthLayout>
  )
}
