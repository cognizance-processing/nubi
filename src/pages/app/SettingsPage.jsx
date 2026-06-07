/**
 * SettingsPage — stub placeholder inside the AppShell.
 * A sibling wave will replace this with real account/org settings.
 */

import { Settings } from 'lucide-react'

export default function SettingsPage() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] px-6 text-center">
      <div
        className="flex items-center justify-center w-14 h-14 rounded-2xl mb-5"
        style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
      >
        <Settings size={24} className="text-white" />
      </div>
      <h1 className="font-display font-semibold text-2xl text-fg mb-2">Settings</h1>
      <p className="text-muted text-sm max-w-xs leading-relaxed">
        Account preferences, API keys, and organisation settings will appear here.
      </p>
    </div>
  )
}
