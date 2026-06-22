/**
 * EmptyState — Nubi zero-data / error / placeholder primitive.
 *
 * Usage:
 *   <EmptyState
 *     icon={<Inbox />}
 *     title="No results"
 *     description="Try adjusting your filters or search terms."
 *     action={<Button size="sm" onClick={clearFilters}>Clear filters</Button>}
 *   />
 *
 *   // Compact inline usage
 *   <EmptyState title="No items" compact />
 *
 * Props:
 *   icon          ReactNode — icon (Lucide icons work great)
 *   title         string
 *   description   string | ReactNode
 *   action        ReactNode — CTA button/link
 *   compact       boolean — smaller padding for inline contexts
 *   className
 */

function cx(...parts) {
  return parts.filter(Boolean).join(' ')
}

export default function EmptyState({
  icon,
  title,
  description,
  action,
  compact = false,
  className,
}) {
  return (
    <div className={cx('nubi-empty', compact && 'py-8 gap-2', className)}>
      {icon && (
        <div className="nubi-empty-icon" aria-hidden="true">
          {icon}
        </div>
      )}
      {title && <p className="nubi-empty-title">{title}</p>}
      {description && <p className="nubi-empty-body">{description}</p>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  )
}
