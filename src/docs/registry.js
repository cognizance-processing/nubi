/**
 * Docs registry — loads all markdown sources via Vite import.meta.glob
 * and builds a structured navigation with groups.
 */

// Eagerly load all markdown files as raw strings
const overviewMds = import.meta.glob(
  ['/README.md', '/ROADMAP.md'],
  { query: '?raw', import: 'default', eager: true }
)

const backendMds = import.meta.glob(
  '/backend/docs/*.md',
  { query: '?raw', import: 'default', eager: true }
)

const sdkMd = import.meta.glob(
  '/sdk/README.md',
  { query: '?raw', import: 'default', eager: true }
)

const cliMd = import.meta.glob(
  '/cli/README.md',
  { query: '?raw', import: 'default', eager: true }
)

const mcpMd = import.meta.glob(
  '/mcp/README.md',
  { query: '?raw', import: 'default', eager: true }
)

const embedMd = import.meta.glob(
  '/embed/README.md',
  { query: '?raw', import: 'default', eager: true }
)

/** Extract the first H1 heading from markdown content, or fall back to filename */
function extractTitle(content, filePath) {
  const h1Match = content.match(/^#\s+(.+)$/m)
  if (h1Match) return h1Match[1].trim()
  // Fall back to filename without extension
  const parts = filePath.split('/')
  const filename = parts[parts.length - 1].replace(/\.md$/, '')
  return filename.charAt(0).toUpperCase() + filename.slice(1).replace(/-/g, ' ')
}

/** Build a slug from a file path */
function pathToSlug(filePath) {
  return filePath
    .replace(/^\//, '')          // strip leading /
    .replace(/\.md$/, '')        // strip .md
    .replace(/\//g, '-')         // slashes → dashes
    .replace(/[^a-z0-9-]/gi, '-') // sanitise
    .toLowerCase()
}

/** Build doc entries for a glob result map */
function buildEntries(globMap, group) {
  return Object.entries(globMap).map(([path, content]) => {
    const slug = pathToSlug(path)
    const title = extractTitle(content, path)
    return { slug, title, group, content, path }
  })
}

// --- Assemble all docs ---

const overviewDocs = buildEntries(overviewMds, 'Overview')
const backendDocs = buildEntries(backendMds, 'Backend & Architecture')
const sdkDocs = buildEntries(sdkMd, 'SDKs & Tools')
const cliDocs = buildEntries(cliMd, 'SDKs & Tools')
const mcpDocs = buildEntries(mcpMd, 'SDKs & Tools')
const embedDocs = buildEntries(embedMd, 'SDKs & Tools')

// Custom ordering for Overview group
const overviewOrder = ['readme', 'roadmap']
overviewDocs.sort((a, b) => {
  const ai = overviewOrder.findIndex(k => a.slug.includes(k))
  const bi = overviewOrder.findIndex(k => b.slug.includes(k))
  return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi)
})

// Custom ordering for Backend group
const backendOrder = ['readme', 'connectors', 'kernel-security', 'conformance', 'cache-key-spec']
backendDocs.sort((a, b) => {
  const ai = backendOrder.findIndex(k => a.slug.includes(k))
  const bi = backendOrder.findIndex(k => b.slug.includes(k))
  return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi)
})

const ALL_DOCS = [
  ...overviewDocs,
  ...backendDocs,
  ...sdkDocs,
  ...cliDocs,
  ...mcpDocs,
  ...embedDocs,
]

// Deduplicate by slug (safety net)
const seen = new Set()
const DOCS = ALL_DOCS.filter(doc => {
  if (seen.has(doc.slug)) return false
  seen.add(doc.slug)
  return true
})

console.log(`[Nubi Docs] Loaded ${DOCS.length} documents across groups:`, {
  Overview: DOCS.filter(d => d.group === 'Overview').length,
  'Backend & Architecture': DOCS.filter(d => d.group === 'Backend & Architecture').length,
  'SDKs & Tools': DOCS.filter(d => d.group === 'SDKs & Tools').length,
})

export const DOC_GROUPS = [
  {
    name: 'Overview',
    docs: DOCS.filter(d => d.group === 'Overview'),
  },
  {
    name: 'Backend & Architecture',
    docs: DOCS.filter(d => d.group === 'Backend & Architecture'),
  },
  {
    name: 'SDKs & Tools',
    docs: DOCS.filter(d => d.group === 'SDKs & Tools'),
  },
]

export function getDocs() {
  return DOCS
}

export function getDoc(slug) {
  return DOCS.find(d => d.slug === slug) ?? null
}

export const FIRST_DOC = DOCS[0] ?? null
