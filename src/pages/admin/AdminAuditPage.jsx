/**
 * AdminAuditPage — /admin/audit
 *
 * Read-only paginated table of org-scoped audit events.
 * Calls GET /api/v1/audit?limit=50&offset=<n> (newest-first).
 * Shape: { items: [...], total: number }
 * Each item: { id, action, actor_email, resource_type, resource_id, resource_name, created_at, meta }
 */

import { useEffect, useState } from 'react'
import { get } from '../../lib/api.js'
import {
  AdminCard,
  AdminTable,
  AdminTableSkeleton,
  Pagination,
  ErrorState,
  EmptyState,
} from './AdminUI.jsx'
import { fmtDateTime } from './format.js'

const LIMIT = 50

// Colour the action verb (last dotted segment, e.g. "org.create" → "create")
// so the log is scannable at a glance without changing the raw value shown.
const ACTION_COLOR = {
  create: 'text-success',
  invite: 'text-success',
  update: 'text-primary',
  edit: 'text-primary',
  delete: 'text-danger',
  remove: 'text-danger',
  revoke: 'text-danger',
}

function ActionLabel({ action }) {
  const verb = (action || '').split('.').pop()
  const color = ACTION_COLOR[verb] || 'text-fg'
  return <span className={`font-mono text-xs ${color}`}>{action}</span>
}

export default function AdminAuditPage() {
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      try {
        const result = await get(`/audit?limit=${LIMIT}&offset=${offset}`)
        if (cancelled) return
        setData(result)
      } catch {
        if (cancelled) return
        setData(null)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [offset, reloadKey])

  const items = data?.items ?? []
  const total = data?.total ?? 0

  return (
    <div className="space-y-4" data-testid="admin-audit">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-muted">
          Org-scoped audit events — read-only, newest first.
        </p>
        <Pagination offset={offset} limit={LIMIT} total={total} onPage={setOffset} />
      </div>

      <AdminCard>
        {loading ? (
          <AdminTableSkeleton columns={6} />
        ) : !data ? (
          <ErrorState message="Could not load audit log." onRetry={() => setReloadKey((k) => k + 1)} />
        ) : items.length === 0 ? (
          <EmptyState message="No audit events found." />
        ) : (
          <AdminTable headers={['Time', 'Action', 'Actor', 'Resource type', 'Resource', 'Resource ID']}>
            {items.map((ev) => (
              <tr key={ev.id} className="hover:bg-surface-2/50 transition-colors">
                <td className="px-4 py-3 whitespace-nowrap text-muted tabular-nums text-xs">
                  {fmtDateTime(ev.created_at)}
                </td>
                <td className="px-4 py-3 whitespace-nowrap">
                  <ActionLabel action={ev.action} />
                </td>
                <td className="px-4 py-3 max-w-[180px] truncate text-muted text-sm" title={ev.actor_email || undefined}>{ev.actor_email || '—'}</td>
                <td className="px-4 py-3 whitespace-nowrap text-muted text-xs">{ev.resource_type || '—'}</td>
                <td className="px-4 py-3 max-w-[200px] truncate text-fg text-sm" title={ev.resource_name || undefined}>{ev.resource_name || '—'}</td>
                <td className="px-4 py-3 whitespace-nowrap text-muted font-mono text-xs">{ev.resource_id || '—'}</td>
              </tr>
            ))}
          </AdminTable>
        )}
      </AdminCard>
    </div>
  )
}
