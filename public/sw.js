/**
 * sw.js — Nubi notification service worker.
 *
 * Two responsibilities only (kept deliberately tiny; this is NOT a PWA/offline
 * worker — there is no caching/fetch handler so it never interferes with the
 * app or the Vite dev server):
 *
 *   1. `push`            — show a notification from the server's JSON payload.
 *   2. `notificationclick` — focus an existing app tab (and navigate it to the
 *                          notification's deep link) or open a new one.
 *
 * Payload shape (sent by backend app/notify/push.py):
 *   { title, body, link?, severity?, id?, tag? }
 *
 * We take control immediately on install/activate so the opt-in flow works on
 * the first registration without a reload.
 */

self.addEventListener('install', () => {
  // Activate this worker as soon as it finishes installing.
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  // Become the active worker for all open clients right away.
  event.waitUntil(self.clients.claim())
})

self.addEventListener('push', (event) => {
  let payload = {}
  if (event.data) {
    try {
      payload = event.data.json()
    } catch {
      // Fall back to a plain-text body if the payload isn't JSON.
      payload = { body: event.data.text() }
    }
  }

  const title = payload.title || 'Nubi'
  const options = {
    body: payload.body || '',
    // A stable tag collapses repeat notifications of the same thing.
    tag: payload.tag || payload.id || undefined,
    // Stash the deep link + id so notificationclick can route.
    data: {
      link: payload.link || '/',
      id: payload.id,
      severity: payload.severity,
    },
    badge: '/nubi.png',
    icon: '/nubi.png',
  }

  event.waitUntil(self.registration.showNotification(title, options))
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()

  const link = (event.notification.data && event.notification.data.link) || '/'
  // Resolve to an absolute URL against the worker's scope (its origin).
  const targetUrl = new URL(link, self.location.origin).href

  event.waitUntil(
    self.clients
      .matchAll({ type: 'window', includeUncontrolled: true })
      .then((clientList) => {
        // Prefer focusing an already-open same-origin tab and routing it.
        for (const client of clientList) {
          if (new URL(client.url).origin === self.location.origin && 'focus' in client) {
            client.postMessage({ type: 'notification-click', link, id: event.notification.data?.id })
            if ('navigate' in client) {
              return client.focus().then(() => client.navigate(targetUrl).catch(() => {}))
            }
            return client.focus()
          }
        }
        // No suitable tab — open a fresh one at the deep link.
        if (self.clients.openWindow) {
          return self.clients.openWindow(targetUrl)
        }
        return undefined
      }),
  )
})
