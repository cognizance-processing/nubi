/**
 * The logo registry is the source of truth for every third-party mark. These
 * tests keep it honest: files exist and match their declared viewBox, flat
 * marks carry no baked colour (colour comes from props), every file is
 * documented in public/logos/SOURCES.md, connectors point at registered marks,
 * and brand colours stay legible on both surfaces.
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

import { TOOL_LOGOS, TOOL_ALIASES, getToolLogo, getToolLogoByFile, opticalBox } from './registry.js'
import { CONNECTOR_TYPES } from '../../data/connectors.js'

const root = process.cwd()
const pub = (f: string) => path.join(root, 'public', f)

function lum(hex: string) {
  const n = parseInt(hex.replace('#', ''), 16)
  const c = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
    const s = v / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
}
function contrast(a: string, b: string) {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p)
  return (x + 0.05) / (y + 0.05)
}

test('slugs and files are unique; every file exists and matches its declared viewBox', () => {
  const slugs = new Set<string>()
  const files = new Set<string>()
  for (const l of TOOL_LOGOS) {
    assert.ok(!slugs.has(l.slug), `duplicate slug ${l.slug}`)
    assert.ok(!files.has(l.file), `duplicate file ${l.file}`)
    slugs.add(l.slug)
    files.add(l.file)
    const svg = fs.readFileSync(pub(l.file), 'utf8')
    const vb = svg.match(/viewBox="([^"]+)"/)
    assert.ok(vb, `${l.file} has no viewBox`)
    const [, , w, h] = vb![1].split(/\s+/).map(Number)
    assert.ok(Math.abs(w - l.vb[0]) < 0.01 && Math.abs(h - l.vb[1]) < 0.01, `${l.slug}: viewBox ${w}x${h} != registry ${l.vb}`)
    assert.ok(!/<(title|script|style|image)\b/i.test(svg), `${l.file} must not contain title/script/style/image`)
    assert.ok(!/\swidth="|\sheight="/.test(svg.match(/<svg\b[^>]*>/)![0]), `${l.file} root must not set width/height`)
  }
})

test('flat marks bake in no colour or gradient (colour comes from props)', () => {
  for (const l of TOOL_LOGOS.filter((x) => x.kind !== 'art')) {
    const svg = fs.readFileSync(pub(l.file), 'utf8')
    assert.ok(!/Gradient/.test(svg), `${l.slug} has a gradient`)
    assert.ok(!/(fill|stroke)="#(?!000"|000000")/i.test(svg), `${l.slug} has a baked colour`)
  }
})

test('every logo file on disk is registered and documented in SOURCES.md', () => {
  const sources = fs.readFileSync(pub('logos/SOURCES.md'), 'utf8')
  for (const dir of ['tools', 'connectors']) {
    for (const f of fs.readdirSync(pub(`logos/${dir}`)).filter((x) => x.endsWith('.svg'))) {
      assert.ok(getToolLogoByFile(`/logos/${dir}/${f}`), `${dir}/${f} is not in the registry`)
      assert.ok(sources.includes(`\`${dir}/${f}\``), `${dir}/${f} is not listed in SOURCES.md`)
    }
  }
  for (const l of TOOL_LOGOS) {
    assert.ok(l.source && l.license, `${l.slug} needs a source and licence`)
  }
})

test('aliases resolve to registered marks', () => {
  for (const [alias, slug] of Object.entries(TOOL_ALIASES)) {
    assert.equal(getToolLogo(alias)?.slug, slug, alias)
  }
})

test('every connector in the catalog points at a registered mark', () => {
  for (const c of CONNECTOR_TYPES) {
    assert.ok(getToolLogoByFile(c.logo), `${c.id}: ${c.logo} is not a registered logo`)
  }
})

test('brand colours stay visible on both surfaces', () => {
  for (const l of TOOL_LOGOS.filter((x) => x.hex)) {
    assert.ok(contrast(l.hex!, '#ffffff') >= 2.2, `${l.slug} ${l.hex} on white = ${contrast(l.hex!, '#ffffff').toFixed(2)}`)
    assert.ok(contrast(l.darkHex ?? l.hex!, '#141518') >= 2.2, `${l.slug} dark ${l.darkHex} on #141518 = ${contrast(l.darkHex ?? l.hex!, '#141518').toFixed(2)}`)
  }
})

test('optical sizing keeps every mark inside its slot (wide marks may overhang by 30%)', () => {
  for (const l of TOOL_LOGOS) {
    const { w, h } = opticalBox(l, 24)
    assert.ok(w <= 24 * 1.3 * 1.15 + 0.01 && h <= 24 * 1.15 + 0.01, `${l.slug} ${w}x${h}`)
    assert.ok(Math.abs(w / h - l.vb[0] / l.vb[1]) < 0.02, `${l.slug} aspect`)
  }
})

test('ProviderIcon draws no marks of its own', () => {
  const src = fs.readFileSync(path.join(root, 'src/components/ai/ProviderIcon.tsx'), 'utf8')
  assert.ok(!/Gradient|<path|<svg/.test(src))
})
