/**
 * Brand — a third-party mark for marketing and auth surfaces. A thin wrapper
 * over the shared <ToolLogo/> (official marks, one registry); it never draws a
 * mark by hand. An unknown slug renders the vendor's name.
 */
import ToolLogo from '../brand/ToolLogo.jsx'

export default function Brand({
  slug, name, mode = 'mono', size = 16, decorative = true, className,
}: {
  slug: string
  name: string
  mode?: 'mono' | 'brand'
  size?: number
  /** True when the vendor name is printed beside the mark. */
  decorative?: boolean
  className?: string
}) {
  return <ToolLogo slug={slug} mode={mode} size={size} title={name} decorative={decorative} className={className} />
}
