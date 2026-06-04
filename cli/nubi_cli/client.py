"""Thin httpx wrapper with auth headers and structured error handling."""

from __future__ import annotations

from typing import Any

import httpx

from .config import get_api_url, load_token


class CLIError(Exception):
    """Raised when the server returns an error payload or a network error occurs."""

    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(f"[{code}] {message}")


def _auth_headers() -> dict[str, str]:
    token = load_token()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _raise_for_error(response: httpx.Response) -> None:
    """Parse `{error:{code,message}}` and raise CLIError; fallback to HTTP status."""
    if response.is_success:
        return
    try:
        body = response.json()
        err = body.get("error", {})
        code = err.get("code", "unknown_error")
        message = err.get("message", response.text or f"HTTP {response.status_code}")
    except Exception:
        code = "http_error"
        message = f"HTTP {response.status_code}: {response.text[:200]}"
    raise CLIError(code=code, message=message, status=response.status_code)


# ---------------------------------------------------------------------------
# Low-level verbs (module-level so tests can monkeypatch them)
# ---------------------------------------------------------------------------


def get(path: str, **kwargs: Any) -> httpx.Response:
    """GET request to *path* (relative to the base API URL)."""
    url = f"{get_api_url()}/{path.lstrip('/')}"
    headers = {**_auth_headers(), **kwargs.pop("headers", {})}
    response = httpx.get(url, headers=headers, **kwargs)
    _raise_for_error(response)
    return response


def post(path: str, json: Any = None, **kwargs: Any) -> httpx.Response:
    """POST request to *path*."""
    url = f"{get_api_url()}/{path.lstrip('/')}"
    headers = {**_auth_headers(), **kwargs.pop("headers", {})}
    response = httpx.post(url, json=json, headers=headers, **kwargs)
    _raise_for_error(response)
    return response


def put(path: str, json: Any = None, **kwargs: Any) -> httpx.Response:
    """PUT request to *path*."""
    url = f"{get_api_url()}/{path.lstrip('/')}"
    headers = {**_auth_headers(), **kwargs.pop("headers", {})}
    response = httpx.put(url, json=json, headers=headers, **kwargs)
    _raise_for_error(response)
    return response


def delete(path: str, **kwargs: Any) -> httpx.Response:
    """DELETE request to *path*."""
    url = f"{get_api_url()}/{path.lstrip('/')}"
    headers = {**_auth_headers(), **kwargs.pop("headers", {})}
    response = httpx.delete(url, headers=headers, **kwargs)
    _raise_for_error(response)
    return response
