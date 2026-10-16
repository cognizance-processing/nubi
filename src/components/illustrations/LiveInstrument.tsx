import { makeLiveFigure } from './LiveFigure'

/** Interactive: a 3D line-drawn bar chart re-aggregating 1M rows in the browser. */
const LiveInstrument = makeLiveFigure(async () => {
  const [markup, fig] = await Promise.all([import('./figures/instrument.markup'), import('./figures/instrument.js'), import('./figures/figures.css')])
  return { markup: markup.default, mount: fig.mount }
}, 'Live instrument', 640)
export default LiveInstrument
