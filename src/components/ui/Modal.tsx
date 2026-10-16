/**
 * Modal / Dialog — Nubi overlay primitive (Swiss).
 *
 * 1px border, 2px ink top rule, 4px radius, --shadow-pop. Enters with a 160ms
 * opacity + scale(.96 -> 1) on --ease-out (no overshoot); the scrim is a flat
 * 50% ink, no blur. Focus is trapped while open and restored to the trigger on
 * close; Escape closes; the page behind does not scroll.
 *
 * Usage:
 *   const [open, setOpen] = useState(false)
 *
 *   <Modal open={open} onClose={() => setOpen(false)} title="Confirm">
 *     <Modal.Body>Are you sure?</Modal.Body>
 *     <Modal.Footer>
 *       <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
 *       <Button onClick={handleConfirm}>Confirm</Button>
 *     </Modal.Footer>
 *   </Modal>
 *
 * Props:
 *   open         boolean — controls visibility
 *   onClose      () => void — called on scrim click or Escape
 *   title        string — dialog title (renders in a header)
 *   description  string — optional subtitle under the title
 *   size         'sm' | 'md' (default) | 'lg' | 'xl' | '2xl' | 'full'
 *   hideClose    boolean — hide the X button
 *   persistent   boolean — prevent closing on scrim click (Escape still works)
 *   className    applied to the dialog panel
 *   children
 *
 * Sub-components:
 *   Modal.Footer  — right-aligned action row with a hairline above
 *   Modal.Body    — padded content area
 */

import { useEffect, useId, useLayoutEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'

function cx(...parts: unknown[]) {
  return parts.filter(Boolean).join(' ')
}

const SIZE_CLASS = {
  sm:   'max-w-sm',
  md:   'max-w-md',
  lg:   'max-w-lg',
  xl:   'max-w-xl',
  '2xl':'max-w-2xl',
  full: 'max-w-[calc(100vw-2rem)]',
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

function ModalFooter({ className = undefined, children, ...rest }: { className?: string; children?: React.ReactNode; [k: string]: any }) {
  return (
    <div
      className={cx(
        'flex items-center justify-end gap-2 px-6 py-4 border-t border-border shrink-0',
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  )
}

function ModalBody({ className = undefined, children, ...rest }: { className?: string; children?: React.ReactNode; [k: string]: any }) {
  return (
    <div className={cx('px-6 py-5 flex-1 overflow-y-auto text-body', className)} {...rest}>
      {children}
    </div>
  )
}

function Modal({
  open,
  onClose,
  title = undefined,
  description = undefined,
  size = 'md',
  hideClose = false,
  persistent = false,
  className = undefined,
  children,
}: {
  open: boolean
  onClose?: () => void
  title?: React.ReactNode
  description?: React.ReactNode
  size?: keyof typeof SIZE_CLASS
  hideClose?: boolean
  persistent?: boolean
  className?: string
  children?: React.ReactNode
}) {
  const dialogRef = useRef<HTMLDivElement | null>(null)
  const uid = useId()
  const titleId = `${uid}-title`
  const descId = `${uid}-desc`
  const onCloseRef = useRef(onClose)
  useLayoutEffect(() => {
    onCloseRef.current = onClose
  })

  // Escape closes; Tab is trapped inside the dialog
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onCloseRef.current?.()
        return
      }
      if (e.key !== 'Tab') return
      const root = dialogRef.current
      if (!root) return
      const items = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.offsetParent !== null)
      if (items.length === 0) {
        e.preventDefault()
        root.focus()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      const active = document.activeElement
      if (e.shiftKey && (active === first || active === root)) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && active === last) {
        e.preventDefault()
        first.focus()
      } else if (!root.contains(active)) {
        e.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open])

  // Move focus in on open (first field if any, else the panel); restore on close
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    const t = setTimeout(() => {
      const root = dialogRef.current
      if (!root || root.contains(document.activeElement)) return
      const target = root.querySelector<HTMLElement>('[data-autofocus], input:not([type="hidden"]), textarea, select')
      ;(target ?? root).focus()
    }, 30)
    return () => {
      clearTimeout(t)
      previous?.focus?.()
    }
  }, [open])

  // Prevent body scroll while open
  useEffect(() => {
    if (!open) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [open])

  if (!open) return null

  return createPortal(
    <>
      {/* Scrim */}
      <div
        className="nubi-backdrop nubi-animate-fade-in"
        onClick={persistent ? undefined : onClose}
        aria-hidden="true"
        style={{ zIndex: 'var(--z-modal)' }}
      />

      {/* Dialog positioner */}
      <div
        className="fixed inset-0 flex items-center justify-center p-4 pointer-events-none"
        style={{ zIndex: 'var(--z-modal)' }}
      >
        <div
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby={title ? titleId : undefined}
          aria-describedby={description ? descId : undefined}
          tabIndex={-1}
          className={cx(
            'pointer-events-auto w-full flex flex-col nubi-panel nubi-animate-scale-in',
            SIZE_CLASS[size] ?? SIZE_CLASS.md,
            'max-h-[calc(100vh-4rem)] outline-none',
            className,
          )}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          {(title || !hideClose) && (
            <div className="flex items-start gap-3 px-6 pt-5 pb-4 border-b border-border shrink-0">
              {title && (
                <div className="flex-1 min-w-0">
                  <h2 id={titleId} className="text-section text-fg">
                    {title}
                  </h2>
                  {description && (
                    <p id={descId} className="text-small text-muted mt-1">
                      {description}
                    </p>
                  )}
                </div>
              )}
              {!hideClose && (
                <button
                  type="button"
                  onClick={onClose}
                  aria-label="Close dialog"
                  className="nubi-btn nubi-btn-ghost nubi-btn-icon-xs shrink-0 -mt-1 -mr-2 ml-auto"
                >
                  <X size={16} aria-hidden="true" />
                </button>
              )}
            </div>
          )}

          {/* Content: raw children or structured Body / Footer */}
          {children}
        </div>
      </div>
    </>,
    document.body,
  )
}

Modal.Footer = ModalFooter
Modal.Body = ModalBody
export default Modal
