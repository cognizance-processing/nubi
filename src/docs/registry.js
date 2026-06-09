/**
 * Docs registry — loads all markdown sources from docs/*.md via Vite
 * import.meta.glob and builds a structured navigation with groups.
 *
 * All doc content lives under /docs/ in the repo root.
 * Slugs are derived from filenames; the home doc uses the slug "home".
 */

// ── Load markdown files eagerly ───────────────────────────────────────────────

const homeMd = import.meta.glob(
  '/docs/index.md',
  { query: '?raw', import: 'default', eager: true }
)

const overviewMds = import.meta.glob(
  [
    '/docs/getting-started.md',
  ],
  { query: '?raw', import: 'default', eager: true }
)

const coreMds = import.meta.glob(
  [
    '/docs/connectors.md',
    '/docs/queries-and-params.md',
    '/docs/dashboards.md',
    '/docs/exports-and-jobs.md',
    '/docs/flows.md',
    '/docs/pre-aggregations.md',
    '/docs/cache-key-spec.md',
    '/docs/kernel-security.md',
    '/docs/conformance.md',
    '/docs/connector-security.md',
  ],
  { query: '?raw', import: 'default', eager: true }
)

const buildMds = import.meta.glob(
  [
    '/docs/ai-and-mcp.md',
    '/docs/embedding.md',
    '/docs/git-sync.md',
    '/docs/bridges.md',
    '/docs/sdk-and-cli.md',
  ],
  { query: '?raw', import: 'default', eager: true }
)

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Extract the first H1 heading from markdown content */
function extractTitle(content, filePath) {
  const h1Match = content.match(/^#\s+(.+)$/m)
  if (h1Match) return h1Match[1].trim()
  const parts = filePath.split('/')
  const filename = parts[parts.length - 1].replace(/\.md$/, '')
  return filename.charAt(0).toUpperCase() + filename.slice(1).replace(/-/g, ' ')
}

/** Build a slug from a docs/ file path: /docs/foo-bar.md → "foo-bar" */
function pathToSlug(filePath) {
  const parts = filePath.split('/')
  const filename = parts[parts.length - 1].replace(/\.md$/, '')
  return filename.toLowerCase()
}

/** Build doc entries from a glob result map */
function buildEntries(globMap, group) {
  return Object.entries(globMap).map(([path, content]) => {
    const slug = pathToSlug(path)
    const title = extractTitle(content, path)
    return { slug, title, group, content, path }
  })
}

// ── Assemble docs ─────────────────────────────────────────────────────────────

// Home doc — always slug "home"
const rawHomeDocs = buildEntries(homeMd, 'Home')
const homeDocs = rawHomeDocs.map(doc => ({
  ...doc,
  slug: 'home',
  title: 'Nubi Docs',
}))

const overviewDocs = buildEntries(overviewMds, 'Overview')

// Core platform docs — explicit order
const coreDocsRaw = buildEntries(coreMds, 'Core Platform')
const coreOrder = [
  'connectors',
  'connector-security',
  'queries-and-params',
  'dashboards',
  'exports-and-jobs',
  'flows',
  'pre-aggregations',
  'cache-key-spec',
  'kernel-security',
  'conformance',
]
const coreDocs = [...coreDocsRaw].sort((a, b) => {
  const ai = coreOrder.indexOf(a.slug)
  const bi = coreOrder.indexOf(b.slug)
  return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi)
})

// SDK / embed / AI docs — explicit order
const buildDocsRaw = buildEntries(buildMds, 'Build & Integrate')
const buildOrder = ['ai-and-mcp', 'embedding', 'git-sync', 'bridges', 'sdk-and-cli']
const buildDocs = [...buildDocsRaw].sort((a, b) => {
  const ai = buildOrder.indexOf(a.slug)
  const bi = buildOrder.indexOf(b.slug)
  return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi)
})

const ALL_DOCS = [
  ...homeDocs,
  ...overviewDocs,
  ...coreDocs,
  ...buildDocs,
]

// Deduplicate by slug (safety net)
const seen = new Set()
const DOCS = ALL_DOCS.filter(doc => {
  if (seen.has(doc.slug)) return false
  seen.add(doc.slug)
  return true
})

// ── Exported groups ───────────────────────────────────────────────────────────

export const DOC_GROUPS = [
  {
    name: 'Home',
    docs: DOCS.filter(d => d.group === 'Home'),
  },
  {
    name: 'Overview',
    docs: DOCS.filter(d => d.group === 'Overview'),
  },
  {
    name: 'Core Platform',
    docs: DOCS.filter(d => d.group === 'Core Platform'),
  },
  {
    name: 'Build & Integrate',
    docs: DOCS.filter(d => d.group === 'Build & Integrate'),
  },
]

export function getDocs() {
  return DOCS
}

export function getDoc(slug) {
  return DOCS.find(d => d.slug === slug) ?? null
}

export const FIRST_DOC = DOCS[0] ?? null
