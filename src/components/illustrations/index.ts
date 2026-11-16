/**
 * Illustration barrel. Import figures from here:
 *   import { EmptyBoards, EMPTY_FIGURES } from '../components/illustrations'
 * Every figure takes `{ className?, title?, compact? }` (see kit/index.tsx).
 */
export type { FigProps } from './kit'
export { default as HeroIllustration } from './HeroIllustration'
export { default as QueryWorkspace } from './QueryWorkspace'
export { default as DashboardCanvas } from './DashboardCanvas'
export { default as KernelInBrowser } from './KernelInBrowser'
export { default as EdgeCache } from './EdgeCache'
export { default as WebGLPerf } from './WebGLPerf'
export { default as EmbedAuth } from './EmbedAuth'
export { default as LlmDashboards } from './LlmDashboards'
export { default as ConnectorSdk } from './ConnectorSdk'
export { default as FlowOrchestration } from './FlowOrchestration'
export { default as OpenCoreSplit } from './OpenCoreSplit'
export { default as SelfHostTopology } from './SelfHostTopology'
export { default as TrustBoundary } from './TrustBoundary'
export { default as AuthPoster } from './AuthPoster'
export { default as NotFoundFigure } from './NotFoundFigure'
export { default as OnboardingSteps } from './OnboardingSteps'
export { default as OAuthConsentFlow } from './OAuthConsentFlow'
export { default as MeteringWallet } from './MeteringWallet'
export { default as CostVsViewers } from './CostVsViewers'
export { default as BridgeTopology } from './BridgeTopology'
export { default as CliTerminal } from './CliTerminal'
export { default as SecretsVault } from './SecretsVault'
export { default as RlsBoundary } from './RlsBoundary'
export {
  EmptyBoards, EmptyQueries, EmptyConnectors, EmptyData, EmptyFlows, EmptyWatches, EmptySearch, ErrorFigure,
  EMPTY_FIGURES,
} from './EmptyFigures'
export type { EmptyFigureName } from './EmptyFigures'
