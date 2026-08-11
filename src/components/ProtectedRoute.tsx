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
import BrandLoader from './ui/BrandLoader.jsx'

export default function ProtectedRoute({ children }) {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg">
        <BrandLoader size="lg" label="Loading…" />
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  return children ?? <Outlet />
}
