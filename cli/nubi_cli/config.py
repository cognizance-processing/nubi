"""Configuration helpers: base URL + token from env and/or ~/.nubi/credentials."""

from __future__ import annotations

import json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Defaults & env vars
# ---------------------------------------------------------------------------

DEFAULT_API_URL = "http://localhost:8000/api/v1"
_CREDENTIALS_PATH = Path.home() / ".nubi" / "credentials"


def get_api_url() -> str:
    """Return the base API URL from NUBI_API_URL env var or the default."""
    return os.environ.get("NUBI_API_URL", DEFAULT_API_URL).rstrip("/")


def _read_credentials() -> dict:
    """Read the credentials file; return an empty dict if absent or malformed."""
    if _CREDENTIALS_PATH.exists():
        try:
            return json.loads(_CREDENTIALS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def load_token() -> str | None:
    """Return the stored Bearer token.

    Precedence: NUBI_TOKEN env var > ~/.nubi/credentials file.
    """
    env_token = os.environ.get("NUBI_TOKEN")
    if env_token:
        return env_token
    return _read_credentials().get("access_token")


def save_token(token: str) -> None:
    """Persist *token* to ~/.nubi/credentials as JSON.

    Creates the directory if it does not exist.
    """
    # The file holds a bearer credential: owner-only directory and file, set
    # at creation (not after the fact) and re-applied on every write.
    _CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(_CREDENTIALS_PATH.parent, 0o700)
    creds = _read_credentials()
    creds["access_token"] = token
    fd = os.open(_CREDENTIALS_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(creds, indent=2))
    os.chmod(_CREDENTIALS_PATH, 0o600)


def clear_token() -> bool:
    """Remove the stored access token from ~/.nubi/credentials.

    Returns True when a token was present and removed, False otherwise.  The
    NUBI_TOKEN env var (if any) is NOT touched — it is the caller's environment.
    """
    creds = _read_credentials()
    had = "access_token" in creds
    creds.pop("access_token", None)
    if _CREDENTIALS_PATH.exists():
        _CREDENTIALS_PATH.write_text(json.dumps(creds, indent=2))
    return had
