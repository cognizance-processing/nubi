/**
 * docMap.js — names → illustration components for use inside docs markdown.
 *
 * Docs embed a brand illustration with an image whose src uses the
 * ``illustration:`` scheme, e.g.:
 *
 *     ![Write SQL, see results instantly](illustration:QueryWorkspace)
 *
 * MarkdownRenderer intercepts that src and renders the mapped component (the
 * same verified, light/dark-safe SVGs used on the landing + gallery) instead
 * of a raw <img>. Keeping this as a fixed allowlist avoids rendering arbitrary
 * HTML/SVG from markdown.
 */
import HeroIllustration from './HeroIllustration.jsx'
import QueryWorkspace from './QueryWorkspace.jsx'
import DashboardCanvas from './DashboardCanvas.jsx'
import KernelInBrowser from './KernelInBrowser.jsx'
import EdgeCache from './EdgeCache.jsx'
import WebGLPerf from './WebGLPerf.jsx'
import EmbedAuth from './EmbedAuth.jsx'
import LlmDashboards from './LlmDashboards.jsx'
import ConnectorSdk from './ConnectorSdk.jsx'
import FlowOrchestration from './FlowOrchestration.jsx'
import OpenCoreSplit from './OpenCoreSplit.jsx'
import SelfHostTopology from './SelfHostTopology.jsx'
import LakehouseFlow from './LakehouseFlow.jsx'
import TrustBoundary from './TrustBoundary.jsx'

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
  LakehouseFlow,
  TrustBoundary,
}

/** Parse an `illustration:Name` src; returns the component or null. */
export function resolveDocIllustration(src) {
  if (typeof src !== 'string') return null
  const m = /^illustration:([A-Za-z0-9_]+)$/.exec(src.trim())
  if (!m) return null
  return DOC_ILLUSTRATIONS[m[1]] ?? null
}
