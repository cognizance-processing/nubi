/**
 * sqlExamples.js — canned SQL snippets for the SqlCell snippet picker.
 *
 * These replace the removed advanced palette kinds with copy-in templates. The
 * "Load from object storage (bucket)" snippet is the `bucket_load` replacement —
 * a SELECT over the object-storage connector. Set the cell's "Run against"
 * connector (config.datastore_id) to your storage/datastore connector.
 *
 * Each entry has a label and a sql string.
 */

export const SQL_EXAMPLES = [
  {
    label: 'Load from object storage (bucket)',
    sql: `-- Load Parquet/CSV/JSON from object storage (the bucket_load replacement).
-- Point the cell's "Run against" connector at your object-storage / datastore
-- connector, then read the files directly:
SELECT *
FROM read_parquet('s3://my-bucket/path/*.parquet')
LIMIT 1000;

-- CSV:  SELECT * FROM read_csv_auto('s3://my-bucket/path/*.csv')
-- JSON: SELECT * FROM read_json_auto('s3://my-bucket/path/*.json')`,
  },
  {
    label: 'Reference an upstream cell',
    sql: `-- Reference an earlier cell's result by its key (registered as a table).
SELECT *
FROM upstream_cell_key
LIMIT 100;`,
  },
  {
    label: 'Aggregate by group',
    sql: `SELECT
  group_column,
  COUNT(*)        AS n,
  SUM(value)      AS total,
  AVG(value)      AS mean
FROM source_table
GROUP BY group_column
ORDER BY total DESC;`,
  },
]
