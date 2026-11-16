/**
 * The ink material (storyboard §1.5, F2.4–F2.7). Unlit, GLSL3, two outputs (colour + group id) for the
 * MRT contour pass.
 *
 * Vertex: texelFetch the FROM and TO poses of this instance from the layout atlas (3 texels per instance per
 * layout) and morph by kind: rise, sink, flip, slide, lift, arc, helix, scale, cut. Local progress
 *   e = clamp((m − order·spread) / (1 − spread)); travel cubic-in-out, rises expo-out, sinks cubic-in.
 * Tone switches crisply at toneAt; an optional flight tone flashes while .15 < e < .85.
 * Fragment: view-space key-light tones, height AO, fwidth edges with per-tone strength, opaque dashed GHOST,
 * see-through dashed XRAY, optional depth fade. Palette uniforms are plain vec3 from CSS hex (no colour
 * management), so a camera-facing accent face reads the token exactly.
 */
import * as THREE from 'three'
import { ATLAS_W, KIND_CODE } from './assign'
import { EDGE_K, type Palette } from './palette'
import type { MorphSpec } from './types'

export const KEY_DIR = new THREE.Vector3(-0.32, 0.62, 0.72).normalize()

const COMMON = /* glsl */ `
float cubicInOut(float t) { return t < .5 ? 4. * t * t * t : 1. - pow(-2. * t + 2., 3.) / 2.; }
float expoOut(float t) { return t >= 1. ? 1. : 1. - pow(2., -10. * t); }
float cubicIn(float t) { return t * t * t; }
`

