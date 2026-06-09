/**
 * RequireSuperadmin — route guard for the /admin portal.
 *
 * Reads the authenticated user from AuthContext:
 *   - while the session restore is in flight → render a spinner (no flash);
 *   - user.is_superadmin === true          → render children;
 *   - otherwise                            → render a generic 404-style view.
 *
 * Non-admins deliberately get the SAME "page not found" experience as any
 * unknown route — the portal's existence is never revealed (no redirect to
 * /login, no "forbidden" message).
 */

import { Link } from 'react-router-dom'
import { useAuth } from '../../contexts/AuthContext.jsx'

/** Generic not-found view rendered inside the app shell. */
export function AdminNotFound() {
  return (
    <div className="min-h-full flex flex-col items-center justify-center text-center px-4 py-24">
      <p className="font-display font-extrabold text-6xl text-primary mb-4">404</p>
      <h1 className="font-display font-semibold text-2xl text-fg mb-2">Page not found</h1>
      <p className="text-sm text-muted mb-8">
        The page you&apos;re looking for doesn&apos;t exist or has been moved.
      </p>
      <Link
        to="/home"
        className="inline-flex items-center px-5 py-2.5 rounded-xl bg-primary text-primary-fg
          text-sm font-medium hover:opacity-90 transition-opacity"
      >
        Back to home
      </Link>
    </div>
  )
}

export default function RequireSuperadmin({ children }) {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="min-h-full flex items-center justify-center py-24">
        <div
          className="h-8 w-8 rounded-full border-4 border-primary border-t-transparent animate-spin"
          role="status"
          aria-label="Loading"
        />
      </div>
    )
  }

  if (!user?.is_superadmin) {
    return <AdminNotFound />
  }

  return children
}
