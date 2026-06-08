/**
 * Compare registry — loads all compare content from src/content/compare/*.md
 * via Vite import.meta.glob (eager), parses YAML frontmatter, and exposes
 * structured data to ComparePage.jsx.
 *
 * Pattern mirrors src/docs/registry.js.
 *
 * Content sources:
 *   src/content/compare/intro.md          → INTRO (hero narrative + positioning table)
 *   src/content/compare/why-nubi.md       → WHY_NUBI (differentiators + limitations)
 *   src/content/compare/caveat.md         → CAVEAT (honest cost-claim caveat)
 *   src/content/compare/matrix.md         → MATRIX_META (dimensions + full matrix data)
 *   src/content/compare/competitors/*.md  → COMPETITORS (one file per tool)
 */

// ── Eager glob imports ────────────────────────────────────────────────────────

const sectionMds = import.meta.glob(
  '/src/content/compare/*.md',
  { query: '?raw', import: 'default', eager: true }
)

const competitorMds = import.meta.glob(
  '/src/content/compare/competitors/*.md',
  { query: '?raw', import: 'default', eager: true }
)

const orchestratorMds = import.meta.glob(
  '/src/content/compare/orchestrators/*.md',
  { query: '?raw', import: 'default', eager: true }
)

// ── Frontmatter parser ────────────────────────────────────────────────────────

/**
 * Parse YAML frontmatter from a markdown string.
 * Returns { data: Object, content: string }
 *
 * Supports:
 *   - scalar strings (quoted and unquoted)
 *   - booleans (true/false)
 *   - lists of scalars (- item)
 *   - nested mappings (key:\n  subkey: value)
 *   - deeply nested mappings for the matrix (2 levels of indentation)
 */
function parseFrontmatter(raw) {
  const fmMatch = raw.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/)
  if (!fmMatch) return { data: {}, content: raw }

  const yamlStr = fmMatch[1]
  const content = fmMatch[2].trimStart()
  const data = parseYamlBlock(yamlStr)
  return { data, content }
}

/**
 * Minimal YAML block parser. Handles:
 *   key: scalar
 *   key: "quoted scalar"
 *   key:
 *     subkey: scalar
 *     subkey2: scalar
 *   key:
 *     subkey:
 *       deepkey: scalar
 *   key:
 *     - item1
 *     - item2
 */
function parseYamlBlock(yaml) {
  const lines = yaml.split('\n')
  const result = {}
  let i = 0

  while (i < lines.length) {
    const line = lines[i]
    // Skip blank lines
    if (!line.trim()) { i++; continue }

    // Top-level key: indent = 0
    const topKeyMatch = line.match(/^([a-zA-Z_][a-zA-Z0-9_-]*):\s*(.*)$/)
    if (!topKeyMatch) { i++; continue }

    const key = topKeyMatch[1]
    const inlineVal = topKeyMatch[2].trim()

    if (inlineVal) {
      // Inline scalar value
      result[key] = parseScalar(inlineVal)
      i++
    } else {
      // Look ahead for nested content
      i++
      const nested = {}
      const list = []
      let isMapping = false
      let isList = false

      while (i < lines.length) {
        const nextLine = lines[i]
        if (!nextLine.trim()) { i++; continue }

        // Check indentation
        const indent = nextLine.match(/^(\s+)/)?.[1]?.length ?? 0
        if (indent === 0) break // back to top-level

        const trimmed = nextLine.trim()

        // List item
        if (trimmed.startsWith('- ')) {
          isList = true
          const dashIndent = indent
          const itemInline = trimmed.slice(2).trim()
          // A list item can be a scalar (- foo) or a mapping
          // (- key: value\n  label: value\n  description: value).
          const inlineMap = itemInline.match(/^([a-zA-Z_][a-zA-Z0-9_\s/]*):\s*(.*)$/)
          if (inlineMap) {
            const obj = { [inlineMap[1].trim()]: parseScalar(inlineMap[2].trim()) }
            i++
            // Absorb continuation lines indented past the dash into this object.
            while (i < lines.length) {
              const contLine = lines[i]
              if (!contLine.trim()) { i++; continue }
              const contIndent = contLine.match(/^(\s+)/)?.[1]?.length ?? 0
              const contTrimmed = contLine.trim()
              if (contIndent <= dashIndent || contTrimmed.startsWith('- ')) break
              const contMatch = contTrimmed.match(/^([a-zA-Z_][a-zA-Z0-9_\s/]*):\s*(.*)$/)
              if (contMatch) obj[contMatch[1].trim()] = parseScalar(contMatch[2].trim())
              i++
            }
            list.push(obj)
          } else {
            list.push(parseScalar(itemInline))
            i++
          }
          continue
        }

        // Nested mapping
        const nestedKeyMatch = trimmed.match(/^([a-zA-Z_][a-zA-Z0-9_\s/]*):\s*(.*)$/)
        if (nestedKeyMatch) {
          isMapping = true
          const nKey = nestedKeyMatch[1].trim()
          const nVal = nestedKeyMatch[2].trim()

          if (nVal) {
            nested[nKey] = parseScalar(nVal)
            i++
          } else {
            // Deeper nesting (e.g. matrix sub-objects)
            i++
            const deepNested = {}
            while (i < lines.length) {
              const deepLine = lines[i]
              if (!deepLine.trim()) { i++; continue }
              const deepIndent = deepLine.match(/^(\s+)/)?.[1]?.length ?? 0
              if (deepIndent <= indent) break
              const deepTrimmed = deepLine.trim()
              const deepMatch = deepTrimmed.match(/^([a-zA-Z_][a-zA-Z0-9_\s/]*):\s*(.*)$/)
              if (deepMatch) {
                deepNested[deepMatch[1].trim()] = parseScalar(deepMatch[2].trim())
              }
              i++
            }
            nested[nKey] = deepNested
          }
          continue
        }

        i++
      }

      result[key] = isList ? list : (isMapping ? nested : {})
    }
  }

  return result
}

