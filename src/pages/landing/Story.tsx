/**
 * The story (storyboard §1.1, §1.7, §1.8): one #story section.
 *   desktop: a CSS-sticky 100svh pin holding the stage, the label layer, six stacked caption plates (one active),
 *            the rail and the hero's proof strip; zero-height anchors per beat label give the scroll its length.
 *   mobile:  the pin is a sticky band under the header; chapters flow beneath it and each beat is a scroll step.
 * Copy is triggered (time-based swap), the 3D is scrubbed. Controls are DOM twins of every interaction.
 */
import { useCallback, useEffect, useId, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import type { Director } from './stage/director'
import type { Beat, Control, SceneModule } from './stage/types'
import { PROOF_BAND_MARKUP, PROOF_STRIP_MARKUP, WORKS_WITH_MARKUP } from './landingMarkup'

export interface StoryEnv { mobile: boolean; reduced: boolean; debug: boolean; ui: boolean }
export interface StoryApi { jumpToLabel(T: number, instant?: boolean): void; jumpToScene(si: number, instant?: boolean): void }

/** Re-render when the active scene, its current beat or any scene state changes (not on every scroll frame). */
export function useDirector(director: Director) {
  const sub = useCallback((f: () => void) => director.on(f), [director])
  const snap = useSyncExternalStore(sub, () => `${director.activeScene()}:${director.currentBeat()}:${director.rev}`)
  const [a, b] = snap.split(':').map(Number)
  return { active: a, beat: b }
}

const pad2 = (n: number) => String(n).padStart(2, '0')

/* ───────────── controls (DOM twins) ───────────── */

function Controls({ mod, si, director, announce }: { mod: SceneModule<any>; si: number; director: Director; announce: (s: string) => void }) {
  const s = director.sceneState(si)
  const apply = (c: Control<any>, v: string | number) => {
    const next = director.setSceneState(si, c.set(director.sceneState(si), v))
    if (c.announce) announce(c.announce(next))
  }
  const uid = useId()
  return (
    <div className="ctls">
      {mod.controls.map(c => {
        const id = `${uid}-${c.id}`
        if (c.kind === 'button') return <button key={c.id} type="button" className="btn sm" onClick={() => apply(c, c.get(s))}>{c.label}</button>
        if (c.kind === 'chips') return (
          <div key={c.id} className="chips" role="group" aria-label={c.label}>
            {c.options?.map(o => <button key={o.value} type="button" aria-pressed={String(c.get(s)) === o.value} onClick={() => apply(c, o.value)}>{o.label}</button>)}
          </div>)
        if (c.kind === 'range') return (
          <label key={c.id} className="rng" htmlFor={id}><span>{c.label}</span>
            <input id={id} type="range" min={c.min} max={c.max} step={c.step ?? 1} value={Number(c.get(s))} aria-valuetext={c.announce?.(s)}
              onChange={e => apply(c, +e.currentTarget.value)} />
            <b className="num">{c.get(s)}</b></label>)
        return (
          <label key={c.id} className="sel" htmlFor={id}><span className="sr">{c.label}</span>
            <select id={id} value={String(c.get(s))} onChange={e => apply(c, e.currentTarget.value)}>
              {c.options?.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select></label>)
      })}
    </div>
  )
}

/* ───────────── one chapter's copy (plate on desktop, flow on mobile) ───────────── */

function Html({ as: As = 'span', html, ...rest }: { as?: any; html: string; [k: string]: any }) {
  return <As {...rest} dangerouslySetInnerHTML={{ __html: html }} />
}

interface ChapterProps {
  mod: SceneModule<any>; si: number; total: number; director: Director; active: boolean; beat: number
  mobile: boolean; api: StoryApi; beatsId: string; out?: boolean
}

function Chapter({ mod, si, total, director, active, beat, mobile, api, beatsId, out }: ChapterProps) {
  const [msg, setMsg] = useState('')
  const s = director.sceneState(si)
  const hero = mod.id === 'hero'
  const title = typeof mod.copy.title === 'function' ? mod.copy.title(s) : mod.copy.title
  const code = typeof mod.copy.code === 'function' ? mod.copy.code(s) : mod.copy.code
  const labelOf = (b: number) => director.story.labels.find(l => l.scene === si && l.beat === b)!
  const H = hero ? 'h1' : 'h2'
  const headId = `chh-${mod.id}`
  let i = 0
  const st = () => ({ ['--i' as any]: i++ })
  const idx = hero
    ? <div key="idx" className="idx" style={st()}><b>NUBI</b><span>OPEN SOURCE · APACHE-2.0</span><span>{mod.index} / {pad2(total - 1)}</span></div>
    : <div key="idx" className="idx" style={st()}><b>{mod.index}</b><span>{mod.kicker}</span><span>{mod.index} / {pad2(total - 1)}</span></div>
  const beats = mod.beats as Beat<any>[]
  const beatList = !hero && !mobile && (
    <ol className="beats" id={beatsId}>
      {beats.map((b, k) => (
        <li key={b.id}><button type="button" aria-current={active && k === beat ? 'step' : undefined} onClick={() => api.jumpToLabel(labelOf(k).T)}>
          <span className="bi">{pad2(k + 1)}</span><span>{b.caption}</span></button></li>
      ))}
    </ol>
  )
  const heroHint = hero && !mobile && (
    <p className="hint" id={beatsId} aria-live="off"><span aria-hidden="true">↓ </span>{mod.copy.hint?.[beats[active ? beat : 0].id] ?? ''}</p>
  )
  const steps = mobile && (
    <ol className="steps" id={beatsId}>
      {beats.map((b, k) => (
        <li key={b.id} className="step" data-t={labelOf(k).T} aria-current={active && k === beat ? 'step' : undefined}>
          <span className="bi">{pad2(k + 1)}</span><p>{b.caption}</p>
          {hero && mod.copy.hint?.[b.id] ? <p className="hint">{mod.copy.hint[b.id]}</p> : null}
        </li>
      ))}
    </ol>
  )
  const facts = mod.copy.facts && (
    <div className={`facts${mod.copy.facts.length === 3 ? ' three' : ''}`}>
      {mod.copy.facts.map(([b, t]) => <div key={b + t}><b>{b}</b><span>{t}</span></div>)}
    </div>
  )
  const body: ReactNode[] = [
    idx,
    <Html key="h" as={H} id={headId} style={st()} html={title} />,
    <Html key="p" as="p" className={hero ? 'lead' : undefined} style={st()} html={mod.copy.body} />,
  ]
  if (hero) body.push(
    <div key="cta" className="ctas" style={st()}>
      <a className="btn pri" href="/register">Start free</a><a className="btn" href="#live">Run it in this tab</a>
      <a className="more" href="#selfhost">Self-host with Docker <span aria-hidden="true">→</span></a>
    </div>)
  if (mobile) body.push(<div key="steps">{steps}</div>)
  else body.push(<div key="beats" className="bl" style={st()}>{beatList}{heroHint}</div>)
  if (facts) body.push(<div key="facts" className="fx" style={st()}>{facts}</div>)
  if (code) body.push(<div key="code" className="scrollx cx" style={st()}><code className="k">{code}</code></div>)
  if (mod.copy.try || mod.controls.length) body.push(
    <div key="try" className="try" style={st()}><b>TRY</b><div><span>{mod.copy.try}</span> <Controls mod={mod} si={si} director={director} announce={setMsg} /></div></div>)
  if (mod.id === 'agents') body.push(<div key="works" className="works-row" style={st()}><span className="label">Works with</span><Html className="wl" html={WORKS_WITH_MARKUP} /></div>)
  body.push(<a key="spec" className="more" style={st()} href={mod.copy.spec.href}>{mod.copy.spec.text ?? 'Read the spec'} <span aria-hidden="true">→</span></a>)
  body.push(<p key="live" className="sr" role="status" aria-live="polite">{msg}</p>)

  const onFocus = () => { if (!active) api.jumpToScene(si, true) }
  return (
    <section className={`chapter${active ? ' on' : ''}${out ? ' out' : ''}${hero ? ' hero' : ''}`} id={mobile ? `ch-${mod.id}` : undefined} data-scene={mod.id}
      aria-labelledby={headId} onFocus={onFocus}>
      {body}
    </section>
  )
}

/* ───────────── the story ───────────── */

export function Story({ director, env, api, stage, labelsRef }: {
  director: Director; env: StoryEnv; api: StoryApi; stage: ReactNode; labelsRef: React.RefObject<HTMLDivElement | null>
}) {
  const { active, beat } = useDirector(director)
  const scenes = director.scenes
  const story = director.story
  const total = scenes.length
  const beatsIds = scenes.map(s => `beats-${s.id}`)
  const pinRef = useRef<HTMLDivElement>(null)

  // story height (desktop): one viewport + T_total beats of scroll
  const [beatH, setBeatH] = useState(() => (typeof window === 'undefined' ? 400 : (window.innerHeight >= 820 ? 0.48 : 0.42) * window.innerHeight))
  useEffect(() => {
    const f = () => setBeatH((window.innerHeight >= 820 ? 0.48 : 0.42) * window.innerHeight)
    window.addEventListener('resize', f); return () => window.removeEventListener('resize', f)
  }, [])

  const mod = scenes[active]
  const heroOn = active === 0
  // caption swap: the outgoing plate fades up and out (220 ms), the incoming one staggers in (380 ms, 40 ms apart)
  const prev = useRef(active), [out, setOut] = useState(-1), [init, setInit] = useState(true)
  useEffect(() => {
    if (prev.current === active) return
    setOut(prev.current); setInit(false); prev.current = active
    const t = setTimeout(() => setOut(-1), 240)
    return () => clearTimeout(t)
  }, [active])
  const rail = (
    <nav className="rail" aria-label="Chapters">
      {scenes.map((s, k) => (
        <a key={s.id} href={`#ch-${s.id}`} className={k === active ? 'on' : undefined} aria-current={k === active ? 'true' : undefined}
          onClick={e => { e.preventDefault(); api.jumpToScene(k) }}><span>{s.index} {s.id === 'hero' ? 'TAB' : s.kicker.split(' ')[0]}</span></a>
      ))}
    </nav>
  )
  const canvasLabel = mod.summary
  if (env.mobile) {
    return (
      <section id="story" className="story m" aria-label="How Nubi works">
        <div className="story-pin" ref={pinRef}>
          <div className="stage" role="img" aria-label={canvasLabel} aria-describedby={beatsIds[active]}>{stage}</div>
          <div className="labels" ref={labelsRef} aria-hidden="true" />
        </div>
        <div className="chapters gut">
          {scenes.map((s, k) => (
            <div key={s.id} className="chwrap">
              <Chapter mod={s} si={k} total={total} director={director} active={k === active} beat={k === active ? beat : 0} mobile api={api} beatsId={beatsIds[k]} />
              {k === 0 && <div className="proof-m" dangerouslySetInnerHTML={{ __html: PROOF_BAND_MARKUP }} />}
            </div>
          ))}
        </div>
      </section>
    )
  }
  return (
    <section id="story" className={`story d${env.ui ? '' : ' noui'}`} aria-label="How Nubi works" style={{ height: `calc(100svh + ${(story.total * beatH).toFixed(0)}px)` }}>
      <div className="story-pin" ref={pinRef}>
        <div className="stage" role="img" aria-label={canvasLabel} aria-describedby={beatsIds[active]}>{stage}</div>
        <div className="labels" ref={labelsRef} aria-hidden="true" />
        <div className="gut plate-row"><div className="inner">
          <div className={`plates${init ? ' init' : ''}`}>
            {scenes.map((s, k) => (
              <Chapter key={s.id} mod={s} si={k} total={total} director={director} active={k === active} out={k === out} beat={k === active ? beat : 0} mobile={false} api={api} beatsId={beatsIds[k]} />
            ))}
          </div>
        </div></div>
        {rail}
        <div className={`proof-strip gut${heroOn ? ' on' : ''}`} aria-hidden={!heroOn} role="group" aria-label="What Nubi is built on and works with">
          <div className="inner" dangerouslySetInnerHTML={{ __html: PROOF_STRIP_MARKUP }} />
        </div>
      </div>
      <div className="anchors" aria-hidden="true">
        {story.labels.map(l => (
          <div key={l.name} className="anchor" data-label={l.name} id={l.beat === 0 ? `ch-${scenes[l.scene].id}` : undefined} style={{ top: `${(l.T * beatH).toFixed(1)}px` }} />
        ))}
      </div>
    </section>
  )
}
