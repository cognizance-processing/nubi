import { useState, useEffect } from 'react'
import { Settings, X } from 'lucide-react'

const DEFAULT_SETTINGS = {
    // AI Settings
    maxToolIterations: 10,
    temperature: 0.3,
    maxOutputTokens: 8192,
    autoRetryOnError: true,
    
    // UI Settings
    autoSaveInterval: 30, // seconds
    showThinkingByDefault: true,
    showCodeDiffByDefault: true,
    compactMode: false,
    
    // Editor Settings
    editorFontSize: 14,
    editorTabSize: 4,
    editorWordWrap: true,
}

export default function SettingsPanel() {
    const [isOpen, setIsOpen] = useState(false)
    const [settings, setSettings] = useState(DEFAULT_SETTINGS)
    const [activeTab, setActiveTab] = useState('ai') // 'ai', 'ui', 'editor'

    // Load settings from localStorage on mount
    useEffect(() => {
        const saved = localStorage.getItem('nubi_settings')
        if (saved) {
            try {
                setSettings({ ...DEFAULT_SETTINGS, ...JSON.parse(saved) })
            } catch (e) {
                console.error('Failed to load settings:', e)
            }
        }
    }, [])

    // Save settings to localStorage whenever they change
    useEffect(() => {
        localStorage.setItem('nubi_settings', JSON.stringify(settings))
        // Dispatch custom event so other components can react to settings changes
        window.dispatchEvent(new CustomEvent('nubi:settings-changed', { detail: settings }))
    }, [settings])

    const updateSetting = (key, value) => {
        setSettings(prev => ({ ...prev, [key]: value }))
    }

    const resetToDefaults = () => {
        if (confirm('Reset all settings to defaults?')) {
            setSettings(DEFAULT_SETTINGS)
        }
    }

    return (
        <>
            {/* Settings Button */}
            <button
                onClick={() => setIsOpen(true)}
                className="p-2 rounded-lg text-text-secondary hover:text-text-primary hover:bg-background-tertiary transition-all"
                title="Settings"
            >
                <Settings size={20} />
            </button>

            {/* Settings Modal */}
            {isOpen && (
                <>
                    <div className="fixed inset-0 bg-black/50 z-[70]" onClick={() => setIsOpen(false)} />
                    <div className="fixed right-4 top-4 w-[500px] max-h-[calc(100vh-2rem)] bg-background-secondary border border-border-primary rounded-xl shadow-2xl z-[71] flex flex-col">
                        {/* Header */}
                        <div className="flex items-center justify-between px-5 py-4 border-b border-border-primary">
                            <h2 className="text-lg font-semibold text-text-primary">Settings</h2>
                            <button
                                onClick={() => setIsOpen(false)}
                                className="p-1 rounded-lg text-text-secondary hover:text-text-primary hover:bg-background-tertiary transition-all"
                            >
                                <X size={18} />
                            </button>
                        </div>

                        {/* Tabs */}
                        <div className="flex gap-1 px-5 pt-4 border-b border-border-primary/50">
                            {[
                                { id: 'ai', label: 'AI Settings' },
                                { id: 'ui', label: 'Interface' },
                                { id: 'editor', label: 'Editor' },
                            ].map(tab => (
                                <button
                                    key={tab.id}
                                    onClick={() => setActiveTab(tab.id)}
                                    className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-all ${
                                        activeTab === tab.id
                                            ? 'text-accent-primary bg-background-tertiary border-b-2 border-accent-primary'
                                            : 'text-text-secondary hover:text-text-primary hover:bg-background-tertiary/50'
                                    }`}
                                >
                                    {tab.label}
                                </button>
                            ))}
                        </div>

                        {/* Content */}
                        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
                            {activeTab === 'ai' && (
                                <>
                                    <SettingItem
                                        label="Max Tool Iterations"
                                        description="Maximum number of AI function calls per request (higher = more thorough but slower)"
                                        type="number"
                                        value={settings.maxToolIterations}
                                        min={1}
                                        max={20}
                                        onChange={(value) => updateSetting('maxToolIterations', parseInt(value))}
                                    />
                                    <SettingItem
                                        label="Temperature"
                                        description="Creativity level (0 = deterministic, 1 = creative)"
                                        type="range"
                                        value={settings.temperature}
                                        min={0}
                                        max={1}
                                        step={0.1}
                                        onChange={(value) => updateSetting('temperature', parseFloat(value))}
                                        showValue
                                    />
                                    <SettingItem
                                        label="Max Output Tokens"
                                        description="Maximum length of AI responses"
                                        type="number"
                                        value={settings.maxOutputTokens}
                                        min={1024}
                                        max={16384}
                                        step={512}
                                        onChange={(value) => updateSetting('maxOutputTokens', parseInt(value))}
                                    />
                                    <SettingItem
                                        label="Auto-Retry on Error"
                                        description="Automatically attempt to fix errors once before prompting"
                                        type="toggle"
                                        value={settings.autoRetryOnError}
                                        onChange={(value) => updateSetting('autoRetryOnError', value)}
                                    />
                                </>
                            )}

                            {activeTab === 'ui' && (
                                <>
                                    <SettingItem
                                        label="Auto-Save Interval"
                                        description="Save changes every N seconds (0 = manual only)"
                                        type="number"
                                        value={settings.autoSaveInterval}
                                        min={0}
                                        max={300}
                                        step={5}
                                        onChange={(value) => updateSetting('autoSaveInterval', parseInt(value))}
                                    />
                                    <SettingItem
                                        label="Show AI Thinking"
                                        description="Display AI reasoning process by default"
                                        type="toggle"
                                        value={settings.showThinkingByDefault}
                                        onChange={(value) => updateSetting('showThinkingByDefault', value)}
                                    />
                                    <SettingItem
                                        label="Show Code Diffs"
                                        description="Display code changes with additions/deletions highlighted"
                                        type="toggle"
                                        value={settings.showCodeDiffByDefault}
                                        onChange={(value) => updateSetting('showCodeDiffByDefault', value)}
                                    />
                                    <SettingItem
                                        label="Compact Mode"
                                        description="Reduce spacing and padding for denser layout"
                                        type="toggle"
                                        value={settings.compactMode}
                                        onChange={(value) => updateSetting('compactMode', value)}
                                    />
                                </>
                            )}

                            {activeTab === 'editor' && (
                                <>
                                    <SettingItem
                                        label="Font Size"
                                        description="Editor font size in pixels"
                                        type="number"
                                        value={settings.editorFontSize}
                                        min={10}
                                        max={24}
                                        onChange={(value) => updateSetting('editorFontSize', parseInt(value))}
                                    />
                                    <SettingItem
                                        label="Tab Size"
                                        description="Number of spaces per tab"
                                        type="number"
                                        value={settings.editorTabSize}
                                        min={2}
                                        max={8}
                                        onChange={(value) => updateSetting('editorTabSize', parseInt(value))}
                                    />
                                    <SettingItem
                                        label="Word Wrap"
                                        description="Wrap long lines in the editor"
                                        type="toggle"
                                        value={settings.editorWordWrap}
                                        onChange={(value) => updateSetting('editorWordWrap', value)}
                                    />
                                </>
                            )}
                        </div>

                        {/* Footer */}
                        <div className="flex items-center justify-between px-5 py-4 border-t border-border-primary">
                            <button
                                onClick={resetToDefaults}
                                className="px-3 py-1.5 text-sm text-text-secondary hover:text-text-primary hover:bg-background-tertiary rounded-lg transition-all"
                            >
                                Reset to Defaults
                            </button>
                            <button
                                onClick={() => setIsOpen(false)}
                                className="px-4 py-1.5 text-sm font-medium bg-accent-gradient text-white rounded-lg hover:opacity-90 transition-all"
                            >
                                Done
                            </button>
                        </div>
                    </div>
                </>
            )}
        </>
    )
}

function SettingItem({ label, description, type, value, onChange, min, max, step, showValue }) {
    return (
        <div className="space-y-2">
            <div className="flex items-center justify-between">
                <label className="text-sm font-medium text-text-primary">{label}</label>
                {type === 'range' && showValue && (
                    <span className="text-sm text-text-muted font-mono">{value}</span>
                )}
            </div>
            <p className="text-xs text-text-muted leading-relaxed">{description}</p>
            
            {type === 'number' && (
                <input
                    type="number"
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                    min={min}
                    max={max}
                    step={step}
                    className="w-full px-3 py-2 bg-background-tertiary border border-border-primary rounded-lg text-sm text-text-primary focus:outline-none focus:border-accent-primary transition-all"
                />
            )}
            
            {type === 'range' && (
                <input
                    type="range"
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                    min={min}
                    max={max}
                    step={step}
                    className="w-full h-2 bg-background-tertiary rounded-lg appearance-none cursor-pointer accent-accent-primary"
                />
            )}
            
            {type === 'toggle' && (
                <button
                    onClick={() => onChange(!value)}
                    className={`relative w-12 h-6 rounded-full transition-all ${
                        value ? 'bg-accent-primary' : 'bg-background-tertiary border border-border-primary'
                    }`}
                >
                    <span
                        className={`absolute top-0.5 w-5 h-5 bg-white rounded-full transition-all ${
                            value ? 'left-6' : 'left-0.5'
                        }`}
                    />
                </button>
            )}
        </div>
    )
}

// Hook to access settings from any component
export function useSettings() {
    const [settings, setSettings] = useState(DEFAULT_SETTINGS)

    useEffect(() => {
        const saved = localStorage.getItem('nubi_settings')
        if (saved) {
            try {
                setSettings({ ...DEFAULT_SETTINGS, ...JSON.parse(saved) })
            } catch (e) {
                console.error('Failed to load settings:', e)
            }
        }

        const handleSettingsChange = (event) => {
            setSettings(event.detail)
        }

        window.addEventListener('nubi:settings-changed', handleSettingsChange)
        return () => window.removeEventListener('nubi:settings-changed', handleSettingsChange)
    }, [])

    return settings
}
