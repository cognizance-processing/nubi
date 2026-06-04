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
│   ├── config.py       # URL + token helpers (env / ~/.nubi/credentials)
│   ├── client.py       # thin httpx wrapper with CLIError
│   └── main.py         # typer app: login / deploy / run / diff / pull
├── tests/
│   └── test_cli.py     # CliRunner tests (no real HTTP)
├── requirements.txt
├── setup.py
└── README.md
```
