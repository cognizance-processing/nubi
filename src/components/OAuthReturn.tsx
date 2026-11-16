/**
 * OAuthReturn — resumes an AI-app connection approval after a full-page sign-in.
 *
 * Google sign-in leaves the SPA and comes back to '/', losing the consent URL
 * the user was on. Login parks it in sessionStorage (lib/oauthConnect.ts);
 * once a user is signed in, this sends them back to it. Renders nothing.
 */

import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext.jsx'
import { takeOAuthReturn } from '../lib/oauthConnect.js'

export default function OAuthReturn() {
  const { user } = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    if (!user) return
    const path = takeOAuthReturn()
    if (path) navigate(path, { replace: true })
  }, [user, navigate])

  return null
}
