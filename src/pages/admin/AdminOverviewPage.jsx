/**
 * AdminOverviewPage — /admin
 *
 * Instance-wide counts (stat cards), signups + logins over the last 30 days
 * (compact CSS spark-bar charts — deliberately not echarts to keep the admin
 * bundle light), and a countries summary from the geo endpoint.
 */

import { useEffect, useState } from 'react'
import {
  Users,
  Building2,
  FolderKanban,
  SearchCode,
  LayoutDashboard,
  Workflow,
  Database,
} from 'lucide-react'
import { getAdminOverview, getAdminGeoSummary } from '../../lib/admin.js'
import {
  AdminCard,
  StatCard,
  SparkBars,
  BarList,
  LoadingState,
  ErrorState,
  EmptyState,
} from './AdminUI.jsx'

const STATS = [
  { key: 'users', label: 'Users', icon: Users },
  { key: 'orgs', label: 'Orgs', icon: Building2 },
  { key: 'projects', label: 'Projects', icon: FolderKanban },
  { key: 'queries', label: 'Queries', icon: SearchCode },
  { key: 'boards', label: 'Dashboards', icon: LayoutDashboard },
  { key: 'flows', label: 'Flows', icon: Workflow },
  { key: 'datastores', label: 'Datastores', icon: Database },
]

export default function AdminOverviewPage() {
  const [overview, setOverview] = useState(null)
  const [geo, setGeo] = useState(null)
  const [loading, setLoading] = useState(true)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      const [ov, g] = await Promise.all([getAdminOverview(), getAdminGeoSummary()])
      if (cancelled) return
      setOverview(ov)
      setGeo(g)
      setLoading(false)
    }
    load()
    return () => { cancelled = true }
  }, [reloadKey])

  if (loading) return <LoadingState />
  if (!overview) {
    return (
      <ErrorState
        message="Could not load the admin overview."
        onRetry={() => setReloadKey((k) => k + 1)}
      />
    )
  }

  const counts = overview.counts ?? {}

  return (
    <div className="space-y-6" data-testid="admin-overview">
      {/* ── Stat cards ──────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7 gap-3">
        {STATS.map((s) => (
          <StatCard
            key={s.key}
            icon={s.icon}
            label={s.label}
            value={counts[s.key]}
            testId={`admin-stat-${s.key}`}
          />
        ))}
      </div>

      {/* ── Activity charts ─────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <AdminCard title="Signups" description="New users per day, last 30 days">
          <SparkBars series={overview.signups_by_day ?? []} ariaLabel="Signups per day" />
        </AdminCard>
        <AdminCard title="Logins" description="Logins per day, last 30 days">
          <SparkBars series={overview.logins_by_day ?? []} ariaLabel="Logins per day" />
        </AdminCard>
      </div>

      {/* ── Geo summary ─────────────────────────────────────────────────── */}
      <AdminCard
        title="Countries"
        description={
          geo
            ? `${geo.total_located ?? 0} of ${geo.total_events ?? 0} auth events located`
            : 'Login locations'
        }
      >
        {geo ? (
          <BarList items={geo.countries ?? []} labelKey="country" countKey="count" />
        ) : (
          <EmptyState message="Geo summary unavailable." />
        )}
      </AdminCard>
    </div>
  )
}
