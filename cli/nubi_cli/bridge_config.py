"""Bridge-agent configuration: token + identity from flags, env, or config file.

The bridge agent authenticates the CONTROL channel with a bridge token
(``nubi_br_<43-char-base64url>``, design §7).  The token is bound to an
``(org, bridge_id)`` identity by the control plane; the agent only ever holds
it to present on the WebSocket handshake and on every heartbeat.

Resolution precedence (highest wins), mirroring ``config.load_token``:

    flag  >  env (NUBI_BRIDGE_TOKEN)  >  ~/.nubi/bridge.json

The config file reuses the CLI's ``~/.nubi/`` home (same dir as
``credentials``) so ``pip install nubi`` users have one place for state.  The
token is the only secret stored; like ``credentials`` it is written with
owner-only permissions and NEVER logged.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

# ``~/.nubi/bridge.json`` — same home as the credentials file (config.py).
_BRIDGE_CONFIG_PATH = Path.home() / ".nubi" / "bridge.json"

# Default control-plane base — WebSocket scheme, matches bridges/agent.py.
DEFAULT_CONTROL_PLANE_URL = "ws://localhost:8000/api/v1"

#: Bridge tokens carry this prefix (design §7) — used only for a friendly
#: "that doesn't look like a bridge token" hint, never for validation.
BRIDGE_TOKEN_PREFIX = "nubi_br_"


@dataclass
class BridgeIdentity:
    """Resolved control-channel identity for the agent.

    ``token`` is held in memory only and is excluded from ``__repr__`` so it
    never leaks into a stack trace, log line, or ``rich`` dump.
    """

    bridge_id: str
    control_plane_url: str
    token: str

    def __repr__(self) -> str:  # never expose the token
        return (
            f"BridgeIdentity(bridge_id={self.bridge_id!r}, "
            f"control_plane_url={self.control_plane_url!r}, token=<redacted>)"
        )


def _read_config() -> dict:
    """Read ``~/.nubi/bridge.json``; empty dict when absent or malformed."""
    if _BRIDGE_CONFIG_PATH.exists():
        try:
            return json.loads(_BRIDGE_CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def config_path() -> Path:
    """Return the bridge config file path (for status / docs)."""
    return _BRIDGE_CONFIG_PATH


def save_bridge_config(
    *,
    bridge_id: str | None = None,
    control_plane_url: str | None = None,
    token: str | None = None,
) -> None:
    """Persist non-None fields to ``~/.nubi/bridge.json`` (owner-only perms).

    Merges into any existing config so callers can update one field at a time.
    """
    _BRIDGE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:  # owner-only on the directory too (no group/other traversal)
        _BRIDGE_CONFIG_PATH.parent.chmod(0o700)
    except OSError:
        pass
    cfg = _read_config()
    if bridge_id is not None:
        cfg["bridge_id"] = bridge_id
    if control_plane_url is not None:
        cfg["control_plane_url"] = control_plane_url
    if token is not None:
        cfg["token"] = token
    payload = json.dumps(cfg, indent=2)
    # Write through a 0600 file descriptor so the token is NEVER world-readable
    # on disk, not even for the window between write_text() and a follow-up
    # chmod() (that race would briefly expose the token under a 0644 umask).
    # O_NOFOLLOW refuses to write through a symlink an attacker may have planted.
    fd = os.open(
        _BRIDGE_CONFIG_PATH,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    try:  # tighten in case the file pre-existed with looser perms
        _BRIDGE_CONFIG_PATH.chmod(0o600)
    except OSError:
        pass


def resolve_token(flag_token: str | None = None) -> str | None:
    """Resolve the bridge token: flag > NUBI_BRIDGE_TOKEN env > config file."""
    if flag_token:
        return flag_token
    env_token = os.environ.get("NUBI_BRIDGE_TOKEN")
    if env_token:
        return env_token
    return _read_config().get("token")


def resolve_bridge_id(flag_id: str | None = None) -> str | None:
    """Resolve the bridge id: flag > NUBI_BRIDGE_ID env > config file."""
    if flag_id:
        return flag_id
    env_id = os.environ.get("NUBI_BRIDGE_ID")
    if env_id:
        return env_id
    return _read_config().get("bridge_id")


def resolve_control_plane_url(flag_url: str | None = None) -> str:
    """Resolve the control-plane WS base: flag > env > config file > default."""
    if flag_url:
        return flag_url.rstrip("/")
    env_url = os.environ.get("NUBI_CONTROL_PLANE_URL")
    if env_url:
        return env_url.rstrip("/")
    cfg_url = _read_config().get("control_plane_url")
    if cfg_url:
        return str(cfg_url).rstrip("/")
    return DEFAULT_CONTROL_PLANE_URL


def resolve_identity(
    *,
    token: str | None = None,
    bridge_id: str | None = None,
    control_plane_url: str | None = None,
) -> BridgeIdentity:
    """Resolve the full control-channel identity, raising on a missing token/id.

    Raises
    ------
    BridgeConfigError
        When no token, or no bridge_id, can be resolved from any source.
    """
    tok = resolve_token(token)
    if not tok:
        raise BridgeConfigError(
            "No bridge token found. Pass --token nubi_br_…, set NUBI_BRIDGE_TOKEN, "
            "or run `nubi bridge configure --token …`."
        )
    bid = resolve_bridge_id(bridge_id)
    if not bid:
        raise BridgeConfigError(
            "No bridge id found. Pass --bridge-id, set NUBI_BRIDGE_ID, "
            "or run `nubi bridge configure --bridge-id …`."
        )
    return BridgeIdentity(
        bridge_id=bid,
        control_plane_url=resolve_control_plane_url(control_plane_url),
        token=tok,
    )


class BridgeConfigError(Exception):
    """Raised when required bridge identity (token / id) cannot be resolved."""
