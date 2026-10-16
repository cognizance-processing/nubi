/**
 * Registry of every third-party mark Nubi renders (database vendors, AI
 * providers, the open-source stack we are built on). One entry per file in
 * public/logos/{tools,connectors}/. The licence and source of each file is also
 * written up in public/logos/SOURCES.md - keep the two in step (the registry
 * test fails when they drift).
 *
 * Marks are NEVER drawn by hand. To add one, drop the vendor's official SVG
 * (simple-icons, or the vendor's press kit) into public/logos/tools/, trim its
 * viewBox to the artwork, strip baked-in fills, and add an entry below.
 *
 * kind
 *   glyph  flat single-colour artwork with no fill baked in; rendered as a CSS
 *          mask so it takes currentColor (mono) or the brand colour (brand).
 *   art    multi-colour official artwork (AWS / Azure service icons) rendered
 *          as an image, unmodified. Mono mode greys it.
 *   icon   generic (non-brand) semantic glyph; always takes currentColor.
 *
 * vb     width and height of the trimmed viewBox, so the component can reserve
 *        the exact aspect ratio before the file loads.
 * ink    share of the viewBox covered by artwork (0-1), measured by rasterising
 *        the file. Drives optical sizing: dense marks (solid tiles) are drawn
 *        smaller than open marks so a row of logos reads at one visual weight.
 * hex / darkHex
 *        brand-mode colour on light / dark surfaces. Official colour unless it
 *        fails to read as a mark (pale yellows, near-black on dark), in which
 *        case the vendor's own black/white treatment is used. null for kinds
 *        that carry their own colour (art) or none (icon).
 */

export type LogoKind = 'glyph' | 'art' | 'icon'

export interface ToolLogoEntry {
  slug: string
  /** Product name as the vendor writes it; used as the default accessible name. */
  name: string
  /** Public URL of the SVG. */
  file: string
  kind: LogoKind
  vb: readonly [number, number]
  ink: number
  hex: string | null
  darkHex: string | null
  /**
   * Optical gain (default 1). Hairline line-art marks (ink well under 0.2) read
   * smaller and fainter than solid neighbours even at the same box size, so they
   * may be drawn up to this much larger than the slot (see opticalBox).
   */
  gain?: number
  source: string
  license: string
}

const entry = (e: ToolLogoEntry): ToolLogoEntry => e

// ---------------------------------------------------------------------------
// public/logos/tools - the stack, AI providers and shared database vendors
// ---------------------------------------------------------------------------

