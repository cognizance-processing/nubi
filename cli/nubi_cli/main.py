"""Nubi CLI — files-as-code entry point (see docs/files-as-code.md).

Top-level commands
------------------
login / logout / whoami   Auth: save / clear token, show identity.
init [--project --ci]     Scaffold nubi.yaml + .nubi/ + .gitignore (+ CI template).
pull [--kinds]            Download ALL resources into the canonical file tree (A).
push [--dry-run]          Upload changed non-secret manifests (POST /import).
sync --env-id [--strategy] Two-way reconcile via the project's git binding.
deploy [--env]            CI deploy: materialize secrets, push manifests + secrets.
diff                      Compare local resource files vs server (read-only).
run <query-id>            Execute a registered query and report rows.
status                    Show project binding, env, last-sync commit graph.
deploy-files / pull-raw   Legacy flat-JSON workflows (superseded by push/pull).

Sub-apps
--------
flows run|push|pull
dashboards pull|push       queries pull|push       connectors pull|push|test
secrets set|list|pull|push|materialize|delete
git connect|graph

Secrets live in the gitignored .nubi/secrets/{connectors,flow}.env tree;
`secrets push --target github|gitlab` syncs them to the CI secret store, and
`secrets materialize` expands NUBI_SECRET__* / NUBI_CONNECTOR__* env vars back
into the .env files for pipeline use.

Run as a module: python -m nubi_cli.main --help
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from . import client as _client
from . import project as _project
from . import secrets_files as _secrets_files
from . import vcs_secrets as _vcs
from .client import CLIError
from .config import clear_token, load_token, save_token
from .flows_files import FlowFileError, dump_flow, load_flow_file

app = typer.Typer(
    name="nubi",
    help="Nubi CLI — dashboards-as-code, query runner, and resource manager.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True, style="bold red")

# ---------------------------------------------------------------------------
# Sub-apps: flows + secrets
# ---------------------------------------------------------------------------

flows_app = typer.Typer(
    name="flows",
    help="Manage and run Nubi flows (local execution and cloud sync).",
    no_args_is_help=True,
)
app.add_typer(flows_app, name="flows")

secrets_app = typer.Typer(
    name="secrets",
    help="Manage secrets locally and via the Nubi API.",
    no_args_is_help=True,
)
app.add_typer(secrets_app, name="secrets")

dashboards_app = typer.Typer(
    name="dashboards", help="Sync dashboards as files.", no_args_is_help=True
)
app.add_typer(dashboards_app, name="dashboards")

queries_app = typer.Typer(
    name="queries", help="Sync queries as files (3-file form).", no_args_is_help=True
)
app.add_typer(queries_app, name="queries")

connectors_app = typer.Typer(
    name="connectors", help="Sync connectors (non-secret manifests + secrets).", no_args_is_help=True
)
app.add_typer(connectors_app, name="connectors")

git_app = typer.Typer(
    name="git", help="Bind the project to a remote and inspect its commit graph.", no_args_is_help=True
)
app.add_typer(git_app, name="git")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RESOURCE_TYPES = ("datastores", "boards", "widgets", "queries")

#: list endpoint + export envelope kind per portable kind (doc D tables).
_KIND_LIST_ENDPOINT = {
    "dashboard": "boards",
    "query": "queries",
    "flow": "flows",
    "connector": "connectors",
}


def _require_login() -> None:
    """Exit with an actionable error when no token is available."""
    if not load_token():
        err_console.print("Not logged in.  Run 'nubi login' first, or set NUBI_TOKEN.")
        raise typer.Exit(code=1)


def _project_root() -> Path:
    """Return the project root (cwd) — where nubi.yaml / .nubi live."""
    return Path(".")


def _resolve_project_id(root: Path) -> str | None:
    """Resolve the bound project_id from .nubi/project.json (then nubi.yaml)."""
    pointer = _project.read_project_json(root)
    pid = pointer.get("project_id")
    if pid:
        return str(pid)
    manifest = _project.read_nubi_yaml(root)
    meta = manifest.get("metadata") or {}
    return str(meta["id"]) if meta.get("id") else None


def _project_headers(root: Path) -> dict[str, str]:
    """Build the X-Project-Id header when the project is bound (scopes the API)."""
    pid = _resolve_project_id(root)
    return {"X-Project-Id": pid} if pid else {}


def _list_resources(endpoint: str, headers: dict[str, str] | None = None) -> list[dict]:
    """GET a list endpoint, tolerating list-or-wrapped-list responses."""
    resp = _client.get(endpoint, headers=headers or {})
    items = resp.json()
    if isinstance(items, dict):
        items = items.get(endpoint, items.get("items", []))
    return items if isinstance(items, list) else []


def _export_envelope(kind: str, resource_id: str, headers: dict[str, str] | None = None) -> dict:
    """GET /export/{kind}/{id}?format=json → parsed envelope dict."""
    resp = _client.get(
        f"export/{kind}/{resource_id}", params={"format": "json"}, headers=headers or {}
    )
    return resp.json()


def _load_resource_files(directory: Path) -> list[tuple[Path, dict]]:
    """Return list of (path, parsed_json) for all *.json files in *directory*."""
    files = sorted(directory.glob("*.json"))
    if not files:
        console.print(f"[yellow]No *.json files found in {directory}[/yellow]")
    results = []
    for f in files:
        try:
            data = json.loads(f.read_text())
        except json.JSONDecodeError as exc:
            err_console.print(f"Skipping {f.name}: invalid JSON — {exc}")
            continue
        results.append((f, data))
    return results


def _resource_path(resource: str, resource_id: str | None = None) -> str:
    """Build a resource URL path segment like 'datastores' or 'datastores/<id>'."""
    if resource_id:
        return f"{resource}/{resource_id}"
    return resource


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


@app.command()
def login(
    email: str = typer.Option(..., prompt=True, help="Account e-mail address"),
    password: str = typer.Option(
        ..., prompt=True, hide_input=True, help="Account password"
    ),
) -> None:
    """Authenticate with the Nubi API and save your access token."""
    try:
        resp = _client.post("auth/login", json={"email": email, "password": password})
    except CLIError as exc:
        err_console.print(f"Login failed: {exc.message}")
        raise typer.Exit(code=1)

    body = resp.json()
    token = body.get("access_token")
    if not token:
        err_console.print("Login failed: server did not return an access_token.")
        raise typer.Exit(code=1)

    save_token(token)
    console.print("[green]Logged in successfully. Token saved.[/green]")


# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------


@app.command("deploy-files")
def deploy_files(
    directory: Path = typer.Argument(
        ..., exists=True, file_okay=False, help="Directory containing *.json resource files"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print planned actions without making any API calls"
    ),
) -> None:
    """Legacy: deploy raw dashboards-as-code *.json from *directory* (resource/name).

    Superseded by ``nubi push`` for the canonical file tree; retained for the
    flat-JSON workflow. If an ``id`` is present the resource is updated (PUT);
    otherwise created (POST). ``--dry-run`` makes no HTTP calls.
    """
    resources = _load_resource_files(directory)
    if not resources:
        raise typer.Exit(code=0)

    tbl = Table("File", "Resource", "Action", title="Deploy plan")
    plan: list[tuple[Path, dict, str]] = []

    for path, data in resources:
        resource_type = data.get("resource")
        if not resource_type:
            err_console.print(
                f"Skipping {path.name}: missing required 'resource' field."
            )
            continue
        if resource_type not in _RESOURCE_TYPES:
            err_console.print(
                f"Skipping {path.name}: unknown resource type {resource_type!r}. "
                f"Choose from: {', '.join(_RESOURCE_TYPES)}"
            )
            continue
        if not data.get("name"):
            err_console.print(
                f"Skipping {path.name}: missing required 'name' field."
            )
            continue
        resource_id = data.get("id")
        action = "UPDATE" if resource_id else "CREATE"
        plan.append((path, data, action))
        tbl.add_row(path.name, resource_type, action)

    if not plan:
        err_console.print("No valid resource files to deploy.")
        raise typer.Exit(code=1)

    console.print(tbl)

    if dry_run:
        console.print("[yellow]Dry run — no API calls made.[/yellow]")
        return

    # Live deploy
    for path, data, action in plan:
        resource_type = data.get("resource", "")
        resource_id = data.get("id")
        name = data.get("name", path.stem)
        config = data.get("config", {})
        payload = {"name": name, "config": config}

        try:
            if action == "UPDATE":
                resp = _client.put(
                    _resource_path(resource_type, resource_id), json=payload
                )
                console.print(f"[green]UPDATED[/green] {name} ({path.name})")
            else:
                resp = _client.post(_resource_path(resource_type), json=payload)
                created_id = resp.json().get("id", "?")
                console.print(
                    f"[green]CREATED[/green] {name} ({path.name}) → id={created_id}"
                )
        except CLIError as exc:
            err_console.print(f"Failed {action} {path.name}: {exc.message}")


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@app.command()
def run(
    query_id: str = typer.Argument(..., help="Registered query ID to execute"),
) -> None:
    """Execute a registered query and report the number of rows returned.

    The server responds with an Arrow IPC stream. If *pyarrow* is installed the
    rows are counted precisely; otherwise the raw byte length is reported.
    """
    try:
        resp = _client.post("query", json={"query_id": query_id})
    except CLIError as exc:
        err_console.print(f"Query failed: {exc.message}")
        raise typer.Exit(code=1)

    raw = resp.content

    try:
        import pyarrow.ipc as pa_ipc  # type: ignore
        import io

        reader = pa_ipc.open_stream(io.BytesIO(raw))
        table = reader.read_all()
        console.print(
            f"[green]Query {query_id!r} returned {table.num_rows:,} row(s).[/green]"
        )
    except ImportError:
        console.print(
            f"[yellow]Query {query_id!r} returned {len(raw):,} bytes "
            "(install pyarrow for row count).[/yellow]"
        )
    except Exception as exc:
        console.print(
            f"[yellow]Query {query_id!r} returned {len(raw):,} bytes "
            f"(could not parse Arrow: {exc}).[/yellow]"
        )


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


@app.command()
def diff(
    directory: Path = typer.Argument(
        ..., exists=True, file_okay=False, help="Directory containing local *.json files"
    ),
) -> None:
    """Show differences between local resource files and the server state.

    Read-only — no writes are made. Resources without an ``id`` are marked NEW.
    """
    resources = _load_resource_files(directory)
    if not resources:
        raise typer.Exit(code=0)

    any_diff = False
    for path, local in resources:
        resource_type = local.get("resource", "")
        resource_id = local.get("id")

        if not resource_id:
            console.print(f"[cyan]NEW[/cyan]  {path.name} — no id, would be created")
            any_diff = True
            continue

        # Fetch server copy
        try:
            resp = _client.get(_resource_path(resource_type, resource_id))
        except CLIError as exc:
            if exc.status == 404:
                console.print(
                    f"[cyan]NEW[/cyan]  {path.name} — id={resource_id} not found on server"
                )
            else:
                err_console.print(
                    f"Error fetching {path.name}: {exc.message}"
                )
            any_diff = True
            continue

        server = resp.json()
        diffs_found = False

        for field in ("name", "config"):
            local_val = local.get(field)
            server_val = server.get(field)
            if local_val != server_val:
                if not diffs_found:
                    console.print(f"[bold]{path.name}[/bold] (id={resource_id})")
                    diffs_found = True
                    any_diff = True
                console.print(f"  [red]- {field}: {server_val!r}[/red]")
                console.print(f"  [green]+ {field}: {local_val!r}[/green]")

        if not diffs_found:
            console.print(f"[dim]OK[/dim]  {path.name} — no changes")

    if not any_diff:
        console.print("[green]All resources match the server.[/green]")


# ---------------------------------------------------------------------------
# pull-raw (legacy single-resource dump)
# ---------------------------------------------------------------------------


@app.command("pull-raw")
def pull_raw(
    resource: str = typer.Argument(
        ..., help=f"Resource type to pull: {', '.join(_RESOURCE_TYPES)}"
    ),
    directory: Path = typer.Argument(
        ..., help="Destination directory (created if absent)"
    ),
) -> None:
    """Legacy: download all server rows of *resource* type to ``<id>.json`` files.

    Superseded by ``nubi pull`` (canonical file tree). Retained for raw dumps.
    """
    if resource not in _RESOURCE_TYPES:
        err_console.print(
            f"Unknown resource type {resource!r}. "
            f"Choose from: {', '.join(_RESOURCE_TYPES)}"
        )
        raise typer.Exit(code=1)

    try:
        resp = _client.get(resource)
    except CLIError as exc:
        err_console.print(f"Failed to list {resource}: {exc.message}")
        raise typer.Exit(code=1)

    items = resp.json()
    if not isinstance(items, list):
        # Some APIs wrap lists in a key
        items = items.get(resource, items.get("items", []))

    directory.mkdir(parents=True, exist_ok=True)
    for item in items:
        item_id = item.get("id", "unknown")
        out = directory / f"{item_id}.json"
        # Annotate with the resource type so deploy can use it
        item.setdefault("resource", resource)
        out.write_text(json.dumps(item, indent=2))
        console.print(f"[green]Wrote[/green] {out}")

    console.print(f"Pulled {len(items)} {resource}.")


# ---------------------------------------------------------------------------
# Local secrets file helpers
# ---------------------------------------------------------------------------

_LOCAL_SECRETS_PATH = Path.home() / ".nubi" / "secrets"


def _read_local_secrets() -> dict[str, str]:
    """Read the local secrets file; return an empty dict if absent or malformed."""
    if _LOCAL_SECRETS_PATH.exists():
        try:
            return json.loads(_LOCAL_SECRETS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_local_secrets(secrets: dict[str, str]) -> None:
    """Persist *secrets* to ~/.nubi/secrets as JSON."""
    _LOCAL_SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LOCAL_SECRETS_PATH.write_text(json.dumps(secrets, indent=2))


# ---------------------------------------------------------------------------
# flows run
# ---------------------------------------------------------------------------


@flows_app.command("run")
def flows_run(
    file: Path = typer.Argument(..., help="Path to a YAML or JSON flow spec file."),
    param: list[str] = typer.Option(
        [],
        "--param",
        "-p",
        help="Flow parameter in key=value format.  May be repeated.",
    ),
) -> None:
    """Execute a flow LOCALLY end-to-end using InMemoryFlowStore + file:// storage.

    Reads the flow file, validates the spec, resolves parameters from
    ``--param`` flags and the local secrets file, then drives the flows
    runtime to completion using the same executor and registry as the
    cloud.  Prints task states and results on completion.

    The backend package must be importable (dev checkout layout is used
    automatically — see flows_files.py).  If it is not importable an
    actionable error is printed.
    """
    # ── Load & validate the spec ─────────────────────────────────────────────
    try:
        spec = load_flow_file(file)
    except FileNotFoundError as exc:
        err_console.print(str(exc))
        raise typer.Exit(code=1)
    except FlowFileError as exc:
        err_console.print(str(exc))
        raise typer.Exit(code=1)

    # ── Parse --param flags ──────────────────────────────────────────────────
    params: dict[str, str] = {}
    for entry in param:
        if "=" not in entry:
            err_console.print(
                f"Invalid --param {entry!r}: expected key=value format."
            )
            raise typer.Exit(code=1)
        k, _, v = entry.partition("=")
        params[k.strip()] = v

    # ── Bootstrap backend path ───────────────────────────────────────────────
    try:
        from .flows_files import _ensure_backend_on_path  # noqa: PLC0415

        if not _ensure_backend_on_path():
            err_console.print(
                "Could not locate the backend package.  Ensure you are running "
                "from a nubi checkout and the backend/ directory is present at "
                "the repository root."
            )
            raise typer.Exit(code=1)

        import asyncio  # noqa: PLC0415
        from datetime import datetime, timezone  # noqa: PLC0415

        from app.flows.runtime import drain_flow_run, materialize_flow_run  # noqa: PLC0415
        from app.flows.store import InMemoryFlowStore  # noqa: PLC0415

    except ImportError as exc:
        err_console.print(
            f"Backend import failed: {exc}\n"
            "Install the backend dependencies or run from the nubi checkout root."
        )
        raise typer.Exit(code=1)

    # ── Local secrets: project .nubi/secrets/flow.env > ~/.nubi/secrets > env ──
    # Per doc B.207 the project's flow.env is the source of truth for a local
    # run; the legacy global ~/.nubi/secrets still seeds (lower precedence) and
    # NUBI_SECRET_<NAME> env vars override both.
    local_secrets = _read_local_secrets()
    local_secrets.update(_secrets_files.load_flow_secrets(_project_root(), dict(os.environ)))

    # ── Stub out app.secrets.store so the runtime can call resolve_all ────────
    # The secrets store seam lives in app.secrets.store; if it is not yet
    # landed we inject a thin stub so local runs work without the full backend.
    _patch_secrets_store(local_secrets)

    # ── Create an InMemoryFlowStore + a synthetic flow dict ──────────────────
    store = InMemoryFlowStore()
    flow_name = spec.get("name", file.stem)

    async def _run_locally() -> dict:
        flow = await store.create_flow(
            org_id="local",
            created_by="cli",
            name=flow_name,
            spec=spec,
        )
        now = datetime.now(timezone.utc)
        flow_run = await materialize_flow_run(store, flow, params, "manual", now)
        console.print(
            f"Flow [bold]{flow_name!r}[/bold] started "
            f"(run_id=[dim]{flow_run['id']}[/dim])."
        )
        final = await drain_flow_run(store, flow_run["id"], now, claims={})
        return final

    final_run = asyncio.run(_run_locally())

    # ── Print results ────────────────────────────────────────────────────────
    task_runs = asyncio.run(_list_task_runs_local(store, final_run["id"]))

    tbl = Table("Task", "State", "Result / Error", title=f"Flow '{flow_name}' run")
    for tr in task_runs:
        state = tr.get("state", "?")
        result_or_err = tr.get("error") or json.dumps(tr.get("result") or {})
        if len(result_or_err) > 80:
            result_or_err = result_or_err[:77] + "..."
        colour = "green" if state == "success" else "red" if state in ("failed", "timed_out") else "yellow"
        tbl.add_row(tr.get("task_key", "?"), f"[{colour}]{state}[/{colour}]", result_or_err)

    console.print(tbl)

    flow_state = final_run.get("state", "?")
    if flow_state == "success":
        console.print(f"[green]Flow completed successfully.[/green]")
    else:
        err_console.print(f"Flow finished with state: {flow_state}")
        raise typer.Exit(code=1)


async def _list_task_runs_local(store: Any, flow_run_id: str) -> list:
    """Async helper to fetch task_runs from the in-memory store."""
    return await store.list_task_runs(flow_run_id)


def _patch_secrets_store(local_secrets: dict[str, str]) -> None:
    """Wire *local_secrets* into the flows runtime's secrets seam.

    The runtime resolves ``TaskContext.secrets`` (and ``{{ secrets.NAME }}``
    templates) via ``app.secrets.store.get_secret_store().resolve_all(org_id)``.
    For a local run there is no Postgres, so the default ``PgSecretStore``
    would fail and the runtime would silently fall back to ``{}`` — local
    secrets and ``NUBI_SECRET_*`` env vars would never reach the flow.

    When the real ``app.secrets.store`` module is importable we therefore
    inject an in-memory store via its ``set_secret_store(...)`` seam, seeded
    from *local_secrets* and serving them for ANY org_id (local runs use
    org_id='local').  When the module is NOT importable we fall back to
    stubbing it so the runtime's ``resolve_all`` call still succeeds.

    Must be called BEFORE materialize/drain.
    """
    _captured = dict(local_secrets)

    class _LocalSecretStore:
        """In-memory store matching the app.secrets.store interface.

        Serves the captured local secrets regardless of *org_id* — a local
        run has exactly one (implicit) org.
        """

        async def resolve_all(self, org_id: str) -> dict[str, str]:
            return dict(_captured)

        async def get_secret(self, org_id: str, name: str) -> str | None:
            return _captured.get(name)

        async def list_secrets(self, org_id: str) -> list[dict]:
            return [{"name": k} for k in _captured]

        async def set_secret(self, org_id, name, value, created_by=None):  # type: ignore[override]
            _captured[name] = value
            return {"org_id": org_id, "name": name}

        async def delete_secret(self, org_id, name) -> bool:
            return _captured.pop(name, None) is not None

    store = _LocalSecretStore()

    try:
        try:
            import app.secrets.store as _ss  # noqa: PLC0415
        except ImportError:
            _ss = None

        if _ss is not None and hasattr(_ss, "set_secret_store"):
            # Real seam is present — inject the local store through it so
            # get_secret_store() returns our in-memory store for this run.
            _ss.set_secret_store(store)
            return

        # Fallback: module not importable — stub it so resolve_all works.
        import types  # noqa: PLC0415

        mod = types.ModuleType("app.secrets.store")
        mod.get_secret_store = lambda: store  # type: ignore[attr-defined]
        mod.set_secret_store = lambda s: None  # type: ignore[attr-defined]
        sys.modules.setdefault("app.secrets", types.ModuleType("app.secrets"))
        sys.modules["app.secrets.store"] = mod
    except Exception:  # noqa: BLE001
        pass  # Wiring is best-effort; run proceeds without local secrets.


# ---------------------------------------------------------------------------
# flows push
# ---------------------------------------------------------------------------


@flows_app.command("push")
def flows_push(
    files: list[Path] = typer.Argument(
        None,
        help="One or more YAML/JSON flow files to push.  Defaults to all *.yaml/*.json in the current directory.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print planned actions without making any API calls."
    ),
) -> None:
    """Create or update flows in the cloud from local files.

    Matches by flow name: if a flow with the same name already exists in
    the API it is updated (PUT); otherwise it is created (POST).
    """
    if not load_token():
        err_console.print(
            "Not logged in.  Run 'nubi login' first, or set NUBI_TOKEN."
        )
        raise typer.Exit(code=1)

    # If no files specified, discover in cwd.
    if not files:
        cwd = Path(".")
        files = sorted(cwd.glob("*.yaml")) + sorted(cwd.glob("*.yml")) + sorted(cwd.glob("*.json"))
        if not files:
            console.print("[yellow]No flow files found in the current directory.[/yellow]")
            raise typer.Exit(code=0)

    # Load + validate each file.
    loaded: list[tuple[Path, dict]] = []
    for f in files:
        try:
            spec = load_flow_file(f)
            loaded.append((f, spec))
        except (FileNotFoundError, FlowFileError) as exc:
            err_console.print(f"Skipping {f.name}: {exc}")

    if not loaded:
        raise typer.Exit(code=1)

    # Fetch existing flows from the API.
    try:
        resp = _client.get("flows")
        existing_flows = resp.json()
        if isinstance(existing_flows, dict):
            existing_flows = existing_flows.get("flows", existing_flows.get("items", []))
    except CLIError as exc:
        err_console.print(f"Failed to list flows from API: {exc.message}")
        raise typer.Exit(code=1)

    existing_by_name: dict[str, dict] = {f.get("name", ""): f for f in existing_flows}

    # Plan.
    tbl = Table("File", "Flow name", "Action", title="Push plan")
    plan: list[tuple[Path, dict, str, str | None]] = []  # (path, spec, action, id_or_none)
    for path, spec in loaded:
        name = spec.get("name", path.stem)
        existing = existing_by_name.get(name)
        action = "UPDATE" if existing else "CREATE"
        flow_id = existing.get("id") if existing else None
        plan.append((path, spec, action, flow_id))
        tbl.add_row(path.name, name, action)

    console.print(tbl)

    if dry_run:
        console.print("[yellow]Dry run — no API calls made.[/yellow]")
        return

    # Execute.
    for path, spec, action, flow_id in plan:
        name = spec.get("name", path.stem)
        payload = {"name": name, "spec": spec}
        try:
            if action == "UPDATE" and flow_id:
                _client.put(f"flows/{flow_id}", json=payload)
                console.print(f"[green]UPDATED[/green] {name} ({path.name})")
            else:
                resp = _client.post("flows", json=payload)
                created_id = resp.json().get("id", "?")
                console.print(f"[green]CREATED[/green] {name} ({path.name}) → id={created_id}")
        except CLIError as exc:
            err_console.print(f"Failed {action} {path.name}: {exc.message}")


# ---------------------------------------------------------------------------
# flows pull
# ---------------------------------------------------------------------------


@flows_app.command("pull")
def flows_pull(
    directory: Path = typer.Option(
        Path("flows"),
        "--dir",
        "-d",
        help="Destination directory for pulled flow files.  Default: flows/",
    ),
) -> None:
    """Fetch flows from the API and write them as YAML files.

    Each flow is written to ``<directory>/<flow_name>.yaml``.
    If PyYAML is not installed the files are written as ``.json`` instead.
    """
    if not load_token():
        err_console.print(
            "Not logged in.  Run 'nubi login' first, or set NUBI_TOKEN."
        )
        raise typer.Exit(code=1)

    try:
        resp = _client.get("flows")
        flows = resp.json()
        if isinstance(flows, dict):
            flows = flows.get("flows", flows.get("items", []))
    except CLIError as exc:
        err_console.print(f"Failed to list flows: {exc.message}")
        raise typer.Exit(code=1)

    if not flows:
        console.print("[yellow]No flows found on the server.[/yellow]")
        return

    directory.mkdir(parents=True, exist_ok=True)

    try:
        import yaml as _yaml_check  # noqa: PLC0415, F401

        ext = ".yaml"
    except ImportError:
        ext = ".json"

    for flow in flows:
        spec = flow.get("spec") or {}
        name = flow.get("name") or spec.get("name") or flow.get("id", "unknown")
        safe_name = name.replace("/", "_").replace(" ", "_")
        out = directory / f"{safe_name}{ext}"
        try:
            dump_flow(spec, out)
            console.print(f"[green]Wrote[/green] {out}")
        except FlowFileError as exc:
            err_console.print(f"Failed to write {out.name}: {exc}")

    console.print(f"Pulled {len(flows)} flow(s) to {directory}/.")


# ---------------------------------------------------------------------------
# secrets set
# ---------------------------------------------------------------------------


@secrets_app.command("set")
def secrets_set(
    name: str = typer.Argument(..., help="Secret name (e.g. MY_API_KEY)."),
    value: str = typer.Argument(..., help="Secret value."),
    local_only: bool = typer.Option(
        False, "--local-only", help="Write only to the local secrets file; skip the API."
    ),
    connector: Optional[str] = typer.Option(
        None, "--connector", help="Connector slug — write a connector secret field instead."
    ),
) -> None:
    """Set a flow or connector secret locally (and via API when logged in).

    Without --connector this is a flow/org secret: written to the project's
    .nubi/secrets/flow.env (and ~/.nubi/secrets for the local flow runtime) and
    POST /secrets when logged in. With --connector <slug> it is a connector
    secret FIELD (e.g. ``password``): written to .nubi/secrets/connectors.env as
    ``<SLUG>__<FIELD>`` and rotated via PUT /connectors/{id} on next push.
    """
    root = _project_root()

    # ── Connector secret field ────────────────────────────────────────────────
    if connector:
        key = _secrets_files.connector_key(connector, name)
        _secrets_files.upsert_dotenv(
            _secrets_files.connectors_env_path(root), key, value,
            header="Nubi connector secrets — never commit",
        )
        console.print(f"[green]Set[/green] connector secret {key!r} locally.")
        return

    # ── Flow/org secret ────────────────────────────────────────────────────────
    # Project-scoped flow.env (when a project tree exists) + the legacy global
    # ~/.nubi/secrets so existing `flows run` keeps working.
    if (root / ".nubi").exists() or (root / "nubi.yaml").exists():
        _secrets_files.upsert_dotenv(
            _secrets_files.flow_env_path(root), name, value,
            header="Nubi flow/org secrets — never commit",
        )
    secrets = _read_local_secrets()
    secrets[name] = value
    _write_local_secrets(secrets)
    console.print(f"[green]Set[/green] secret {name!r} locally.")

    if not local_only and load_token():
        try:
            _client.post("secrets", json={"name": name, "value": value}, headers=_project_headers(root))
            console.print(f"[green]Set[/green] secret {name!r} via API.")
        except CLIError as exc:
            err_console.print(
                f"API secret set failed (local write succeeded): {exc.message}"
            )
    elif not local_only and not load_token():
        console.print(
            "[dim]Not logged in — secret saved locally only.  "
            "Run 'nubi login' to also sync to the cloud.[/dim]"
        )


# ---------------------------------------------------------------------------
# secrets list
# ---------------------------------------------------------------------------


@secrets_app.command("list")
def secrets_list(
    local_only: bool = typer.Option(
        False, "--local-only", help="Show only locally stored secrets; skip the API."
    ),
) -> None:
    """List secrets locally and/or from the API.

    Values are NEVER shown — only names are printed.
    """
    local = _read_local_secrets()
    tbl = Table("Name", "Stored locally?", "API?", title="Secrets")

    api_names: set[str] = set()
    api_available = False

    if not local_only and load_token():
        try:
            resp = _client.get("secrets")
            api_secrets = resp.json()
            if isinstance(api_secrets, dict):
                api_secrets = api_secrets.get("secrets", api_secrets.get("items", []))
            api_names = {s.get("name", "") for s in api_secrets}
            api_available = True
        except CLIError as exc:
            err_console.print(f"Could not list API secrets: {exc.message}")

    all_names = sorted(set(local.keys()) | api_names)

    if not all_names:
        console.print("[dim]No secrets found.[/dim]")
        return

    for n in all_names:
        local_mark = "yes" if n in local else "-"
        api_mark = "yes" if n in api_names else ("-" if api_available else "n/a")
        tbl.add_row(n, local_mark, api_mark)

    console.print(tbl)


# ---------------------------------------------------------------------------
# secrets pull / push / materialize / delete
# ---------------------------------------------------------------------------


@secrets_app.command("pull")
def secrets_pull(
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project root."),
) -> None:
    """Scaffold empty .env keys from the cloud secret NAMES (values stay remote).

    Lists flow secrets (GET /secrets) and connector secrets (GET /connectors)
    and writes blank placeholders into .nubi/secrets/*.env so a user knows which
    keys to fill in. Existing values are never overwritten. (doc D)
    """
    _require_login()
    root = directory
    headers = _project_headers(root)

    # Flow secret names.
    flow = _secrets_files.read_dotenv(_secrets_files.flow_env_path(root))
    try:
        for s in _list_resources("secrets", headers):
            flow.setdefault(s.get("name", ""), "")
    except CLIError as exc:
        err_console.print(f"Could not list flow secrets: {exc.message}")
    flow.pop("", None)
    _secrets_files.write_dotenv(
        _secrets_files.flow_env_path(root), flow, header="Nubi flow/org secrets — fill in values"
    )

    # Connector secret keys (from declared `secrets:` lists in manifests).
    conn = _secrets_files.read_dotenv(_secrets_files.connectors_env_path(root))
    for envlp in _project.read_all(root, ["connector"]).get("connector", []):
        name = (envlp.get("metadata") or {}).get("name") or ""
        slug = _project.slugify(name)
        for field in (envlp.get("spec") or {}).get("secrets", []) or []:
            conn.setdefault(_secrets_files.connector_key(slug, field), "")
    _secrets_files.write_dotenv(
        _secrets_files.connectors_env_path(root),
        conn,
        header="Nubi connector secrets — fill in values",
    )
    console.print(
        f"[green]Scaffolded[/green] {len(flow)} flow + {len(conn)} connector secret key(s)."
    )


@secrets_app.command("push")
def secrets_push(
    target: str = typer.Option(..., "--target", help="github | gitlab."),
    token: Optional[str] = typer.Option(None, "--token", help="Admin PAT (else GITHUB_TOKEN/GITLAB_TOKEN env)."),
    env_scope: str = typer.Option("*", "--env-scope", help="GitLab environment_scope."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project root."),
) -> None:
    """Write local secrets into the repo's GH Actions / GitLab CI store (doc C).

    Reads .nubi/secrets/*.env, prefixes the keys (NUBI_SECRET__/NUBI_CONNECTOR__),
    and uploads them — GitHub values are libsodium-sealed (PyNaCl), GitLab as
    masked CI variables. The repo_url/provider come from nubi.yaml spec.git.
    """
    root = directory
    flow = _secrets_files.read_dotenv(_secrets_files.flow_env_path(root))
    conn = _secrets_files.read_dotenv(_secrets_files.connectors_env_path(root))
    secrets = _vcs.prefixed_names(flow, conn)
    if not secrets:
        console.print("[yellow]No local secrets to push.[/yellow]")
        raise typer.Exit(code=0)

    manifest = _project.read_nubi_yaml(root)
    git_info = (manifest.get("spec") or {}).get("git") or {}
    repo_url = git_info.get("repo_url")
    if not repo_url:
        err_console.print("No git.repo_url in nubi.yaml; run 'nubi git connect' first.")
        raise typer.Exit(code=1)

    target = target.lower()
    tok = token or (
        os.environ.get("GITHUB_TOKEN") if target == "github" else os.environ.get("GITLAB_TOKEN")
    )
    if not tok:
        err_console.print(f"No token; pass --token or set {target.upper()}_TOKEN.")
        raise typer.Exit(code=1)

    try:
        if target == "github":
            written = _vcs.push_github(repo_url, tok, secrets)
        elif target == "gitlab":
            written = _vcs.push_gitlab(repo_url, tok, secrets, environment_scope=env_scope)
        else:
            err_console.print(f"Unknown --target {target!r}. Use github or gitlab.")
            raise typer.Exit(code=1)
    except _vcs.VcsSecretError as exc:
        err_console.print(str(exc))
        raise typer.Exit(code=1)
    console.print(f"[green]Pushed[/green] {len(written)} secret(s) to {target}.")


@secrets_app.command("materialize")
def secrets_materialize(
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project root."),
) -> None:
    """Expand NUBI_SECRET__* / NUBI_CONNECTOR__* env vars into .env files (doc C).

    Pipeline use: no backend call. Each NUBI_SECRET__<NAME> becomes a flow.env
    line; each NUBI_CONNECTOR__<SLUG>__<FIELD> becomes a connectors.env line.
    """
    counts = _secrets_files.materialize(directory, dict(os.environ))
    console.print(
        f"[green]Materialized[/green] {counts['flow']} flow + "
        f"{counts['connector']} connector secret(s)."
    )


@secrets_app.command("delete")
def secrets_delete(
    name: str = typer.Argument(..., help="Cloud secret name to delete."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project root."),
) -> None:
    """Delete a cloud flow/org secret (DELETE /secrets/{name})."""
    _require_login()
    try:
        _client.delete(f"secrets/{name}", headers=_project_headers(directory))
    except CLIError as exc:
        err_console.print(f"Delete failed: {exc.message}")
        raise typer.Exit(code=1)
    console.print(f"[green]Deleted[/green] cloud secret {name!r}.")


# ---------------------------------------------------------------------------
# logout / whoami
# ---------------------------------------------------------------------------


@app.command()
def logout() -> None:
    """Clear the locally stored access token (doc D: POST /auth/logout)."""
    # Best-effort server-side revoke; the local token clear is authoritative.
    if load_token():
        try:
            _client.post("auth/logout")
        except CLIError:
            pass
    cleared = clear_token()
    if cleared:
        console.print("[green]Logged out — local token cleared.[/green]")
    else:
        console.print("[dim]No local token was stored.[/dim]")


@app.command()
def whoami() -> None:
    """Show the current user/org (GET /auth/me)."""
    _require_login()
    try:
        resp = _client.get("auth/me")
    except CLIError as exc:
        err_console.print(f"Could not fetch identity: {exc.message}")
        raise typer.Exit(code=1)
    body = resp.json()
    user = body.get("user", body)
    console.print(
        f"[green]{user.get('email', '?')}[/green] "
        f"(id={user.get('id', '?')}, org={user.get('org_id', user.get('default_org_id', '?'))})"
    )


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@app.command()
def init(
    project: Optional[str] = typer.Option(None, "--project", help="Bind to an existing project id."),
    name: Optional[str] = typer.Option(None, "--name", help="Project display name for nubi.yaml."),
    default_env: str = typer.Option("dev", "--env", help="Default environment key."),
    ci: Optional[str] = typer.Option(None, "--ci", help="Scaffold a CI pipeline: github | gitlab."),
    directory: Path = typer.Argument(Path("."), help="Project directory (default: cwd)."),
) -> None:
    """Scaffold a local project: nubi.yaml, .nubi/project.json, .gitignore (doc A/D).

    When ``--project`` is given the manifest is bound to that id; otherwise it is
    scaffolded unbound (pull/push resolve the id later). ``--ci github|gitlab``
    also copies the matching pipeline template (doc E).
    """
    root = directory
    root.mkdir(parents=True, exist_ok=True)

    org_id: str | None = None
    proj_name = name or root.resolve().name
    # When logged in + a project id given, fetch its real name/org.
    if project and load_token():
        try:
            resp = _client.get(f"projects/{project}")
            row = resp.json()
            proj_name = name or row.get("name") or proj_name
            org_id = row.get("org_id")
        except CLIError:
            pass

    manifest = _project.build_manifest(
        proj_name, project, org_id, default_env=default_env, environments=[default_env]
    )
    _project.write_nubi_yaml(root, manifest)
    _project.write_project_json(
        root,
        {
            "project_id": project,
            "org_id": org_id,
            "api_url": _client.get_api_url() if hasattr(_client, "get_api_url") else None,
            "default_env": default_env,
        },
    )
    appended = _project.write_gitignore(root)

    console.print(f"[green]Initialized[/green] Nubi project at {root}/")
    console.print("  wrote nubi.yaml, .nubi/project.json")
    console.print(f"  {'wrote' if appended else 'kept'} .gitignore (secrets ignored)")

    if ci:
        written = _scaffold_ci(root, ci)
        if written:
            console.print(f"  scaffolded CI: {written}")


def _scaffold_ci(root: Path, target: str) -> str | None:
    """Copy a CI template (doc E) into the project; return the written path."""
    templates = Path(__file__).parent.parent / "templates"
    target = target.lower()
    if target == "github":
        src = templates / "github" / "nubi-deploy.yml"
        dest = root / ".github" / "workflows" / "nubi-deploy.yml"
    elif target == "gitlab":
        src = templates / "gitlab" / ".gitlab-ci.yml"
        dest = root / ".gitlab-ci.yml"
    else:
        err_console.print(f"Unknown --ci target {target!r}. Use 'github' or 'gitlab'.")
        return None
    if not src.exists():
        err_console.print(f"CI template not found: {src}")
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return str(dest)


# ---------------------------------------------------------------------------
# pull / push (the full file tree, doc A + D)
# ---------------------------------------------------------------------------

_ALL_KINDS = ("dashboard", "query", "flow", "connector")


def _connector_envelope_from_row(row: dict) -> dict:
    """Build a connector envelope from a sanitised GET /connectors row.

    The list response is already scrubbed (no secret material). ``connector_type``
    lives inside ``config``; we surface it plus the remaining non-secret fields.
    """
    config = dict(row.get("config") or {})
    spec: dict[str, Any] = {}
    if config.get("connector_type"):
        spec["connector_type"] = config.pop("connector_type")
    spec.update({k: v for k, v in config.items() if k != "system"})
    meta = {"name": row.get("name") or ""}
    if row.get("id"):
        meta["id"] = str(row["id"])
    return {"kind": "connector", "apiVersion": _project.API_VERSION, "metadata": meta, "spec": spec}


def _pull_kind(root: Path, kind: str, headers: dict[str, str]) -> int:
    """Pull all resources of *kind* into the file tree; return the count."""
    endpoint = _KIND_LIST_ENDPOINT[kind]
    rows = _list_resources(endpoint, headers)
    count = 0
    for row in rows:
        rid = row.get("id")
        if not rid:
            continue
        try:
            if kind == "connector":
                # Prefer the NEW /export/connector/{id}; fall back to the list row.
                try:
                    env = _export_envelope("connector", rid, headers)
                except CLIError:
                    env = _connector_envelope_from_row(row)
            else:
                env = _export_envelope(kind, rid, headers)
            items = _project.envelope_to_files(env)
            _project.write_files(root, items)
            count += 1
        except CLIError as exc:
            err_console.print(f"Skipping {kind} {rid}: {exc.message}")
    return count


@app.command()
def pull(  # noqa: F811 — replaces the legacy single-resource pull
    env: Optional[str] = typer.Option(None, "--env", help="Environment key (informational)."),
    kinds: Optional[str] = typer.Option(
        None, "--kinds", help="Comma-separated kinds: dashboard,query,flow,connector."
    ),
    directory: Path = typer.Argument(Path("."), help="Project root (default: cwd)."),
) -> None:
    """Download ALL project resources into the local file tree (doc A + D).

    Writes dashboards/queries/flows/connectors using the canonical on-disk
    layout. Connectors write NON-SECRET manifests only — secrets stay in the
    gitignored .nubi/secrets/ tree.
    """
    _require_login()
    root = directory
    wanted = [k.strip() for k in kinds.split(",")] if kinds else list(_ALL_KINDS)
    headers = _project_headers(root)

    total = 0
    for kind in wanted:
        if kind not in _KIND_LIST_ENDPOINT:
            err_console.print(f"Unknown kind {kind!r}; skipping.")
            continue
        try:
            n = _pull_kind(root, kind, headers)
            total += n
            console.print(f"[green]Pulled[/green] {n} {kind}(s).")
        except CLIError as exc:
            err_console.print(f"Failed to list {kind}: {exc.message}")
    # Keep the secrets gitignore present after a pull.
    _project.write_gitignore(root)
    console.print(f"Pulled {total} resource(s) into {root}/.")


def _import_envelope(env: dict, headers: dict[str, str], dry_run: bool, root: Path | None = None) -> str:
    """POST /import a single envelope (or connector upsert); return an action label."""
    kind = env.get("kind")
    meta = env.get("metadata") or {}
    has_id = bool(meta.get("id"))
    label = ("UPDATE" if has_id else "CREATE") + f" {kind} '{meta.get('name', '?')}'"
    if dry_run:
        return label
    if kind == "connector":
        _push_connector(env, headers, root)
    else:
        # POST /import accepts the YAML/JSON envelope as the request body.
        _client.post(
            "import",
            json=env,
            headers={**headers, **{}},
        )
    return label


@app.command()
def push(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan; make no API calls."),
    env: Optional[str] = typer.Option(None, "--env", help="Environment key (informational)."),
    kinds: Optional[str] = typer.Option(None, "--kinds", help="Comma-separated kinds to push."),
    directory: Path = typer.Argument(Path("."), help="Project root (default: cwd)."),
) -> None:
    """Upload changed non-secret manifests to the cloud (doc D).

    Dashboards/queries/flows go through POST /import (upsert by embedded id);
    connectors upsert via POST/PUT /connectors. Secrets are NEVER pushed here —
    use ``nubi deploy`` or ``nubi secrets push`` for that.
    """
    _require_login()
    root = directory
    wanted = [k.strip() for k in kinds.split(",")] if kinds else list(_ALL_KINDS)
    headers = _project_headers(root)
    tree = _project.read_all(root, wanted)

    tbl = Table("Kind", "Name", "Action", title="Push plan")
    plan: list[dict] = []
    for kind in wanted:
        for envlp in tree.get(kind, []):
            meta = envlp.get("metadata") or {}
            tbl.add_row(kind, str(meta.get("name", "?")), "UPDATE" if meta.get("id") else "CREATE")
            plan.append(envlp)
    if not plan:
        console.print("[yellow]No manifests found to push.[/yellow]")
        raise typer.Exit(code=0)
    console.print(tbl)

    if dry_run:
        console.print("[yellow]Dry run — no API calls made.[/yellow]")
        return

    for envlp in plan:
        try:
            label = _import_envelope(envlp, headers, dry_run=False, root=root)
            console.print(f"[green]{label}[/green]")
        except CLIError as exc:
            err_console.print(f"Failed to push {envlp.get('kind')}: {exc.message}")


# ---------------------------------------------------------------------------
# sync / status
# ---------------------------------------------------------------------------


@app.command()
def sync(
    env_id: str = typer.Option(..., "--env-id", help="Environment id to reconcile."),
    strategy: Optional[str] = typer.Option(
        None, "--strategy", help="Conflict strategy: take_branch | take_env."
    ),
) -> None:
    """Two-way reconcile local tree ↔ cloud via the project's git binding (doc D).

    Pushes then pulls the env-bound branch using the existing git-env routes.
    """
    _require_login()
    push_body = {}
    pull_body = {"strategy": strategy} if strategy else {}
    try:
        presp = _client.post(f"environments/{env_id}/git/push", json=push_body)
        console.print(f"[green]push[/green]: {json.dumps(presp.json())[:200]}")
        qresp = _client.post(f"environments/{env_id}/git/pull", json=pull_body)
        console.print(f"[green]pull[/green]: {json.dumps(qresp.json())[:200]}")
    except CLIError as exc:
        err_console.print(f"Sync failed: {exc.message}")
        raise typer.Exit(code=1)


@app.command()
def status(
    directory: Path = typer.Argument(Path("."), help="Project root (default: cwd)."),
) -> None:
    """Show the project binding, env, and last-sync commit graph (doc D).

    Reads the local pointer + nubi.yaml; queries GET /projects/{id}/git/graph
    for branch heads when logged in.
    """
    root = directory
    pointer = _project.read_project_json(root)
    manifest = _project.read_nubi_yaml(root)
    pid = _resolve_project_id(root)

    tbl = Table("Field", "Value", title="Nubi project status")
    tbl.add_row("project_id", str(pid or "(unbound)"))
    tbl.add_row("org_id", str(pointer.get("org_id") or "-"))
    tbl.add_row("default_env", str(pointer.get("default_env") or (manifest.get("spec") or {}).get("default_env") or "-"))
    tbl.add_row("api_url", _client.get_api_url())
    console.print(tbl)

    if pid and load_token():
        try:
            resp = _client.get(f"projects/{pid}/git/graph")
            graph = resp.json()
            for branch in graph.get("branches", []):
                head = (branch.get("head_sha") or "")[:8] or "(empty)"
                console.print(
                    f"  branch [cyan]{branch.get('branch')}[/cyan] "
                    f"(env={branch.get('env_key')}) @ {head}"
                )
        except CLIError as exc:
            err_console.print(f"Could not fetch git graph: {exc.message}")


# ---------------------------------------------------------------------------
# deploy (CI-oriented full pipeline, doc E ordering)
# ---------------------------------------------------------------------------


@app.command(name="deploy")
def deploy_project(  # noqa: F811 — name="deploy" keeps the CLI verb; new behaviour
    env: Optional[str] = typer.Option(None, "--env", help="Target environment key (e.g. prod)."),
    directory: Path = typer.Argument(Path("."), help="Project root (default: cwd)."),
) -> None:
    """CI deploy: materialize secrets, push manifests + secrets to the cloud (doc E).

    Ordering (idempotent): (1) secrets materialize; (2) connector manifests +
    secrets; (3) flow/org secrets; (4) import dashboards/queries/flows. The
    optional checkpoint/promote steps are skipped when the project is unbound.
    """
    _require_login()
    root = directory
    headers = _project_headers(root)

    # (1) Materialize secrets from CI env vars (no backend call).
    counts = _secrets_files.materialize(root, dict(os.environ))
    console.print(f"materialized {counts['flow']} flow + {counts['connector']} connector secret(s)")

    # (2) Connector manifests + secrets.
    tree = _project.read_all(root, ["connector"])
    for envlp in tree.get("connector", []):
        try:
            _push_connector(envlp, headers, root)
            console.print(f"[green]connector[/green] {envlp.get('metadata', {}).get('name')}")
        except CLIError as exc:
            err_console.print(f"Connector push failed: {exc.message}")

    # (3) Flow/org secrets → POST /secrets.
    flow_secrets = _secrets_files.read_dotenv(_secrets_files.flow_env_path(root))
    for name, value in flow_secrets.items():
        try:
            _client.post("secrets", json={"name": name, "value": value}, headers=headers)
        except CLIError as exc:
            err_console.print(f"Secret {name!r} push failed: {exc.message}")
    if flow_secrets:
        console.print(f"[green]pushed[/green] {len(flow_secrets)} flow secret(s)")

    # (4) Import dashboards/queries/flows.
    tree = _project.read_all(root, ["dashboard", "query", "flow"])
    for kind in ("dashboard", "query", "flow"):
        for envlp in tree.get(kind, []):
            try:
                _client.post("import", json=envlp, headers=headers)
                console.print(f"[green]imported[/green] {kind} {envlp.get('metadata', {}).get('name')}")
            except CLIError as exc:
                err_console.print(f"Import {kind} failed: {exc.message}")

    console.print(f"[green]Deploy complete[/green] (env={env or 'default'}).")


# ---------------------------------------------------------------------------
# Per-kind wrappers: dashboards / queries / connectors
# ---------------------------------------------------------------------------


@dashboards_app.command("pull")
def dashboards_pull(directory: Path = typer.Option(Path("."), "--dir", "-d")) -> None:
    """Pull just dashboards (GET /boards + /export/dashboard/{id})."""
    _require_login()
    n = _pull_kind(directory, "dashboard", _project_headers(directory))
    console.print(f"[green]Pulled[/green] {n} dashboard(s).")


@dashboards_app.command("push")
def dashboards_push(directory: Path = typer.Option(Path("."), "--dir", "-d")) -> None:
    """Push just dashboards (POST /import)."""
    _require_login()
    _push_kind(directory, "dashboard")


@queries_app.command("pull")
def queries_pull(directory: Path = typer.Option(Path("."), "--dir", "-d")) -> None:
    """Pull just queries in the 3-file form (GET /queries + /export/query/{id})."""
    _require_login()
    n = _pull_kind(directory, "query", _project_headers(directory))
    console.print(f"[green]Pulled[/green] {n} query(ies).")


@queries_app.command("push")
def queries_push(directory: Path = typer.Option(Path("."), "--dir", "-d")) -> None:
    """Push just queries (POST /import)."""
    _require_login()
    _push_kind(directory, "query")


def _push_kind(root: Path, kind: str) -> None:
    """Shared push for a single kind via POST /import."""
    headers = _project_headers(root)
    tree = _project.read_all(root, [kind])
    for envlp in tree.get(kind, []):
        try:
            label = _import_envelope(envlp, headers, dry_run=False, root=root)
            console.print(f"[green]{label}[/green]")
        except CLIError as exc:
            err_console.print(f"Failed to push {kind}: {exc.message}")


@connectors_app.command("pull")
def connectors_pull(directory: Path = typer.Option(Path("."), "--dir", "-d")) -> None:
    """Write non-secret connectors/<slug>.yaml (GET /connectors)."""
    _require_login()
    n = _pull_kind(directory, "connector", _project_headers(directory))
    console.print(f"[green]Pulled[/green] {n} connector(s) (non-secret).")


@connectors_app.command("push")
def connectors_push(directory: Path = typer.Option(Path("."), "--dir", "-d")) -> None:
    """Upsert connector non-secret config + secrets from connectors.env."""
    _require_login()
    root = directory
    headers = _project_headers(root)
    tree = _project.read_all(root, ["connector"])
    for envlp in tree.get("connector", []):
        try:
            _push_connector(envlp, headers, root)
            console.print(f"[green]pushed[/green] connector {envlp.get('metadata', {}).get('name')}")
        except CLIError as exc:
            err_console.print(f"Connector push failed: {exc.message}")


@connectors_app.command("test")
def connectors_test(connector_id: str = typer.Argument(..., help="Connector id to test.")) -> None:
    """Validate config + secret resolvability (POST /connectors/{id}/test)."""
    _require_login()
    try:
        resp = _client.post(f"connectors/{connector_id}/test")
        body = resp.json()
        ok = body.get("ok", body.get("success"))
        if ok is False:
            err_console.print(f"Connector test FAILED: {json.dumps(body)[:300]}")
            raise typer.Exit(code=1)
        console.print(f"[green]Connector OK[/green]: {json.dumps(body)[:200]}")
    except CLIError as exc:
        err_console.print(f"Connector test failed: {exc.message}")
        raise typer.Exit(code=1)


def _push_connector(env: dict, headers: dict[str, str], root: Path | None = None) -> None:
    """Upsert a connector: non-secret config (+ secrets from connectors.env).

    Tries POST /import (kind: connector, NEW endpoint) first for upsert-by-id;
    falls back to POST/PUT /connectors when the import path rejects connectors.
    Secret fields are pulled from ``<root>/.nubi/secrets/connectors.env`` keyed
    ``<SLUG>__<FIELD>`` and sent via the connector ``secret`` blob.
    """
    meta = env.get("metadata") or {}
    spec = dict(env.get("spec") or {})
    name = meta.get("name") or "Connector"
    rid = meta.get("id")
    connector_type = spec.pop("connector_type", None)
    declared = spec.pop("secrets", []) or []

    # Resolve secret fields from connectors.env (in the active project tree).
    root = root or _project_root()
    env_file = _secrets_files.read_dotenv(_secrets_files.connectors_env_path(root))
    slug = _project.slugify(name)
    secret: dict[str, str] = {}
    for field in declared:
        key = _secrets_files.connector_key(slug, field)
        if key in env_file:
            secret[field] = env_file[key]

    # Try the uniform NEW import path first (upsert by embedded id).
    try:
        _client.post("import", json=env, headers=headers)
        # The import path never touches the secret store; rotate secrets after.
        if secret and rid:
            _client.put(f"connectors/{rid}", json={"secret": secret}, headers=headers)
        return
    except CLIError as exc:
        if exc.status not in (400, 404, 422):
            raise

    # Fallback: POST/PUT /connectors with the legacy shape.
    payload = {"name": name, "type": connector_type, "config": spec, "secret": secret}
    if rid:
        _client.put(f"connectors/{rid}", json=payload, headers=headers)
    else:
        _client.post("connectors", json=payload, headers=headers)


# ---------------------------------------------------------------------------
# git connect / graph
# ---------------------------------------------------------------------------


@git_app.command("connect")
def git_connect(
    provider: str = typer.Option(..., "--provider", help="github | gitlab."),
    repo_url: str = typer.Option(..., "--repo-url", help="Remote repo URL."),
    token: str = typer.Option(..., "--token", help="PAT bound to the project (stored server-side)."),
    branch: str = typer.Option("main", "--branch", help="Default branch."),
    base_path: str = typer.Option("", "--base-path", help="Subdir within the repo."),
    directory: Path = typer.Argument(Path("."), help="Project root (default: cwd)."),
) -> None:
    """Bind the project to a remote (POST /git/connect)."""
    _require_login()
    pid = _resolve_project_id(directory)
    if not pid:
        err_console.print("No project bound; run 'nubi init --project <id>' first.")
        raise typer.Exit(code=1)
    body = {
        "project_id": pid,
        "provider": provider,
        "repo_url": repo_url,
        "branch": branch,
        "base_path": base_path,
        "token": token,
    }
    try:
        _client.post("git/connect", json=body)
    except CLIError as exc:
        err_console.print(f"git connect failed: {exc.message}")
        raise typer.Exit(code=1)
    console.print(f"[green]Connected[/green] {provider} → {repo_url} ({branch}).")

    # Mirror the (non-secret) binding into nubi.yaml spec.git.
    manifest = _project.read_nubi_yaml(directory)
    spec = manifest.setdefault("spec", {})
    spec["git"] = {"provider": provider, "repo_url": repo_url}
    if manifest:
        _project.write_nubi_yaml(directory, manifest)


@git_app.command("graph")
def git_graph(directory: Path = typer.Argument(Path("."), help="Project root (default: cwd).")) -> None:
    """Print the env-branch commit graph (GET /projects/{id}/git/graph)."""
    _require_login()
    pid = _resolve_project_id(directory)
    if not pid:
        err_console.print("No project bound.")
        raise typer.Exit(code=1)
    try:
        resp = _client.get(f"projects/{pid}/git/graph")
    except CLIError as exc:
        err_console.print(f"Could not fetch graph: {exc.message}")
        raise typer.Exit(code=1)
    for branch in resp.json().get("branches", []):
        console.print(f"[cyan]{branch.get('branch')}[/cyan] (env={branch.get('env_key')})")
        for c in branch.get("commits", [])[:10]:
            console.print(f"  {c.get('sha', '')[:8]}  {c.get('message', '')}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Console-scripts entry point."""
    app()


if __name__ == "__main__":
    main()
