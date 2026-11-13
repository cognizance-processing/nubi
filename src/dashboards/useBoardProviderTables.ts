/**
 * useBoardProviderTables — BET-3 DataProvider wiring shared by the live board
 * (SpecRenderer) and the editor canvas (DashboardEditor).
 *
 * One provider fetch per declared `spec.data[i]`, sliced per widget by its
 * `widget.source = { provider, result }` binding. Returns
 * `{ [widgetId]: arrow.Table | null }` — pass the entry as `providerTable`.
 *
 * Params are resolved with `resolveParams(provider.params, variables)` — the
 * same function `useResolvedParams` wraps — so callers outside a
 * <VariableProvider> (the editor, which seeds only defaults) can pass their own
 * variable values.
 *
 * Fixed 8 slots, unconditional, in fixed order — satisfies Rules of Hooks.
 */

import { useMemo } from 'react'
import { useProviderData } from './useProviderData.js'
import { resolveParams } from './VariableStore.jsx'
import { providerSlotsFor, buildWidgetProviderTableMap } from './providerDataUtils.js'

export function useBoardProviderTables(
  boardId: string | null,
  specData: any,
  widgets: any[],
  variables: Record<string, any>,
  refreshEpoch = 0,
): Record<string, any> {
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const boardProviders = useMemo(() => Array.isArray(specData) ? specData : [], [JSON.stringify(specData)])

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const providerSlots = useMemo(() => providerSlotsFor(boardProviders), [JSON.stringify(boardProviders)])

  // Resolved params per slot (same deps as useResolvedParams).
  const resolved = useMemo(
    () => providerSlots.map(p => resolveParams(p?.params ?? {}, variables)),
    [providerSlots, variables],
  )

  // 8× useProviderData (unconditional, fixed order)
  const slot0 = useProviderData(boardId, providerSlots[0]?.id ?? null, resolved[0], refreshEpoch)
  const slot1 = useProviderData(boardId, providerSlots[1]?.id ?? null, resolved[1], refreshEpoch)
  const slot2 = useProviderData(boardId, providerSlots[2]?.id ?? null, resolved[2], refreshEpoch)
  const slot3 = useProviderData(boardId, providerSlots[3]?.id ?? null, resolved[3], refreshEpoch)
  const slot4 = useProviderData(boardId, providerSlots[4]?.id ?? null, resolved[4], refreshEpoch)
  const slot5 = useProviderData(boardId, providerSlots[5]?.id ?? null, resolved[5], refreshEpoch)
  const slot6 = useProviderData(boardId, providerSlots[6]?.id ?? null, resolved[6], refreshEpoch)
  const slot7 = useProviderData(boardId, providerSlots[7]?.id ?? null, resolved[7], refreshEpoch)

  return useMemo(
    () => buildWidgetProviderTableMap(widgets, boardProviders, [slot0, slot1, slot2, slot3, slot4, slot5, slot6, slot7]),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(widgets), boardProviders, slot0, slot1, slot2, slot3, slot4, slot5, slot6, slot7],
  )
}