export const INK_VERT = /* glsl */ `
precision highp float;
precision highp int;
precision highp sampler2D;
uniform sampler2D uAtlas;
uniform int uFrom;            // first texel row of the FROM layout
uniform int uTo;              // first texel row of the TO layout
uniform float uM;
uniform float uSpread;
uniform int uKind;
uniform vec4 uParams;         // x arc height, y toneAt, z morph flight tone + 1 (0 = none)
uniform vec4 uHelix;          // centre xyz, radius
uniform float uTurns;
uniform float uHover;         // hovered group id (0 = none)
uniform vec3 uHoverLift;
uniform float uHoverT;
uniform vec4 uWave;           // centre xyz, radius
uniform float uWaveW;         // band half-width (0 = off)
uniform vec3 uPal[10];
uniform float uEdgeK[10];
uniform vec3 uAccSoft;
out vec2 vUv;
out vec3 vN;
out vec3 vC;
out float vY;
out float vK;
out float vWy;
out float vDepth;
flat out int vGhost;
flat out float vGid;
flat out float vHot;
${COMMON}
vec4 fetchT(int row, int t) { int idx = gl_InstanceID * 3 + t; return texelFetch(uAtlas, ivec2(idx % ${ATLAS_W}, row + idx / ${ATLAS_W}), 0); }
mat3 rotY(float a) { float c = cos(a), s = sin(a); return mat3(c, 0., -s, 0., 1., 0., s, 0., c); }
mat3 rotZ(float a) { float c = cos(a), s = sin(a); return mat3(c, s, 0., -s, c, 0., 0., 0., 1.); }
mat3 rotX(float a) { float c = cos(a), s = sin(a); return mat3(1., 0., 0., 0., c, s, 0., -s, c); }
void main() {
  vec4 a0 = fetchT(uFrom, 0), a1 = fetchT(uFrom, 1), a2 = fetchT(uFrom, 2);
  vec4 b0 = fetchT(uTo, 0), b1 = fetchT(uTo, 1), b2 = fetchT(uTo, 2);
  bool fromParked = a1.x + a1.y + a1.z <= 0.;
  bool toParked = b1.x + b1.y + b1.z <= 0.;
  if (fromParked && toParked) { gl_Position = vec4(2., 2., 2., 1.); return; }
  int fl = int(b2.w + .5);
  int kind = (fl & 15) > 0 ? (fl & 15) : uKind;
  int flight = (fl >> 4) & 15;
  bool waveCell = ((fl >> 8) & 1) == 1;
  float order = b2.z;
  float e = clamp((uM - order * uSpread) / max(1e-4, 1. - uSpread), 0., 1.);
  vec3 pA = a0.xyz, pB = b0.xyz, sA = a1.xyz, sB = b1.xyz;
  float yawA = a2.x, yawB = b2.x, rollA = a2.y, rollB = b2.y;
  if (fromParked) {
    yawA = yawB; rollA = rollB;
    if (kind == ${KIND_CODE.flip} || kind == ${KIND_CODE.scale} || kind == ${KIND_CODE.cut}) { pA = pB; sA = kind == ${KIND_CODE.flip} ? sB : vec3(0.); }
    else { pA = vec3(pB.x, 0., pB.z); sA = vec3(sB.x, 0., sB.z); }
  }
  if (toParked) {
    yawB = yawA; rollB = rollA;
    if (kind == ${KIND_CODE.scale} || kind == ${KIND_CODE.cut}) { pB = pA; sB = vec3(0.); }
    else { pB = vec3(pA.x, 0., pA.z); sB = vec3(sA.x, 0., sA.z); kind = ${KIND_CODE.sink}; }
  }
  float k;
  if (kind == ${KIND_CODE.rise} || kind == ${KIND_CODE.scale} || kind == ${KIND_CODE.flip}) k = expoOut(e);
  else if (kind == ${KIND_CODE.sink}) k = cubicIn(e);
  else if (kind == ${KIND_CODE.cut}) k = step(.5, e);
  else k = cubicInOut(e);
  vec3 p = mix(pA, pB, k);
  vec3 s = mix(sA, sB, k);
  float flipA = 0.;
  if (kind == ${KIND_CODE.slide}) {
    float ex = cubicInOut(clamp(e * 1.6, 0., 1.)), ey = cubicInOut(clamp((e - .4) / .6, 0., 1.));
    p.xz = mix(pA.xz, pB.xz, ex); p.y = mix(pA.y, pB.y, ey);
  } else if (kind == ${KIND_CODE.lift}) {
    float u = cubicInOut(clamp((e - .25) / .5, 0., 1.));
    p.xz = mix(pA.xz, pB.xz, u);
    p.y = mix(pA.y, pB.y, u) + uParams.x * smoothstep(0., .25, e) * (1. - smoothstep(.75, 1., e));
  } else if (kind == ${KIND_CODE.arc}) {
    p.y += uParams.x * 4. * k * (1. - k);
  } else if (kind == ${KIND_CODE.helix}) {
    vec2 da = pA.xz - uHelix.xz, db = pB.xz - uHelix.xz;
    float thA = atan(da.y, da.x), thB = atan(db.y, db.x);
    float dth = mod(thB - thA, 6.2831853) + uTurns * 6.2831853;
    float th = thA + dth * k;
    // pull in over most of the flight (not a lunge in its first third), so far cells spiral rather than jump
    float r = mix(mix(length(da), uHelix.w, smoothstep(0., .6, e)), length(db), smoothstep(.6, 1., e));
    p.xz = uHelix.xz + r * vec2(cos(th), sin(th));
    p.y = mix(pA.y, pB.y, k);
  } else if (kind == ${KIND_CODE.flip}) {
    flipA = (1. - k) * -1.5707963;
    if (fromParked) s.y *= smoothstep(0., .12, e);
  }
  if (!fromParked && !toParked) s = max(s, .4 * min(sA, sB));
  float gid = e < .5 ? (fromParked ? b1.w : a1.w) : (toParked ? a1.w : b1.w);
  if (uHover > 0. && abs(gid - uHover) < .5) p += uHoverLift * uHoverT;
  float band = 0.;
  if (waveCell && uWaveW > 0.) {
    float d = distance(p.xy, uWave.xy);
    band = 1. - smoothstep(0., uWaveW, abs(d - uWave.w));
    p.z += .5 * band;
  }
  float yaw = mix(yawA, yawB, k), roll = mix(rollA, rollB, k);
  vec3 v = position * s;
  vec3 n = normal;
  if (flipA != 0.) { v.y -= s.y * .5; v = rotX(flipA) * v; v.y += s.y * .5; n = rotX(flipA) * n; }
  mat3 R = rotY(yaw) * rotZ(roll);
  v = R * v; n = R * n;
  vec3 wp = p + v;
  // tone: crisp switch at toneAt; flight flash in the middle of the flight
  float toneAt = uParams.y;
  float tone = fromParked ? b0.w : toParked ? a0.w : (e < toneAt ? a0.w : b0.w);
  int ft = flight > 0 ? flight : int(uParams.z + .5);
  if (ft > 0 && e > .15 && e < .85) tone = float(ft - 1);
  int t = int(tone + .5);
  vC = mix(uPal[t], uAccSoft, band * .85);
  vK = uEdgeK[t];
  vGhost = t == 3 ? 1 : (t == 9 ? 2 : 0);
  vGid = t == 9 ? 0. : gid;
  vHot = uHover > 0. && abs(gid - uHover) < .5 ? 1. : 0.;
  vUv = uv;
  vec3 wn = normalize(n);
  vWy = wn.y;
  vN = normalize(mat3(viewMatrix) * wn);
  vY = wp.y;
  vec4 mv = viewMatrix * vec4(wp, 1.);
  vDepth = -mv.z;
  gl_Position = projectionMatrix * mv;
}
`

