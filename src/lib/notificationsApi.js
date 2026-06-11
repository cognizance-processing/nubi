/**
 * notificationsApi.js — thin transport layer for the in-app notification feed
 * and Web Push subscriptions.
 *
 * Powers the bell + notification center (mounted in the AppShell right rail) and
 * the push opt-in toggle.
 *
 * Contract (backend/app/routes/notifications.py + push.py — paths under /api/v1):
 *   GET  /notifications                  listNotifications  (?unread=1&limit=)
 *   GET  /notifications/unread_count     unreadCount
 *   POST /notifications/{id}/read        markRead
 *   POST /notifications/read_all         markAllRead
 *   GET  /push/vapid_key                 getVapidKey
 *   POST /push/subscribe                 subscribePush
 *   POST /push/unsubscribe               unsubscribePush
 *
 * Read/count helpers degrade gracefully (return safe empty values) so the bell
 * renders even when the backend is down or the endpoints 404. Write helpers
 * re-throw so callers can surface a failure.
 *
 * Notification shape:
 *   {
 *     id, type, severity ('info'|'success'|'warning'|'error'),
 *     title, body, link, metadata, read_at, created_at,
 *   }
 */

import { get, post } from './api.js'

// ---------------------------------------------------------------------------
// Feed
// ---------------------------------------------------------------------------

/**
 * List the feed for the active org/user.
 * @param {{ unread?: boolean, limit?: number }} [opts]
 * @returns {Promise<Array<object>>}  [] on any failure.
 */
export async function listNotifications({ unread = false, limit } = {}) {
  const params = new URLSearchParams()
  if (unread) params.set('unread', '1')
  if (limit) params.set('limit', String(limit))
  const qs = params.toString()
  try {
    const data = await get(`/notifications${qs ? `?${qs}` : ''}`)
    if (Array.isArray(data)) return data
    if (Array.isArray(data?.notifications)) return data.notifications
    if (Array.isArray(data?.items)) return data.items
    return []
  } catch (err) {
    console.warn('[notifications] listNotifications failed; returning []:', err.message)
    return []
  }
}

/**
 * Get the unread count for the bell badge.
 * @returns {Promise<number>}  0 on any failure.
 */
export async function unreadCount() {
  try {
    const data = await get('/notifications/unread_count')
    if (typeof data === 'number') return data
    if (typeof data?.count === 'number') return data.count
    if (typeof data?.unread === 'number') return data.unread
    return 0
  } catch (err) {
    console.warn('[notifications] unreadCount failed; returning 0:', err.message)
    return 0
  }
}

/**
 * Mark a single notification read.
 * @param {string} id
 * @returns {Promise<boolean>}  true on success, false on failure.
 */
export async function markRead(id) {
  try {
    await post(`/notifications/${encodeURIComponent(id)}/read`, {})
    return true
  } catch (err) {
    console.warn('[notifications] markRead failed:', err.message)
    return false
  }
}

/**
 * Mark every notification read.
 * @returns {Promise<boolean>}  true on success, false on failure.
 */
export async function markAllRead() {
  try {
    await post('/notifications/read_all', {})
    return true
  } catch (err) {
    console.warn('[notifications] markAllRead failed:', err.message)
    return false
  }
}

// ---------------------------------------------------------------------------
// Web Push
// ---------------------------------------------------------------------------

/**
 * Fetch the server's VAPID public key (base64url).
 * @returns {Promise<string|null>}  null on any failure (push then unavailable).
 */
export async function getVapidKey() {
  try {
    const data = await get('/push/vapid_key')
    if (typeof data === 'string') return data
    return data?.key ?? data?.public_key ?? data?.vapid_public_key ?? null
  } catch (err) {
    console.warn('[push] getVapidKey failed; push unavailable:', err.message)
    return null
  }
}

/**
 * Register a Web Push subscription with the backend (upsert by endpoint).
 * Re-throws on failure so the toggle can roll back its optimistic state.
 * @param {PushSubscriptionJSON} subscription  the .toJSON() of a PushSubscription
 * @returns {Promise<object>}
 */
export function subscribePush(subscription) {
  return post('/push/subscribe', {
    subscription,
    user_agent: typeof navigator !== 'undefined' ? navigator.userAgent : undefined,
  })
}

/**
 * Remove a Web Push subscription from the backend.
 * @param {string} endpoint  the subscription endpoint URL.
 * @returns {Promise<boolean>}  true on success, false on failure.
 */
export async function unsubscribePush(endpoint) {
  try {
    await post('/push/unsubscribe', { endpoint })
    return true
  } catch (err) {
    console.warn('[push] unsubscribePush failed:', err.message)
    return false
  }
}
