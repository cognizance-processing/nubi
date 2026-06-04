/**
 * ProtectedRoute — guards routes that require authentication.
 *
 * - While auth is loading: show a centered spinner.
 * - If no user: redirect to /login, preserving the intended path as `from`
 *   in location state so Login can navigate back after success.
 * - If authenticated: render children or <Outlet /> (works both ways).
 */

import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext.jsx'

export default function ProtectedRoute({ children }) {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg">
        <div className="flex flex-col items-center gap-3">
          <div
            className="h-8 w-8 rounded-full border-4 border-primary border-t-transparent animate-spin"
            role="status"
            aria-label="Loading"
          />
          <p className="text-sm text-muted font-sans">Loading…</p>
        </div>
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  return children ?? <Outlet />
}