export const INK_FRAG = /* glsl */ `
precision highp float;
precision highp int;
uniform vec3 uEdge;
uniform vec3 uTone;
uniform vec3 uKey;
uniform vec3 uBg;
uniform float uFadeNear;
uniform float uFadeFar;
in vec2 vUv;
in vec3 vN;
in vec3 vC;
in float vY;
in float vK;
in float vWy;
in float vDepth;
flat in int vGhost;
flat in float vGid;
flat in float vHot;
layout(location = 0) out highp vec4 oColor;
layout(location = 1) out highp vec4 oId;
void main() {
  vec3 n = normalize(vN);
  float tone = mix(uTone.z, uTone.x, smoothstep(-.15, .7, dot(n, uKey)));
  float ao = mix(.86, 1., smoothstep(0., 2.5, vY));
  vec3 c = vGhost > 0 ? uBg : clamp(vC * tone * mix(ao, 1., max(vWy, 0.)), 0., 1.);
  vec2 fw = fwidth(vUv);
  vec2 a = min(vUv, 1. - vUv) / max(fw, vec2(1e-5));
  float e = 1. - smoothstep(.45, 1.35, min(a.x, a.y));
  float fade = 1. - smoothstep(.09, .26, max(fw.x, fw.y));
  float k = vK;
  if (vGhost > 0) {
    float along = a.x < a.y ? vUv.y : vUv.x;
    k *= step(.5, fract(along * 3. + .25));
    fade = 1.;
    if (vGhost == 2 && e * k < .05) discard;
  }
  c = mix(c, uEdge, e * fade * k);
  if (uFadeFar > uFadeNear) c = mix(c, uBg, .7 * smoothstep(uFadeNear, uFadeFar, vDepth));
  oColor = vec4(c, 1.);
  // id in RG; B = 'hovered group' flag (MSAA may average ids at edges, so hover is a flag, not an id match)
  oId = vec4(mod(vGid, 256.) / 255., floor(vGid / 256.) / 255., vGhost == 2 ? 0. : vHot, 1.);
}
`

/** A simple (non-morphing) ink box material for extras: per-instance tone + group id via attributes. */
export const BOX_VERT = /* glsl */ `
precision highp float;
in float aTone;
in float aGid;
uniform vec3 uPal[10];
uniform float uEdgeK[10];
out vec2 vUv; out vec3 vN; out vec3 vC; out float vY; out float vK; out float vWy; out float vDepth;
flat out int vGhost; flat out float vGid; flat out float vHot;
void main() {
  mat4 m = modelMatrix * instanceMatrix;
  vHot = 0.;
  vec3 wn = normalize(mat3(m) * normal);
  int t = int(aTone + .5);
  vC = uPal[t]; vK = uEdgeK[t]; vGhost = t == 3 ? 1 : (t == 9 ? 2 : 0); vGid = t == 9 ? 0. : aGid;
  vUv = uv; vWy = wn.y; vN = normalize(mat3(viewMatrix) * wn);
  vec4 wp = m * vec4(position, 1.); vY = wp.y;
  vec4 mv = viewMatrix * wp; vDepth = -mv.z;
  gl_Position = projectionMatrix * mv;
}
`

export interface InkUniforms { [k: string]: THREE.IUniform }

