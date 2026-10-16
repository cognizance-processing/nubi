import { makeLiveFigure } from './LiveFigure'

/** Interactive: one dashboard view, warehouse → edge → tabs, in five steps. */
const ViewAnatomy = makeLiveFigure(async () => {
  const [markup, fig] = await Promise.all([import('./figures/viewAnatomy.markup'), import('./figures/viewAnatomy.js'), import('./figures/figures.css')])
  return { markup: markup.default, mount: fig.mount }
}, 'View anatomy', 760)
export default ViewAnatomy
