"""GitHub Actions / GitLab CI secret sync (files-as-code, doc Section C).

``nubi secrets push --target github|gitlab`` writes the local
``.nubi/secrets/*.env`` values into the repo's Actions/CI secret store so
pipelines consume them WITHOUT the plaintext ever being committed.

Naming (doc C.266): connector secrets are prefixed ``NUBI_CONNECTOR__<NAME>``
and flow secrets ``NUBI_SECRET__<NAME>`` so the materialize step is predictable.

GitHub requires each value be libsodium-sealed against the repo public key
before upload; we use PyNaCl when available and degrade with a clear error when
it is not installed (per the build instructions). GitLab needs no sealing.

This module is HTTP-client agnostic for testability: callers inject a
``transport`` callable ``(method, url, headers, json) -> (status, body_dict)``
so the sealing/encoding logic is unit-testable with a stub. The default
transport uses ``httpx``.
"""

from __future__ import annotations

import base64
import re
from typing import Any, Callable, Tuple
from urllib.parse import quote

# A transport is (method, url, headers, json_body) -> (status_code, parsed_body)
Transport = Callable[[str, str, dict, Any], Tuple[int, Any]]


class VcsSecretError(Exception):
    """Raised when a secret-store API call or its prerequisites fail."""


# ---------------------------------------------------------------------------
# repo_url parsing
# ---------------------------------------------------------------------------


def parse_repo_url(repo_url: str) -> Tuple[str, str]:
    """Return ``(owner, repo)`` from an https or ssh git URL.

    Handles ``https://host/owner/repo(.git)`` and ``git@host:owner/repo.git``.
    For GitLab subgroups the full path (``group/subgroup/repo``) is returned as
    the ``repo`` part with the first segment as ``owner`` — callers that need the
    URL-encoded project path use :func:`gitlab_project_path`.
    """
    url = repo_url.strip()
    if url.endswith(".git"):
        url = url[: -len(".git")]
    m = re.match(r"^git@[^:]+:(.+)$", url)
    if m:
        path = m.group(1)
    else:
        m = re.match(r"^https?://[^/]+/(.+)$", url)
        if not m:
            raise VcsSecretError(f"Could not parse owner/repo from URL: {repo_url!r}")
        path = m.group(1)
    parts = path.strip("/").split("/")
    if len(parts) < 2:
        raise VcsSecretError(f"Could not parse owner/repo from URL: {repo_url!r}")
    return parts[0], "/".join(parts[1:])


def gitlab_project_path(repo_url: str) -> str:
    """Return the URL-encoded ``namespace/project`` path GitLab's API expects."""
    owner, repo = parse_repo_url(repo_url)
    return quote(f"{owner}/{repo}", safe="")


# ---------------------------------------------------------------------------
# Secret name prefixing
# ---------------------------------------------------------------------------


def prefixed_names(flow: dict[str, str], connector: dict[str, str]) -> dict[str, str]:
    """Combine flow + connector secrets into uppercased, prefixed CI keys.

    - flow      ``<NAME>``         → ``NUBI_SECRET__<NAME>``
    - connector ``<SLUG>__<FIELD>``→ ``NUBI_CONNECTOR__<SLUG>__<FIELD>``
    """
    out: dict[str, str] = {}
    for name, value in flow.items():
        out[f"NUBI_SECRET__{name.upper()}"] = value
    for name, value in connector.items():
        out[f"NUBI_CONNECTOR__{name.upper()}"] = value
    return out


# ---------------------------------------------------------------------------
# libsodium sealing (PyNaCl)
# ---------------------------------------------------------------------------


def seal_secret(public_key_b64: str, value: str) -> str:
    """Seal *value* with the GitHub repo public key (libsodium sealed box).

    Returns the base64-encoded ciphertext GitHub expects.  Raises a clear
    error when PyNaCl is not installed (doc: "degrade with a clear error").
    """
    try:
        from nacl import encoding, public  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - exercised via stub in tests
        raise VcsSecretError(
            "PyNaCl is required to seal GitHub Actions secrets. "
            "Install it with: pip install pynacl"
        ) from exc

    pk = public.PublicKey(public_key_b64.encode("utf-8"), encoding.Base64Encoder())
    sealed = public.SealedBox(pk).encrypt(value.encode("utf-8"))
    return base64.b64encode(sealed).decode("utf-8")


# ---------------------------------------------------------------------------
# Default httpx transport
# ---------------------------------------------------------------------------


def _httpx_transport(method: str, url: str, headers: dict, json_body: Any) -> Tuple[int, Any]:
    import httpx  # noqa: PLC0415

    resp = httpx.request(method, url, headers=headers, json=json_body, timeout=30.0)
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        body = resp.text
    return resp.status_code, body


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


def push_github(
    repo_url: str,
    token: str,
    secrets: dict[str, str],
    *,
    transport: Transport | None = None,
    seal: Callable[[str, str], str] = seal_secret,
) -> list[str]:
    """Upload *secrets* (already prefixed) to GitHub Actions (doc C.256).

    1. GET the repo public key.
    2. Seal each value with libsodium.
    3. PUT ``actions/secrets/{NAME}`` with ``{encrypted_value, key_id}``.

    Returns the list of secret names written.  *seal* is injectable so tests can
    verify the encode/PUT path without PyNaCl.
    """
    tx = transport or _httpx_transport
    owner, repo = parse_repo_url(repo_url)
    base = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    status, body = tx("GET", f"{base}/actions/secrets/public-key", headers, None)
    if status != 200 or not isinstance(body, dict):
        raise VcsSecretError(f"Could not fetch GitHub repo public key (HTTP {status}).")
    key = body.get("key")
    key_id = body.get("key_id")
    if not key or not key_id:
        raise VcsSecretError("GitHub public-key response missing 'key'/'key_id'.")

    written: list[str] = []
    for name, value in secrets.items():
        encrypted = seal(key, value)
        put_body = {"encrypted_value": encrypted, "key_id": key_id}
        status, _ = tx("PUT", f"{base}/actions/secrets/{name}", headers, put_body)
        if status not in (201, 204):
            raise VcsSecretError(f"Failed to set GitHub secret {name!r} (HTTP {status}).")
        written.append(name)
    return written


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------


def push_gitlab(
    repo_url: str,
    token: str,
    secrets: dict[str, str],
    *,
    environment_scope: str = "*",
    transport: Transport | None = None,
    api_base: str = "https://gitlab.com/api/v4",
) -> list[str]:
    """Upload *secrets* (already prefixed) to GitLab CI/CD variables (doc C.270).

    POST to create, PUT to update; ``masked: true`` and an ``environment_scope``
    matching the Nubi env.  Returns the list of variable keys written.
    """
    tx = transport or _httpx_transport
    project = gitlab_project_path(repo_url)
    base = f"{api_base}/projects/{project}/variables"
    headers = {"PRIVATE-TOKEN": token}

    written: list[str] = []
    for key, value in secrets.items():
        payload = {
            "key": key,
            "value": value,
            "protected": False,
            "masked": True,
            "environment_scope": environment_scope,
        }
        # Try update first; create on 404.
        status, _ = tx("PUT", f"{base}/{key}", headers, payload)
        if status == 404:
            status, _ = tx("POST", base, headers, payload)
        if status not in (200, 201):
            raise VcsSecretError(f"Failed to set GitLab variable {key!r} (HTTP {status}).")
        written.append(key)
    return written
