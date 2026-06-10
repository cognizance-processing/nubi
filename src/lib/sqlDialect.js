/**
 * sqlDialect.js — Map connector_type → SQL dialect config for Monaco.
 *
 * Used by CodeEditor and SqlEditor to pick the right dialect token set
 * when applying syntax hints. Monaco's built-in 'sql' language handles
 * basic highlighting; dialect-specific reserved words are surfaced as
 * completion hints but don't change the tokeniser (Monaco doesn't have
 * separate BigQuery / DuckDB grammars out of the box).
 *
 * Exports:
 *   CONNECTOR_DIALECT_MAP  — connector_type → dialect string
 *   dialectForConnectorType(connectorType) → dialect string
 *   SQL_KEYWORDS_BY_DIALECT — dialect → extra keyword set
 */

// ---------------------------------------------------------------------------
// Connector type → dialect string
// ---------------------------------------------------------------------------

export const CONNECTOR_DIALECT_MAP = {
  postgres: 'postgres',
  postgresql: 'postgres',
  redshift: 'postgres',
  duckdb: 'duckdb',
  motherduck: 'duckdb',
  mysql: 'mysql',
  mariadb: 'mysql',
  bigquery: 'bigquery',
  http_json: 'duckdb',
  none: 'duckdb',
}

const DEFAULT_DIALECT = 'duckdb'

/**
 * Map a connector_type string to one of the four supported dialect keys.
 * Handles upper/lower case and unknown types (falls back to 'duckdb').
 */
export function dialectForConnectorType(connectorType) {
  if (!connectorType) return DEFAULT_DIALECT
  const key = connectorType.toString().toLowerCase().trim()
  return CONNECTOR_DIALECT_MAP[key] ?? DEFAULT_DIALECT
}

// ---------------------------------------------------------------------------
// Per-dialect extra SQL keywords (supplements Monaco's built-in sql set)
// ---------------------------------------------------------------------------

const BASE_KEYWORDS = [
  'SELECT', 'FROM', 'WHERE', 'GROUP BY', 'ORDER BY', 'HAVING', 'LIMIT',
  'OFFSET', 'JOIN', 'LEFT JOIN', 'RIGHT JOIN', 'INNER JOIN', 'FULL JOIN',
  'CROSS JOIN', 'ON', 'AS', 'AND', 'OR', 'NOT', 'IN', 'IS NULL',
  'IS NOT NULL', 'LIKE', 'ILIKE', 'BETWEEN', 'DISTINCT', 'COUNT', 'SUM',
  'AVG', 'MIN', 'MAX', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'WITH',
  'UNION', 'UNION ALL', 'EXCEPT', 'INTERSECT', 'CAST', 'COALESCE',
  'NULLIF', 'EXTRACT', 'DATE_TRUNC', 'NOW', 'CURRENT_DATE', 'INTERVAL',
  'ASC', 'DESC', 'OVER', 'PARTITION BY', 'ROW_NUMBER', 'RANK', 'DENSE_RANK',
  'LAG', 'LEAD', 'FIRST_VALUE', 'LAST_VALUE', 'NTILE', 'PERCENT_RANK',
  'CUME_DIST', 'CREATE', 'INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER',
  'TABLE', 'VIEW', 'INDEX', 'SCHEMA', 'DATABASE', 'EXISTS', 'IF NOT EXISTS',
]

const DUCKDB_EXTRA = [
  'READ_CSV', 'READ_PARQUET', 'READ_JSON', 'SUMMARIZE', 'DESCRIBE',
  'COPY', 'EXPORT DATABASE', 'IMPORT DATABASE', 'ATTACH', 'DETACH',
  'PIVOT', 'UNPIVOT', 'ASOF JOIN', 'POSITIONAL JOIN', 'LATERAL',
  'RANGE BETWEEN', 'ROWS BETWEEN', 'EXCLUDE', 'REPLACE', 'STRUCT',
  'LIST', 'MAP', 'UNION', 'ENUM', 'QUALIFY', 'SAMPLE', 'USING SAMPLE',
  'TRY_CAST', 'IFNULL', 'LIST_AGG', 'ARRAY_AGG', 'STRING_AGG',
  'APPROX_COUNT_DISTINCT', 'MEDIAN', 'PERCENTILE_CONT', 'PERCENTILE_DISC',
]

