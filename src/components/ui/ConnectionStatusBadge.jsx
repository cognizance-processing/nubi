/**
 * ConnectionStatusBadge — one visual language for "is this thing reachable",
 * shared by Connectors, Settings -> Bridges, and Settings -> MCP servers.
 *
 * Usage:
 *   <ConnectionStatusBadge state="online" detail="Bridge “prod-vpc” online" />
 */

import Badge from './Badge.jsx'
import { statusBadgeProps } from '../../lib/connectionStatus.js'

export default function ConnectionStatusBadge({ state, detail = undefined, size = 'sm', className = undefined }) {
  const { variant, label } = statusBadgeProps(state)
  return (
    <Badge variant={variant} dot size={size} title={detail || label} className={className}>
      {label}
    </Badge>
  )
}
