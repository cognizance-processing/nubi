/**
 * Allowlist for an image widget's `<img src>`. Apply AFTER resolveImageUrl()
 * (`safeImageSrc(resolveImageUrl(url))`) so both deployment shapes are covered:
 * a stored image resolves to either "/api/v1/images/{id}" (same-origin BASE) or
 * "https://backend/api/v1/images/{id}" (VITE_BACKEND_URL set).
 *
 * Accepts only absolute http(s) URLs and the exact stored-image path (a bare
 * uuid4().hex id — same shape as svg_render.py's _LOCAL_IMAGE_URL_RE).
 * Anything else — javascript:, data:, vbscript:, protocol-relative, other
 * relative paths, empty — is null.
 */

// eslint-disable-next-line no-control-regex
const CONTROL_CHARS = /[\u0000-\u001f\u007f]/;
const LOCAL_IMAGE_PATH = /^\/api\/v1\/images\/[0-9a-fA-F]{32}$/;

export function safeImageSrc(resolved: string): string | null {
  const url = String(resolved ?? "").trim();
  if (!url || CONTROL_CHARS.test(url)) return null;
  if (LOCAL_IMAGE_PATH.test(url)) return url;
  if (!/^https?:\/\//i.test(url)) return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "http:" || parsed.protocol === "https:")
      return parsed.toString();
  } catch {
    /* not a valid absolute URL */
  }
  return null;
}
