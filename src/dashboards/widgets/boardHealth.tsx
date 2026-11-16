/**
 * boardHealth.tsx — the board-level view of widget failures.
 *
 * Every failed tile (WidgetError) reports itself here. When three or more tiles
 * share a cause (the warehouse is down, a credential expired) the board shows
 * ONE banner that says so, instead of leaving the viewer to read six identical
 * error tiles. The banner offers "Retry all", which re-runs every failed tile.
 *
 * Two contexts on purpose: tiles only need the stable `report` function, so a
 * tile failing never re-renders its siblings; only the banner subscribes to the
 * list of failures.
 */

import {
  createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState,
  type ReactNode,
} from 'react'
import { AlertTriangle, RotateCw } from 'lucide-react'
import Button from '../../components/ui/Button.jsx'
import { useCanWrite } from '../../contexts/OrgContext.jsx'
import { groupFailures, type FailureEntry } from './failure.js'

/**
 * Whether engine details may be shown. Editors (anyone who can write to the org)
 * see them; viewers never do. Fails closed outside an OrgProvider (a standalone
 * render of the board), where there is no role to check.
 */
export function useCanSeeDetails(): boolean {
  try {
    // useCanWrite reads one context and throws only when it is missing, so the
    // hook order is the same on every render.
    // eslint-disable-next-line react-hooks/rules-of-hooks
    return useCanWrite()
  } catch {
    return false
  }
}

interface Reported extends FailureEntry {
  retry?: () => void
}

interface Reporter {
  report: (id: string, entry: Reported | null) => void
}

const ReporterContext = createContext<Reporter | null>(null)
const EntriesContext = createContext<Reported[]>([])

export function BoardHealthProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<Reported[]>([])

  const report = useCallback((id: string, entry: Reported | null) => {
    setEntries(prev => {
      const rest = prev.filter(e => e.id !== id)
      if (!entry) return rest.length === prev.length ? prev : rest
      return [...rest, entry]
    })
  }, [])

  const reporter = useMemo(() => ({ report }), [report])

  return (
    <ReporterContext.Provider value={reporter}>
      <EntriesContext.Provider value={entries}>{children}</EntriesContext.Provider>
    </ReporterContext.Provider>
  )
}

/**
 * Register one failed tile with the board while it is mounted. A no-op outside a
 * BoardHealthProvider (the editor canvas, embeds), so a tile never needs to know.
 */
export function useReportFailure(
  failure: Omit<FailureEntry, 'id'> | null,
  retry?: () => void,
) {
  const reporter = useContext(ReporterContext)
  const id = useId()
  // The retry callback changes identity every render; keep the latest in a ref
  // so re-registering is driven only by the failure itself.
  const retryRef = useRef(retry)
  useEffect(() => { retryRef.current = retry })

  const key = failure?.key
  const cause = failure?.cause
  const label = failure?.label
  const message = failure?.message
  useEffect(() => {
    if (!reporter) return
    if (!key || !cause || !label) { reporter.report(id, null); return }
    reporter.report(id, {
      id, key, cause, label, message: message ?? '',
      retry: () => retryRef.current?.(),
    })
    return () => reporter.report(id, null)
  }, [reporter, id, key, cause, label, message])
}

/**
 * The grouped banner. Renders nothing until ≥ 3 tiles share a cause. Viewers
 * get the plain sentence; editors also get the engine's own message.
 */
export function BoardErrorBanner({ className = '' }: { className?: string }) {
  const entries = useContext(EntriesContext)
  const canWrite = useCanSeeDetails()
  const groups = useMemo(() => groupFailures(entries), [entries])
  if (groups.length === 0) return null

  const retryGroup = (ids: string[]) => {
    for (const e of entries) if (ids.includes(e.id)) e.retry?.()
  }

  return (
    <div className={['space-y-2', className].filter(Boolean).join(' ')} data-testid="board-error-banner">
      {groups.map(g => (
        <div key={g.key} className="nbc-banner items-start" role="alert">
          <AlertTriangle size={16} strokeWidth={1.5} className="shrink-0 mt-0.5" aria-hidden="true" />
          <div className="flex-1 min-w-0">
            <p className="text-[13px] font-semibold leading-snug">
              <span className="font-mono tabular-nums">{g.count}</span> tiles couldn&apos;t load
            </p>
            <p className="text-[13px] leading-snug mt-0.5">{g.label} They share one cause, so the tiles below are quiet.</p>
            {canWrite && g.sample && (
              <details className="mt-1.5 group">
                <summary className="cursor-pointer text-[12px] font-medium underline-offset-2 hover:underline select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm w-fit">
                  Details
                </summary>
                <pre className="mt-1.5 max-h-32 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-snug border-t border-current/20 pt-1.5">
                  {g.sample}
                </pre>
              </details>
            )}
          </div>
          <Button
            size="xs"
            variant="secondary"
            onClick={() => retryGroup(g.ids)}
            className="shrink-0"
          >
            <RotateCw size={12} strokeWidth={1.5} aria-hidden="true" />
            Retry all
          </Button>
        </div>
      ))}
    </div>
  )
}

/** True when the tile belongs to a group the banner already explains. */
export function useIsGrouped(key: string | undefined): boolean {
  const entries = useContext(EntriesContext)
  return useMemo(() => {
    if (!key) return false
    return groupFailures(entries).some(g => g.key === key)
  }, [entries, key])
}
