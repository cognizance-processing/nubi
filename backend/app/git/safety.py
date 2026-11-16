"""Input rules for everything that ends up on a ``git`` command line.

A repository URL and a branch name are caller-supplied strings that become
arguments of a subprocess.  They are validated here — once, the same way, for
every caller — and git itself is always run with a restricted environment.
"""

from __future__ import annotations

import os
import re
from typing import Mapping
from urllib.parse import urlsplit

from app.errors import AppError

_HOST_RE = re.compile(r"\A(?=.{1,253}\Z)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+\Z")
_PATH_RE = re.compile(r"\A(/[A-Za-z0-9._~-]+)+/?\Z")
_BRANCH_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\Z")


def _host_is_public(host: str) -> bool:
    """True when *host* resolves only to public addresses."""
    from app.connectors.ssrf import (  # noqa: PLC0415
        _is_forbidden,
        _resolve_addresses,
    )

    return not any(_is_forbidden(ip, allow_private=False) for ip in _resolve_addresses(host))


def safe_repo_url(url: object) -> str:
    """Return *url* stripped, or raise 400 unless it is ``https://host/path``.

    No other scheme (``ssh``, ``git``, ``file``, ``ext::``), no credentials in
    the URL, no options or whitespace, and a host that is a public name — the
    platform fetches from it.
    """

    def _bad() -> AppError:
        return AppError(
            "git_repo_url_invalid",
            "The repository URL must be an https:// URL of a hosted git repository.",
            400,
        )

    if not isinstance(url, str):
        raise _bad()
    text = url.strip()
    if not text or text.startswith("-") or any(ch.isspace() or ord(ch) < 32 for ch in text):
        raise _bad()
    try:
        parts = urlsplit(text)
    except ValueError:
        raise _bad() from None
    if parts.scheme != "https" or parts.username or parts.password or parts.query or parts.fragment:
        raise _bad()
    try:
        port = parts.port
    except ValueError:
        raise _bad() from None
    host = parts.hostname or ""
    if port not in (None, 443) or not _HOST_RE.match(host):
        raise _bad()
    if not _PATH_RE.match(parts.path or "") or ".." in parts.path.split("/"):
        raise _bad()
    if not _host_is_public(host):
        raise _bad()
    return text


def safe_branch(name: object) -> str:
    """Return *name*, or raise 400 unless it is a plain branch name.

    The rules of ``git check-ref-format --branch``, restricted further to a
    conservative character set: it can never start with ``-`` (an option) and
    never carries ref syntax (``..``, ``~``, ``^``, ``:``, ``@{``, globs).
    """
    ok = (
        isinstance(name, str)
        and bool(_BRANCH_RE.match(name))
        and ".." not in name
        and "//" not in name
        and "@{" not in name
        and not name.endswith(("/", ".", ".lock"))
        and not any(part.startswith(".") or part.endswith(".lock") for part in name.split("/"))
    )
    if not ok:
        raise AppError("git_branch_invalid", "That is not a valid branch name.", 400)
    return name  # type: ignore[return-value]


def git_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment every git subprocess runs with.

    Only the HTTPS transport is allowed (no ``ext::``, ``file``, ``ssh``), git
    never prompts, and system-wide configuration is ignored.
    """
    env = dict(os.environ if base is None else base)
    env["GIT_ALLOW_PROTOCOL"] = "https"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env
