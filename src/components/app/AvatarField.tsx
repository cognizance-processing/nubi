/**
 * AvatarField — controlled avatar picker.
 *
 * Props:
 *   value        {string|null}  current avatar URL (controlled)
 *   onChange     {(url:string|null) => void}  called with the new URL
 *   fallbackName {string}       used to render initials when no avatar is set
 *   googleUrl    {string|null}  when present, shows a "Use Google photo" affordance
 *   uploading    {boolean}      pass true while a parent-driven upload is in flight
 *
 * The component is deliberately thin — it does NOT call the API itself.
 * The parent (SettingsPage) drives the PATCH /me or PATCH /orgs/{id} call.
 * File upload emits a File object via onChange so the parent can POST it and
 * then call onChange again with the returned URL.
 *
 * Local file handling: when a file is picked, we create a temporary object URL
 * for the preview AND emit the File via onChange so the parent can upload.
 * The parent should revoke the object URL once the real URL comes back.
 */

import { useRef, useState } from 'react'
import { Camera, Link2, X, Loader2, CheckCircle } from 'lucide-react'
import Button from '../ui/Button.jsx'
import { ToolLogo } from '../brand/index.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Derive initials from a display name (up to two letters).
 * @param {string} name
 * @returns {string}
 */
function initials(name = '') {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

// ---------------------------------------------------------------------------
// AvatarPreview — the circular avatar / initials display
// ---------------------------------------------------------------------------

function AvatarPreview({ src, fallbackName, size = 80 }) {
  const [imgError, setImgError] = useState(false)

  if (src && !imgError) {
    return (
      <img
        src={src}
        alt={fallbackName || 'Avatar'}
        width={size}
        height={size}
        className="rounded-full object-cover"
        style={{ width: size, height: size }}
        onError={() => setImgError(true)}
      />
    )
  }

  // Initials fallback
  const label = initials(fallbackName)
  return (
    <div
      className="rounded-full flex items-center justify-center font-semibold select-none"
      style={{
        width: size,
        height: size,
        fontSize: size * 0.32,
        background: 'var(--ink)',
        color: 'var(--ink-fg)',
      }}
      aria-label={fallbackName || 'Avatar'}
    >
      {label}
    </div>
  )
}

// ---------------------------------------------------------------------------
// AvatarField (default export)
// ---------------------------------------------------------------------------

/**
 * @param {{
 *   value: string|null,
 *   onChange: (urlOrFile: string|File|null) => void,
 *   fallbackName?: string,
 *   googleUrl?: string|null,
 *   uploading?: boolean,
 * }} props
 */
export default function AvatarField({
  value,
  onChange,
  fallbackName = '',
  googleUrl = null,
  uploading = false,
}) {
  const fileInputRef = useRef(null)
  const [urlMode, setUrlMode] = useState(false)
  const [urlDraft, setUrlDraft] = useState('')
  const [urlError, setUrlError] = useState(null)

  // ---- URL entry -----------------------------------------------------------

  function handleUrlApply() {
    const trimmed = urlDraft.trim()
    if (!trimmed) {
      setUrlError('Please enter a URL.')
      return
    }
    try {
      new URL(trimmed)
    } catch {
      setUrlError('That does not look like a valid URL.')
      return
    }
    setUrlError(null)
    onChange(trimmed)
    setUrlMode(false)
    setUrlDraft('')
  }

  function handleUrlKeyDown(e) {
    if (e.key === 'Enter') { e.preventDefault(); handleUrlApply() }
    if (e.key === 'Escape') { setUrlMode(false); setUrlDraft('') }
  }

  // ---- File pick -----------------------------------------------------------

  function handleFilePick(e) {
    const file = e.target.files?.[0]
    if (!file) return
    // Emit the File so the parent can upload it; parent will call onChange(url)
    // once the upload resolves. We preview optimistically via an object URL.
    const preview = URL.createObjectURL(file)
    // Emit preview URL first so the image shows immediately
    onChange(preview)
    // Then emit the File object tagged on the same call via a second cb.
    // Convention: if onChange receives a File, parent handles upload.
    // We use a separate prop-pattern: emit { _file: File, preview } object.
    // Actually, simplest: call onChange twice — first with preview, then with File.
    // To stay truly controlled and not double-render with conflicts we instead
    // pass the file wrapped so parent can distinguish:
    onChange({ _file: file, _preview: preview })
    // Reset the input so the same file can be re-selected
    e.target.value = ''
  }

  // ---- Clear ---------------------------------------------------------------

  function handleClear() {
    onChange(null)
  }

  // ---- Google photo --------------------------------------------------------

  function handleUseGoogle() {
    if (googleUrl) onChange(googleUrl)
  }

  // --------------------------------------------------------------------------

  return (
    <div className="flex items-start gap-5">
      {/* Avatar circle with optional upload overlay */}
      <div className="relative shrink-0 group">
        <AvatarPreview src={value} fallbackName={fallbackName} size={80} />

        {/* Camera overlay on hover */}
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading}
          aria-label="Upload photo"
          className="
            absolute inset-0 rounded-full
            flex items-center justify-center
            bg-[var(--sh-scrim)] text-white
            opacity-0 group-hover:opacity-100 focus-visible:opacity-100
            transition-opacity duration-100
            disabled:cursor-not-allowed
            focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface
          "
        >
          {uploading
            ? <Loader2 size={20} strokeWidth={1.5} className="animate-spin" aria-hidden="true" />
            : <Camera size={20} strokeWidth={1.5} aria-hidden="true" />
          }
        </button>

        {/* Hidden file input */}
        <input
          ref={fileInputRef}
          type="file"
          accept="image/jpeg,image/png,image/webp,image/gif"
          className="hidden"
          onChange={handleFilePick}
        />
      </div>

      {/* Controls */}
      <div className="flex-1 min-w-0 space-y-2 pt-1">
        {/* URL entry area */}
        {urlMode ? (
          <div className="space-y-1.5">
            <div className="flex gap-2">
              <input
                type="url"
                autoFocus
                value={urlDraft}
                onChange={e => { setUrlDraft(e.target.value); setUrlError(null) }}
                onKeyDown={handleUrlKeyDown}
                placeholder="https://example.com/photo.jpg"
                aria-label="Photo URL"
                className="
                  flex-1 h-8 px-3 rounded-sm text-sm
                  bg-surface border border-[var(--frame-line)] text-fg placeholder:text-subtle
                  hover:border-[var(--ink)] focus:border-[var(--ink)]
                "
              />
              <Button
                type="button"
                onClick={handleUrlApply}
                size="sm"
              >
                <CheckCircle size={14} strokeWidth={1.5} aria-hidden="true" />
                Apply
              </Button>
              <Button
                type="button"
                onClick={() => { setUrlMode(false); setUrlDraft(''); setUrlError(null) }}
                variant="secondary"
                size="icon-sm"
                aria-label="Cancel URL entry"
              >
                <X size={14} strokeWidth={1.5} aria-hidden="true" />
              </Button>
            </div>
            {urlError && (
              <p className="text-xs text-danger-fg" role="alert">{urlError}</p>
            )}
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="secondary" size="sm" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
              <Camera size={14} strokeWidth={1.5} aria-hidden="true" />
              Upload photo
            </Button>

            <Button type="button" variant="secondary" size="sm" onClick={() => setUrlMode(true)}>
              <Link2 size={14} strokeWidth={1.5} aria-hidden="true" />
              Set URL
            </Button>

            {/* Use Google photo (shown only when googleUrl is available and different) */}
            {googleUrl && googleUrl !== value && (
              <Button type="button" variant="secondary" size="sm" onClick={handleUseGoogle}>
                <ToolLogo slug="google" mode="brand" size={14} decorative />
                Use Google photo
              </Button>
            )}

            {value && (
              <Button type="button" variant="secondary" size="sm" onClick={handleClear}>
                <X size={14} strokeWidth={1.5} aria-hidden="true" />
                Remove
              </Button>
            )}
          </div>
        )}

        <p className="text-xs text-muted">
          JPG, PNG, WebP or GIF. Stored and served from your own domain.
        </p>
      </div>
    </div>
  )
}
