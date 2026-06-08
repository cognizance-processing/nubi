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
      className="rounded-full flex items-center justify-center font-display font-semibold text-white select-none"
      style={{
        width: size,
        height: size,
        fontSize: size * 0.32,
        background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)',
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
            bg-black/50 text-white
            opacity-0 group-hover:opacity-100
            transition-opacity duration-150
            disabled:cursor-not-allowed
          "
        >
          {uploading
            ? <Loader2 size={20} className="animate-spin" />
            : <Camera size={20} />
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
                className="
                  flex-1 px-3 py-1.5 rounded-xl text-sm
                  bg-bg border border-border text-fg placeholder:text-muted
                  focus:outline-none focus:border-primary
                "
              />
              <button
                type="button"
                onClick={handleUrlApply}
                className="
                  inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-sm font-medium
                  text-white transition-opacity
                "
                style={{ background: 'linear-gradient(135deg, #2456a6, #17b3a3)' }}
              >
                <CheckCircle size={14} />
                Apply
              </button>
              <button
                type="button"
                onClick={() => { setUrlMode(false); setUrlDraft(''); setUrlError(null) }}
                className="p-1.5 rounded-xl border border-border text-muted hover:text-fg transition-colors"
                aria-label="Cancel URL entry"
              >
                <X size={14} />
              </button>
            </div>
            {urlError && (
              <p className="text-xs text-red-600 dark:text-red-400">{urlError}</p>
            )}
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            {/* Upload file */}
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              className="
                inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium
                border border-border text-muted hover:text-fg hover:bg-surface-2
                transition-colors disabled:opacity-50 disabled:cursor-not-allowed
              "
            >
              <Camera size={13} />
              Upload photo
            </button>

            {/* Set URL */}
            <button
              type="button"
              onClick={() => setUrlMode(true)}
              className="
                inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium
                border border-border text-muted hover:text-fg hover:bg-surface-2
                transition-colors
              "
            >
              <Link2 size={13} />
              Set URL
            </button>

            {/* Use Google photo (shown only when googleUrl is available and different) */}
            {googleUrl && googleUrl !== value && (
              <button
                type="button"
                onClick={handleUseGoogle}
                className="
                  inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium
                  border border-border text-muted hover:text-fg hover:bg-surface-2
                  transition-colors
                "
              >
                {/* Google colourful G mark, inline SVG */}
                <svg width="13" height="13" viewBox="0 0 48 48" aria-hidden="true">
                  <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
                  <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
                  <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
                  <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.18 1.48-4.97 2.31-8.16 2.31-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
                </svg>
                Use Google photo
              </button>
            )}

            {/* Clear */}
            {value && (
              <button
                type="button"
                onClick={handleClear}
                className="
                  inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium
                  border border-border text-muted hover:text-red-600 hover:border-red-300 hover:bg-red-50
                  dark:hover:bg-red-900/20
                  transition-colors
                "
              >
                <X size={13} />
                Remove
              </button>
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
