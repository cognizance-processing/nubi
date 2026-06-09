/**
 * AdminOrgsPage — /admin/orgs
 *
 * Searchable, paginated table of all organizations.
 * Row click → /admin/orgs/:id detail.
 */

import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getAdminOrgs } from '../../lib/admin.js'
import {
  AdminCard,
  AdminTable,
  SearchInput,
  Pagination,
  LoadingState,
  ErrorState,
  EmptyState,
} from './AdminUI.jsx'
import { fmtDate } from './format.js'

const LIMIT = 50

export default function AdminOrgsPage() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [reloadKey, setReloadKey] = useState(0)
  const debounceRef = useRef(null)

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
      const result = await getAdminOrgs({ search, limit: LIMIT, offset })
      if (cancelled) return
      setData(result)
      setLoading(false)
    }
    load()
    return () => { cancelled = true }
    // search changes only take effect via reloadKey (debounced) or offset.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offset, reloadKey])

  const orgs = data?.orgs ?? []
  const total = data?.total ?? 0

  return (
    <div className="space-y-4" data-testid="admin-orgs">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SearchInput value={search} onChange={onSearch} placeholder="Search organizations…" />
        <Pagination offset={offset} limit={LIMIT} total={total} onPage={setOffset} />
      </div>

      <AdminCard>
        {loading ? (
          <LoadingState />
        ) : !data ? (
          <ErrorState message="Could not load organizations." onRetry={() => setReloadKey((k) => k + 1)} />
        ) : orgs.length === 0 ? (
          <EmptyState message="No organizations match this search." />
        ) : (
          <AdminTable headers={['Name', 'Slug', 'Members', 'Projects', 'Created']}>
            {orgs.map((o) => (
              <tr
                key={o.id}
                onClick={() => navigate(`/admin/orgs/${o.id}`)}
                className="cursor-pointer hover:bg-surface-2/50 transition-colors"
              >
                <td className="px-4 py-3 whitespace-nowrap text-fg font-medium">{o.name}</td>
                <td className="px-4 py-3 whitespace-nowrap text-muted">{o.slug || '—'}</td>
                <td className="px-4 py-3 whitespace-nowrap text-muted tabular-nums">{o.member_count}</td>
                <td className="px-4 py-3 whitespace-nowrap text-muted tabular-nums">{o.project_count}</td>
                <td className="px-4 py-3 whitespace-nowrap text-muted tabular-nums">{fmtDate(o.created_at)}</td>
              </tr>
            ))}
          </AdminTable>
        )}
      </AdminCard>
    </div>
  )
}
