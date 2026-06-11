# Nubi CLI

A Python CLI for the [Nubi](https://github.com/imranparuk/nubi) REST API.  
Implements **dashboards-as-code** workflows: deploy, diff, pull, and run queries.

---

## Installation

```bash
cd cli
pip install -r requirements.txt
pip install -e .          # registers the `nubi` console script
```

Or run directly without installing:

```bash
cd cli
python -m nubi_cli.main --help
```

---

## Configuration

| Source | Variable | Default |
|--------|----------|---------|
| Env var | `NUBI_API_URL` | `http://localhost:8000/api/v1` |
| Env var | `NUBI_TOKEN` | — |
| File | `~/.nubi/credentials` (JSON) | — |

`NUBI_TOKEN` overrides a stored token.  `nubi login` writes to `~/.nubi/credentials`.

---

## Commands

### `nubi login`

Authenticate and save your access token locally.

```bash
nubi login
# Email: you@example.com
# Password: ••••••
```

### `nubi deploy <dir> [--dry-run]`

Push all `*.json` resource files in `<dir>` to the API.

Each JSON file must contain at minimum:

```json
{
  "resource": "boards",
  "name": "My Dashboard",
  "config": {}
}
```

If an `"id"` key is present the resource is updated (`PUT`); otherwise it is
created (`POST`).

```bash
# Preview without writing
nubi deploy ./dashboards --dry-run

# Live deploy
nubi deploy ./dashboards
```

### `nubi run <query_id>`

Execute a registered query and print the row count.

```bash
nubi run 3fa85f64-5717-4562-b3fc-2c963f66afa6
# Query '3fa8...' returned 1,234 rows.
```

Requires `pyarrow` for row counting; falls back to byte count otherwise.

### `nubi diff <dir>`

Compare local resource files against the server state.  **Read-only** — no
writes are made.

```bash
nubi diff ./dashboards
# board.json (id=board-123)
#   - name: 'Old Name'
#   + name: 'New Name'
# new_resource.json — NEW (no id)
```

### `nubi pull <resource> <dir>`

Download all server resources of a given type to local JSON files.

```bash
nubi pull boards ./downloaded/boards
# Wrote ./downloaded/boards/board-123.json
# Pulled 3 boards.
```

Valid resource types: `datastores`, `boards`, `widgets`, `queries`.

---

## Bridge agent (Bridge v2)

The bridge agent ships inside the CLI. It runs on a customer machine, dials
**out** to the Nubi control plane over the existing WebSocket reverse tunnel
(no inbound firewall holes), and runs ingestion tasks for sources reachable
only inside the customer network (SFTP/FTP/bucket).

```bash
pip install nubi[bridge]                 # core CLI + the websockets dep
nubi bridge start --token nubi_br_…      # token via flag…
# …or store it once and start without the flag:
nubi bridge configure --token nubi_br_… --bridge-id <id> \
                      --control-plane-url wss://api.nubi.dev/api/v1
nubi bridge start
nubi bridge status                       # show identity (token presence only)
```

**Token resolution** (highest wins): `--token` flag > `NUBI_BRIDGE_TOKEN` env >
`~/.nubi/bridge.json`. The token authenticates the **control channel only** —
it lets the agent claim this bridge's tasks but reads no org data and no
connector secrets. It is presented on the handshake and every heartbeat; on a
*bridge revoked* / auth-reject the agent exits cleanly (code 2).

**How a task runs (memory-only credentials):** the agent claims a `file_ingest`
task, receives an ephemeral **write-only, prefix-pinned, short-TTL staging
grant** (presigned PUT URLs / STS token) over the tunnel — held in memory only,
never written to disk — streams the local source to staging in bounded chunks,
and reports the manifest `{files:[{path,size,sha256}], row_counts}`. The central
worker verifies + promotes/loads; the agent never receives a stored connector
secret.

| Source | Variable | File |
|--------|----------|------|
| Bridge token | `NUBI_BRIDGE_TOKEN` | `~/.nubi/bridge.json` (`token`) |
| Bridge id | `NUBI_BRIDGE_ID` | `~/.nubi/bridge.json` (`bridge_id`) |
| Control plane URL | `NUBI_CONTROL_PLANE_URL` | `~/.nubi/bridge.json` (`control_plane_url`) |

---

## Running Tests

```bash
cd cli
pip install -r requirements.txt
python -m pytest tests -q
```

---

## File Layout

```
cli/
├── nubi_cli/
│   ├── __init__.py     # package version
│   ├── config.py        # URL + token helpers (env / ~/.nubi/credentials)
│   ├── client.py        # thin httpx wrapper with CLIError
│   ├── bridge_config.py # bridge token/id resolution (~/.nubi/bridge.json)
│   ├── bridge_agent.py  # bridge control channel + agent-side ingest (§7)
│   ├── bridge_sources.py# local file-source opener + presigned staging uploader
│   └── main.py          # typer app: login / deploy / run / diff / pull / bridge
├── tests/
│   ├── test_cli.py        # CliRunner tests (no real HTTP)
│   └── test_cli_bridge.py # bridge agent: handshake/revoke/ingest/manifest
├── requirements.txt
├── setup.py
└── README.md
```
