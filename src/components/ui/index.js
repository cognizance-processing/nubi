/**
 * Nubi Design-System Primitives — barrel export.
 *
 * Import individual primitives:
 *   import Button from '@/components/ui/Button'
 *   import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/Tabs'
 *   import { toast, Toaster } from '@/components/ui/Toast'
 *
 * Or import from the barrel:
 *   import { Button, Badge, Card, Modal } from '@/components/ui'
 *
 * ── Primitive index ────────────────────────────────────────────────────────
 *
 * Button           variants: primary | secondary | ghost | danger | outline | accent
 *                  sizes: xs | sm | md | lg | xl | icon | icon-sm | icon-lg
 *                  props: loading, asChild
 *
 * Input            sizes: sm | md | lg  |  props: error, hint, label, required
 * Field            label, hint, error, required — wraps any form element
 * Select           options array or children <option>  — same props as Input
 * Textarea         same props as Input + rows
 *
 * Card             raised, interactive, noPadding
 * CardHeader / CardTitle / CardDescription / CardBody / CardFooter
 *
 * Badge            variants: default | primary | success | warning | danger | info | accent
 *                  sizes: sm | md | lg  |  dot prop
 *
 * Spinner          sizes: xs | sm | md | lg | xl  |  accent prop
 *
 * Skeleton         className for dimensions
 * Skeleton.Text    lines, lastLineWidth
 * Skeleton.Title
 * Skeleton.Avatar  size (px)
 * Skeleton.Card    rows
 *
 * Modal            open, onClose, title, description, size, hideClose, persistent
 * Modal.Body / Modal.Footer
 *
 * Tooltip          content, side (top|bottom|left|right), delay, disabled
 *
 * Table            columns, rows, zebra, sticky, loading, onSort, sortKey, sortDir
 *
 * EmptyState       icon, title, description, action, compact
 *
 * Toaster          position — mount once at app root
 * toast            imperative API: toast.success/error/warning/info/dismiss
 *
 * Tabs             value, defaultValue, onValueChange, variant (underline|pills)
 * TabsList / TabsTrigger / TabsContent
 *
 * SegmentedControl value, onChange, options, size
 */

export { default as Button } from './Button.jsx'
export { default as Input, Field, Select, Textarea } from './Input.jsx'
export { default as Card, CardHeader, CardTitle, CardDescription, CardBody, CardFooter } from './Card.jsx'
export { default as Badge } from './Badge.jsx'
export { default as Spinner } from './Spinner.jsx'
export { default as Skeleton } from './Skeleton.jsx'
export { default as Modal } from './Modal.jsx'
export { default as Tooltip } from './Tooltip.jsx'
export { default as Table } from './Table.jsx'
export { default as EmptyState } from './EmptyState.jsx'
export { default as Toaster, toast } from './Toast.jsx'
export { Tabs, TabsList, TabsTrigger, TabsContent } from './Tabs.jsx'
export { default as SegmentedControl } from './SegmentedControl.jsx'
