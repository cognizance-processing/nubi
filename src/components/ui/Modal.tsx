/**
 * Modal / Dialog — Nubi overlay primitive.
 *
 * Usage:
 *   const [open, setOpen] = useState(false)
 *
 *   <Modal open={open} onClose={() => setOpen(false)} title="Confirm">
 *     <p>Are you sure?</p>
 *     <Modal.Footer>
 *       <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
 *       <Button onClick={handleConfirm}>Confirm</Button>
 *     </Modal.Footer>
 *   </Modal>
 *
 * Props:
 *   open         boolean — controls visibility
 *   onClose      () => void — called on backdrop click or Escape
 *   title        string — dialog title (renders in a header)
 *   description  string — optional subtitle under the title
 *   size         'sm' | 'md' (default) | 'lg' | 'xl' | 'full'
 *   hideClose    boolean — hide the X button
 *   persistent   boolean — prevent closing on backdrop click (Escape still works)
 *   className    applied to the dialog panel
 *   children
 *
 * Sub-components:
 *   Modal.Footer  — right-aligned action row with top border
 *   Modal.Body    — padded content area
 */

import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'

function cx(...parts) {
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

function ModalFooter({ className = undefined, children, ...rest }) {
  return (
    <div
      className={cx(
        'flex items-center justify-end gap-3 px-6 pb-6 pt-4 border-t border-border shrink-0',
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  )
}

function ModalBody({ className = undefined, children, ...rest }) {
  return (
    <div className={cx('px-6 py-5 flex-1 overflow-y-auto', className)} {...rest}>
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
}) {
  const dialogRef = useRef(null)

  // Escape to close
  useEffect(() => {
    if (!open) return
    const handler = (e) => {
      if (e.key === 'Escape') onClose?.()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  // Focus trap — focus the dialog on open
  useEffect(() => {
    if (!open) return
    const t = setTimeout(() => dialogRef.current?.focus(), 50)
    return () => clearTimeout(t)
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
      {/* Backdrop */}
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
          aria-labelledby={title ? 'nubi-modal-title' : undefined}
          aria-describedby={description ? 'nubi-modal-desc' : undefined}
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
            <div className="flex items-start gap-3 px-6 pt-6 pb-4 border-b border-border shrink-0">
              {title && (
                <div className="flex-1 min-w-0">
                  <h2
                    id="nubi-modal-title"
                    className="font-display font-semibold text-base text-fg"
                  >
                    {title}
                  </h2>
                  {description && (
                    <p id="nubi-modal-desc" className="text-sm text-muted mt-0.5">
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
                  className="shrink-0 p-1.5 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ml-auto"
                >
                  <X size={16} />
                </button>
              )}
            </div>
          )}

          {/* Content — either raw children or structured Body/Footer */}
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
