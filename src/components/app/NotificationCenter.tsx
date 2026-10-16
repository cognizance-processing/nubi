/**
 * NotificationCenter — the in-app notification feed + Web Push opt-in.
 *
 * Mounted once by AppShell. It owns TWO things:
 *
 *   1. The unread-count badge. The bell itself is a button in the persistent
 *      topbar's global panel switcher (AppTopbar); AppShell passes its toggle
 *      + count in.
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
import { toast } from '../ui/Toast.jsx'
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
  success: { Icon: CheckCircle2, cls: 'text-success' },
  info: { Icon: Info, cls: 'text-subtle' },
  warning: { Icon: AlertTriangle, cls: 'text-warning' },
  error: { Icon: AlertCircle, cls: 'text-danger' },
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
    <div className="px-5 py-2.5 border-b border-border">
      <label className="flex items-center gap-2.5 text-xs text-fg cursor-pointer select-none">
        <button
          type="button"
          role="switch"
          aria-checked={on}
          onClick={toggle}
          disabled={busy}
          className={[
            'relative inline-flex h-5 w-9 shrink-0 items-center rounded-sm border transition-colors disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface',
            on ? 'bg-[var(--ink)] border-[var(--ink)]' : 'bg-surface border-[var(--frame-line)]',
          ].join(' ')}
        >
          {busy ? (
            <Loader2 size={12} strokeWidth={1.5} className="animate-spin mx-auto text-muted" aria-hidden="true" />
          ) : (
            <span
              className={[
                'inline-block h-3 w-3 rounded-sm transition-transform',
                on ? 'translate-x-[1.1rem] bg-[var(--ink-fg)]' : 'translate-x-0.5 bg-[var(--ink)]',
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
      {hint && <p className="text-[11px] text-warning mt-1.5 pl-11" role="status">{hint}</p>}
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
        'group flex items-start gap-3 px-5 py-3 border-b border-border border-l-2 transition-colors',
        unread ? 'border-l-[var(--ink)]' : 'border-l-transparent',
        n.link ? 'cursor-pointer hover:bg-[var(--frame-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring' : '',
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
      <Icon size={16} strokeWidth={1.5} className={`shrink-0 mt-0.5 ${cls}`} aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className={`text-[13px] truncate m-0 ${unread ? 'font-semibold text-fg' : 'font-medium text-muted'}`}>
            {n.title || 'Notification'}
          </p>
          {unread && <span className="sr-only">Unread</span>}
        </div>
        {n.body && <p className="text-xs text-muted mt-0.5 line-clamp-3">{n.body}</p>}
        <p className="font-mono text-[11px] text-subtle tabular-nums mt-1 m-0">{relativeTime(n.created_at)}</p>
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
          className="sh-icon-btn shrink-0 opacity-0 group-hover:opacity-100 focus-visible:opacity-100" style={{ width: 28, height: 28 }}
        >
          <Check size={14} strokeWidth={1.5} aria-hidden="true" />
        </button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// The slide-over panel
// ---------------------------------------------------------------------------

function NotificationPanel({ open, onClose, items, loading, onMarkRead, onMarkAll, onNavigate, embedded = false }) {
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
      {/* Backdrop — overlay mode only (embedded lives inside the shared sidebar) */}
      {!embedded && (
        <div
          className="fixed inset-0 z-[55] bg-[var(--sh-scrim)]"
          onClick={onClose}
          aria-hidden="true"
        />
      )}
      <aside
        role="dialog"
        aria-modal={embedded ? undefined : 'true'}
        aria-labelledby="notif-panel-title"
        className={embedded
          ? 'flex h-full w-full flex-col bg-surface'
          : 'sh-pop fixed inset-y-0 right-0 z-[55] w-full max-w-md flex flex-col'}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-border shrink-0">
          <div className="min-w-0 flex-1">
            <h2 id="notif-panel-title" className="m-0 text-[15px] font-semibold text-fg">
              Notifications
            </h2>
            <p className="m-0 text-xs text-muted truncate">Alerts from watches, flows and shares.</p>
          </div>
          <button
            type="button"
            onClick={onMarkAll}
            disabled={!hasUnread}
            title="Mark all as read"
            className="shrink-0 inline-flex items-center gap-1.5 px-2.5 h-8 rounded-sm text-xs font-medium text-muted hover:text-fg hover:bg-[var(--frame-hover)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <CheckCheck size={14} strokeWidth={1.5} aria-hidden="true" />
            <span className="hidden sm:inline">Mark all read</span>
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close notifications"
            className="sh-icon-btn shrink-0"
          >
            <X size={16} strokeWidth={1.5} aria-hidden="true" />
          </button>
        </div>

        {/* Push opt-in */}
        <PushToggle />

        {/* Feed */}
        <div className="flex-1 min-h-0 overflow-y-auto">
          {loading && items.length === 0 ? (
            <div className="flex items-center justify-center gap-2 text-sm text-muted py-16">
              <Loader2 size={16} strokeWidth={1.5} className="animate-spin" aria-hidden="true" /> Loading…
            </div>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center justify-center text-center py-20 px-6 gap-3">
              <Inbox size={28} strokeWidth={1.5} className="text-subtle" aria-hidden="true" />
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
 *   embedded?: boolean,
 * }} props
 */
export default function NotificationCenter({ open, onClose, onCount, embedded = false }) {
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
  // Also surfaces a toast the moment the unread count RISES — this is what
  // makes an async background job (e.g. an AI chat build) feel like it
  // "notifies you": you don't have to have the bell panel open to see it
  // land. The first tick only seeds the baseline (pre-existing unread items
  // must never toast on page load); `seenRef` guards against re-toasting the
  // same row if it's still unread on a later poll.
  const lastUnreadRef = useRef(0)
  const seenRef = useRef(new Set())
  const initializedRef = useRef(false)
  useEffect(() => {
    let timer = null

    const tick = async () => {
      if (typeof document !== 'undefined' && document.hidden) return
      try {
        const n = await fetchUnreadCount()
        onCountRef.current?.(n)

        if (!initializedRef.current) {
          initializedRef.current = true
          lastUnreadRef.current = n
          return
        }
        if (n > lastUnreadRef.current) {
          try {
            const fresh = await listNotifications({ unread: true, limit: Math.min(n, 10) })
            for (const item of fresh) {
              if (!item?.id || seenRef.current.has(item.id)) continue
              seenRef.current.add(item.id)
              const variant = ['success', 'error', 'warning', 'info'].includes(item.severity)
                ? item.severity
                : 'info'
              toast(item.body || item.title || 'New notification', {
                variant,
                title: item.title,
                action: item.link ? { label: 'Open', onClick: () => navigate(item.link) } : undefined,
              })
            }
          } catch {
            // Best-effort — the badge already reflects the new count either way.
          }
        }
        lastUnreadRef.current = n
      } catch {
        // Transient (e.g. the poll racing auth on a hard reload) — ignore;
        // the next tick recovers. Never let a badge poll throw unhandled.
      }
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
    () => [...items].sort((a, b) => Number(new Date(b.created_at || 0)) - Number(new Date(a.created_at || 0))),
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
      embedded={embedded}
    />
  )
}
