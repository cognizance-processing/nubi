/**
 * Skeleton — Nubi loading placeholder primitive.
 *
 * Usage:
 *   <Skeleton className="h-4 w-48" />
 *   <Skeleton.Text lines={3} />
 *   <Skeleton.Title className="w-64" />
 *   <Skeleton.Avatar size={40} />
 *   <Skeleton.Card />
 *
 * Props (base):
 *   className — width/height applied via Tailwind utilities
 *
 * Sub-components:
 *   Skeleton.Text   lines (number, default 3), lastLineWidth ('75%')
 *   Skeleton.Title  className
 *   Skeleton.Avatar size (number px, default 32)
 *   Skeleton.Card   rows (number of text rows, default 3)
 */

function cx(...parts) {
  return parts.filter(Boolean).join(' ')
}

function SkeletonBase({ className, ...rest }) {
  return (
    <div
      className={cx('nubi-skeleton', className)}
      aria-hidden="true"
      {...rest}
    />
  )
}

function SkeletonText({ lines = 3, lastLineWidth = '60%', className }) {
  return (
    <div className={cx('flex flex-col gap-2', className)} aria-hidden="true">
      {Array.from({ length: lines }).map((_, i) => (
        <SkeletonBase
          key={i}
          className="nubi-skeleton-text"
          style={i === lines - 1 ? { width: lastLineWidth } : undefined}
        />
      ))}
    </div>
  )
}

function SkeletonTitle({ className }) {
  return <SkeletonBase className={cx('nubi-skeleton-title', className)} aria-hidden="true" />
}

function SkeletonAvatar({ size = 32 }) {
  return (
    <SkeletonBase
      className="nubi-skeleton-avatar shrink-0"
      style={{ width: size, height: size }}
      aria-hidden="true"
    />
  )
}

function SkeletonCard({ rows = 3 }) {
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

const Skeleton = Object.assign(SkeletonBase, {
  Text: SkeletonText,
  Title: SkeletonTitle,
  Avatar: SkeletonAvatar,
  Card: SkeletonCard,
})

export default Skeleton