const BIGQUERY_EXTRA = [
  'ARRAY', 'STRUCT', 'UNNEST', 'GENERATE_ARRAY', 'GENERATE_DATE_ARRAY',
  'GENERATE_TIMESTAMP_ARRAY', 'ARRAY_AGG', 'ARRAY_LENGTH', 'ARRAY_TO_STRING',
  'STRING_AGG', 'ANY_VALUE', 'COUNTIF', 'LOGICAL_AND', 'LOGICAL_OR',
  'APPROX_COUNT_DISTINCT', 'APPROX_QUANTILES', 'APPROX_TOP_COUNT',
  'APPROX_TOP_SUM', 'DATE', 'DATETIME', 'TIMESTAMP', 'TIME', 'GEOGRAPHY',
  'TABLESAMPLE', 'FOR SYSTEM_TIME AS OF', 'PIVOT', 'UNPIVOT',
  'QUALIFY', 'WINDOW', 'EXCEPT', 'REPLACE', 'SAFE_CAST', 'SAFE.',
  'MERGE', 'MATCHED', 'NOT MATCHED', 'BY SOURCE', 'BY TARGET',
]

const POSTGRES_EXTRA = [
  'RETURNING', 'ON CONFLICT', 'DO NOTHING', 'DO UPDATE', 'EXCLUDED',
  'SERIAL', 'BIGSERIAL', 'UUID', 'JSONB', 'JSON', 'HSTORE', 'ARRAY',
  'UNNEST', 'GENERATE_SERIES', 'STRING_AGG', 'ARRAY_AGG',
  'FILTER', 'WITHIN GROUP', 'PERCENTILE_CONT', 'PERCENTILE_DISC',
  'LATERAL', 'TABLESAMPLE', 'MATERIALIZED', 'REFRESH MATERIALIZED VIEW',
  'VACUUM', 'ANALYZE', 'EXPLAIN', 'WITH RECURSIVE', 'WINDOW',
  'ILIKE', 'SIMILAR TO', 'ISNULL', 'NOTNULL', 'FETCH', 'FOR UPDATE',
]

const MYSQL_EXTRA = [
  'FULLTEXT', 'SPATIAL', 'STRAIGHT_JOIN', 'SQL_NO_CACHE', 'HIGH_PRIORITY',
  'LOW_PRIORITY', 'DELAYED', 'INSERT IGNORE', 'REPLACE INTO',
  'ON DUPLICATE KEY UPDATE', 'AUTO_INCREMENT', 'UNSIGNED', 'ZEROFILL',
  'TINYINT', 'MEDIUMINT', 'TINYTEXT', 'MEDIUMTEXT', 'LONGTEXT',
  'ENUM', 'SET', 'GROUP_CONCAT', 'JSON_EXTRACT', 'JSON_TABLE',
  'EXPLAIN FORMAT', 'SHOW TABLES', 'SHOW COLUMNS', 'SHOW INDEX',
  'IFNULL', 'IF', 'FIND_IN_SET',
]

export const SQL_KEYWORDS_BY_DIALECT = {
  duckdb: [...BASE_KEYWORDS, ...DUCKDB_EXTRA],
  bigquery: [...BASE_KEYWORDS, ...BIGQUERY_EXTRA],
  postgres: [...BASE_KEYWORDS, ...POSTGRES_EXTRA],
  mysql: [...BASE_KEYWORDS, ...MYSQL_EXTRA],
}

/** Get the keyword list for a given dialect (falls back to base + duckdb). */
export function keywordsForDialect(dialect) {
  return SQL_KEYWORDS_BY_DIALECT[dialect] ?? SQL_KEYWORDS_BY_DIALECT.duckdb
}
