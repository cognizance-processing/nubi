# Third-party marks: sources and licences

Every file under `public/logos/` is listed here. Nothing is hand-drawn: each mark is the vendor's own artwork (or a faithful redistribution of it) with the viewBox trimmed to the artwork and baked-in fills removed so colour can come from the page. Path data is never edited. The registry in `src/components/brand/registry.ts` carries the same facts as typed data.

**Trademarks.** Logos are trademarks of their owners. Nubi shows them only to identify the product or service a user connects to or that Nubi is built on ("Built on", "Connects to", "Works with"), which does not imply endorsement. Do not use them to suggest partnership. If an owner asks us to remove a mark, delete the file and its registry entry.

**Processing applied to every file.** (1) `viewBox` trimmed to the visible artwork (1/1000 unit precision) and `width`/`height`/`role`/`<title>` removed. (2) For flat glyphs, `fill`/`stroke` colour attributes removed so the mark can be masked and coloured by the page. (3) For multi-colour service icons (AWS, Azure) the artwork is untouched apart from step 1. Generic glyphs (not brands) come from Lucide and use a 1.75px stroke.

**Sources used.**
- simple-icons (https://simpleicons.org, https://github.com/simple-icons/simple-icons), CC0-1.0, fetched from jsDelivr at the pinned version shown per file. Some brands were removed from later simple-icons releases at the owners' request; those files stay pinned at 13.21.0.
- Devicon (https://github.com/devicons/devicon), MIT.
- Microsoft Azure Architecture icons (https://learn.microsoft.com/azure/architecture/icons/) and AWS Architecture Icons (https://aws.amazon.com/architecture/icons/), used unmodified to represent the named service.
- Lucide (https://lucide.dev), ISC, for generic non-brand glyphs.

Brand-mode colours: `hex` on light surfaces, `darkHex` on dark. Official colour unless it would not read as a mark (pale yellow, near-black on dark), where the vendor's black/white treatment is used.

| File | Mark | Source | Licence | Official colour | Trademark owner |
|---|---|---|---|---|---|
| `tools/duckdb.svg` | DuckDB | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/duckdb.svg | CC0-1.0 (simple-icons) | FFF000 | DuckDB Foundation |
| `tools/apachearrow.svg` | Apache Arrow | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/apachearrow.svg | Apache-2.0 (simple-icons; derived from the Apache Arrow logo) | 000000 | The Apache Software Foundation |
| `tools/apacheecharts.svg` | Apache ECharts | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/apacheecharts.svg | Apache-2.0 (simple-icons; derived from the Apache ECharts logo) | AA344D | The Apache Software Foundation |
| `tools/postgresql.svg` | PostgreSQL | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/postgresql.svg | CC0-1.0 (simple-icons) | 4169E1 | PostgreSQL Community Association of Canada |
| `tools/mysql.svg` | MySQL | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/mysql.svg (dolphin only: the two subpaths of the dolphin, converted to absolute coordinates and cropped to their bounds; the "MySQL" wordmark is dropped as it is unreadable at 24px) | CC0-1.0 (simple-icons) | 4479A1 | Oracle Corporation |
| `tools/snowflake.svg` | Snowflake | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/snowflake.svg | CC0-1.0 (simple-icons) | 29B5E8 | Snowflake Inc. |
| `tools/googlebigquery.svg` | Google BigQuery | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/googlebigquery.svg | CC0-1.0 (simple-icons) | 669DF6 | Google LLC |
| `tools/databricks.svg` | Databricks | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/databricks.svg | CC0-1.0 (simple-icons) | FF3621 | Databricks, Inc. |
| `tools/clickhouse.svg` | ClickHouse | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/clickhouse.svg | CC0-1.0 (simple-icons) | FFCC01 | ClickHouse, Inc. |
| `tools/anthropic.svg` | Anthropic | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/anthropic.svg | CC0-1.0 (simple-icons) | 191919 | Anthropic, PBC |
| `tools/claude.svg` | Claude | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/claude.svg | CC0-1.0 (simple-icons) | D97757 | Anthropic, PBC |
| `tools/claudecode.svg` | Claude Code | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/claudecode.svg | CC0-1.0 (simple-icons) | D97757 | Anthropic, PBC |
| `tools/openai.svg` | OpenAI | https://cdn.jsdelivr.net/npm/simple-icons@13.21.0/icons/openai.svg | CC0-1.0 (simple-icons); removed from later simple-icons releases, so pinned at 13.21.0 | 412991 (we render ink, matching OpenAI's current black mark) | OpenAI, Inc. |
| `tools/googlegemini.svg` | Google Gemini | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/googlegemini.svg | CC0-1.0 (simple-icons) | 8E75B2 | Google LLC |
| `tools/google.svg` | Google | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/google.svg | CC0-1.0 (simple-icons) | 4285F4 | Google LLC |
| `tools/github.svg` | GitHub | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/github.svg | CC0-1.0 (simple-icons) | 181717 | GitHub, Inc. |
| `tools/docker.svg` | Docker | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/docker.svg | CC0-1.0 (simple-icons) | 2496ED | Docker, Inc. |
| `tools/kubernetes.svg` | Kubernetes | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/kubernetes.svg | CC0-1.0 (simple-icons) | 326CE5 | The Linux Foundation |
| `tools/dbt.svg` | dbt | https://cdn.jsdelivr.net/npm/simple-icons@13.21.0/icons/dbt.svg | CC0-1.0 (simple-icons); removed from later simple-icons releases, so pinned at 13.21.0 | FF694B | dbt Labs, Inc. |
| `tools/modelcontextprotocol.svg` | Model Context Protocol | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/modelcontextprotocol.svg | CC0-1.0 (simple-icons) (mark from the MCP project site, https://modelcontextprotocol.io) | 000000 | The Linux Foundation / MCP project |
| `tools/ollama.svg` | Ollama | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/ollama.svg | CC0-1.0 (simple-icons) | 000000 | Ollama |
| `connectors/mariadb.svg` | MariaDB | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/mariadb.svg | CC0-1.0 (simple-icons) | 003545 | MariaDB Foundation |
| `connectors/microsoftsqlserver.svg` | Microsoft SQL Server | https://api.iconify.design/devicon-plain/microsoftsqlserver.svg (Devicon, https://github.com/devicons/devicon) | MIT (Devicon) | CC2927 | Microsoft Corporation |
| `connectors/oracle.svg` | Oracle | https://cdn.jsdelivr.net/npm/simple-icons@13.21.0/icons/oracle.svg | CC0-1.0 (simple-icons); removed from later simple-icons releases, so pinned at 13.21.0 | F80000 | Oracle Corporation |
| `connectors/cockroachlabs.svg` | CockroachDB | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/cockroachlabs.svg | CC0-1.0 (simple-icons) | 6933FF | Cockroach Labs, Inc. |
| `connectors/googlecloud.svg` | Google Cloud | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/googlecloud.svg | CC0-1.0 (simple-icons) | 4285F4 | Google LLC |
| `connectors/googlecloudstorage.svg` | Google Cloud Storage | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/googlecloudstorage.svg | CC0-1.0 (simple-icons) | AECBFA (too pale on white, so we render Google blue) | Google LLC |
| `connectors/trino.svg` | Trino | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/trino.svg | CC0-1.0 (simple-icons) | DD00A1 | Trino Software Foundation |
| `connectors/presto.svg` | Presto | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/presto.svg | CC0-1.0 (simple-icons) | 5890FF | The Linux Foundation (Presto Foundation) |
| `connectors/sqlite.svg` | SQLite | https://cdn.jsdelivr.net/npm/simple-icons@16.30.0/icons/sqlite.svg | CC0-1.0 (simple-icons) | 003B57 | SQLite Consortium (public-domain software; the logo is a trademark of the project) |
| `connectors/azuresqldatabase.svg` | Azure SQL Database | https://raw.githubusercontent.com/benc-uk/icon-collection/master/azure-icons/SQL-Database.svg (mirror of the Microsoft Azure Architecture icon set, https://learn.microsoft.com/azure/architecture/icons/) | Microsoft Azure icon terms: use unmodified to represent the Azure service (https://learn.microsoft.com/azure/architecture/icons/) | multi-colour artwork | Microsoft Corporation |
| `connectors/azuresynapse.svg` | Azure Synapse Analytics | https://raw.githubusercontent.com/benc-uk/icon-collection/master/azure-icons/Azure-Synapse-Analytics.svg (mirror of the Microsoft Azure Architecture icon set, https://learn.microsoft.com/azure/architecture/icons/) | Microsoft Azure icon terms: use unmodified to represent the Azure service | multi-colour artwork | Microsoft Corporation |
| `connectors/amazonathena.svg` | Amazon Athena | AWS Architecture Icons, https://aws.amazon.com/architecture/icons/ (distributed in gilbarbara/logos as aws-athena; https://api.iconify.design/logos/aws-athena.svg) | AWS Architecture Icons terms: use unmodified to represent the AWS service | multi-colour artwork | Amazon Web Services, Inc. |
| `connectors/amazonredshift.svg` | Amazon Redshift | AWS Architecture Icons, https://aws.amazon.com/architecture/icons/ (distributed in gilbarbara/logos as aws-redshift; https://api.iconify.design/logos/aws-redshift.svg) | AWS Architecture Icons terms: use unmodified to represent the AWS service | multi-colour artwork | Amazon Web Services, Inc. |
| `connectors/amazons3.svg` | Amazon S3 | AWS Architecture Icons, https://aws.amazon.com/architecture/icons/ (distributed in gilbarbara/logos as aws-s3; https://api.iconify.design/logos/aws-s3.svg) | AWS Architecture Icons terms: use unmodified to represent the AWS service | multi-colour artwork | Amazon Web Services, Inc. |
| `connectors/demo.svg` | Demo data | Lucide "box", https://lucide.dev/icons/box | ISC (Lucide) | n/a | none (generic icon, not a brand) |
| `connectors/ftp.svg` | FTP | Lucide "folder-up", https://lucide.dev/icons/folder-up | ISC (Lucide) | n/a | none (generic icon, not a brand) |
| `connectors/sftp.svg` | SFTP | Lucide "folder-lock", https://lucide.dev/icons/folder-lock | ISC (Lucide) | n/a | none (generic icon, not a brand) |
| `connectors/http-json.svg` | HTTP / JSON API | Lucide "braces", https://lucide.dev/icons/braces | ISC (Lucide) | n/a | none (generic icon, not a brand) |
| `connectors/jdbc.svg` | JDBC | Lucide "database", https://lucide.dev/icons/database | ISC (Lucide) | n/a | none (generic icon, not a brand) |
