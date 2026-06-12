/**
 * connectors.js — single source of truth for connector types.
 *
 * Every connector the app can create is described here: its label, marketing
 * description, category, brand color, real brand logo (served from
 * /public/logos/connectors/*.svg), and a declarative field schema that drives
 * the create/edit form (see connectorForms.jsx → <DynamicForm>).
 *
 * The `id` is the value sent to the API as `type` and stored as
 * `config.connector_type`; the backend registry resolves it to a connector
 * factory (backend/app/connectors/registry.py). Keep these ids in sync with
 * the backend ConnectorType literal in backend/app/routes/connectors.py.
 *
 * Field schema
 * ────────────
 *   { key, label, type, group?, default?, placeholder?, required?, optional?,
 *     options?, help?, width?, showIf? }
 *     group   — 'config' (default) | 'secret'   (secret keys are encrypted)
 *     type    — text | number | password | url | select | textarea
 *               | segmented | sa_json
 *     width   — 'full' (default) | 'half' | 'third'
 *     showIf  — (config) => boolean   (conditional visibility)
 */

const logo = (file) => `/logos/connectors/${file}`

// ── Field helpers ─────────────────────────────────────────────────────────

const NETWORK_FIELD = {
  key: 'network_mode',
  label: 'Network mode',
  type: 'select',
  optional: true,
  width: 'full',
  options: [
    { value: '', label: '— direct —' },
    { value: 'direct', label: 'direct' },
    { value: 'bridge', label: 'bridge (VPC tunnel)' },
  ],
  help: 'Use "bridge" if Nubi reaches your database through a private-network agent.',
}

const SSL_FIELD = {
  key: 'sslmode',
  label: 'SSL mode',
  type: 'select',
  default: 'prefer',
  width: 'full',
  options: ['disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full'],
}

/** Standard host/port/database/user/password fields shared by SQL databases. */
function sqlFields({
  port,
  userDefault = '',
  dbKey = 'database',
  dbLabel = 'Database',
  dbPlaceholder = 'mydb',
  ssl = false,
  network = true,
} = {}) {
  const fields = [
    { key: 'host', label: 'Host', type: 'text', placeholder: 'localhost', required: true, width: 'half' },
    { key: 'port', label: 'Port', type: 'number', default: port, width: 'half' },
    { key: dbKey, label: dbLabel, type: 'text', placeholder: dbPlaceholder, required: true, width: 'half' },
    { key: 'user', label: 'User', type: 'text', default: userDefault, width: 'half' },
    { key: 'password', label: 'Password', type: 'password', group: 'secret', width: 'full' },
  ]
  if (ssl) fields.push(SSL_FIELD)
  if (network) fields.push(NETWORK_FIELD)
  return fields
}

/** Generic "host:port · database · user" summary for the connector card. */
function hostSummary(cfg = {}) {
  const parts = []
  if (cfg.host) parts.push(cfg.host + (cfg.port ? `:${cfg.port}` : ''))
  if (cfg.database) parts.push(cfg.database)
  if (cfg.service_name) parts.push(cfg.service_name)
  if (cfg.user) parts.push(cfg.user)
  return parts.join(' · ')
}

// ── Categories (display order) ────────────────────────────────────────────

export const CONNECTOR_CATEGORIES = [
  { id: 'relational', label: 'Relational databases' },
  { id: 'cloud', label: 'Cloud-managed SQL' },
  { id: 'warehouse', label: 'Cloud warehouses' },
  { id: 'engine', label: 'Query engines' },
  { id: 'lake', label: 'Lakehouse & files' },
  { id: 'api', label: 'APIs & custom' },
]

// ── Catalog ───────────────────────────────────────────────────────────────