const duckdb = entry({
  slug: 'duckdb',
  name: 'DuckDB',
  file: '/logos/tools/duckdb.svg',
  kind: 'glyph',
  vb: [24, 24],
  ink: 0.628,
  hex: '#0b0c0e',
  darkHex: '#FFF000',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/duckdb.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const apachearrow = entry({
  slug: 'apachearrow',
  name: 'Apache Arrow',
  file: '/logos/tools/apachearrow.svg',
  kind: 'glyph',
  vb: [24, 20.04],
  ink: 0.399,
  hex: '#0b0c0e',
  darkHex: '#f4f5f7',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/apachearrow.svg',
  license: 'Apache-2.0 (simple-icons; derived from the Apache Arrow logo)',
})
const apacheecharts = entry({
  slug: 'apacheecharts',
  name: 'Apache ECharts',
  file: '/logos/tools/apacheecharts.svg',
  kind: 'glyph',
  vb: [24, 24],
  ink: 0.547,
  hex: '#AA344D',
  darkHex: '#E0728A',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/apacheecharts.svg',
  license: 'Apache-2.0 (simple-icons; derived from the Apache ECharts logo)',
})
const postgresql = entry({
  slug: 'postgresql',
  name: 'PostgreSQL',
  file: '/logos/tools/postgresql.svg',
  kind: 'glyph',
  vb: [23.24, 24],
  ink: 0.198,
  hex: '#4169E1',
  darkHex: '#6F8FF0',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/postgresql.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const mysql = entry({
  slug: 'mysql',
  name: 'MySQL',
  file: '/logos/tools/mysql.svg',
  kind: 'glyph',
  vb: [9.83, 9.65],
  ink: 0.101,
  gain: 1.1,
  hex: '#4479A1',
  darkHex: '#86B9DD',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/mysql.svg (dolphin only; the "MySQL" wordmark subpaths are dropped because the text is unreadable at connector sizes)',
  license: 'CC0-1.0 (simple-icons)',
})
const snowflake = entry({
  slug: 'snowflake',
  name: 'Snowflake',
  file: '/logos/tools/snowflake.svg',
  kind: 'glyph',
  vb: [24, 22.92],
  ink: 0.434,
  hex: '#29B5E8',
  darkHex: '#29B5E8',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/snowflake.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const googlebigquery = entry({
  slug: 'googlebigquery',
  name: 'Google BigQuery',
  file: '/logos/tools/googlebigquery.svg',
  kind: 'glyph',
  vb: [24, 24],
  ink: 0.391,
  hex: '#669DF6',
  darkHex: '#669DF6',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/googlebigquery.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const databricks = entry({
  slug: 'databricks',
  name: 'Databricks',
  file: '/logos/tools/databricks.svg',
  kind: 'glyph',
  vb: [22.16, 24],
  ink: 0.278,
  hex: '#FF3621',
  darkHex: '#FF3621',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/databricks.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const clickhouse = entry({
  slug: 'clickhouse',
  name: 'ClickHouse',
  file: '/logos/tools/clickhouse.svg',
  kind: 'glyph',
  vb: [24, 21.4],
  ink: 0.464,
  hex: '#0b0c0e',
  darkHex: '#FFCC01',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/clickhouse.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const anthropic = entry({
  slug: 'anthropic',
  name: 'Anthropic',
  file: '/logos/tools/anthropic.svg',
  kind: 'glyph',
  vb: [24, 16.96],
  ink: 0.484,
  hex: '#0b0c0e',
  darkHex: '#f4f5f7',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/anthropic.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const claude = entry({
  slug: 'claude',
  name: 'Claude',
  file: '/logos/tools/claude.svg',
  kind: 'glyph',
  vb: [24, 24],
  ink: 0.397,
  hex: '#D97757',
  darkHex: '#D97757',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/claude.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const claudecode = entry({
  slug: 'claudecode',
  name: 'Claude Code',
  file: '/logos/tools/claudecode.svg',
  kind: 'glyph',
  vb: [24, 15.04],
  ink: 0.673,
  hex: '#D97757',
  darkHex: '#D97757',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/claudecode.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const openai = entry({
  slug: 'openai',
  name: 'OpenAI',
  file: '/logos/tools/openai.svg',
  kind: 'glyph',
  vb: [23.72, 24],
  ink: 0.38,
  hex: '#0b0c0e',
  darkHex: '#f4f5f7',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@13.21.0/icons/openai.svg',
  license: 'CC0-1.0 (simple-icons); removed from later simple-icons releases, so pinned at 13.21.0',
})
const googlegemini = entry({
  slug: 'googlegemini',
  name: 'Google Gemini',
  file: '/logos/tools/googlegemini.svg',
  kind: 'glyph',
  vb: [24, 23.96],
  ink: 0.215,
  hex: '#8E75B2',
  darkHex: '#A992C8',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/googlegemini.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const google = entry({
  slug: 'google',
  name: 'Google',
  file: '/logos/tools/google.svg',
  kind: 'glyph',
  vb: [23.44, 24],
  ink: 0.389,
  hex: '#4285F4',
  darkHex: '#4285F4',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/google.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const github = entry({
  slug: 'github',
  name: 'GitHub',
  file: '/logos/tools/github.svg',
  kind: 'glyph',
  vb: [24, 23.48],
  ink: 0.438,
  hex: '#0b0c0e',
  darkHex: '#f4f5f7',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/github.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const docker = entry({
  slug: 'docker',
  name: 'Docker',
  file: '/logos/tools/docker.svg',
  kind: 'glyph',
  vb: [24, 17.28],
  ink: 0.524,
  hex: '#2496ED',
  darkHex: '#2496ED',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/docker.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const kubernetes = entry({
  slug: 'kubernetes',
  name: 'Kubernetes',
  file: '/logos/tools/kubernetes.svg',
  kind: 'glyph',
  vb: [24, 23.36],
  ink: 0.558,
  hex: '#326CE5',
  darkHex: '#5B8CF0',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/kubernetes.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const dbt = entry({
  slug: 'dbt',
  name: 'dbt',
  file: '/logos/tools/dbt.svg',
  kind: 'glyph',
  vb: [24, 24],
  ink: 0.642,
  hex: '#FF694B',
  darkHex: '#FF694B',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@13.21.0/icons/dbt.svg',
  license: 'CC0-1.0 (simple-icons); removed from later simple-icons releases, so pinned at 13.21.0',
})
const modelcontextprotocol = entry({
  slug: 'modelcontextprotocol',
  name: 'Model Context Protocol',
  file: '/logos/tools/modelcontextprotocol.svg',
  kind: 'glyph',
  vb: [21.64, 24],
  ink: 0.297,
  hex: '#0b0c0e',
  darkHex: '#f4f5f7',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/modelcontextprotocol.svg',
  license: 'CC0-1.0 (simple-icons) (mark from the MCP project site, https://modelcontextprotocol.io)',
})
const ollama = entry({
  slug: 'ollama',
  name: 'Ollama',
  file: '/logos/tools/ollama.svg',
  kind: 'glyph',
  vb: [18.2, 24],
  ink: 0.281,
  hex: '#0b0c0e',
  darkHex: '#f4f5f7',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/ollama.svg',
  license: 'CC0-1.0 (simple-icons)',
})

// ---------------------------------------------------------------------------
// public/logos/connectors - connector-only marks and generic glyphs
// ---------------------------------------------------------------------------

const mariadb = entry({
  slug: 'mariadb',
  name: 'MariaDB',
  file: '/logos/connectors/mariadb.svg',
  kind: 'glyph',
  vb: [24, 15.54],
  ink: 0.298,
  hex: '#003545',
  darkHex: '#6CC0D8',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/mariadb.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const microsoftsqlserver = entry({
  slug: 'microsoftsqlserver',
  name: 'Microsoft SQL Server',
  file: '/logos/connectors/microsoftsqlserver.svg',
  kind: 'glyph',
  vb: [117.227, 128],
  ink: 0.178,
  gain: 1.12,
  hex: '#CC2927',
  darkHex: '#F06B69',
  source: 'https://api.iconify.design/devicon-plain/microsoftsqlserver.svg (Devicon, https://github.com/devicons/devicon)',
  license: 'MIT (Devicon)',
})
const oracle = entry({
  slug: 'oracle',
  name: 'Oracle',
  file: '/logos/connectors/oracle.svg',
  kind: 'glyph',
  vb: [24, 15.24],
  ink: 0.429,
  hex: '#F80000',
  darkHex: '#FF5A5A',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@13.21.0/icons/oracle.svg',
  license: 'CC0-1.0 (simple-icons); removed from later simple-icons releases, so pinned at 13.21.0',
})
const cockroachlabs = entry({
  slug: 'cockroachlabs',
  name: 'CockroachDB',
  file: '/logos/connectors/cockroachlabs.svg',
  kind: 'glyph',
  vb: [19.28, 24],
  ink: 0.252,
  hex: '#6933FF',
  darkHex: '#9370FF',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/cockroachlabs.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const googlecloud = entry({
  slug: 'googlecloud',
  name: 'Google Cloud',
  file: '/logos/connectors/googlecloud.svg',
  kind: 'glyph',
  vb: [24, 19.32],
  ink: 0.544,
  hex: '#4285F4',
  darkHex: '#4285F4',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/googlecloud.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const googlecloudstorage = entry({
  slug: 'googlecloudstorage',
  name: 'Google Cloud Storage',
  file: '/logos/connectors/googlecloudstorage.svg',
  kind: 'glyph',
  vb: [24, 19.24],
  ink: 0.748,
  hex: '#4285F4',
  darkHex: '#8AB4F8',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/googlecloudstorage.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const trino = entry({
  slug: 'trino',
  name: 'Trino',
  file: '/logos/connectors/trino.svg',
  kind: 'glyph',
  vb: [16.24, 24],
  ink: 0.348,
  hex: '#DD00A1',
  darkHex: '#F04FC4',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/trino.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const presto = entry({
  slug: 'presto',
  name: 'Presto',
  file: '/logos/connectors/presto.svg',
  kind: 'glyph',
  vb: [24, 24],
  ink: 0.904,
  hex: '#5890FF',
  darkHex: '#5890FF',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/presto.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const sqlite = entry({
  slug: 'sqlite',
  name: 'SQLite',
  file: '/logos/connectors/sqlite.svg',
  kind: 'glyph',
  vb: [21.52, 24],
  ink: 0.647,
  hex: '#003B57',
  darkHex: '#4FA3D1',
  source: 'https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/sqlite.svg',
  license: 'CC0-1.0 (simple-icons)',
})
const azuresqldatabase = entry({
  slug: 'azuresqldatabase',
  name: 'Azure SQL Database',
  file: '/logos/connectors/azuresqldatabase.svg',
  kind: 'art',
  vb: [12.87, 17.04],
  ink: 0.936,
  hex: null,
  darkHex: null,
  source: 'https://raw.githubusercontent.com/benc-uk/icon-collection/master/azure-icons/SQL-Database.svg (mirror of the Microsoft Azure Architecture icon set, https://learn.microsoft.com/azure/architecture/icons/)',
  license: 'Microsoft Azure icon terms: use unmodified to represent the Azure service (https://learn.microsoft.com/azure/architecture/icons/)',
})
const azuresynapse = entry({
  slug: 'azuresynapse',
  name: 'Azure Synapse Analytics',
  file: '/logos/connectors/azuresynapse.svg',
  kind: 'art',
  vb: [15.75, 18],
  ink: 0.41,
  hex: null,
  darkHex: null,
  source: 'https://raw.githubusercontent.com/benc-uk/icon-collection/master/azure-icons/Azure-Synapse-Analytics.svg (mirror of the Microsoft Azure Architecture icon set, https://learn.microsoft.com/azure/architecture/icons/)',
  license: 'Microsoft Azure icon terms: use unmodified to represent the Azure service',
})
const amazonathena = entry({
  slug: 'amazonathena',
  name: 'Amazon Athena',
  file: '/logos/connectors/amazonathena.svg',
  kind: 'art',
  vb: [256, 256],
  ink: 1,
  hex: null,
  darkHex: null,
  source: 'AWS Architecture Icons, https://aws.amazon.com/architecture/icons/ (distributed in gilbarbara/logos as aws-athena; https://api.iconify.design/logos/aws-athena.svg)',
  license: 'AWS Architecture Icons terms: use unmodified to represent the AWS service',
})
const amazonredshift = entry({
  slug: 'amazonredshift',
  name: 'Amazon Redshift',
  file: '/logos/connectors/amazonredshift.svg',
  kind: 'art',
  vb: [256, 256],
  ink: 1,
  hex: null,
  darkHex: null,
  source: 'AWS Architecture Icons, https://aws.amazon.com/architecture/icons/ (distributed in gilbarbara/logos as aws-redshift; https://api.iconify.design/logos/aws-redshift.svg)',
  license: 'AWS Architecture Icons terms: use unmodified to represent the AWS service',
})
const amazons3 = entry({
  slug: 'amazons3',
  name: 'Amazon S3',
  file: '/logos/connectors/amazons3.svg',
  kind: 'art',
  vb: [256, 256],
  ink: 1,
  hex: null,
  darkHex: null,
  source: 'AWS Architecture Icons, https://aws.amazon.com/architecture/icons/ (distributed in gilbarbara/logos as aws-s3; https://api.iconify.design/logos/aws-s3.svg)',
  license: 'AWS Architecture Icons terms: use unmodified to represent the AWS service',
})
const demo = entry({
  slug: 'demo',
  name: 'Demo data',
  file: '/logos/connectors/demo.svg',
  kind: 'icon',
  vb: [19.8, 21.8],
  ink: 0.354,
  hex: null,
  darkHex: null,
  source: 'Lucide \'box\', https://lucide.dev/icons/box',
  license: 'ISC (Lucide)',
})
const ftp = entry({
  slug: 'ftp',
  name: 'FTP',
  file: '/logos/connectors/ftp.svg',
  kind: 'icon',
  vb: [21.8, 18.8],
  ink: 0.357,
  hex: null,
  darkHex: null,
  source: 'Lucide \'folder-up\', https://lucide.dev/icons/folder-up',
  license: 'ISC (Lucide)',
})
const sftp = entry({
  slug: 'sftp',
  name: 'SFTP',
  file: '/logos/connectors/sftp.svg',
  kind: 'icon',
  vb: [21.8, 20.8],
  ink: 0.318,
  hex: null,
  darkHex: null,
  source: 'Lucide \'folder-lock\', https://lucide.dev/icons/folder-lock',
  license: 'ISC (Lucide)',
})
const http_json = entry({
  slug: 'http-json',
  name: 'HTTP / JSON API',
  file: '/logos/connectors/http-json.svg',
  kind: 'icon',
  vb: [19.8, 19.8],
  ink: 0.225,
  hex: null,
  darkHex: null,
  source: 'Lucide \'braces\', https://lucide.dev/icons/braces',
  license: 'ISC (Lucide)',
})
const jdbc = entry({
  slug: 'jdbc',
  name: 'JDBC',
  file: '/logos/connectors/jdbc.svg',
  kind: 'icon',
  vb: [19.8, 21.8],
  ink: 0.413,
  hex: null,
  darkHex: null,
  source: 'Lucide \'database\', https://lucide.dev/icons/database',
  license: 'ISC (Lucide)',
})

export const TOOL_LOGOS: readonly ToolLogoEntry[] = [
  duckdb,
  apachearrow,
  apacheecharts,
  postgresql,
  mysql,
  snowflake,
  googlebigquery,
  databricks,
  clickhouse,
  anthropic,
  claude,
  claudecode,
  openai,
  googlegemini,
  google,
  github,
  docker,
  kubernetes,
  dbt,
  modelcontextprotocol,
  ollama,
  mariadb,
  microsoftsqlserver,
  oracle,
  cockroachlabs,
  googlecloud,
  googlecloudstorage,
  trino,
  presto,
  sqlite,
  azuresqldatabase,
  azuresynapse,
  amazonathena,
  amazonredshift,
  amazons3,
  demo,
  ftp,
  sftp,
  http_json,
  jdbc,
]

export type ToolSlug =
  | 'duckdb'
  | 'apachearrow'
  | 'apacheecharts'
  | 'postgresql'
  | 'mysql'
  | 'snowflake'
  | 'googlebigquery'
  | 'databricks'
  | 'clickhouse'
  | 'anthropic'
  | 'claude'
  | 'claudecode'
  | 'openai'
  | 'googlegemini'
  | 'google'
  | 'github'
  | 'docker'
  | 'kubernetes'
  | 'dbt'
  | 'modelcontextprotocol'
  | 'ollama'
  | 'mariadb'
  | 'microsoftsqlserver'
  | 'oracle'
  | 'cockroachlabs'
  | 'googlecloud'
  | 'googlecloudstorage'
  | 'trino'
  | 'presto'
  | 'sqlite'
  | 'azuresqldatabase'
  | 'azuresynapse'
  | 'amazonathena'
  | 'amazonredshift'
  | 'amazons3'
  | 'demo'
  | 'ftp'
  | 'sftp'
  | 'http-json'
  | 'jdbc'

/** Friendly names other code already uses for the same mark (connector ids, provider ids). */
export const TOOL_ALIASES: Readonly<Record<string, ToolSlug>> = {
  postgres: 'postgresql',
  bigquery: 'googlebigquery',
  gemini: 'googlegemini',
  mcp: 'modelcontextprotocol',
  chatgpt: 'openai',
  sqlserver: 'microsoftsqlserver',
  azuresql: 'azuresqldatabase',
  cloudsql: 'googlecloud',
  cockroachdb: 'cockroachlabs',
  gcs: 'googlecloudstorage',
  redshift: 'amazonredshift',
  athena: 'amazonathena',
  s3: 'amazons3',
  object_storage: 'amazons3',
  duckdb_storage: 'amazons3',
  http_json: 'http-json',
  echarts: 'apacheecharts',
  arrow: 'apachearrow',
  k8s: 'kubernetes',
  claude_code: 'claudecode',
}

const BY_SLUG = new Map(TOOL_LOGOS.map((l) => [l.slug, l]))
const BY_FILE = new Map(TOOL_LOGOS.map((l) => [l.file, l]))

/** Look a mark up by slug or alias. Case-insensitive; undefined when unknown. */
export function getToolLogo(slug: string | null | undefined): ToolLogoEntry | undefined {
  if (!slug) return undefined
  const k = slug.toLowerCase()
  return BY_SLUG.get(k) ?? BY_SLUG.get(TOOL_ALIASES[k] ?? '')
}

/** Look a mark up by its public file URL (what connectors.ts stores in `logo`). */
export function getToolLogoByFile(file: string | null | undefined): ToolLogoEntry | undefined {
  return file ? BY_FILE.get(file) : undefined
}

/** Fraction of the viewBox the artwork should cover once drawn. See opticalBox(). */
const TARGET_INK = 0.55
const MIN_FILL = 0.78

/**
 * Optical sizing: the pixel box (w, h) a mark is drawn in inside a square slot
 * of \`size\`. Solid marks (high ink coverage, such as tiles and filled discs)
 * shrink toward MIN_FILL so they do not out-weigh open marks; open marks fill
 * the slot. Wide marks may extend past the slot width by up to 30% (the slot is
 * centred, so they overhang evenly) because a wide mark confined to a square
 * slot would otherwise be drawn far smaller than its neighbours. Tall marks are
 * limited by the slot height. Aspect ratio is always kept.
 */
export function opticalBox(entry: Pick<ToolLogoEntry, 'vb' | 'ink' | 'gain'>, size: number): { w: number; h: number } {
  const [vw, vh] = entry.vb
  const a = vw / vh
  const fill = Math.min(1, Math.max(MIN_FILL, Math.sqrt(TARGET_INK / Math.max(entry.ink, 0.01))))
  const gain = Math.min(1.15, Math.max(1, entry.gain ?? 1))
  const slotW = size * gain * Math.min(1.3, Math.max(1, 1 + 0.6 * (a - 1)))
  let h = Math.min(size * gain, (gain * fill * size) / Math.sqrt(a))
  let w = h * a
  if (w > slotW) {
    w = slotW
    h = w / a
  }
  return { w: Math.round(w * 100) / 100, h: Math.round(h * 100) / 100 }
}
