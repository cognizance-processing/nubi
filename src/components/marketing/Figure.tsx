/**
 * Figure — places a named illustration from the shared catalog
 * (src/components/illustrations/<Name>.tsx), lazily. When the figure is not
 * available it renders the fallback (or nothing), so a page never breaks on a
 * missing piece of art. Art is decorative unless a `title` is passed.
 */
import { lazy, Suspense, useMemo, type ComponentType, type ReactNode } from 'react'

const loaders = import.meta.glob('../illustrations/*.tsx') as Record<string, () => Promise<{ default: ComponentType<any> }>>

export function hasFigure(name: string) {
  return Boolean(loaders[`../illustrations/${name}.tsx`])
}

export default function Figure({
  name, fallback = null, className = '', ...rest
}: {
  name: string
  fallback?: ReactNode
  className?: string
  /** Any figure prop: title, compact, step, … */
  [prop: string]: unknown
}) {
  const load = loaders[`../illustrations/${name}.tsx`]
  const Lazy = useMemo(() => (load ? lazy(load) : null), [load])
  if (!Lazy) return <>{fallback}</>
  return (
    <Suspense fallback={<div className={className} style={{ aspectRatio: '8 / 5' }} aria-hidden="true" />}>
      <Lazy className={className} {...rest} />
    </Suspense>
  )
}
