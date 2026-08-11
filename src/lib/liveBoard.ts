/**
 * liveBoard.js — the impure half of draft-vs-live (see dashboards/liveState.js).
 *
 * Split for testability, the same way widgetLibrary.js splits from
 * widgetLibraryShape.js: everything here reaches lib/api.js (via lib/versions.js),
 * which reads `import.meta.env` and therefore only exists under Vite. The pure
 * decision logic lives in dashboards/liveState.js so `node --test` can cover it,
 * and is re-exported here so callers have one import.
 */

import { checkpoint, promote } from './versions.js'
import { resolveDraftEnvKey, resolveLiveEnvKey, summarisePush } from '../dashboards/liveState.js'

export {
  resolveLiveEnvKey,
  resolveDraftEnvKey,
  resolveLiveRender,
  canPushToLive,
  summarisePush,
  FALLBACK_LIVE_ENV,
  FALLBACK_DRAFT_ENV,
} from '../dashboards/liveState.js'

/**
 * Push the current draft live: checkpoint it, then promote that version.
 *
 * Sequenced, NOT parallel — promote copies whatever pointer the draft env
 * currently holds, so the checkpoint must land first. Racing them would promote
 * the PREVIOUS version and silently publish stale work, which is the worst
 * possible failure for a button called "Push to live".
 *
 * `include_dependencies` carries the board's registered queries along: a board
 * promoted without them would go live pointing at query definitions its viewers
 * can't resolve in that env.
 *
 * @returns Throws on failure (both calls throw; the caller surfaces the message).
 */
export async function pushToLive(
  boardId: string,
  { message, environments = null }: { message?: string; environments?: any[] | null } = {},
): Promise<{ version: number | null; deduped: boolean; promoted: number }> {
  const from_env = resolveDraftEnvKey(environments)
  const to_env = resolveLiveEnvKey(environments)

  const version = await checkpoint('board', boardId, { message, env_key: from_env })
  const result = await promote({
    kind: 'board',
    resource_id: boardId,
    from_env,
    to_env,
    include_dependencies: true,
  })

  return summarisePush(version, result)
}
