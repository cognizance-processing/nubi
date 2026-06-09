"""Nubi CLI — entry point.

Commands
--------
login           Authenticate and save an access token.
deploy          Push dashboards-as-code (*.json) to the server.
run             Execute a registered query and report results.
diff            Compare local resource files against the server (read-only).
pull            Download server resources to local JSON files (bonus).
flows run       Execute a flow locally end-to-end (file:// storage, in-memory store).
flows push      Create/update flows in the cloud from local files.
flows pull      Download flows from the API and write them as YAML files.
secrets set     Set a secret locally (and via API when logged in).
secrets list    List secrets locally and/or from the API.

Usage (after `pip install -e .` or via console_scripts):
    nubi --help
    nubi login
    nubi deploy ./dashboards
    nubi deploy ./dashboards --dry-run
    nubi run <query-id>
    nubi diff ./dashboards
    nubi pull datastores ./out/
    nubi flows run my_flow.yaml --param region=us --param date=2024-01-01
    nubi flows push flows/my_flow.yaml flows/other_flow.yaml
    nubi flows pull --dir flows/
    nubi secrets set MY_KEY my_value
    nubi secrets list

Alternatively run as a module:
    python -m nubi_cli.main --help
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
from .client import CLIError
from .config import load_token, save_token
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RESOURCE_TYPES = ("datastores", "boards", "widgets", "queries")


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


@app.command()
def deploy(
    directory: Path = typer.Argument(
        ..., exists=True, file_okay=False, help="Directory containing *.json resource files"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print planned actions without making any API calls"
    ),
) -> None:
    """Deploy dashboards-as-code from *directory* to the Nubi API.

    Each JSON file must have at minimum a ``resource`` and ``name`` field.
    If an ``id`` is present the resource will be updated (PUT); otherwise it
    will be created (POST).

    With ``--dry-run`` no HTTP calls are made.
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
# pull (bonus)
# ---------------------------------------------------------------------------


@app.command()
def pull(
    resource: str = typer.Argument(
        ..., help=f"Resource type to pull: {', '.join(_RESOURCE_TYPES)}"
    ),
    directory: Path = typer.Argument(
        ..., help="Destination directory (created if absent)"
    ),
) -> None:
    """Download all server resources of *resource* type to local JSON files.

    Writes one file per resource, named ``<id>.json``.
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

    # ── Local secrets from ~/.nubi/secrets + env vars ────────────────────────
    local_secrets = _read_local_secrets()
    # Env vars override the file (NUBI_SECRET_<NAME> → <NAME>).
    for key, val in os.environ.items():
        if key.startswith("NUBI_SECRET_"):
            secret_name = key[len("NUBI_SECRET_"):]
            local_secrets[secret_name] = val

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
) -> None:
    """Set a secret locally (in ~/.nubi/secrets) and via the API when logged in.

    Local secrets are used by 'nubi flows run' to populate TaskContext.secrets
    and to resolve {{ secrets.NAME }} templates in flow configs.

    If a Bearer token is present the secret is also persisted via the cloud
    API (POST /secrets).  Use --local-only to skip the API call.
    """
    # ── Local write ──────────────────────────────────────────────────────────
    secrets = _read_local_secrets()
    secrets[name] = value
    _write_local_secrets(secrets)
    console.print(f"[green]Set[/green] secret {name!r} locally.")

    # ── API write (if logged in and not local-only) ───────────────────────────
    if not local_only and load_token():
        try:
            _client.post("secrets", json={"name": name, "value": value})
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
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Console-scripts entry point."""
    app()


if __name__ == "__main__":
    main()
