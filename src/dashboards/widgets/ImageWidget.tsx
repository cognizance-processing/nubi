/**
 * ImageWidget.jsx — a static image block (logo, header art, branding).
 *
 * widget shape (type === 'image'):
 *   {
 *     id, type: 'image',
 *     props: {
 *       url: string,          // backend-relative, e.g. "/api/v1/images/{id}"
 *                             // (from uploadImage/uploadImageFromUrl/the
 *                             // upload_image AI tool) — resolved via
 *                             // resolveImageUrl() before use as <img src>.
 *                             // A full http(s):// URL is passed through as-is.
 *       alt?: string,
 *       fit?: 'contain' | 'cover',   // default 'contain'
 *       align?: 'left' | 'center' | 'right',  // default 'center'
 *     },
 *     pos
 *   }
 *
 * Purely presentational — no query, mirrors SectionWidget/TextWidget's shape.
 */

import { resolveImageUrl } from '../../lib/api'
import EmptyState from '../../components/ui/EmptyState.jsx'

export default function ImageWidget({ widget }) {
  const props = widget.props ?? {}
  const url = props.url ?? ''
  const alt = props.alt ?? ''
  const fit = props.fit === 'cover' ? 'cover' : 'contain'
  const align = props.align === 'left' ? 'justify-start' : props.align === 'right' ? 'justify-end' : 'justify-center'

  if (!url) {
    return (
      <div className="flex items-center justify-center h-full px-4">
        <EmptyState title="No image set" description="Upload a file or paste a URL." compact />
      </div>
    )
  }

  return (
    <div className={`flex items-center h-full w-full p-2 ${align}`}>
      <img
        src={resolveImageUrl(url)}
        alt={alt}
        style={{ maxHeight: '100%', maxWidth: '100%', objectFit: fit }}
      />
    </div>
  )
}
