/**
 * Nubi Design-System Primitives — barrel export (Swiss).
 * Contract: reports/design-system-tokens.md. Tokens: src/index.css.
 *
 *   import { Button, Tag, EmptyState, Modal } from '@/components/ui'
 *   import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/Tabs'
 *   import { toast, Toaster } from '@/components/ui/Toast'
 *
 * ── Primitive index ────────────────────────────────────────────────────────
 *
 * Button           variants: primary (ink) | secondary | ghost | outline | danger
 *                  sizes: xs 28 | sm 32 | md 36 | lg 40 | xl 48 | icon-xs/icon-sm/icon/icon-lg
 *                  props: loading, asChild
 * Tag              variants: neutral | ink | accent | danger | success | warning
 *                  props: dot, size (sm|md), caps, onRemove, as — replaces every pill
 * Badge            legacy name for Tag (same look)
 *
 * Input            sizes: xs | sm | md | lg | xl  |  props: icon, trailing, error, hint, label
 * Field            label, hint, error, required — wraps any form element
 * Select / Textarea  same props as Input
 * Checkbox         checked, indeterminate, label
 *
 * Card             raised (2px ink top rule), interactive (ink border on hover), noPadding, as
 * CardHeader / CardTitle / CardDescription / CardBody / CardFooter
 *
 * Spinner          sizes: xs | sm | md | lg | xl  |  accent, label
 * Skeleton         className; .Text .Title .Avatar .Card .Rows .Table .Chart .Kpi
 * BrandLoader      size ('sm'|'md'|'lg'|px), label — the mark draws itself
 *
 * Modal            open, onClose, title, description, size, hideClose, persistent
 * Modal.Body / Modal.Footer
 * Tooltip          content, kbd, side (top|bottom|left|right), delay, disabled
 * Toaster / toast  toast.success/error/warning/info/dismiss — mount <Toaster/> once
 *
 * Table            columns, rows, density, selectable, stickyFirstColumn, loading, onSort …
 * EmptyState       kind (blank|none-match|gated|error), art, icon, title, description,
 *                  action, secondaryAction, requestId, compact, bordered
 * Tabs             value, defaultValue, onValueChange, variant (underline|pills)
 * SegmentedControl value, onChange, options, size (sm|md|lg)
 */

export { default as Button } from './Button.jsx'
export { default as Input, Field, Select, Textarea, Checkbox } from './Input.jsx'
export { default as Card, CardHeader, CardTitle, CardDescription, CardBody, CardFooter } from './Card.jsx'
export { default as Badge } from './Badge.jsx'
export { default as Tag } from './Tag.jsx'
export type { TagProps, TagVariant } from './Tag.jsx'
export { default as ConnectionStatusBadge } from './ConnectionStatusBadge.jsx'
export { default as Spinner } from './Spinner.jsx'
export { default as BrandLoader } from './BrandLoader.jsx'
export { default as Skeleton } from './Skeleton.jsx'
export { default as Modal } from './Modal.jsx'
export { default as Tooltip } from './Tooltip.jsx'
export { default as Table } from './Table.jsx'
export { default as EmptyState } from './EmptyState.jsx'
export { default as Toaster, toast } from './Toast.jsx'
export { Tabs, TabsList, TabsTrigger, TabsContent } from './Tabs.jsx'
export { default as SegmentedControl } from './SegmentedControl.jsx'
