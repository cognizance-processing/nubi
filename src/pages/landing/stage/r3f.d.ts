/**
 * Minimal local types for @react-three/fiber, imported as '@nubi/r3f' (aliased in vite.config.js).
 * Importing R3F's own declarations would add three.js elements to the global JSX namespace and break
 * polymorphic `as` props elsewhere in the app, so the stage adds three objects imperatively instead of JSX.
 */
declare module '@nubi/r3f' {
  import type { CSSProperties, ReactNode, JSX } from 'react'
  import type { Camera, Scene, WebGLRenderer } from 'three'
  export interface RootStateLite {
    gl: WebGLRenderer
    camera: Camera
    scene: Scene
    size: { width: number; height: number; top: number; left: number }
    viewport: { dpr: number }
    invalidate(frames?: number): void
    setDpr(dpr: number | [number, number]): void
  }
  export interface CanvasProps {
    children?: ReactNode
    orthographic?: boolean
    legacy?: boolean
    linear?: boolean
    flat?: boolean
    frameloop?: 'always' | 'demand' | 'never'
    dpr?: number | [number, number]
    camera?: Record<string, unknown>
    gl?: Record<string, unknown>
    style?: CSSProperties
    className?: string
    'aria-hidden'?: boolean | 'true' | 'false'
    onCreated?(state: RootStateLite): void
  }
  export function Canvas(props: CanvasProps): JSX.Element
  export function useThree(): RootStateLite
  export function useFrame(cb: (state: RootStateLite, delta: number) => void, priority?: number): null
}
