/**
 * NotificationCenter — the in-app notification feed + Web Push opt-in.
 *
 * Mounted once by AppShell. It owns TWO things:
 *
 *   1. The unread-count badge. The bell itself is a button in the persistent
 *      right-edge rail (AppRightRail); AppShell passes its toggle + count in.
 *      We poll GET /notifications/unread_count on a reasonable interval and
 *      PAUSE polling while the tab is hidden (visibilitychange) to be cheap.
 *
 *   2. The slide-over panel (this component), which lists the feed with a
 *      severity icon / relative time / deep link per item, per-item mark-read,
 *      a "mark all read" action, and the Web Push opt-in toggle in its header.
 *
 * Visual language mirrors GitSyncPanel: a fixed backdrop + right-anchored
 * <aside> slide-over using the same Tailwind tokens.
 *
 * react-hooks/set-state-in-effect: we NEVER setState synchronously in an effect
 * body. Polling/loading happen in interval callbacks and async handlers; the
 * feed is loaded via a deferred timeout (setTimeout(…, 0)) on open, matching
 * the WatchesPage pattern.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Bell,
  X,
  Check,
  CheckCheck,
  Loader2,
  Info,
  CheckCircle2,
  AlertTriangle,
  AlertCircle,
  Inbox,
} from 'lucide-react'

import {
  listNotifications,
  markRead,
  markAllRead,
  unreadCount as fetchUnreadCount,
} from '../../lib/notificationsApi.js'
import {
  pushSupported,
  isSubscribed,
  enablePush,
  disablePush,
  notificationPermission,
} from '../../lib/push.js'

const POLL_MS = 30_000

// ---------------------------------------------------------------------------
// Relative-time + severity helpers (pure)
// ---------------------------------------------------------------------------

function relativeTime(iso) {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const secs = Math.round((Date.now() - then) / 1000)
  if (secs < 60) return 'just now'
  const mins = Math.round(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.round(hrs / 24)
  if (days < 7) return `${days}d ago`
  return new Date(iso).toLocaleDateString()
}

const SEVERITY = {
  success: { Icon: CheckCircle2, cls: 'text-emerald-500' },
  info: { Icon: Info, cls: 'text-sky-500' },
  warning: { Icon: AlertTriangle, cls: 'text-amber-500' },
  error: { Icon: AlertCircle, cls: 'text-red-500' },
}

function severityOf(n) {
  return SEVERITY[n?.severity] ?? SEVERITY.info
}

function isUnread(n) {
  return !n?.read_at
}

// ---------------------------------------------------------------------------
// Push opt-in toggle (panel header)
// ---------------------------------------------------------------------------

function PushToggle() {
  const supported = pushSupported()
  const [on, setOn] = useState(false)
  const [busy, setBusy] = useState(false)
  const [hint, setHint] = useState(null)

  // Reflect the current subscription state once on mount (deferred so we never
  // setState synchronously inside the effect body).
  useEffect(() => {
    if (!supported) return undefined
    let cancelled = false
    const t = setTimeout(async () => {
      const sub = await isSubscribed()
      if (!cancelled) setOn(sub)
    }, 0)
    return () => {
      cancelled = true
      clearTimeout(t)
    }
  }, [supported])

  const toggle = useCallback(async () => {
    setBusy(true)
    setHint(null)
    try {
      if (on) {
        await disablePush()
        setOn(false)
      } else {
        const res = await enablePush()
        if (res.ok) {
          setOn(true)
        } else {
          const msg = {
            denied:
              notificationPermission() === 'denied'
                ? 'Notifications are blocked in your browser settings.'
                : 'Permission was not granted.',
            'no-vapid-key': 'Push is not configured on the server yet.',
            unsupported: 'This browser does not support push.',
            error: 'Could not enable push notifications.',
          }[res.reason]
          setHint(msg || 'Could not enable push notifications.')
          setOn(false)
        }
      }
    } finally {
      setBusy(false)
    }
  }, [on])

  if (!supported) return null

  return (
    <div className="px-5 py-2.5 border-b border-border bg-surface-2/30">
      <label className="flex items-center gap-2.5 text-xs text-fg cursor-pointer select-none">
        <button
          type="button"
          role="switch"
          aria-checked={on}
          onClick={toggle}
          disabled={busy}
          className={[
            'relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
            on ? 'bg-primary' : 'bg-surface-2 border border-border',
          ].join(' ')}
        >
          {busy ? (
            <Loader2 size={11} className="animate-spin mx-auto text-muted" />
          ) : (
            <span
              className={[
                'inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform',
                on ? 'translate-x-4' : 'translate-x-0.5',
              ].join(' ')}
            />
          )}
        </button>
        <span className="min-w-0">
          <span className="font-medium text-fg">Push notifications</span>
          <span className="text-muted">
            {' '}
            — get alerts even when Nubi is closed
          </span>
        </span>
      </label>
      {hint && <p className="text-[11px] text-amber-600 dark:text-amber-400 mt-1.5 pl-11">{hint}</p>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// One feed item
// ---------------------------------------------------------------------------

function NotificationItem({ n, onMarkRead, onNavigate }) {
  const { Icon, cls } = severityOf(n)
  const unread = isUnread(n)

  const open = useCallback(() => {
    if (unread) onMarkRead(n.id)
    if (n.link) onNavigate(n.link)
  }, [unread, n.id, n.link, onMarkRead, onNavigate])

  return (
    <div
      className={[
        'group flex items-start gap-3 px-5 py-3 border-b border-border/60 transition-colors',
        unread ? 'bg-primary/[0.04]' : '',
        n.link ? 'cursor-pointer hover:bg-surface-2/60' : '',
      ].join(' ')}
      onClick={n.link ? open : undefined}
      role={n.link ? 'button' : undefined}
      tabIndex={n.link ? 0 : undefined}
      onKeyDown={
        n.link
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                open()
              }
            }
          : undefined
      }
    >
      <Icon size={16} className={`shrink-0 mt-0.5 ${cls}`} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className={`text-sm truncate ${unread ? 'font-semibold text-fg' : 'font-medium text-fg/90'}`}>
            {n.title || 'Notification'}
          </p>
          {unread && <span className="w-1.5 h-1.5 rounded-full bg-primary shrink-0" aria-label="Unread" />}
        </div>
        {n.body && <p className="text-xs text-muted mt-0.5 line-clamp-3">{n.body}</p>}
        <p className="text-[11px] text-muted/70 mt-1">{relativeTime(n.created_at)}</p>
      </div>
      {unread && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            onMarkRead(n.id)
          }}
          title="Mark as read"
          aria-label="Mark as read"
          className="shrink-0 p-1 rounded-lg text-muted/60 opacity-0 group-hover:opacity-100 hover:text-fg hover:bg-surface-2 transition-all focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        >
          <Check size={14} />
        </button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// The slide-over panel
// ---------------------------------------------------------------------------

function NotificationPanel({ open, onClose, items, loading, onMarkRead, onMarkAll, onNavigate }) {
  // ESC to close.
  useEffect(() => {
    if (!open) return undefined
    const onKey = (e) => {
      if (e.key === 'Escape') onClose?.()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const hasUnread = items.some(isUnread)

  return (
    <>
      <div
        className="fixed inset-0 z-[55] bg-black/40 backdrop-blur-[1px]"
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="notif-panel-title"
        className="fixed inset-y-0 right-0 z-[55] w-full max-w-md bg-surface border-l border-border shadow-2xl flex flex-col"
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-border shrink-0">
          <div className="shrink-0 w-9 h-9 rounded-xl flex items-center justify-center bg-primary/10">
            <Bell size={17} className="text-primary" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 id="notif-panel-title" className="font-display font-semibold text-base text-fg">
              Notifications
            </h2>
            <p className="text-xs text-muted truncate">Alerts from watches, flows and shares.</p>
          </div>
          <button
            type="button"
            onClick={onMarkAll}
            disabled={!hasUnread}
            title="Mark all as read"
            className="shrink-0 inline-flex items-center gap-1.5 px-2.5 h-8 rounded-lg text-xs font-medium text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-40 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            <CheckCheck size={14} />
            <span className="hidden sm:inline">Mark all read</span>
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close notifications"
            className="shrink-0 p-1.5 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            <X size={16} />
          </button>
        </div>

        {/* Push opt-in */}
        <PushToggle />

        {/* Feed */}
        <div className="flex-1 min-h-0 overflow-y-auto">
          {loading && items.length === 0 ? (
            <div className="flex items-center justify-center gap-2 text-sm text-muted py-16">
              <Loader2 size={16} className="animate-spin" /> Loading…
            </div>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center justify-center text-center py-20 px-6 gap-3">
              <Inbox size={30} className="text-muted/40" />
              <p className="text-sm font-medium text-fg">You're all caught up</p>
              <p className="text-xs text-muted max-w-xs">
                New alerts from watches, flow runs and shared resources will show up here.
              </p>
            </div>
          ) : (
            items.map((n) => (
              <NotificationItem key={n.id} n={n} onMarkRead={onMarkRead} onNavigate={onNavigate} />
            ))
          )}
        </div>
      </aside>
    </>
  )
}

