/** A labelled placeholder scene used by the registry when a scene module is missing. */
import { kit } from './kit'
import { TONE, type SceneId, type SceneModule } from './types'

const INDEX: Record<SceneId, string> = { hero: '00', columns: '01', cache: '02', security: '03', board: '04', agents: '05' }

export function placeholderScene(id: SceneId): SceneModule<Record<string, never>> {
  const layout = () => kit.merge(kit.pane({ id: 'pane', at: [0, 0, 0], w: 30, h: 20, tone: TONE.SHEET }), kit.placeholder({ id: 'slot', at: [-12, 3, 0.3], w: 24, h: 14 }))
  return {
    id, index: INDEX[id], kicker: id.toUpperCase(),
    copy: { title: `${id} (placeholder)`, body: 'This scene module has not landed yet.', try: '', spec: { href: '/docs' } },
    summary: `Placeholder for the ${id} scene.`,
    initialState: {},
    in: id === 'hero' ? undefined : { kind: 'rise', len: 1, spread: 0.5 },
    beats: [{ id: 'main', caption: 'Placeholder', len: 0.6, camera: { az: -0.3, el: 0.25, target: [0, 10, 0], zoom: 1 }, layout }],
    callouts: [], surface: [], controls: [], poster: 'main',
  }
}
