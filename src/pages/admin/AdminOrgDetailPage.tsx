/**
 * AdminOrgDetailPage — /admin/orgs/:id
 *
 * Org info + members table + projects table (read-only).
 */

import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { Link } from 'react-router-dom'
import { ArrowLeft, Building2, Calendar, Users, FolderKanban, LogIn } from 'lucide-react'
import { getAdminOrg, enterAdminOrg } from '../../lib/admin.js'
import {
  AdminCard,
  AdminTable,
  Avatar,
  RoleChip,
  LoadingState,
  ErrorState,
  EmptyState,
} from './AdminUI.jsx'
import { fmtDate } from './format.js'
import { useAsyncLoad } from '../../hooks/useAsyncLoad.js'
import AdminOrgBillingPanel from './AdminOrgBillingPanel.jsx'
import Modal from '../../components/ui/Modal.jsx'
import Button from '../../components/ui/Button.jsx'

// Must match OrgContext.jsx's ACTIVE_ORG_KEY — this page intentionally
// doesn't use useOrg().setActiveOrg() because that only accepts an id
// already present in the context's in-memory org list, which (before this
// page grants membership) never includes an org the superadmin isn't a
// member of yet. Writing the same localStorage key and doing a hard
// navigation forces OrgProvider to remount and refetch GET /orgs fresh,
// which by then DOES include it.
const ACTIVE_ORG_KEY = 'nubi-active-org-id'

function EnterOrgButton({ orgId, orgName }) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  async function handleConfirm() {
    setLoading(true)
    setError(null)
    try {
      await enterAdminOrg(orgId)
      try { localStorage.setItem(ACTIVE_ORG_KEY, orgId) } catch { /* ignore */ }
      window.location.assign('/dashboards')
    } catch (err) {
      setError(err?.message || 'Could not enter this organization.')
      setLoading(false)
    }
  }

  return (
    <>
      <Button variant="secondary" size="sm" onClick={() => setOpen(true)}>
        <LogIn size={14} aria-hidden="true" />
        Enter workspace
      </Button>
      <Modal
        open={open}
        onClose={loading ? undefined : () => setOpen(false)}
        persistent={loading}
        title="Enter this organization?"
        description={`You'll be added as a member of "${orgName}" (if you aren't already) and taken into its workspace, exactly like any regular member.`}
      >
        <Modal.Body>
          <p className="text-sm text-muted leading-relaxed">
            This is a real cross-tenant access grant, not a local view — it
            adds an actual membership row and is audit-logged. It does not
            grant owner-only powers (deleting the org, billing, managing
            other members).
          </p>
          {error && (
            <p className="text-sm text-danger mt-3" role="alert">{error}</p>
          )}
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={() => setOpen(false)} disabled={loading}>
            Cancel
          </Button>
          <Button onClick={handleConfirm} disabled={loading}>
            {loading ? 'Entering…' : 'Enter workspace'}
          </Button>
        </Modal.Footer>
      </Modal>
    </>
  )
}

export default function AdminOrgDetailPage() {
  const { id } = useParams()
  const { data, loading, reload } = useAsyncLoad(() => getAdminOrg(id), [id])

  if (loading) return <LoadingState />
  if (!data?.org) {
    return (
      <ErrorState
        message="Could not load this organization."
        onRetry={reload}
      />
    )
  }

  const { org, members = [], projects = [] } = data

  return (
    <div className="space-y-4" data-testid="admin-org-detail">
      <Link
        to="/admin/orgs"
        className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg transition-colors"
      >
        <ArrowLeft size={14} />
        All organizations
      </Link>

      {/* ── Org info ────────────────────────────────────────────────────── */}
      <AdminCard>
        <div className="px-5 py-4 flex flex-wrap items-center gap-x-8 gap-y-3">
          <div className="flex items-center gap-3">
            <Avatar icon={Building2} iconSize={18} className="h-10 w-10 text-sm" />
            <div>
              <h2 className="m-0 text-lg font-semibold leading-tight text-fg">{org.name}</h2>
              <p className="text-xs text-muted mt-0.5">{org.slug || org.id}</p>
            </div>
          </div>
          <div className="flex items-center gap-1.5 text-sm text-muted">
            <Calendar size={14} className="shrink-0" />
            Created <span className="text-fg tabular-nums">{fmtDate(org.created_at)}</span>
          </div>
          <div className="flex items-center gap-1.5 text-sm text-muted">
            <Users size={14} className="shrink-0" />
            <span className="text-fg tabular-nums">{members.length}</span> members
          </div>
          <div className="flex items-center gap-1.5 text-sm text-muted">
            <FolderKanban size={14} className="shrink-0" />
            <span className="text-fg tabular-nums">{projects.length}</span> projects
          </div>
          <div className="ml-auto">
            <EnterOrgButton orgId={org.id} orgName={org.name} />
          </div>
        </div>
      </AdminCard>

      {/* ── Billing & limits (EE; degrades to a note on OSS) ────────────── */}
      <AdminOrgBillingPanel orgId={id} />

      {/* ── Members ─────────────────────────────────────────────────────── */}
      <AdminCard title="Members">
        {members.length === 0 ? (
          <EmptyState message="No members." />
        ) : (
          <AdminTable headers={['Email', 'Name', 'Role']}>
            {members.map((m) => (
              <tr key={m.user_id} className="">
                <td className="px-4 py-2 max-w-[240px]">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <Avatar label={m.name || m.email} />
                    <span className="truncate text-fg font-medium" title={m.email}>{m.email}</span>
                  </div>
                </td>
                <td className="px-4 py-2 max-w-[180px] truncate text-muted" title={m.name || undefined}>{m.name || '—'}</td>
                <td className="px-4 py-2 whitespace-nowrap"><RoleChip>{m.role}</RoleChip></td>
              </tr>
            ))}
          </AdminTable>
        )}
      </AdminCard>

      {/* ── Projects ────────────────────────────────────────────────────── */}
      <AdminCard title="Projects">
        {projects.length === 0 ? (
          <EmptyState message="No projects." />
        ) : (
          <AdminTable headers={['Name', 'Slug', 'Created']}>
            {projects.map((p) => (
              <tr key={p.id} className="">
                <td className="px-4 py-2 max-w-[240px] truncate text-fg font-medium" title={p.name}>{p.name}</td>
                <td className="px-4 py-2 max-w-[180px] truncate text-muted" title={p.slug || undefined}>{p.slug || '—'}</td>
                <td className="px-4 py-2 whitespace-nowrap text-muted tabular-nums">{fmtDate(p.created_at)}</td>
              </tr>
            ))}
          </AdminTable>
        )}
      </AdminCard>
    </div>
  )
}
