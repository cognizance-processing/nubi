"""Nubi CLI — entry point.

Commands
--------
login       Authenticate and save an access token.
deploy      Push dashboards-as-code (*.json) to the server.
run         Execute a registered query and report results.
diff        Compare local resource files against the server (read-only).
pull        Download server resources to local JSON files (bonus).

Usage (after `pip install -e .` or via console_scripts):
    nubi --help
    nubi login
    nubi deploy ./dashboards
    nubi deploy ./dashboards --dry-run
    nubi run <query-id>
    nubi diff ./dashboards
    nubi pull datastores ./out/

Alternatively run as a module:
    python -m nubi_cli.main --help
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import client as _client
from .client import CLIError
from .config import load_token, save_token

app = typer.Typer(
    name="nubi",
    help="Nubi CLI — dashboards-as-code, query runner, and resource manager.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True, style="bold red")


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
        resource_type = data.get("resource", "")
        resource_id = data.get("id")
        action = "UPDATE" if resource_id else "CREATE"
        plan.append((path, data, action))
        tbl.add_row(path.name, resource_type or "(unknown)", action)

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
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Console-scripts entry point."""
    app()


if __name__ == "__main__":
    main()
