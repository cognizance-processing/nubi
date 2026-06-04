"""Physical query plan — the language-neutral boundary between planner and executor.

The planner (sqlglot, Python) compiles ``(logical query + RLS claims)`` into a
``PhysicalPlan``.  The executor runs the plan, encodes Arrow, streams, and caches.
Only the executor is ever ported (e.g. to Rust/WASM); the planner stays Python.

``PhysicalPlan`` is the frozen contract described in ROADMAP §3.1 rule 2.
It MUST remain JSON-serialisable so that a future Rust executor can consume it
without a Python runtime.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field


class PhysicalPlan(BaseModel):
    """Serialisable physical query plan produced by the planner.

    Fields
    ------
    dialect:
        SQL dialect string passed to sqlglot (e.g. ``"postgres"``).
    sql:
        The rewritten SQL string ready for the executor to run verbatim.
        Contains no Python f-string interpolation — all runtime values are in
        ``params``.
    params:
        Ordered positional parameters bound to ``$N`` / ``?`` / ``%s``
        placeholders in ``sql``.  May be empty.
    projection:
        Column names selected by the push-down projection, or ``None`` when the
        original ``SELECT *`` / full column list is used unchanged.
    predicates:
        Human-readable list of predicate strings injected into the WHERE clause
        (e.g. ``["tenant_id = 'abc'", "region = 'us-east'"]``).  Used for
        observability and conformance assertions; not re-evaluated.
    rls_claims:
        The raw RLS claim dict that was used during predicate injection.  Stored
        so the cache layer can verify key derivation without re-running the planner.
    cache_key:
        SHA-256 hex digest of the canonical JSON representation of the plan
        inputs.  See ``cache_key.py`` and ``docs/cache-key-spec.md`` for the
        exact algorithm that a future Rust executor must reproduce byte-for-byte.
    """

    dialect: str = Field(default="postgres", description="sqlglot dialect name")
    sql: str = Field(description="Rewritten SQL string, ready for execution")
    params: list[Any] = Field(
        default_factory=list,
        description="Positional parameters bound to placeholders in sql",
    )
    projection: list[str] | None = Field(
        default=None,
        description="Projected column names, or None for the full result set",
    )
    predicates: list[str] = Field(
        default_factory=list,
        description="Human-readable injected predicate strings (observability only)",
    )
    rls_claims: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw RLS claims used during predicate injection",
    )
    cache_key: str = Field(
        description="SHA-256 hex digest of the canonical plan inputs (cache-key-spec.md)",
    )

    model_config = {"frozen": True}

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def to_canonical_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-serialisable dict representation of this plan.

        The dict is suitable for serialisation across language boundaries (e.g. to
        a Rust executor via protobuf/JSON).  Keys are sorted alphabetically and all
        values are plain Python primitives (no pydantic models).

        The cache_key field is included so that the receiver can verify integrity
        without re-computing it.
        """
        return dict(
            sorted(
                {
                    "cache_key": self.cache_key,
                    "dialect": self.dialect,
                    "params": list(self.params),
                    "predicates": list(self.predicates),
                    "projection": list(self.projection) if self.projection is not None else None,
                    "rls_claims": dict(sorted(self.rls_claims.items())),
                    "sql": self.sql,
                }.items()
            )
        )

    def to_json(self, **kwargs: Any) -> str:
        """Serialise the plan to a compact JSON string.

        Uses ``to_canonical_dict()`` (sorted keys, no extra whitespace) so that
        the wire representation is deterministic across Python versions.
        """
        return json.dumps(self.to_canonical_dict(), separators=(",", ":"), **kwargs)
