/**
 * AdminUsersPage — /admin/users
 *
 * Searchable, paginated, read-only table of all users. Superadmin status is
 * DB-managed by design, so there are no mutations here — just visibility.
 */

import { useEffect, useRef, useState } from 'react'
import { getAdminUsers } from '../../lib/admin.js'
import {
  AdminCard,
  AdminTable,
  SearchInput,
  Pagination,
  RoleChip,
  SuperadminBadge,
  LoadingState,
  ErrorState,
  EmptyState,
} from './AdminUI.jsx'
import { fmtDate, fmtDateTime } from './format.js'

const LIMIT = 50

export default function AdminUsersPage() {
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [reloadKey, setReloadKey] = useState(0)
  const debounceRef = useRef(null)

  // Debounce the search box → reset to page 1 and trigger a reload.
  function onSearch(value) {
    setSearch(value)
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setOffset(0)
      setReloadKey((k) => k + 1)
    }, 300)
  }

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      const result = await getAdminUsers({ search, limit: LIMIT, offset })
      if (cancelled) return
      setData(result)
      setLoading(false)
    }
    load()
    return () => { cancelled = true }
    // search changes only take effect via reloadKey (debounced) or offset.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offset, reloadKey])

  const users = data?.users ?? []
  const total = data?.total ?? 0

  return (
    <div className="space-y-4" data-testid="admin-users">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SearchInput value={search} onChange={onSearch} placeholder="Search users by email or name…" />
        <Pagination offset={offset} limit={LIMIT} total={total} onPage={setOffset} />
      </div>

      <p className="text-xs text-muted">
        Read-only — superadmin status is managed directly in the database by design.
      </p>

      <AdminCard>
        {loading ? (
          <LoadingState />
        ) : !data ? (
          <ErrorState message="Could not load users." onRetry={() => setReloadKey((k) => k + 1)} />
        ) : users.length === 0 ? (
          <EmptyState message="No users match this search." />
        ) : (
          <AdminTable headers={['Email', 'Name', 'Created', 'Last login', 'Last location', 'Orgs']}>
            {users.map((u) => (
              <tr key={u.id} className="hover:bg-surface-2/50 transition-colors">
                <td className="px-4 py-3 whitespace-nowrap">
                  <div className="flex items-center gap-2">
                    <span className="text-fg font-medium">{u.email}</span>
                    {u.is_superadmin && <SuperadminBadge />}
                  </div>
                </td>
                <td className="px-4 py-3 whitespace-nowrap text-muted">{u.name || '—'}</td>
                <td className="px-4 py-3 whitespace-nowrap text-muted tabular-nums">{fmtDate(u.created_at)}</td>
                <td className="px-4 py-3 whitespace-nowrap text-muted tabular-nums">{fmtDateTime(u.last_login_at)}</td>
                <td className="px-4 py-3 whitespace-nowrap text-muted">{u.last_location || '—'}</td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap gap-1">
                    {(u.orgs ?? []).length === 0
                      ? <span className="text-muted">—</span>
                      : u.orgs.map((o) => (
                          <RoleChip key={o.id}>{o.name} · {o.role}</RoleChip>
                        ))}
                  </div>
                </td>
              </tr>
            ))}
          </AdminTable>
        )}
      </AdminCard>
    </div>
  )
}
