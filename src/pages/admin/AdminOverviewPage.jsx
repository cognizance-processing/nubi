/**
 * AdminOverviewPage — /admin
 *
 * Instance-wide counts (stat cards), signups + logins over the last 30 days
 * (compact CSS spark-bar charts — deliberately not echarts to keep the admin
 * bundle light), and a countries summary from the geo endpoint.
 */

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
  StatCardSkeleton,
  SparkBars,
  BarList,
  ErrorState,
  EmptyState,
} from './AdminUI.jsx'
import { useAsyncLoad } from '../../hooks/useAsyncLoad.js'

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
  const { data: pageData, loading, reload } = useAsyncLoad(
    async () => {
      const [overview, geo] = await Promise.all([getAdminOverview(), getAdminGeoSummary()])
      return { overview, geo }
    },
    []
  )
  const overview = pageData?.overview ?? null
  const geo = pageData?.geo ?? null

  if (loading) {
    return (
      <div className="space-y-6" data-testid="admin-overview" aria-busy="true">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7 gap-3">
          {STATS.map((s) => <StatCardSkeleton key={s.key} />)}
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <AdminCard title="Signups" description="New users per day, last 30 days">
            <div className="animate-pulse h-24 mx-5 my-4 rounded-lg bg-surface-2" />
          </AdminCard>
          <AdminCard title="Logins" description="Logins per day, last 30 days">
            <div className="animate-pulse h-24 mx-5 my-4 rounded-lg bg-surface-2" />
          </AdminCard>
        </div>
      </div>
    )
  }
  if (!overview) {
    return (
      <ErrorState
        message="Could not load the admin overview."
        onRetry={reload}
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
