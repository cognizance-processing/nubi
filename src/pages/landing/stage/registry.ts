/**
 * Scene registry: the story order and the scene modules. Scene folders under ../scenes/<id>/index.ts are
 * picked up automatically; a missing folder falls back to a labelled placeholder so the page always runs.
 * To move agents after Live, change ORDER only.
 */
import type { SceneId, SceneModule } from './types'
import { placeholderScene } from './placeholder'

export const ORDER: SceneId[] = ['hero', 'columns', 'cache', 'security', 'board', 'agents']

const found = import.meta.glob<{ default: SceneModule<any> }>('../scenes/*/index.ts', { eager: true })

export const SCENES: SceneModule<any>[] = ORDER.map(id => found[`../scenes/${id}/index.ts`]?.default ?? placeholderScene(id))
export const sceneById = (id: string) => SCENES.find(s => s.id === id)
