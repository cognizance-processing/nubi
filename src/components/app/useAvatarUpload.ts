/**
 * useAvatarUpload — parent-side upload driver for AvatarField (see that file).
 */

import { useState } from 'react'
import { uploadImage } from '../../lib/api.js'

/**
 * Wrap a parent's avatar state setter so AvatarField's file picks are uploaded.
 *
 * AvatarField emits a `{ _file, _preview }` object for a picked file; storing
 * that as-is would show a blob: preview that never reaches the server. This
 * hook shows the preview, uploads the file, then replaces it with the served
 * URL (or restores the previous value if the upload fails).
 *
 * @param {string|null} value  current avatar URL
 * @param {(url: string|null) => void} setValue
 * @returns {{ onChange: (v: any) => void, uploading: boolean, uploadError: string|null }}
 */
export function useAvatarUpload(value, setValue) {
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState(null)

  async function onChange(next) {
    setUploadError(null)
    if (!next || typeof next === 'string') {
      setValue(next)
      return
    }
    const { _file: file, _preview: preview } = next
    setValue(preview)
    setUploading(true)
    try {
      const { url } = await uploadImage(file)
      setValue(url)
    } catch (err) {
      setValue(value)
      setUploadError(err?.message ?? 'Failed to upload photo.')
    } finally {
      setUploading(false)
      URL.revokeObjectURL(preview)
    }
  }

  return { onChange, uploading, uploadError }
}
