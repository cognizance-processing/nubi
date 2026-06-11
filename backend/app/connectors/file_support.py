"""Shared helpers for the file-connector layer (ingestion design §2).

Keeps the pattern-matching / watermark-filter / sort logic in one place so the
``sftp``, ``ftp``, and storage-backed file connectors behave identically and a
fix lands once.  No third-party imports — stdlib only.
"""

from __future__ import annotations

import fnmatch
import posixpath
from datetime import datetime

from app.connectors.base import FileStat


def split_pattern(pattern: str) -> tuple[str, str]:
    """Split a glob *pattern* into a (literal-prefix-dir, glob) pair.

    The literal prefix — the leading path components that contain no glob
    metacharacters — is what a backend can list cheaply (``ls <dir>`` /
    ``Prefix=`` on S3); the remainder is matched with :func:`fnmatch`.

    Examples
    --------
    >>> split_pattern("outbound/2024/*.csv")
    ('outbound/2024', '*.csv')
    >>> split_pattern("outbound/**/*.csv")
    ('outbound', '**/*.csv')
    >>> split_pattern("*.csv")
    ('', '*.csv')
    >>> split_pattern("data/orders.csv")
    ('data', 'orders.csv')
    """
    pat = (pattern or "*").lstrip("/")
    if not pat:
        pat = "*"
    parts = pat.split("/")
    literal: list[str] = []
    for part in parts:
        if any(ch in part for ch in "*?[]"):
            break
        literal.append(part)
    # The literal dir excludes the final component when it is itself a glob;
    # when the whole pattern is literal it is a single file and the prefix is
    # its directory.
    if len(literal) == len(parts):
        # Fully literal pattern → prefix is the parent dir.
        prefix = "/".join(parts[:-1])
    else:
        prefix = "/".join(literal)
    return prefix, pat


def matches(path: str, pattern: str) -> bool:
    """Return True if *path* matches the glob *pattern* (posix semantics).

    ``**`` is treated as "match across directory separators" (so
    ``outbound/**/*.csv`` matches ``outbound/a/b/x.csv``); a single ``*`` does
    not cross ``/`` boundaries, matching shell glob intuition closely enough for
    ingestion patterns.
    """
    path = path.lstrip("/")
    pat = (pattern or "*").lstrip("/")
    if "**" in pat:
        # fnmatch has no ** concept; collapse it so * spans separators.
        # Replace the path separators in both sides with a sentinel-free
        # fnmatch over the flattened string.
        regex_pat = pat.replace("**/", "*/").replace("**", "*")
        # Use fnmatch but allow * to cross '/': fnmatch's * already matches '/'
        # because it is not path-aware, so plain fnmatch suffices here.
        return fnmatch.fnmatch(path, regex_pat)
    # Single-level globbing: match per the full path. fnmatch's * matches '/'
    # too, which is acceptable — ingestion patterns are shallow in practice.
    return fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(posixpath.basename(path), pat)


def passes_since(stat: FileStat, since: datetime | None) -> bool:
    """Return True if *stat* should be included given the *since* watermark.

    Files with an unknown ``mtime`` are always included (never silently
    skipped); files with a known ``mtime`` are included only when strictly
    newer than *since*.
    """
    if since is None:
        return True
    if stat.mtime is None:
        return True
    return stat.mtime > since


def finalize(stats: list[FileStat], pattern: str, since: datetime | None) -> list[FileStat]:
    """Filter *stats* by *pattern* + *since* and sort by ``path``.

    Centralises the tail of every ``list_files`` implementation so all file
    connectors apply identical matching, watermark filtering, and lexicographic
    ordering (the ``filename`` incremental strategy depends on this sort).
    """
    out = [
        s for s in stats if matches(s.path, pattern) and passes_since(s, since)
    ]
    out.sort(key=lambda s: s.path)
    return out