export function paletteUniforms(p: Palette): InkUniforms {
  return {
    uPal: { value: p.tones.map(t => new THREE.Vector3(...t)) },
    uEdgeK: { value: EDGE_K.slice() },
    uAccSoft: { value: new THREE.Vector3(...p.accentSoft) },
    uEdge: { value: new THREE.Vector3(...p.edge) },
    uTone: { value: new THREE.Vector3(...p.toneW) },
    uKey: { value: KEY_DIR.clone() },
    uBg: { value: new THREE.Vector3(...p.bg) },
    uFadeNear: { value: 0 },
    uFadeFar: { value: 0 },
  }
}

export function applyPalette(u: InkUniforms, p: Palette) {
  ;(u.uPal.value as THREE.Vector3[]).forEach((v, i) => v.set(...p.tones[i]))
  ;(u.uAccSoft.value as THREE.Vector3).set(...p.accentSoft)
  ;(u.uEdge.value as THREE.Vector3).set(...p.edge)
  ;(u.uTone.value as THREE.Vector3).set(...p.toneW)
  ;(u.uBg.value as THREE.Vector3).set(...p.bg)
}

export function createInkMaterial(atlas: THREE.DataTexture, p: Palette): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    glslVersion: THREE.GLSL3,
    vertexShader: INK_VERT,
    fragmentShader: INK_FRAG,
    uniforms: {
      ...paletteUniforms(p),
      uAtlas: { value: atlas },
      uFrom: { value: 0 }, uTo: { value: 0 }, uM: { value: 1 }, uSpread: { value: 0.45 }, uKind: { value: KIND_CODE.slide },
      uParams: { value: new THREE.Vector4(3, 0.6, 0, 0) }, uHelix: { value: new THREE.Vector4(0, 0, 0, 5) }, uTurns: { value: 1 },
      uHover: { value: 0 }, uHoverLift: { value: new THREE.Vector3(0, 0, 0.6) }, uHoverT: { value: 0 },
      uWave: { value: new THREE.Vector4(0, 0, 0, 0) }, uWaveW: { value: 0 },
    },
  })
}

export function createBoxMaterial(p: Palette): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({ glslVersion: THREE.GLSL3, vertexShader: BOX_VERT, fragmentShader: INK_FRAG, uniforms: paletteUniforms(p) })
}

/** Morph uniforms for a spec. */
export function setMorph(u: InkUniforms, spec: MorphSpec | null, m: number) {
  u.uM.value = m
  u.uKind.value = KIND_CODE[spec?.kind ?? 'cut']
  u.uSpread.value = Math.min(0.85, Math.max(0, spec?.spread ?? 0.45))
  ;(u.uParams.value as THREE.Vector4).set(spec?.arc ?? 3, spec?.toneAt ?? 0.6, spec?.flightTone != null ? spec.flightTone + 1 : 0, 0)
  const h = spec?.helix
  if (h) { (u.uHelix.value as THREE.Vector4).set(h.centre[0], h.centre[1], h.centre[2], h.radius); u.uTurns.value = h.turns }
}

/* ───────────── ground: ruled grid + dots, written as a full-screen pass with the floor's depth ───────────── */

export const GROUND_VERT = /* glsl */ `
out vec2 vNdc;
void main() { vNdc = position.xy; gl_Position = vec4(position.xy, 0., 1.); }
`
export const GROUND_FRAG = /* glsl */ `
precision highp float;
uniform mat4 uInvViewProj;
uniform mat4 uViewProj;
uniform vec3 uInk;
uniform vec3 uBg;
uniform float uGrid;
uniform vec3 uCentre;
uniform float uMaskX;          // device px: the ground fades in to the right of this x (under the caption plate)
uniform float uMaskW;          // device px of the fade
in vec2 vNdc;
layout(location = 0) out highp vec4 oColor;
layout(location = 1) out highp vec4 oId;
void main() {
  vec4 n0 = uInvViewProj * vec4(vNdc, -1., 1.), n1 = uInvViewProj * vec4(vNdc, 1., 1.);
  vec3 a = n0.xyz / n0.w, b = n1.xyz / n1.w, d = b - a;
  float t = abs(d.y) < 1e-6 ? -1. : -a.y / d.y;
  vec3 col = uBg;
  float depth = 1.;
  if (t > 0. && t < 1.) {
    vec3 w = a + d * t;
    vec2 p = w.xz / 4.;
    vec2 g = abs(fract(p - .5) - .5) / max(fwidth(p), vec2(1e-5));
    float l = 1. - min(min(g.x, g.y), 1.);
    vec2 q = w.xz / 2.;
    vec2 fq = max(fwidth(q), vec2(1e-5));
    float dd = length((q - floor(q + .5)) / fq);
    l = max(l, (1. - smoothstep(.6, 1.6, dd)) * .7);
    float r = 1. - smoothstep(30., 70., length(w.xz - uCentre.xz));
    float m = uMaskW > 0. ? smoothstep(uMaskX, uMaskX + uMaskW, gl_FragCoord.x) : 1.;
    col = mix(uBg, uInk, l * uGrid * r * m);
    vec4 c = uViewProj * vec4(w, 1.);
    depth = clamp(c.z / c.w * .5 + .5, 0., 1.);
  }
  gl_FragDepth = depth;
  oColor = vec4(col, 1.);
  oId = vec4(0.);
}
`

