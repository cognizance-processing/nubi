/**
 * ProfileSettings — manage your personal profile.
 *
 * Allows updating display name and avatar.
 * The avatar defaults to the Google OAuth picture; the user can override
 * it by providing a custom URL or uploading a file (handled by AvatarField).
 *
 * Calls: PATCH /auth/me via settings.js#updateMe
 */

import { useState } from 'react'
import { User, Loader2, CheckCircle } from 'lucide-react'
import { useAuth } from '../../../contexts/AuthContext.jsx'
import AvatarField from '../../../components/app/AvatarField.jsx'
import { updateMe } from '../../../lib/settings.js'

export default function ProfileSettings() {
  const { user } = useAuth()

  const [name, setName] = useState(user?.name ?? '')
  const [avatarUrl, setAvatarUrl] = useState(user?.avatar_url ?? '')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(null)

  async function handleSave(e) {
    e.preventDefault()
    setSaving(true)
    setSaved(false)
    setError(null)
    try {
      await updateMe({
        name: name.trim() || undefined,
        avatar_url: avatarUrl || undefined,
      })
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch (err) {
      setError(err?.message ?? 'Failed to save profile.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Section header */}
      <div className="flex items-start gap-4 pb-5 border-b border-border">
        <div
          className="flex items-center justify-center w-10 h-10 rounded-xl shrink-0"
          style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
        >
          <User size={18} className="text-white" />
        </div>
        <div>
          <h2 className="font-display font-semibold text-base text-fg">Your profile</h2>
          <p className="text-sm text-muted mt-0.5">
            Update your display name and the avatar other members see.
            Your email address cannot be changed here.
          </p>
        </div>
      </div>

      <form onSubmit={handleSave} className="space-y-6 max-w-md">
        {/* Avatar */}
        <div className="space-y-1.5">
          <label className="block text-xs font-medium text-muted">Avatar</label>
          <AvatarField
            value={avatarUrl || user?.avatar_url || ''}
            onChange={setAvatarUrl}
            fallbackName={name || user?.email || '?'}
          />
          {!avatarUrl && user?.avatar_url && (
            <p className="text-xs text-muted">
              Using your Google profile picture. Set a custom URL or upload a file to override it.
            </p>
          )}
        </div>

        {/* Display name */}
        <div className="space-y-1.5">
          <label className="block text-xs font-medium text-muted" htmlFor="profile-name">
            Display name
          </label>
          <input
            id="profile-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Your name"
            className="w-full px-3 py-2 rounded-xl bg-bg border border-border text-sm text-fg placeholder:text-muted focus:outline-none focus:border-primary"
          />
        </div>

        {/* Email (read-only) */}
        <div className="space-y-1.5">
          <label className="block text-xs font-medium text-muted">Email address</label>
          <div className="px-3 py-2 rounded-xl bg-bg/60 border border-border text-sm text-muted select-all">
            {user?.email ?? '—'}
          </div>
        </div>

        {/* Save */}
        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={saving}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium text-white transition-opacity disabled:opacity-50"
            style={{ background: 'linear-gradient(135deg, #2456a6, #17b3a3)' }}
          >
            {saving ? <Loader2 size={15} className="animate-spin" /> : null}
            Save profile
          </button>
          {saved && (
            <span className="inline-flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400">
              <CheckCircle size={15} />
              Saved
            </span>
          )}
        </div>

        {error && (
          <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
        )}
      </form>
    </div>
  )
}