export const CONNECTOR_TYPES = [
  // ── Relational ──────────────────────────────────────────────────────────
  {
    id: 'postgres',
    label: 'PostgreSQL',
    description: 'The classic open-source relational database.',
    category: 'relational',
    logo: logo('postgres.svg'),
    color: '#336791',
    fields: sqlFields({ port: 5432, userDefault: 'postgres', ssl: true }),
    summary: hostSummary,
  },
  {
    id: 'mysql',
    label: 'MySQL',
    description: 'The world’s most popular open-source database.',
    category: 'relational',
    logo: logo('mysql.svg'),
    color: '#00758F',
    fields: sqlFields({ port: 3306, userDefault: 'root' }),
    summary: hostSummary,
  },
  {
    id: 'mariadb',
    label: 'MariaDB',
    description: 'Community-developed, MySQL-compatible fork.',
    category: 'relational',
    logo: logo('mariadb.svg'),
    color: '#003545',
    fields: sqlFields({ port: 3306, userDefault: 'root' }),
    summary: hostSummary,
  },
  {
    id: 'sqlserver',
    label: 'Microsoft SQL Server',
    description: 'Microsoft’s enterprise T-SQL database.',
    category: 'relational',
    logo: logo('sqlserver.svg'),
    color: '#CC2927',
    fields: [
      ...sqlFields({ port: 1433 }),
      {
        key: 'encrypt',
        label: 'Encrypt connection',
        type: 'select',
        optional: true,
        default: 'true',
        width: 'full',
        options: ['true', 'false'],
      },
    ],
    summary: hostSummary,
  },
  {
    id: 'oracle',
    label: 'Oracle Database',
    description: 'Oracle’s flagship enterprise database.',
    category: 'relational',
    logo: logo('oracle.svg'),
    color: '#F80000',
    fields: [
      { key: 'host', label: 'Host', type: 'text', placeholder: 'localhost', required: true, width: 'half' },
      { key: 'port', label: 'Port', type: 'number', default: 1521, width: 'half' },
      { key: 'service_name', label: 'Service name / SID', type: 'text', placeholder: 'ORCLPDB1', required: true, width: 'half' },
      { key: 'user', label: 'User', type: 'text', width: 'half' },
      { key: 'password', label: 'Password', type: 'password', group: 'secret', width: 'full' },
      NETWORK_FIELD,
    ],
    summary: hostSummary,
  },
  {
    id: 'cockroachdb',
    label: 'CockroachDB',
    description: 'Distributed SQL, Postgres wire-compatible.',
    category: 'relational',
    logo: logo('cockroachdb.svg'),
    color: '#6933FF',
    fields: sqlFields({ port: 26257, userDefault: 'root', ssl: true }),
    summary: hostSummary,
  },

  // ── Cloud-managed SQL ─────────────────────────────────────────────────────
  {
    id: 'cloudsql',
    label: 'Google Cloud SQL',
    description: 'Managed Postgres on Google Cloud (Postgres wire).',
    category: 'cloud',
    logo: logo('cloudsql.svg'),
    color: '#4285F4',
    fields: sqlFields({ port: 5432, userDefault: 'postgres', ssl: true }),
    summary: hostSummary,
  },
  {
    id: 'azuresql',
    label: 'Azure SQL Database',
    description: 'Microsoft’s managed SQL Server in Azure.',
    category: 'cloud',
    logo: logo('azuresql.svg'),
    color: '#0078D4',
    fields: [
      { key: 'host', label: 'Server', type: 'text', placeholder: 'myserver.database.windows.net', required: true, width: 'full' },
      { key: 'port', label: 'Port', type: 'number', default: 1433, width: 'half' },
      { key: 'database', label: 'Database', type: 'text', placeholder: 'mydb', required: true, width: 'half' },
      { key: 'user', label: 'User', type: 'text', width: 'half' },
      { key: 'password', label: 'Password', type: 'password', group: 'secret', width: 'half' },
    ],
    summary: hostSummary,
  },

  // ── Cloud warehouses ──────────────────────────────────────────────────────
  {
    id: 'bigquery',
    label: 'Google BigQuery',
    description: 'Google’s serverless cloud data warehouse.',
    category: 'warehouse',
    logo: logo('bigquery.svg'),
    color: '#4285F4',
    fields: [
      { key: 'project_id', label: 'GCP Project ID', type: 'text', placeholder: 'my-gcp-project', required: true, width: 'full' },
      {
        key: 'service_account_json',
        label: 'Service account JSON',
        type: 'sa_json',
        group: 'secret',
        width: 'full',
        help: 'Upload or paste a service-account key. Leave blank to use Application Default Credentials.',
      },
    ],
    summary: (cfg) => (cfg.project_id ? `Project: ${cfg.project_id}` : ''),
  },
  {
    id: 'snowflake',
    label: 'Snowflake',
    description: 'Cloud-native data warehouse with elastic compute.',
    category: 'warehouse',
    logo: logo('snowflake.svg'),
    color: '#29B5E8',
    fields: [
      { key: 'account', label: 'Account', type: 'text', placeholder: 'xy12345.us-east-1', required: true, width: 'half' },
      { key: 'user', label: 'User', type: 'text', required: true, width: 'half' },
      { key: 'password', label: 'Password', type: 'password', group: 'secret', width: 'full' },
      { key: 'warehouse', label: 'Warehouse', type: 'text', optional: true, width: 'half' },
      { key: 'database', label: 'Database', type: 'text', optional: true, width: 'half' },
      { key: 'schema', label: 'Schema', type: 'text', optional: true, width: 'half' },
      { key: 'role', label: 'Role', type: 'text', optional: true, width: 'half' },
    ],
    summary: (cfg) => [cfg.account, cfg.warehouse, cfg.database].filter(Boolean).join(' · '),
  },
  {
    id: 'redshift',
    label: 'Amazon Redshift',
    description: 'AWS petabyte-scale warehouse (Postgres wire).',
    category: 'warehouse',
    logo: logo('redshift.svg'),
    color: '#8C4FFF',
    fields: sqlFields({ port: 5439, userDefault: 'awsuser', ssl: true }),
    summary: hostSummary,
  },
  {
    id: 'databricks',
    label: 'Databricks',
    description: 'Lakehouse SQL warehouse on Databricks.',
    category: 'warehouse',
    logo: logo('databricks.svg'),
    color: '#FF3621',
    fields: [
      { key: 'server_hostname', label: 'Server hostname', type: 'text', placeholder: 'dbc-xxxx.cloud.databricks.com', required: true, width: 'full' },
      { key: 'http_path', label: 'HTTP path', type: 'text', placeholder: '/sql/1.0/warehouses/abc123', required: true, width: 'full' },
      { key: 'access_token', label: 'Access token', type: 'password', group: 'secret', required: true, width: 'full' },
      { key: 'catalog', label: 'Catalog', type: 'text', optional: true, width: 'half' },
      { key: 'schema', label: 'Schema', type: 'text', optional: true, width: 'half' },
    ],
    summary: (cfg) => cfg.server_hostname || '',
  },
  {
    id: 'clickhouse',
    label: 'ClickHouse',
    description: 'Column-oriented OLAP database for real-time analytics.',
    category: 'warehouse',
    logo: logo('clickhouse.svg'),
    color: '#FFCC01',
    fields: [
      { key: 'host', label: 'Host', type: 'text', placeholder: 'localhost', required: true, width: 'half' },
      { key: 'port', label: 'Port', type: 'number', default: 8443, width: 'half' },
      { key: 'database', label: 'Database', type: 'text', default: 'default', width: 'half' },
      { key: 'user', label: 'User', type: 'text', default: 'default', width: 'half' },
      { key: 'password', label: 'Password', type: 'password', group: 'secret', width: 'full' },
      { key: 'secure', label: 'TLS (secure)', type: 'select', default: 'true', width: 'full', options: ['true', 'false'] },
    ],
    summary: hostSummary,
  },
  {
    id: 'azuresynapse',
    label: 'Azure Synapse',
    description: 'Microsoft’s analytics warehouse (T-SQL).',
    category: 'warehouse',
    logo: logo('azuresql.svg'),
    color: '#0078D4',
    fields: [
      { key: 'host', label: 'Server', type: 'text', placeholder: 'myworkspace.sql.azuresynapse.net', required: true, width: 'full' },
      { key: 'port', label: 'Port', type: 'number', default: 1433, width: 'half' },
      { key: 'database', label: 'Database (pool)', type: 'text', required: true, width: 'half' },
      { key: 'user', label: 'User', type: 'text', width: 'half' },
      { key: 'password', label: 'Password', type: 'password', group: 'secret', width: 'half' },
    ],
    summary: hostSummary,
  },

  // ── Query engines ─────────────────────────────────────────────────────────
  {
    id: 'athena',
    label: 'Amazon Athena',
    description: 'Serverless SQL over data in Amazon S3.',
    category: 'engine',
    logo: logo('athena.svg'),
    color: '#5A30B5',
    fields: [
      { key: 'region', label: 'AWS region', type: 'text', placeholder: 'us-east-1', required: true, width: 'half' },
      { key: 'database', label: 'Database (schema)', type: 'text', default: 'default', width: 'half' },
      { key: 's3_staging_dir', label: 'S3 staging dir', type: 'text', placeholder: 's3://my-bucket/athena-results/', required: true, width: 'full' },
      { key: 'workgroup', label: 'Workgroup', type: 'text', optional: true, width: 'half' },
      { key: 'catalog_name', label: 'Catalog', type: 'text', optional: true, default: 'AwsDataCatalog', width: 'half' },
      { key: 'aws_access_key_id', label: 'AWS access key ID', type: 'text', optional: true, width: 'full' },
      {
        key: 'aws_secret_access_key',
        label: 'AWS secret access key',
        type: 'password',
        group: 'secret',
        optional: true,
        width: 'full',
        help: 'Leave both key fields blank to use the host’s IAM role / default credential chain.',
      },
    ],
    summary: (cfg) => [cfg.region, cfg.database].filter(Boolean).join(' · '),
  },
  {
    id: 'trino',
    label: 'Trino',
    description: 'Distributed SQL query engine for federated data.',
    category: 'engine',
    logo: logo('trino.svg'),
    color: '#DD00A1',
    fields: [
      { key: 'host', label: 'Coordinator host', type: 'text', placeholder: 'trino.example.com', required: true, width: 'half' },
      { key: 'port', label: 'Port', type: 'number', default: 443, width: 'half' },
      { key: 'catalog', label: 'Catalog', type: 'text', placeholder: 'hive', required: true, width: 'half' },
      { key: 'schema', label: 'Schema', type: 'text', placeholder: 'default', width: 'half' },
      { key: 'user', label: 'User', type: 'text', required: true, width: 'half' },
      { key: 'http_scheme', label: 'Scheme', type: 'select', default: 'https', width: 'half', options: ['https', 'http'] },
      { key: 'password', label: 'Password', type: 'password', group: 'secret', optional: true, width: 'full' },
    ],
    summary: (cfg) => [cfg.host, cfg.catalog].filter(Boolean).join(' / '),
  },
  {
    id: 'presto',
    label: 'Presto',
    description: 'Open-source distributed SQL query engine.',
    category: 'engine',
    logo: logo('presto.svg'),
    color: '#5890FF',
    fields: [
      { key: 'host', label: 'Coordinator host', type: 'text', placeholder: 'presto.example.com', required: true, width: 'half' },
      { key: 'port', label: 'Port', type: 'number', default: 8080, width: 'half' },
      { key: 'catalog', label: 'Catalog', type: 'text', placeholder: 'hive', required: true, width: 'half' },
      { key: 'schema', label: 'Schema', type: 'text', placeholder: 'default', width: 'half' },
      { key: 'user', label: 'User', type: 'text', required: true, width: 'half' },
      { key: 'http_scheme', label: 'Scheme', type: 'select', default: 'http', width: 'half', options: ['https', 'http'] },
      { key: 'password', label: 'Password', type: 'password', group: 'secret', optional: true, width: 'full' },
    ],
    summary: (cfg) => [cfg.host, cfg.catalog].filter(Boolean).join(' / '),
  },

  // ── Lakehouse & files ─────────────────────────────────────────────────────
  // The only user-facing DuckDB/file connector is OBJECT STORAGE. Nubi runs on
  // stateless Cloud-Run containers, so a user-supplied LOCAL file path or an
  // IN-MEMORY DuckDB is meaningless as a data source — those exist only for
  // internal use (flows executor, demo data, fixtures). Here we accept
  // s3:// / gs:// / https:// URLs pointing at Parquet / DuckDB files. This entry
  // submits ``duckdb_storage`` (the httpfs-backed backend factory) directly.
  {
    id: 'duckdb_storage',
    label: 'Object storage (Parquet / DuckDB)',
    description: 'Query Parquet or DuckDB files in S3, GCS, or MinIO.',
    category: 'lake',
    logo: logo('object_storage.svg'),
    color: '#569A31',
    fields: [
      {
        key: 'database',
        label: 'File URL',
        type: 'text',
        placeholder: 's3://my-bucket/warehouse.duckdb  or  https://host/data.parquet',
        required: true,
        width: 'full',
        help: 'An s3:// , gs:// , or https:// URL to a Parquet or DuckDB file.',
      },
      { key: 'endpoint', label: 'Endpoint', type: 'text', optional: true, placeholder: 's3.amazonaws.com (or MinIO host)', width: 'half' },
      { key: 'region', label: 'Region', type: 'text', optional: true, default: 'us-east-1', width: 'half' },
      { key: 'aws_access_key_id', label: 'Access key ID', type: 'text', optional: true, width: 'full' },
      { key: 'aws_secret_access_key', label: 'Secret access key', type: 'password', group: 'secret', optional: true, width: 'full' },
    ],
    summary: (cfg) => cfg.database || '',
  },
  // ── File-only ingestion sources (design §2 — FileConnectorMixin) ───────────
  // sftp/ftp are NOT queryable: they only expose a file interface, consumed by
  // the `file_ingest` flow task (or a Python ingest cell). They land in the
  // catalog so the "Add connector" picker can create them; the backend
  // ConnectorType literal lists both (backend/app/routes/connectors.py).
  {
    id: 'sftp',
    label: 'SFTP',
    description: 'Pull files over SSH (SFTP). Ingest into a queryable target.',
    category: 'lake',
    logo: logo('sftp.svg'),
    color: '#3B6E8F',
    fields: [
      { key: 'host', label: 'Host', type: 'text', placeholder: 'sftp.example.com', required: true, width: 'half' },
      { key: 'port', label: 'Port', type: 'number', default: 22, width: 'half' },
      { key: 'user', label: 'User', type: 'text', optional: true, width: 'half' },
      { key: 'root', label: 'Base path', type: 'text', optional: true, placeholder: '/uploads', width: 'half', help: 'Files are resolved relative to this directory.' },
      { key: 'password', label: 'Password', type: 'password', group: 'secret', optional: true, width: 'full', help: 'Supply a password OR a private key below.' },
      { key: 'private_key', label: 'Private key (PEM)', type: 'textarea', group: 'secret', optional: true, rows: 5, width: 'full', placeholder: '-----BEGIN OPENSSH PRIVATE KEY-----\n...' },
      { key: 'private_key_password', label: 'Key passphrase', type: 'password', group: 'secret', optional: true, width: 'full' },
      { key: 'host_key', label: 'Pinned host key', type: 'text', optional: true, width: 'full', placeholder: 'ssh-ed25519 AAAA...', help: 'Optional. Leave blank to trust-on-first-use; set to pin a known host key.' },
    ],
    summary: (cfg) => [cfg.host && cfg.host + (cfg.port ? `:${cfg.port}` : ''), cfg.user].filter(Boolean).join(' · '),
  },
  {
    id: 'ftp',
    label: 'FTP / FTPS',
    description: 'Pull files over FTP (FTPS by default). Ingest into a queryable target.',
    category: 'lake',
    logo: logo('ftp.svg'),
    color: '#6B7280',
    fields: [
      { key: 'host', label: 'Host', type: 'text', placeholder: 'ftp.example.com', required: true, width: 'half' },
      { key: 'port', label: 'Port', type: 'number', default: 21, width: 'half' },
      { key: 'user', label: 'User', type: 'text', optional: true, width: 'half', help: 'Leave blank for anonymous access.' },
      { key: 'root', label: 'Base path', type: 'text', optional: true, placeholder: '/uploads', width: 'half' },
      { key: 'password', label: 'Password', type: 'password', group: 'secret', optional: true, width: 'full' },
      {
        key: 'tls', label: 'Encryption', type: 'select', default: 'true', width: 'half',
        options: [{ value: 'true', label: 'FTPS (TLS)' }, { value: 'false', label: 'Plain FTP (insecure)' }],
        help: 'Plain FTP sends credentials and data unencrypted — use FTPS unless the server cannot.',
      },
      { key: 'passive', label: 'Passive mode', type: 'select', default: 'true', width: 'half', options: [{ value: 'true', label: 'Passive' }, { value: 'false', label: 'Active' }] },
    ],
    summary: (cfg) => [cfg.host && cfg.host + (cfg.port ? `:${cfg.port}` : ''), cfg.user || 'anonymous'].filter(Boolean).join(' · '),
  },
  // Legacy local/in-memory ``duckdb`` connector. No longer offered in the picker
  // (``hidden: true``) because a local path / in-memory DB is meaningless on
  // stateless Cloud-Run containers — but the type stays in the catalog so any
  // already-saved ``duckdb`` connector still renders with a label, logo, and
  // summary and remains queryable. Backend keeps the ``duckdb`` factory for
  // internal use (flows, demo data, fixtures).
  {
    id: 'duckdb',
    label: 'DuckDB (file)',
    description: 'Local DuckDB / file database.',
    category: 'lake',
    hidden: true,
    logo: logo('duckdb.svg'),
    color: '#FFF000',
    fields: [
      { key: 'database', label: 'Database (file path)', type: 'text', placeholder: '/data/analytics.duckdb', required: true, width: 'full' },
    ],
    summary: (cfg) => {
      if (cfg.in_memory === true || cfg.in_memory === 'true') return 'in-memory'
      return cfg.database || cfg.path || 'file'
    },
  },
  {
    // Built-in demo dataset — a VIRTUAL/system connector the backend injects
    // into every org's list (see backend/app/routes/connectors.py). It has no
    // configurable fields: it is removable (DELETE hides it per-org) and
    // re-addable (the picker POSTs {type:'demo'}). Marked system so the UI
    // hides the edit affordance and renders a read-only card.
    id: 'demo',
    label: 'Demo data',
    description: 'Built-in sample dataset — query it instantly, no setup.',
    category: 'lake',
    system: true,
    logo: logo('demo.svg'),
    color: '#17b3a3',
    fields: [],
    summary: () => 'Built-in sample dataset',
  },

  // ── APIs & custom ─────────────────────────────────────────────────────────
  {
    id: 'http_json',
    label: 'HTTP / JSON API',
    description: 'Any REST API that returns JSON.',
    category: 'api',
    logo: logo('http_json.svg'),
    color: '#0f9e90',
    fields: [
      { key: 'base_url', label: 'Base URL', type: 'url', placeholder: 'https://api.example.com/v1', required: true, width: 'full' },
      { key: 'token', label: 'Bearer token', type: 'password', group: 'secret', optional: true, width: 'full' },
      {
        key: 'headers',
        label: 'Extra headers (JSON)',
        type: 'textarea',
        optional: true,
        width: 'full',
        placeholder: '{\n  "X-Api-Key": "..."\n}',
        help: 'Non-secret headers only. Put auth tokens in the Bearer token field above.',
      },
    ],
    summary: (cfg) => cfg.base_url || '',
  },
  {
    id: 'jdbc',
    label: 'JDBC (custom driver)',
    description: 'Connect any JDBC-compatible source with a driver JAR.',
    category: 'api',
    logo: logo('jdbc.svg'),
    color: '#2456a6',
    fields: [
      { key: 'jdbc_url', label: 'JDBC URL', type: 'text', placeholder: 'jdbc:mysql://host:3306/db', required: true, width: 'full' },
      { key: 'driver_class', label: 'Driver class', type: 'text', placeholder: 'com.mysql.cj.jdbc.Driver', required: true, width: 'full' },
      { key: 'jar_path', label: 'Driver JAR path', type: 'text', placeholder: '/opt/drivers/mysql.jar', required: true, width: 'full' },
      { key: 'user', label: 'User', type: 'text', optional: true, width: 'half' },
      { key: 'password', label: 'Password', type: 'password', group: 'secret', optional: true, width: 'half' },
    ],
    summary: (cfg) => cfg.jdbc_url || '',
  },
]

// ── Lookups ───────────────────────────────────────────────────────────────

export function getTypeInfo(typeId) {
  return CONNECTOR_TYPES.find((t) => t.id === typeId) ?? CONNECTOR_TYPES[0]
}

/**
 * Connectors grouped by category for the "Add connector" picker.
 *
 * ``hidden`` types (e.g. the legacy ``duckdb_storage``, now folded into the
 * unified DuckDB connector) are excluded from the picker but remain in the
 * catalog so saved connectors of that type still render. ``system`` types
 * (e.g. the demo connector) ARE shown so users can re-add them after removal.
 */
export function getConnectorsByCategory() {
  return CONNECTOR_CATEGORIES.map((cat) => ({
    ...cat,
    connectors: CONNECTOR_TYPES.filter((c) => c.category === cat.id && !c.hidden),
  })).filter((cat) => cat.connectors.length > 0)
}

/** Seed an initial config object from a type's field defaults (config group). */
export function defaultsFor(typeId) {
  const info = getTypeInfo(typeId)
  const out = {}
  for (const f of info.fields ?? []) {
    if (f.default !== undefined && (f.group ?? 'config') === 'config') {
      out[f.key] = f.default
    }
  }
  return out
}
