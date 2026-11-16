/**
 * push.js — browser-side Web Push helpers (Notification + Service Worker + Push
 * APIs). Pairs with ``public/sw.js`` and ``src/lib/notificationsApi.js``.
 *
 * Everything here degrades gracefully on unsupported browsers (Safari < 16,
 * private windows, http origins, etc.): ``pushSupported()`` is the single guard
 * the UI uses to hide/disable the opt-in toggle, and every async helper returns
 * a defined result rather than throwing on capability gaps.
 */

import { getVapidKey, subscribePush, unsubscribePush } from './notificationsApi.js'

const SW_URL = '/sw.js'

/**
 * Whether this browser can do Web Push at all (SW + PushManager + Notification +
 * a secure context). Pure capability check — no side effects.
 * @returns {boolean}
 */
export function pushSupported() {
  return (
    typeof window !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window &&
    // Push requires a secure context (https or localhost).
    (window.isSecureContext ?? true)
  )
}

/** Current Notification permission ('default' | 'granted' | 'denied' | 'unsupported'). */
export function notificationPermission() {
  if (typeof Notification === 'undefined') return 'unsupported'
  return Notification.permission
}

/** Convert a base64url VAPID key to the Uint8Array applicationServerKey wants. */
function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4)
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(base64)
  const output = new Uint8Array(raw.length)
  for (let i = 0; i < raw.length; i++) output[i] = raw.charCodeAt(i)
  return output
}

/**
 * Register (or reuse) the notification service worker.
 * @returns {Promise<ServiceWorkerRegistration|null>}  null if unsupported/failed.
 */
export async function ensureServiceWorker() {
  if (!pushSupported()) return null
  try {
    const existing = await navigator.serviceWorker.getRegistration(SW_URL)
    if (existing) return existing
    return await navigator.serviceWorker.register(SW_URL)
  } catch (err) {
    console.warn('[push] service worker registration failed:', err?.message)
    return null
  }
}

/**
 * Whether the user currently has an active push subscription registered with
 * this browser.
 * @returns {Promise<boolean>}
 */
export async function isSubscribed() {
  if (!pushSupported()) return false
  try {
    const reg = await navigator.serviceWorker.getRegistration(SW_URL)
    if (!reg) return false
    const sub = await reg.pushManager.getSubscription()
    return !!sub
  } catch {
    return false
  }
}

/**
 * Full opt-in flow: request permission → register SW → fetch VAPID key →
 * subscribe → POST to the backend.
 *
 * @returns {Promise<{ ok: boolean, reason?: string }>}
 *   ``reason`` is one of 'unsupported' | 'denied' | 'no-vapid-key' | 'error'
 *   when ``ok`` is false, so the UI can show a precise message.
 */
export async function enablePush() {
  if (!pushSupported()) return { ok: false, reason: 'unsupported' }

  // 1. Permission (must be a user-gesture-initiated call; the toggle handler is).
  let permission = Notification.permission
  if (permission === 'default') {
    permission = await Notification.requestPermission()
  }
  if (permission !== 'granted') return { ok: false, reason: 'denied' }

  // 2. Service worker.
  const reg = await ensureServiceWorker()
  if (!reg) return { ok: false, reason: 'error' }

  try {
    // 3. Reuse an existing subscription, else create one with the VAPID key.
    let sub = await reg.pushManager.getSubscription()
    if (!sub) {
      const vapidKey = await getVapidKey()
      if (!vapidKey) return { ok: false, reason: 'no-vapid-key' }
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapidKey),
      })
    }

    // 4. Persist server-side (re-throws on failure → caught below).
    await subscribePush(sub.toJSON())
    return { ok: true }
  } catch (err) {
    console.warn('[push] enablePush failed:', err?.message)
    return { ok: false, reason: 'error' }
  }
}

/**
 * Opt out: unsubscribe in the browser and tell the backend to prune it.
 * @returns {Promise<{ ok: boolean }>}
 */
export async function disablePush() {
  if (!pushSupported()) return { ok: true }
  try {
    const reg = await navigator.serviceWorker.getRegistration(SW_URL)
    if (!reg) return { ok: true }
    const sub = await reg.pushManager.getSubscription()
    if (!sub) return { ok: true }
    const endpoint = sub.endpoint
    await sub.unsubscribe().catch(() => {})
    await unsubscribePush(endpoint)
    return { ok: true }
  } catch (err) {
    console.warn('[push] disablePush failed:', err?.message)
    return { ok: false }
  }
}
