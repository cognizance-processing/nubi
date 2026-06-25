/**
 * WorkqueuePage — /workqueue
 *
 * Top-level work queue: pending tasks, jobs, and action items for the
 * active workspace. Placed as a primary top-level nav item (outside the
 * workspace switcher dropdown, which handles org/project/env switching only).
 */

import { ListChecks } from 'lucide-react'
import { PageRoot, PageHeader } from '../../components/app/PageShell.jsx'

export default function WorkqueuePage() {
  return (
    <PageRoot>
      <PageHeader
        title="Workqueue"
        subtitle="Pending tasks, jobs, and action items for this workspace."
      />

      <div className="mt-6 flex flex-col items-center justify-center py-24 text-center gap-4">
        <div className="flex items-center justify-center w-16 h-16 rounded-2xl bg-surface-2">
          <ListChecks size={28} className="text-muted" />
        </div>
        <div>
          <p className="font-display font-semibold text-lg text-fg">Work queue</p>
          <p className="text-sm text-muted mt-1 max-w-xs">
            Pending tasks, scheduled jobs, and action items will appear here.
          </p>
        </div>
      </div>
    </PageRoot>
  )
}
