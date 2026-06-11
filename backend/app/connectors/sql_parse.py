"""Bounded, copy-on-return sqlglot parse cache for the query hot path.

sqlglot is a pure-Python tokenizer + parser, and on Nubi's query hot path the
same SQL string is parsed 2–3× per cache-MISS request (``planner.plan`` parses
the original SQL, then ``route_to_rollup_shape`` and the query-log ``record``
each re-parse ``plan.sql``).  Re-tokenizing/re-parsing the *same* string is pure
waste.

This module memoises the expensive parse with an :func:`functools.lru_cache`
keyed on ``(sql, dialect)``.  The first call for a given key pays the full
tokenize+parse cost; subsequent calls (within a request, or across requests for
repeated identical queries) pay only a dict lookup plus a cheap tree copy.

Copy-safety invariant (the core correctness guarantee)
------------------------------------------------------
The cached *canonical* tree is NEVER handed out.  Every call to
:func:`parse_sql_cached` returns ``.copy()`` of the canonical, so callers may
freely mutate the result (rewrite FROM clauses, swap aggregates, etc.) without
ever corrupting the shared cached tree.  Do not change this — returning the
canonical directly would let one caller's mutation leak into every other
caller's parse.

Exception behaviour: :func:`functools.lru_cache` is thread-safe in CPython and
does NOT cache exceptions.  A parse error therefore propagates as the usual
``sqlglot.errors.SqlglotError`` and is not stored, so callers that rely on the
raise (planner) or catch it (query_log) keep working unchanged, and a later
good parse of a previously-bad string still succeeds.
"""

from __future__ import annotations

from functools import lru_cache

import sqlglot


@lru_cache(maxsize=512)
def _parse_canonical(sql: str, dialect: str):
    # Raises sqlglot.errors.SqlglotError on bad SQL — NOT cached (lru_cache does
    # not store exceptions), so the error propagates to the caller every time.
    return sqlglot.parse_one(sql, dialect=dialect)


def parse_sql_cached(sql: str, dialect: str = "postgres"):
    """Parse SQL once per ``(sql, dialect)``; return a FRESH COPY each call so
    callers may mutate freely without corrupting the cached canonical tree."""
    return _parse_canonical(sql, dialect).copy()


def clear_parse_cache() -> None:
    """Evict all memoised parses (primarily for tests)."""
    _parse_canonical.cache_clear()
