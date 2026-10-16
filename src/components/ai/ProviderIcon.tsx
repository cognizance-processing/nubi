/**
 * ProviderIcon - an LLM provider's official mark on a square hairline tile.
 *
 * Used by ModelPicker and Settings > AI providers. The marks are the vendors'
 * own SVGs rendered by ToolLogo (see src/components/brand/registry.ts and
 * public/logos/SOURCES.md); nothing is drawn here. Anthropic and OpenAI render
 * in ink (light variant in dark), Gemini in its brand colour. A provider with
 * no registered mark shows its initial in mono rather than an invented symbol.
 */

import ToolLogo from '../brand/ToolLogo'
import { getToolLogo } from '../brand/registry'

/**
 * @param {{
 *   provider: string,        // 'anthropic' | 'openai' | 'gemini' | 'ollama' | any other id
 *   size?: number,           // mark size in px; the tile is larger
 *   mode?: 'brand' | 'mono',
 *   className?: string,
 * }} props
 */
export default function ProviderIcon({ provider, size = 20, mode = 'brand' as 'brand' | 'mono', className = '' }) {
  const logo = getToolLogo(provider)
  const box = size >= 16 ? size + 12 : Math.round(size * 1.6)

  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center border border-border bg-surface text-fg ${className}`}
      style={{ width: box, height: box, borderRadius: 2 }}
      {...(logo ? {} : { 'aria-hidden': true })}
    >
      {logo ? (
        <ToolLogo slug={logo.slug} mode={mode} size={size} title={`${logo.name} logo`} />
      ) : (
        <span className="font-mono text-[11px] font-medium uppercase leading-none text-muted">
          {(provider || '?').slice(0, 1)}
        </span>
      )}
    </span>
  )
}