// ---------------------------------------------------------------------------
// NotificationCenter — the mounted controller
// ---------------------------------------------------------------------------

/**
 * @param {{
 *   open: boolean,
 *   onClose: () => void,
 *   onCount?: (n: number) => void,   // report unread count up to the rail badge
 * }} props
 */
export default function NotificationCenter({ open, onClose, onCount }) {
  const navigate = useNavigate()
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)

  // Keep the latest onCount in a ref so the polling interval doesn't churn.
  // (Synced in an effect — writing a ref during render is disallowed by lint.)
  const onCountRef = useRef(onCount)
  useEffect(() => {
    onCountRef.current = onCount
  }, [onCount])

  // ---- Unread-count polling (paused while the tab is hidden) -------------
  useEffect(() => {
    let timer = null

    const tick = async () => {
      if (typeof document !== 'undefined' && document.hidden) return
      const n = await fetchUnreadCount()
      onCountRef.current?.(n)
    }

    const start = () => {
      if (timer) return
      tick()
      timer = setInterval(tick, POLL_MS)
    }
    const stop = () => {
      if (timer) {
        clearInterval(timer)
        timer = null
      }
    }

    const onVisibility = () => {
      if (document.hidden) stop()
      else start()
    }

    start()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      stop()
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [])

  // ---- Load the feed when the panel opens (deferred; no sync setState) ----
  const load = useCallback(async () => {
    setLoading(true)
    const data = await listNotifications({ limit: 50 })
    setItems(Array.isArray(data) ? data : [])
    setLoading(false)
    const unread = data.filter(isUnread).length
    onCountRef.current?.(unread)
  }, [])

  useEffect(() => {
    if (!open) return undefined
    const t = setTimeout(load, 0)
    return () => clearTimeout(t)
  }, [open, load])

  const handleMarkRead = useCallback(
    async (id) => {
      // Optimistic update.
      setItems((prev) =>
        prev.map((n) => (n.id === id ? { ...n, read_at: n.read_at || new Date().toISOString() } : n)),
      )
      setItems((prev) => {
        onCountRef.current?.(prev.filter(isUnread).length)
        return prev
      })
      const ok = await markRead(id)
      if (!ok) load()
    },
    [load],
  )

  const handleMarkAll = useCallback(async () => {
    setItems((prev) => prev.map((n) => ({ ...n, read_at: n.read_at || new Date().toISOString() })))
    onCountRef.current?.(0)
    const ok = await markAllRead()
    if (!ok) load()
  }, [load])

  const handleNavigate = useCallback(
    (link) => {
      onClose?.()
      // Internal links route in-app; external/absolute links open normally.
      if (/^https?:\/\//i.test(link)) {
        window.open(link, '_blank', 'noopener')
      } else {
        navigate(link)
      }
    },
    [navigate, onClose],
  )

  // Listen for clicks coming from the service worker (a clicked push notif while
  // the app is already open) and route in-app.
  useEffect(() => {
    if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return undefined
    const onMessage = (event) => {
      if (event.data?.type === 'notification-click' && event.data.link) {
        handleNavigate(event.data.link)
      }
    }
    navigator.serviceWorker.addEventListener('message', onMessage)
    return () => navigator.serviceWorker.removeEventListener('message', onMessage)
  }, [handleNavigate])

  const sorted = useMemo(
    () => [...items].sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0)),
    [items],
  )

  return (
    <NotificationPanel
      open={open}
      onClose={onClose}
      items={sorted}
      loading={loading}
      onMarkRead={handleMarkRead}
      onMarkAll={handleMarkAll}
      onNavigate={handleNavigate}
    />
  )
}
