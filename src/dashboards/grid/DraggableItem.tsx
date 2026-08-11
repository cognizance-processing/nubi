/**
 * DraggableItem.jsx — One grid cell: wires dnd-kit's useDraggable + the 8 resize
 * handles, and places the child via the inline grid-area style GridCanvas passes.
 *
 * The drag listeners are attached ONLY to the element matched by `dragHandle`
 * (default '.drag-handle') — we don't make the whole cell draggable, so clicks on
 * widget bodies / config controls still work. We do this by spreading dnd-kit's
 * listeners onto the WRAPPER but gating activation: dnd-kit's PointerSensor reads
 * the original target, and we additionally guard in onPointerDownCapture so a
 * pointer-down outside the handle never starts a drag.
 *
 * Resize handles are absolutely-positioned children with this component's own
 * `.grid-resize-handle` class (+ a per-direction modifier) so they don't depend
 * on react-resizable CSS.
 */

import { useRef, useCallback } from 'react'
import { useDraggable } from '@dnd-kit/core'

export function DraggableItem({
  item,
  style,
  draggable,
  dragHandle = '.drag-handle',
  selected,
  active,
  resizable,
  resizeHandles = [],
  onResizeHandleDown,
  children,
}) {
  const { attributes, listeners, setNodeRef } = useDraggable({
    id: item.i,
    disabled: !draggable,
  })

  const wrapperRef = useRef(null)

  // Gate drag activation to the handle: if the pointer-down did NOT land inside
  // an element matching `dragHandle`, swallow it so dnd-kit's sensor (which we
  // attached at the wrapper) does not begin a drag. Resize-handle pointer-downs
  // are likewise excluded (they have their own handler).
  const onPointerDownCapture = useCallback((e) => {
    if (!draggable) return
    const target = e.target
    if (target.closest?.('.grid-resize-handle')) return // resize, not drag
    if (dragHandle && !target.closest?.(dragHandle)) {
      // Not on the handle → don't let the drag sensor see this pointer-down.
      e.stopPropagation()
    }
  }, [draggable, dragHandle])

  const setRefs = useCallback((node) => {
    wrapperRef.current = node
    setNodeRef(node)
  }, [setNodeRef])

  const cls = [
    'grid-item',
    selected ? 'grid-item-selected' : '',
    active ? 'grid-item-active' : '',
  ].filter(Boolean).join(' ')

  return (
    <div
      ref={setRefs}
      className={cls}
      style={style}
      data-grid-id={item.i}
      onPointerDownCapture={onPointerDownCapture}
      {...(draggable ? listeners : {})}
      {...(draggable ? attributes : {})}
    >
      {children}
      {resizable && resizeHandles.map(h => (
        <span
          key={h}
          className={`grid-resize-handle grid-resize-${h}`}
          // Stop the drag sensor from also seeing this pointer-down; start resize.
          onPointerDown={(e) => onResizeHandleDown?.(e, item.i, h)}
          role="presentation"
          aria-hidden
        />
      ))}
    </div>
  )
}
