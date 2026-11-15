/**
 * Which image sources may be rendered inside a chat transcript.
 *
 * Chat text can contain content the assistant copied from data it read (a
 * query result, a document, a third-party tool). A remote image URL in that
 * text would make the reader's browser call a third party — and the URL itself
 * can carry data out. So chat renders an image only when it is served by this
 * origin or is an inline raster `data:` image.
 */
const INLINE_RASTER = /^data:image\/(png|jpe?g|gif|webp|avif|bmp);base64,[a-z0-9+/=\s]+$/i

export function isSafeChatImageSrc(src: unknown, origin?: string): boolean {
  if (typeof src !== 'string') return false
  const value = src.trim()
  if (!value) return false
  if (INLINE_RASTER.test(value)) return true
  if (/^data:/i.test(value)) return false
  // Backslashes are treated as slashes by browsers: "/\host" is a remote URL.
  if (value.includes('\\')) return false
  // A root-relative path on this origin (but not protocol-relative "//host").
  if (value.startsWith('/') && !value.startsWith('//')) return true
  const base = origin ?? (typeof window !== 'undefined' ? window.location.origin : undefined)
  if (!base) return false
  try {
    const url = new URL(value)
    return (url.protocol === 'https:' || url.protocol === 'http:') && url.origin === new URL(base).origin
  } catch {
    return false
  }
}
