/**
 * docMap — names → illustration components for use inside docs markdown.
 *
 * Docs embed a brand illustration with an image whose src uses the
 * ``illustration:`` scheme, e.g.:
 *
 *     ![Write SQL, see results instantly](illustration:QueryWorkspace)
 *
 * MarkdownRenderer intercepts that src and renders the mapped component (the
 * same kit-drawn, light/dark-safe SVGs used on the landing + gallery) instead
 * of a raw <img>. Keeping this as a fixed allowlist avoids rendering arbitrary
 * HTML/SVG from markdown.
 *
 * Placement, one picture per idea (the markdown owns which name each page uses):
 *   quickstart                     HeroIllustration
 *   queries-and-params             QueryWorkspace          dashboards          DashboardCanvas
 *   flows                          FlowOrchestration       connectors          ConnectorSdk
 *   sdk-and-cli                    CliTerminal             ai-and-mcp          LlmDashboards
 *   bridges                        BridgeTopology          self-host           SelfHostTopology
 *   kernel-security                KernelInBrowser         secrets, connector-security  SecretsVault
 *   embedding (intro)              EmbedAuth               embedding (security) RlsBoundary
 *   trust model overview           TrustBoundary           open-core pages     OpenCoreSplit
 *   cache-key-spec                 EdgeCache + EdgeCacheKeys (live)
 *   chart-types, performance       WebGLPerf               pre-aggregations    EdgeCache
 *   architecture-and-economics     ViewAnatomy (live)
 *
 * The three live figures are lazy: their code (three.js for two of them) is only
 * fetched when the figure is about to scroll into view (see LiveFigure).
 */
import HeroIllustration from './HeroIllustration'
import QueryWorkspace from './QueryWorkspace'
import DashboardCanvas from './DashboardCanvas'
import KernelInBrowser from './KernelInBrowser'
import EdgeCache from './EdgeCache'
import WebGLPerf from './WebGLPerf'
import EmbedAuth from './EmbedAuth'
import LlmDashboards from './LlmDashboards'
import ConnectorSdk from './ConnectorSdk'
import FlowOrchestration from './FlowOrchestration'
import OpenCoreSplit from './OpenCoreSplit'
import SelfHostTopology from './SelfHostTopology'
import TrustBoundary from './TrustBoundary'
import BridgeTopology from './BridgeTopology'
import CliTerminal from './CliTerminal'
import SecretsVault from './SecretsVault'
import RlsBoundary from './RlsBoundary'
import ViewAnatomy from './ViewAnatomy'
import EdgeCacheKeys from './EdgeCacheKeys'
import LiveInstrument from './LiveInstrument'

export const DOC_ILLUSTRATIONS = {
  HeroIllustration,
  QueryWorkspace,
  DashboardCanvas,
  KernelInBrowser,
  EdgeCache,
  WebGLPerf,
  EmbedAuth,
  LlmDashboards,
  ConnectorSdk,
  FlowOrchestration,
  OpenCoreSplit,
  SelfHostTopology,
  TrustBoundary,
  BridgeTopology,
  CliTerminal,
  SecretsVault,
  RlsBoundary,
  // Interactive (live) figures — full content width, see LiveFigure
  ViewAnatomy,
  EdgeCacheKeys,
  LiveInstrument,
}

/** Parse an `illustration:Name` src; returns the component or null. */
export function resolveDocIllustration(src) {
  if (typeof src !== 'string') return null
  const m = /^illustration:([A-Za-z0-9_]+)$/.exec(src.trim())
  if (!m) return null
  return DOC_ILLUSTRATIONS[m[1]] ?? null
}
