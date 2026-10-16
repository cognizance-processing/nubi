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
import Button from '../../components/ui/Button.jsx'
import EmptyState from '../../components/ui/EmptyState.jsx'
import Spinner from '../../components/ui/Spinner.jsx'
import { NotFoundFigure } from '../../components/illustrations'

/** Generic not-found view rendered inside the app shell. */
export function AdminNotFound() {
  return (
    <div className="flex min-h-full flex-col items-center justify-center px-4 py-24">
      <EmptyState
        art={<NotFoundFigure />}
        label="404"
        title="Page not found"
        description="The page you're looking for doesn't exist or has been moved."
        action={<Button asChild><Link to="/home">Back to home</Link></Button>}
      />
    </div>
  )
}

export default function RequireSuperadmin({ children }) {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="flex min-h-full items-center justify-center py-24">
        <Spinner size="lg" label="Loading" />
      </div>
    )
  }

  if (!user?.is_superadmin) {
    return <AdminNotFound />
  }

  return children
}
