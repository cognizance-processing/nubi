/**
 * AdminOrgDetailPage — /admin/orgs/:id
 *
 * Org info + members table + projects table (read-only).
 */

import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { getAdminOrg } from '../../lib/admin.js'
import {
  AdminCard,
  AdminTable,
  RoleChip,
  LoadingState,
  ErrorState,
  EmptyState,
} from './AdminUI.jsx'
import { fmtDate } from './format.js'

export default function AdminOrgDetailPage() {
  const { id } = useParams()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      const result = await getAdminOrg(id)
      if (cancelled) return
      setData(result)
      setLoading(false)
    }
    load()
    return () => { cancelled = true }
  }, [id, reloadKey])

  if (loading) return <LoadingState />
  if (!data?.org) {
    return (
      <ErrorState
        message="Could not load this organization."
        onRetry={() => setReloadKey((k) => k + 1)}
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
        <div className="px-5 py-4 flex flex-wrap items-center gap-x-8 gap-y-2">
          <div>
            <h2 className="font-display font-semibold text-lg text-fg">{org.name}</h2>
            <p className="text-xs text-muted mt-0.5">{org.slug || org.id}</p>
          </div>
          <div className="text-sm text-muted">
            Created <span className="text-fg tabular-nums">{fmtDate(org.created_at)}</span>
          </div>
          <div className="text-sm text-muted">
            <span className="text-fg tabular-nums">{members.length}</span> members
          </div>
          <div className="text-sm text-muted">
            <span className="text-fg tabular-nums">{projects.length}</span> projects
          </div>
        </div>
      </AdminCard>

      {/* ── Members ─────────────────────────────────────────────────────── */}
      <AdminCard title="Members">
        {members.length === 0 ? (
          <EmptyState message="No members." />
        ) : (
          <AdminTable headers={['Email', 'Name', 'Role']}>
            {members.map((m) => (
              <tr key={m.user_id} className="hover:bg-surface-2/50 transition-colors">
                <td className="px-4 py-3 whitespace-nowrap text-fg font-medium">{m.email}</td>
                <td className="px-4 py-3 whitespace-nowrap text-muted">{m.name || '—'}</td>
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
                <td className="px-4 py-3 whitespace-nowrap text-fg font-medium">{p.name}</td>
                <td className="px-4 py-3 whitespace-nowrap text-muted">{p.slug || '—'}</td>
                <td className="px-4 py-3 whitespace-nowrap text-muted tabular-nums">{fmtDate(p.created_at)}</td>
              </tr>
            ))}
          </AdminTable>
        )}
      </AdminCard>
    </div>
  )
}