function parseScalar(val) {
  if (!val) return ''
  // Quoted string
  if ((val.startsWith('"') && val.endsWith('"')) ||
      (val.startsWith("'") && val.endsWith("'"))) {
    return val.slice(1, -1)
  }
  // Boolean
  if (val === 'true') return true
  if (val === 'false') return false
  // Number
  if (/^-?\d+(\.\d+)?$/.test(val)) return Number(val)
  return val
}

// ── Build section entries ─────────────────────────────────────────────────────

function getSectionContent(slug) {
  for (const [path, raw] of Object.entries(sectionMds)) {
    const file = path.split('/').pop().replace(/\.md$/, '')
    if (file === slug) return parseFrontmatter(raw)
  }
  return { data: {}, content: '' }
}

// ── Exported section data ─────────────────────────────────────────────────────

export const INTRO = getSectionContent('intro')
export const WHY_NUBI = getSectionContent('why-nubi')
export const CAVEAT = getSectionContent('caveat')
export const MATRIX_META = getSectionContent('matrix')

/**
 * COMPARE_DIMENSIONS — array of { key, label, description }
 * Sourced from matrix.md frontmatter `dimensions` list.
 */
export const COMPARE_DIMENSIONS = (MATRIX_META.data.dimensions ?? []).map(d => ({
  key: d.key ?? '',
  label: d.label ?? '',
  description: d.description ?? '',
}))

/**
 * MATRIX — { [dimensionKey]: { [toolName]: string } }
 * Sourced from matrix.md frontmatter `matrix` block.
 */
export const MATRIX = MATRIX_META.data.matrix ?? {}

// ── Competitors ───────────────────────────────────────────────────────────────

/**
 * Explicit display order for competitors in the matrix / cards.
 * Keys must match frontmatter `name` values.
 */
const COMPETITOR_ORDER = [
  // General BI tools
  'Metabase',
  'Hex',
  'Cube',
  'Looker',
  'Sigma Computing',
  'Tableau',
  'Power BI',
  'Preset',
  'Count',
  // Embedded analytics specialists
  'Embeddable',
  'Holistics',
  'Luzmo',
  'Omni Analytics',
  'GoodData',
  // Legacy catch-all (old name)
  'Preset / Apache Superset',
]

/**
 * Build competitor entries from glob results.
 * Each entry: { name, tagline, selfHost, pricing, pricingUnverified, sourceUrls, content, slug }
 */
