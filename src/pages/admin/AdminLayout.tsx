/**
 * AdminLayout — chrome for the /admin portal (inside the authenticated
 * AppShell, gated by RequireSuperadmin).
 *
 * Renders a PageShell header (title + section tabs) and an <Outlet /> for the
 * child pages. The shell's breadcrumb carries the section name on sub-pages.
 */

import { Outlet, useLocation } from 'react-router-dom'
import PageShell from '../../components/app/PageShell.jsx'
import RequireSuperadmin from './RequireSuperadmin.jsx'

const TABS = [
  { id: 'overview', href: '/admin', label: 'Overview' },
  { id: 'users', href: '/admin/users', label: 'Users' },
  { id: 'orgs', href: '/admin/orgs', label: 'Organizations' },
  { id: 'audit', href: '/admin/audit', label: 'Audit log' },
]

/** Which tab owns this path (org detail pages belong to Organizations). */
function activeTabFor(pathname: string) {
  if (pathname.startsWith('/admin/users')) return 'users'
  if (pathname.startsWith('/admin/orgs')) return 'orgs'
  if (pathname.startsWith('/admin/audit')) return 'audit'
  return 'overview'
}

/** Each route owns its h1 (and description) so the heading matches the breadcrumb. */
const HEAD: Record<string, { title: string; description: string }> = {
  overview: { title: 'Overview', description: 'Totals and recent activity across this instance.' },
  users: { title: 'Users', description: 'Everyone with an account on this instance.' },
  orgs: { title: 'Organizations', description: 'Every organization, its members and its plan.' },
  audit: { title: 'Audit log', description: 'Org-scoped audit events, newest first.' },
}

function AdminChrome() {
  const { pathname } = useLocation()
  const tab = activeTabFor(pathname)
  const isOrgDetail = /^\/admin\/orgs\/[^/]+/.test(pathname)
  const head = isOrgDetail
    ? { title: 'Organization', description: 'Plan, billing overrides and members for this organization.' }
    : HEAD[tab]
  return (
    <PageShell
      eyebrow="Instance"
      title={head.title}
      description={head.description}
      tabs={TABS}
      activeTab={tab}
    >
      <Outlet />
    </PageShell>
  )
}

export default function AdminLayout() {
  return (
    <RequireSuperadmin>
      <AdminChrome />
    </RequireSuperadmin>
  )
}
