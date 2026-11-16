/**
 * Skeleton — Nubi loading placeholder primitives (Swiss).
 *
 * Flat tint (`--surface-3`) with a slow opacity pulse: no gradient sweep. The
 * pulse is CSS-delayed by 300ms so a fast load never flashes a skeleton, and it
 * is a static tint under prefers-reduced-motion. Shapes mirror the content they
 * stand in for, so the page does not jump when data arrives.
 *
 * Usage:
 *   <Skeleton className="h-4 w-48" />
 *   <Skeleton.Text lines={3} />
 *   <Skeleton.Title className="w-64" />
 *   <Skeleton.Avatar size={40} />
 *   <Skeleton.Card />
 *   <Skeleton.Rows rows={6} />                   list rows (icon + two lines + meta)
 *   <Skeleton.Table rows={5} cols={4} />         table with a header rule
 *   <Skeleton.Chart height={220} />              axes + gridlines + bars
 *   <Skeleton.Kpi />                             label + big number + sparkline bar
 *
 * Props (base): className (width/height via Tailwind), style.
 */

function cx(...parts: unknown[]) {
  return parts.filter(Boolean).join(' ')
}

function SkeletonBase({ className, ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cx('nubi-skeleton', className)} aria-hidden="true" {...rest} />
}

function SkeletonText({ lines = 3, lastLineWidth = '60%', className = undefined }: { lines?: number; lastLineWidth?: string; className?: string }) {
  return (
    <div className={cx('flex flex-col gap-2', className)} aria-hidden="true">
      {Array.from({ length: lines }).map((_, i) => (
        <SkeletonBase
          key={i}
          className="nubi-skeleton-text"
          style={i === lines - 1 && lines > 1 ? { width: lastLineWidth } : undefined}
        />
      ))}
    </div>
  )
}

function SkeletonTitle({ className }: { className?: string }) {
  return <SkeletonBase className={cx('nubi-skeleton-title', className)} />
}

function SkeletonAvatar({ size = 32 }: { size?: number }) {
  return (
    <SkeletonBase
      className="nubi-skeleton-avatar shrink-0"
      style={{ width: size, height: size }}
    />
  )
}

function SkeletonCard({ rows = 3 }: { rows?: number }) {
  return (
    <div className="nubi-card nubi-card-body space-y-4" aria-hidden="true">
      <div className="flex items-center gap-3">
        <SkeletonAvatar size={40} />
        <div className="flex-1 space-y-2">
          <SkeletonBase className="h-4 w-1/2" />
          <SkeletonBase className="h-3 w-1/3" />
        </div>
      </div>
      <SkeletonText lines={rows} />
    </div>
  )
}

function SkeletonRows({ rows = 5, className = undefined }: { rows?: number; className?: string }) {
  return (
    <div className={cx('border-t border-border', className)} aria-hidden="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 px-4 h-14 border-b border-border">
          <SkeletonBase className="h-8 w-8 shrink-0" />
          <div className="flex-1 space-y-2">
            <SkeletonBase className="h-3" style={{ width: `${62 - ((i * 11) % 28)}%` }} />
            <SkeletonBase className="h-2.5" style={{ width: `${38 - ((i * 7) % 14)}%` }} />
          </div>
          <SkeletonBase className="h-3 w-14 shrink-0" />
        </div>
      ))}
    </div>
  )
}

function SkeletonTable({ rows = 5, cols = 4, className = undefined }: { rows?: number; cols?: number; className?: string }) {
  return (
    <div className={cx('border border-border bg-surface', className)} aria-hidden="true">
      <div className="flex items-center gap-4 px-3 h-9 border-b-2 border-ink">
        {Array.from({ length: cols }).map((_, c) => (
          <SkeletonBase key={c} className="h-2.5 flex-1" style={{ maxWidth: c === 0 ? '30%' : '18%' }} />
        ))}
      </div>
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex items-center gap-4 px-3 h-9 border-b border-border last:border-b-0">
          {Array.from({ length: cols }).map((_, c) => (
            <SkeletonBase
              key={c}
              className="h-3 flex-1"
              style={{ maxWidth: c === 0 ? `${30 - ((r * 5) % 10)}%` : '18%' }}
            />
          ))}
        </div>
      ))}
    </div>
  )
}

function SkeletonChart({ height = 200, bars = 12, className = undefined }: { height?: number; bars?: number; className?: string }) {
  // Axes and gridlines show immediately; the bars are the pulsing part
  return (
    <div className={cx('relative w-full', className)} style={{ height }} aria-hidden="true">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="absolute left-8 right-0 border-t border-border" style={{ top: `${(i / 3) * 88}%` }} />
      ))}
      <div className="absolute left-8 bottom-[12%] right-0 border-t-2 border-ink opacity-30" />
      <div className="absolute left-8 top-0 bottom-[12%] flex items-end gap-1.5 right-0 px-2">
        {Array.from({ length: bars }).map((_, i) => (
          <SkeletonBase key={i} className="flex-1" style={{ height: `${30 + ((i * 37) % 55)}%` }} />
        ))}
      </div>
    </div>
  )
}

function SkeletonKpi({ className = undefined }: { className?: string }) {
  return (
    <div className={cx('flex flex-col gap-3', className)} aria-hidden="true">
      <SkeletonBase className="h-2.5 w-24" />
      <SkeletonBase className="h-8 w-32" />
      <SkeletonBase className="h-1.5 w-full" />
    </div>
  )
}

const Skeleton = Object.assign(SkeletonBase, {
  Text: SkeletonText,
  Title: SkeletonTitle,
  Avatar: SkeletonAvatar,
  Card: SkeletonCard,
  Rows: SkeletonRows,
  Table: SkeletonTable,
  Chart: SkeletonChart,
  Kpi: SkeletonKpi,
})

export default Skeleton