function buildCompetitors() {
  const entries = Object.entries(competitorMds).map(([path, raw]) => {
    const { data, content } = parseFrontmatter(raw)
    const slug = path.split('/').pop().replace(/\.md$/, '')
    return {
      name: data.name ?? slug,
      tagline: data.tagline ?? '',
      selfHost: data.selfHost ?? '',
      pricing: data.pricing ?? '',
      pricingUnverified: data.pricingUnverified === true,
      sourceUrls: Array.isArray(data.sourceUrls) ? data.sourceUrls : [],
      content,
      slug,
    }
  })

  // Sort by COMPETITOR_ORDER; unknown names go to end
  entries.sort((a, b) => {
    const ai = COMPETITOR_ORDER.indexOf(a.name)
    const bi = COMPETITOR_ORDER.indexOf(b.name)
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi)
  })

  return entries
}

export const COMPETITORS = buildCompetitors()

// ── Orchestrators ─────────────────────────────────────────────────────────────

/**
 * Explicit display order for the orchestration section.
 * Keys must match frontmatter `name` values.
 */
const ORCHESTRATOR_ORDER = [
  'Prefect',
  'Apache Airflow',
  'Dagster',
  'n8n',
]

/**
 * Build orchestrator entries from glob results.
 * Same shape as competitor entries — reuses the same CompetitorCard component.
 */
function buildOrchestrators() {
  const entries = Object.entries(orchestratorMds).map(([path, raw]) => {
    const { data, content } = parseFrontmatter(raw)
    const slug = path.split('/').pop().replace(/\.md$/, '')
    return {
      name: data.name ?? slug,
      tagline: data.tagline ?? '',
      selfHost: data.selfHost ?? '',
      pricing: data.pricing ?? '',
      pricingUnverified: data.pricingUnverified === true,
      sourceUrls: Array.isArray(data.sourceUrls) ? data.sourceUrls : [],
      content,
      slug,
    }
  })

  entries.sort((a, b) => {
    const ai = ORCHESTRATOR_ORDER.indexOf(a.name)
    const bi = ORCHESTRATOR_ORDER.indexOf(b.name)
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi)
  })

  return entries
}

export const ORCHESTRATORS = buildOrchestrators()

/**
 * Column names in the matrix (Nubi first, then competitors in order).
 * These must match the keys used in MATRIX[dim][colName].
 */
export const MATRIX_COLUMNS = [
  { key: 'Nubi', label: 'Nubi', subtitle: 'browser-first kernel', isNubi: true },
  // General BI
  { key: 'Metabase', label: 'Metabase', subtitle: 'self-serve BI', isNubi: false },
  { key: 'Hex', label: 'Hex', subtitle: 'notebook + apps', isNubi: false },
  { key: 'Cube', label: 'Cube', subtitle: 'headless semantic layer', isNubi: false },
  { key: 'Looker', label: 'Looker', subtitle: 'enterprise LookML', isNubi: false },
  { key: 'Sigma', label: 'Sigma', subtitle: 'spreadsheet BI', isNubi: false },
  { key: 'Tableau', label: 'Tableau', subtitle: 'viz industry standard', isNubi: false },
  { key: 'PowerBI', label: 'Power BI', subtitle: 'Microsoft ecosystem', isNubi: false },
  { key: 'Superset', label: 'Superset', subtitle: 'open-source BI', isNubi: false },
  { key: 'Count', label: 'Count', subtitle: 'data canvas', isNubi: false },
  // Embedded analytics specialists
  { key: 'Embeddable', label: 'Embeddable', subtitle: 'embed SDK, per-session', isNubi: false },
  { key: 'Holistics', label: 'Holistics', subtitle: 'unlimited viewers, flat fee', isNubi: false },
  { key: 'Luzmo', label: 'Luzmo', subtitle: 'embedded analytics, MAU', isNubi: false },
  { key: 'Preset', label: 'Preset', subtitle: 'managed Superset', isNubi: false },
  { key: 'GoodData', label: 'GoodData', subtitle: 'enterprise, workspace-based', isNubi: false },
  { key: 'Omni', label: 'Omni', subtitle: 'full-stack BI + embed', isNubi: false },
]
