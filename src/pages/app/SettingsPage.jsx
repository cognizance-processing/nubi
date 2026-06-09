/**
 * SettingsPage — redirects to /settings/profile.
 *
 * The Settings section is now a multi-page layout:
 *   /settings/profile       → ProfileSettings
 *   /settings/organization  → OrgSettings
 *   /settings/project       → ProjectSettings
 *   /settings/security      → SecuritySettings
 *
 * This file is kept for any legacy imports but the routing tree in App.jsx
 * uses SettingsLayout + nested routes directly.  Navigating to /settings
 * fires the index redirect → /settings/profile.
 */

import { Navigate } from 'react-router-dom'

export default function SettingsPage() {
  return <Navigate to="/settings/profile" replace />
}
