/**
 * Toast / notification system — Nubi primitive (Swiss).
 *
 * A bordered card with --shadow-pop and a 6px square status dot (no tinted icon
 * tile). Enters in 220ms on --ease-out (8px rise + fade), pauses its timer while
 * hovered or focused, errors are announced assertively.
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
import { X } from 'lucide-react'

// ── Event bus (module-level singleton) ──────────────────────────────────────

let _dispatch = null

function register(fn) { _dispatch = fn }
function unregister()  { _dispatch = null }

let _counter = 0
function uid() { return `toast-${++_counter}` }

// ── Imperative API ────────────────────────────────────────────────────────────

function emit(message, opts: Record<string, any> = {}) {
  const id = opts.id ?? uid()
  _dispatch?.({ type: 'ADD', toast: { id, message, variant: 'info', duration: 4000, ...opts } })
  return id
}

// eslint-disable-next-line react-refresh/only-export-components
export const toast = Object.assign(emit, {
  success: (msg, opts = undefined) => emit(msg, { variant: 'success', ...opts }),
  error:   (msg, opts = undefined) => emit(msg, { variant: 'error',   ...opts }),
  warning: (msg, opts = undefined) => emit(msg, { variant: 'warning', ...opts }),
  info:    (msg, opts = undefined) => emit(msg, { variant: 'info',    ...opts }),
  dismiss: (id = undefined)        => _dispatch?.({ type: 'REMOVE', id }),
})

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
  const [leaving, setLeaving] = useState(false)
  const [paused, setPaused] = useState(false)

  const dismiss = useCallback(() => {
    setLeaving(true)
    setTimeout(() => onDismiss(t.id), 170)
  }, [t.id, onDismiss])

  // Auto-dismiss (paused while hovered or focused)
  useEffect(() => {
    if (!t.duration || paused) return
    const timer = setTimeout(dismiss, t.duration)
    return () => clearTimeout(timer)
  }, [t.duration, dismiss, paused])

  return (
    <div
      role={t.variant === 'error' ? 'alert' : 'status'}
      aria-live={t.variant === 'error' ? 'assertive' : 'polite'}
      aria-atomic="true"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
      className={[
        'nubi-toast',
        `nubi-toast-${t.variant}`,
        leaving ? 'nubi-animate-toast-out' : 'nubi-animate-toast-in',
      ].join(' ')}
    >
      <span className="nubi-toast-dot" aria-hidden="true" />
      <div className="flex-1 min-w-0">
        {t.title && (
          <p className="text-small font-semibold text-fg">{t.title}</p>
        )}
        <p className={['text-small', t.title ? 'text-muted mt-0.5' : 'text-fg font-medium'].join(' ')}>
          {t.message}
        </p>
        {t.action && (
          <button
            type="button"
            onClick={() => { t.action.onClick(); dismiss() }}
            className="mt-1.5 text-xs font-semibold text-fg underline underline-offset-2 hover:text-accent"
          >
            {t.action.label}
          </button>
        )}
      </div>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss notification"
        className="nubi-btn nubi-btn-ghost nubi-btn-icon-xs shrink-0 -mt-1.5 -mr-1.5"
        style={{ width: 24, height: 24 }}
      >
        <X size={14} aria-hidden="true" />
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
      role="region"
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
