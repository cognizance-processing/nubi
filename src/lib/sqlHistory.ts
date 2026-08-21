/**
 * sqlHistory — session-spanning, browser-local "scratch" run history for the
 * query editor's primary cell (SQL-Lab-style: every Run you fire, kept
 * separate from the formal, server-persisted registered-query version
 * history in lib/versions.js).
 *
 * Stored in localStorage, capped at MAX_ENTRIES, newest first. Per-browser,
 * per-viewer — never synced or shared, so every read/write is wrapped in
 * try/catch (private windows, cleared site data, and storage quota errors
 * must never break the editor).
 */

const KEY = 'nubi.sqlHistory'
const MAX_ENTRIES = 50

export interface SqlHistoryEntry {
  id: string
  sql: string
  queryName?: string | null
  ranAt: number
  ok: boolean
  rowCount?: number | null
  elapsedMs?: number | null
  error?: string | null
}

/** Read the stored history, newest first. Never throws. */
export function loadSqlHistory(): SqlHistoryEntry[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

/**
 * Record a run and return the updated list. Re-running the same SQL that's
 * already at the top (e.g. clicking Run again unchanged) replaces that entry
 * rather than piling up duplicates.
 */
export function pushSqlHistory(entry: Omit<SqlHistoryEntry, 'id' | 'ranAt'>): SqlHistoryEntry[] {
  const next: SqlHistoryEntry = {
    ...entry,
    id: `h_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    ranAt: Date.now(),
  }
  const existing = loadSqlHistory()
  const deduped = existing[0]?.sql === next.sql ? existing.slice(1) : existing
  const updated = [next, ...deduped].slice(0, MAX_ENTRIES)
  try { localStorage.setItem(KEY, JSON.stringify(updated)) } catch { /* quota/private-mode — history just won't persist */ }
  return updated
}

/** Clear all stored history and return the (empty) list. */
export function clearSqlHistory(): SqlHistoryEntry[] {
  try { localStorage.removeItem(KEY) } catch { /* ignore */ }
  return []
}
