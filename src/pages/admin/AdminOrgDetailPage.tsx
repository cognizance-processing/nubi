/**
 * AdminOrgDetailPage — /admin/orgs/:id
 *
 * Org info + members table + projects table (read-only).
 */

import { useParams } from 'react-router-dom'
import { Link } from 'react-router-dom'
import { ArrowLeft, Building2, Calendar, Users, FolderKanban } from 'lucide-react'
import { getAdminOrg } from '../../lib/admin.js'
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
            <Avatar icon={Building2} iconSize={18} className="w-10 h-10 rounded-xl text-sm" />
            <div>
              <h2 className="font-display font-semibold text-lg text-fg leading-tight">{org.name}</h2>
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
              <tr key={m.user_id} className="hover:bg-surface-2/50 transition-colors">
                <td className="px-4 py-3 max-w-[240px]">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <Avatar label={m.name || m.email} />
                    <span className="truncate text-fg font-medium" title={m.email}>{m.email}</span>
                  </div>
                </td>
                <td className="px-4 py-3 max-w-[180px] truncate text-muted" title={m.name || undefined}>{m.name || '—'}</td>
                <td className="px-4 py-3 whitespace-nowrap"><RoleChip>{m.role}</RoleChip></td>
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
              <tr key={p.id} className="hover:bg-surface-2/50 transition-colors">
                <td className="px-4 py-3 max-w-[240px] truncate text-fg font-medium" title={p.name}>{p.name}</td>
                <td className="px-4 py-3 max-w-[180px] truncate text-muted" title={p.slug || undefined}>{p.slug || '—'}</td>
                <td className="px-4 py-3 whitespace-nowrap text-muted tabular-nums">{fmtDate(p.created_at)}</td>
              </tr>
            ))}
          </AdminTable>
        )}
      </AdminCard>
    </div>
  )
}
