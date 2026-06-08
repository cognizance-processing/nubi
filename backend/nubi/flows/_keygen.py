"""Deterministic task-key generation utilities.

The keygen module provides stable, collision-resistant key generation for
task nodes within a flow.  Keys must be valid Python identifiers and unique
within a single flow.

Public API
----------
slugify(name) -> str
    Convert a human-readable name to a lowercase snake_case slug safe for use
    as a task key or flow name.

make_unique_key(base, existing) -> str
    Ensure *base* is unique within *existing* by appending ``_2``, ``_3``, …
    Returns *base* unchanged when it is already unique.
"""

from __future__ import annotations

import re


def slugify(name: str) -> str:
    """Convert *name* to a lowercase snake_case identifier.

    Steps
    -----
    1. Lowercase the input.
    2. Replace any run of non-alphanumeric characters (including spaces and
       hyphens) with a single underscore.
    3. Strip leading/trailing underscores.
    4. Ensure the result starts with a letter (prepend ``t_`` if it starts
       with a digit, which is not a valid Python identifier start).

    Parameters
    ----------
    name:
        Human-readable string (e.g. ``"Get Regions!"``).

    Returns
    -------
    str
        A valid Python identifier suitable for use as a task key
        (e.g. ``"get_regions"``).

    Examples
    --------
    >>> slugify("Get Regions!")
    'get_regions'
    >>> slugify("123start")
    't_123start'
    >>> slugify("hello-world  foo")
    'hello_world_foo'
    """
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    if not s:
        return "task"
    if s[0].isdigit():
        s = "t_" + s
    return s


def make_unique_key(base: str, existing: set[str]) -> str:
    """Return a version of *base* that does not collide with *existing*.

    If *base* is already unique, it is returned as-is.  Otherwise numeric
    suffixes (``_2``, ``_3``, …) are appended until a unique variant is found.

    Parameters
    ----------
    base:
        Desired key (e.g. ``"transform"``).
    existing:
        Set of already-used keys in the current scope.

    Returns
    -------
    str
        A key that is not in *existing*.

    Examples
    --------
    >>> make_unique_key("transform", {"transform"})
    'transform_2'
    >>> make_unique_key("transform", {"transform", "transform_2"})
    'transform_3'
    >>> make_unique_key("fetch", {"pull"})
    'fetch'
    """
    if base not in existing:
        return base
    counter = 2
    while True:
        candidate = f"{base}_{counter}"
        if candidate not in existing:
            return candidate
        counter += 1
