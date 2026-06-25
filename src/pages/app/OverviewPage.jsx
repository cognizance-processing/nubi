/**
 * OverviewPage — /overview
 *
 * Top-level workspace overview: an at-a-glance summary of the active
 * project's health, recent activity, and quick-links to key surfaces.
 * Placed as a primary top-level nav item (outside the workspace switcher
 * dropdown, which handles org/project/env switching only).
 */

import { LayoutDashboard } from 'lucide-react'
import { PageRoot, PageHeader } from '../../components/app/PageShell.jsx'

export default function OverviewPage() {
  return (
    <PageRoot>
      <PageHeader
        title="Overview"
        subtitle="A summary of your workspace — activity, health, and quick access to key resources."
      />

      <div className="mt-6 flex flex-col items-center justify-center py-24 text-center gap-4">
        <div className="flex items-center justify-center w-16 h-16 rounded-2xl bg-surface-2">
          <LayoutDashboard size={28} className="text-muted" />
        </div>
        <div>
          <p className="font-display font-semibold text-lg text-fg">Workspace overview</p>
          <p className="text-sm text-muted mt-1 max-w-xs">
            Workspace-level stats, recent changes, and health indicators will appear here.
          </p>
        </div>
      </div>
    </PageRoot>
  )
}