/* ───────────── contour composite (2 CSS px where the group id differs among the 8 neighbours) ───────────── */

export const COMPOSITE_VERT = /* glsl */ `
out vec2 vUv;
void main() { vUv = position.xy * .5 + .5; gl_Position = vec4(position.xy, 0., 1.); }
`
export const COMPOSITE_FRAG = /* glsl */ `
precision highp float;
uniform sampler2D tColor;
uniform sampler2D tId;
uniform vec2 uPx;
uniform float uOff;
uniform vec3 uContour;
uniform vec3 uAccent;
uniform float uContourA;
uniform float uHover;
uniform float uSel;
uniform float uOn;
in vec2 vUv;
layout(location = 0) out highp vec4 oColor;
float idAt(vec2 uv) { vec2 c = texture(tId, uv).rg; return floor(c.r * 255. + .5) + floor(c.g * 255. + .5) * 256.; }
void main() {
  vec3 col = texture(tColor, vUv).rgb;
  if (uOn < .5) { oColor = vec4(col, 1.); return; }
  float id = idAt(vUv);
  float diff = 0.;
  bool hot = texture(tId, vUv).b > .5;
  for (int i = 0; i < 8; i++) {
    float an = float(i) * .7853982;
    vec2 o = vec2(cos(an), sin(an));
    o = vec2(o.x > .3 ? 1. : o.x < -.3 ? -1. : 0., o.y > .3 ? 1. : o.y < -.3 ? -1. : 0.);
    vec2 uv2 = vUv + o * uPx * uOff;
    float nid = idAt(uv2);
    if (abs(nid - id) > .5) { diff += 1.; if (texture(tId, uv2).b > .5) hot = true; }
  }
  float a = smoothstep(0., .25, diff / 8.) * uContourA;
  oColor = vec4(mix(col, hot ? uAccent : uContour, a), 1.);
}
`

/* ───────────── trace lines (2 CSS px, screen-space, dashed or solid; write MRT id 0) ───────────── */

export const LINE_VERT = /* glsl */ `
precision highp float;
in vec3 iA;
in vec3 iB;
in vec2 iD;
uniform vec2 uRes;
uniform float uWidth;
out float vDist;
void main() {
  vec4 ca = projectionMatrix * viewMatrix * vec4(iA, 1.), cb = projectionMatrix * viewMatrix * vec4(iB, 1.);
  vec2 sa = ca.xy / ca.w * uRes * .5, sb = cb.xy / cb.w * uRes * .5;
  vec2 dir = sb - sa; float L = length(dir); dir = L > 1e-4 ? dir / L : vec2(1., 0.);
  vec2 nrm = vec2(-dir.y, dir.x);
  vec4 c = mix(ca, cb, position.x);
  vec2 off = nrm * position.y * uWidth * .5 + dir * (position.x * 2. - 1.) * uWidth * .5;
  c.xy += off / (uRes * .5) * c.w;
  c.z -= .0004 * c.w;
  vDist = mix(iD.x, iD.y, position.x);
  gl_Position = c;
}
`
export const LINE_FRAG = /* glsl */ `
precision highp float;
uniform vec3 uColor;
uniform float uDash;
in float vDist;
layout(location = 0) out highp vec4 oColor;
layout(location = 1) out highp vec4 oId;
void main() {
  if (uDash > 0. && fract(vDist / uDash) > .55) discard;
  oColor = vec4(uColor, 1.);
  oId = vec4(0.);
}
`
