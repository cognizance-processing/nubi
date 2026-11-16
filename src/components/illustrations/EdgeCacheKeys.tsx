import { makeLiveFigure } from './LiveFigure'

/** Interactive: viewers by security scope → content-hashed cache keys → warehouse. */
const EdgeCacheKeys = makeLiveFigure(async () => {
  const [markup, fig] = await Promise.all([import('./figures/edgeCacheKeys.markup'), import('./figures/edgeCacheKeys.js'), import('./figures/figures.css')])
  return { markup: markup.default, mount: fig.mount }
}, 'Edge cache keys', 620)
export default EdgeCacheKeys
