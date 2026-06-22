/**
 * Toast / notification system — Nubi primitive.
 *
 * ── Usage ─────────────────────────────────────────────────────────────────
 *
 * 1. Mount the ToastProvider ONCE at the app root (wraps children):
 *      <ToastProvider />   // or place <Toaster /> anywhere, it portals itself
 *
 * 2. Call the imperative API from anywhere:
 *      import { toast } from './components/ui/Toast'
 *
 *      toast.success('Saved successfully')
 *      toast.error('Something went wrong')
 *      toast.warning('Check your quota')
 *      toast.info('Dashboard shared')
 *      toast('Custom message', { variant: 'info', duration: 6000 })
 *
 * ── API ────────────────────────────────────────────────────────────────────
 *
 * toast(message, options?) — base function
 * toast.success(message, options?)
 * toast.error(message, options?)
 * toast.warning(message, options?)
 * toast.info(message, options?)
 * toast.dismiss(id?)  — dismiss by id, or all if no id
 *
 * Options:
 *   variant   'success' | 'error' | 'warning' | 'info' (default: 'info')
 *   duration  number ms (default 4000; 0 = persistent)
 *   title     string — bold header line
 *   action    { label: string, onClick: () => void }
 *
 * ── Toaster component ─────────────────────────────────────────────────────
 * <Toaster position="bottom-right" />   (default)
 * position: 'top-right' | 'top-left' | 'bottom-right' | 'bottom-left' | 'top-center' | 'bottom-center'
 */

import { createPortal } from 'react-dom'
import { useState, useEffect, useCallback } from 'react'
import { CheckCircle2, AlertCircle, AlertTriangle, Info, X } from 'lucide-react'

// ── Event bus (module-level singleton) ──────────────────────────────────────

let _dispatch = null

function register(fn) { _dispatch = fn }
function unregister()  { _dispatch = null }

let _counter = 0
function uid() { return `toast-${++_counter}` }

// ── Imperative API ────────────────────────────────────────────────────────────

function emit(message, opts = {}) {
  const id = opts.id ?? uid()
  _dispatch?.({ type: 'ADD', toast: { id, message, variant: 'info', duration: 4000, ...opts } })
  return id
}

// eslint-disable-next-line react-refresh/only-export-components
export const toast = Object.assign(emit, {
  success: (msg, opts) => emit(msg, { variant: 'success', ...opts }),
  error:   (msg, opts) => emit(msg, { variant: 'error',   ...opts }),
  warning: (msg, opts) => emit(msg, { variant: 'warning', ...opts }),
  info:    (msg, opts) => emit(msg, { variant: 'info',    ...opts }),
  dismiss: (id)        => _dispatch?.({ type: 'REMOVE', id }),
})

// ── Icon map ─────────────────────────────────────────────────────────────────

const ICON = {
  success: CheckCircle2,
  error:   AlertCircle,
  warning: AlertTriangle,
  info:    Info,
}

// ── Position classes ─────────────────────────────────────────────────────────

const POSITION_CLS = {
  'top-right':     'top-4 right-4 items-end',
  'top-left':      'top-4 left-4  items-start',
  'top-center':    'top-4 left-1/2 -translate-x-1/2 items-center',
  'bottom-right':  'bottom-4 right-4 items-end',
  'bottom-left':   'bottom-4 left-4  items-start',
  'bottom-center': 'bottom-4 left-1/2 -translate-x-1/2 items-center',
}

// ── ToastItem ─────────────────────────────────────────────────────────────────

function ToastItem({ t, onDismiss }) {
  const Icon = ICON[t.variant] ?? Info
  const [leaving, setLeaving] = useState(false)

  const dismiss = useCallback(() => {
    setLeaving(true)
    setTimeout(() => onDismiss(t.id), 230)
  }, [t.id, onDismiss])

  // Auto-dismiss
  useEffect(() => {
    if (!t.duration) return
    const timer = setTimeout(dismiss, t.duration)
    return () => clearTimeout(timer)
  }, [t.duration, dismiss])

  return (
    <div
      role="alert"
      aria-live={t.variant === 'error' ? 'assertive' : 'polite'}
      aria-atomic="true"
      className={[
        'nubi-toast',
        `nubi-toast-${t.variant}`,
        leaving ? 'nubi-animate-toast-out' : 'nubi-animate-toast-in',
      ].join(' ')}
    >
      <div className="nubi-toast-icon-wrap shrink-0">
        <Icon size={16} aria-hidden="true" />
      </div>
      <div className="flex-1 min-w-0">
        {t.title && (
          <p className="text-sm font-semibold text-fg leading-snug">{t.title}</p>
        )}
        <p className={['text-sm text-muted leading-snug', t.title ? 'mt-0.5' : 'text-fg font-medium'].join(' ')}>
          {t.message}
        </p>
        {t.action && (
          <button
            type="button"
            onClick={() => { t.action.onClick(); dismiss() }}
            className="mt-1.5 text-xs font-semibold text-primary hover:underline focus-visible:outline-none"
          >
            {t.action.label}
          </button>
        )}
      </div>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss notification"
        className="shrink-0 p-1 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors -mt-0.5 -mr-0.5"
      >
        <X size={14} />
      </button>
    </div>
  )
}

// ── Toaster ───────────────────────────────────────────────────────────────────

export function Toaster({ position = 'bottom-right' }) {
  const [toasts, setToasts] = useState([])

  useEffect(() => {
    function dispatch({ type, toast: t, id }) {
      if (type === 'ADD') {
        setToasts((prev) => [t, ...prev.filter((x) => x.id !== t.id)])
      } else if (type === 'REMOVE') {
        if (id) setToasts((prev) => prev.filter((x) => x.id !== id))
        else setToasts([])
      }
    }
    register(dispatch)
    return unregister
  }, [])

  const dismiss = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const posCls = POSITION_CLS[position] ?? POSITION_CLS['bottom-right']

  return createPortal(
    <div
      aria-label="Notifications"
      className={`fixed flex flex-col gap-2 pointer-events-none ${posCls}`}
      style={{ zIndex: 'var(--z-toast)', maxWidth: 'min(calc(100vw - 2rem), 26rem)' }}
    >
      {toasts.map((t) => (
        <div key={t.id} className="pointer-events-auto w-full">
          <ToastItem t={t} onDismiss={dismiss} />
        </div>
      ))}
    </div>,
    document.body,
  )
}

export default Toaster
