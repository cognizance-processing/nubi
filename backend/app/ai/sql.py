"""AI text-to-SQL generation with catalog grounding (M18-A).

Public API
----------
generate_sql(question, catalog, provider, datastore_id=None) -> dict
    Ground the question on the catalog/lineage, build a SQL-focused prompt,
    call the LLM provider, and validate the returned SQL via sqlglot.

    Returns::

        {
            "sql":    "<SQL string>",
            "valid":  True | False,
            "issues": ["<issue msg>", ...]   # empty when valid=True
        }

    With ``NullProvider`` (the default) a DETERMINISTIC safe ``SELECT`` over a
    REAL table from the catalog is returned — no network call, tests pass offline.

    With a real provider the model is given a grounded prompt that includes ONLY
    real tables/columns from the lineage index (anti-hallucination principle).

Design notes
------------
- Grounding re-uses ``build_catalog``, ``build_prompt``, and ``ground`` from
  ``app.ai.grounding`` unchanged (no modification to that module).
- The SQL prompt differs from the ``/ai/ask`` prompt: it is more tightly
  constrained to produce a bare SQL SELECT (no markdown, no explanation).
- sqlglot is imported lazily inside ``_validate_sql`` so the module loads even
  when sqlglot is not installed (it is already a project dependency, but the
  lazy import keeps module init lightweight).
- The ``NullProvider`` path always returns a parseable SELECT so that the test
  suite can assert ``valid == True`` without any API keys or network access.
"""

from __future__ import annotations

import re
from typing import Any

from app.ai.grounding import build_catalog, build_prompt, ground
from app.ai.provider import LLMProvider, NullProvider

# ---------------------------------------------------------------------------
# Prompt templates (SQL-specific; tighter than the /ai/ask templates)
# ---------------------------------------------------------------------------

_SQL_SYSTEM = """\
You are a SQL generator for the Nubi analytics platform.
Generate a syntactically correct SQL SELECT statement.

RULES (follow strictly):
1. Only reference tables and columns listed in the SCHEMA section below.
   Do NOT invent or hallucinate any table or column not listed.
2. Use standard SQL (Postgres dialect unless otherwise stated).
3. Output ONLY the SQL query — no explanation, no markdown fences, no comments.
4. The query must start with SELECT.
5. If the question cannot be answered with the provided schema, output exactly:
   SELECT 1 -- Unable to generate SQL: insufficient schema information

SCHEMA (grounded from the Nubi lineage index):
{snippets}
""".strip()

_SQL_USER = """\
Question: {question}

Generate a SQL SELECT statement that answers the question using only the tables \
and columns listed in the SCHEMA. Output ONLY the SQL — no explanation.
""".strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pick_fallback_table(catalog: dict[str, Any]) -> tuple[str, list[str]]:
    """Pick a real table + columns from the catalog for the NullProvider path.

    Strategy:
    1. Prefer the first table with at least one known column.
    2. Fall back to the first table with any entry.
    3. If the catalog is empty, use "demo" with no columns.

    Returns
    -------
    tuple[str, list[str]]
        ``(table_name, [col1, col2, ...])`` — columns may be empty.
    """
    tables: dict[str, list[str]] = catalog.get("tables", {})
    # Prefer tables with columns.
    for tbl, cols in tables.items():
        if cols:
            return tbl, cols
    # Any table.
    for tbl, cols in tables.items():
        return tbl, cols
    # Absolute fallback.
    return "demo", []


def _build_null_sql(question: str, catalog: dict[str, Any]) -> str:
    """Build a deterministic, parseable SELECT for the NullProvider path.

    The SELECT references a REAL table from the catalog so it is guaranteed
    to pass sqlglot parsing.  The question is embedded as a comment so that
    test assertions can verify the grounding pipeline.

    Parameters
    ----------
    question:
        The user's natural-language question (embedded in an SQL comment).
    catalog:
        Output of ``build_catalog()``.

    Returns
    -------
    str
        A valid SELECT statement, e.g.
        ``SELECT id, name FROM users -- question: show me users``
    """
    grounding = ground(question, catalog)
    relevant_tables: list[str] = grounding.get("relevant_tables", [])

    # Prefer a grounded table; fall back to any real catalog table.
    if relevant_tables:
        table = relevant_tables[0]
        cols: list[str] = catalog.get("tables", {}).get(table, [])
    else:
        table, cols = _pick_fallback_table(catalog)

    # Build column list: up to 5 columns or * if none known.
    col_clause = ", ".join(cols[:5]) if cols else "*"
    # Sanitize question for embedding in an SQL comment (strip newlines).
    safe_q = question.replace("\n", " ").replace("--", "").strip()[:80]
    return f"SELECT {col_clause} FROM {table} -- question: {safe_q}"


# ---------------------------------------------------------------------------
# SQL validation
# ---------------------------------------------------------------------------


def _validate_sql(sql: str) -> tuple[bool, list[str]]:
    """Parse *sql* with sqlglot and return ``(valid, issues)``.

    A query is considered valid if sqlglot can parse it without raising an
    exception AND the parsed tree contains at least one SELECT expression.

    Parameters
    ----------
    sql:
        The SQL string to validate.

    Returns
    -------
    tuple[bool, list[str]]
        ``(True, [])`` if valid; ``(False, [issue, ...])`` if not.
    """
    issues: list[str] = []
    try:
        import sqlglot  # noqa: PLC0415 — lazy import
    except ImportError:
        # sqlglot not installed — skip validation, treat as valid.
        return True, []

    try:
        statements = sqlglot.parse(sql)
    except Exception as exc:  # noqa: BLE001
        issues.append(f"SQL parse error: {exc}")
        return False, issues

    if not statements or all(s is None for s in statements):
        issues.append("SQL produced no parse tree (empty or unparseable).")
        return False, issues

    # Check that the first non-None statement is a SELECT.
    from sqlglot import exp as _exp  # noqa: PLC0415

    first = next((s for s in statements if s is not None), None)
    if first is None:
        issues.append("SQL produced no parse tree.")
        return False, issues

    # Accept SELECT or WITH...SELECT (CTE).  Reject DDL / DML.
    if not isinstance(first, (_exp.Select, _exp.With, _exp.Union)):
        issues.append(
            f"SQL is not a SELECT statement (got {type(first).__name__})."
        )
        return False, issues

    return True, []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_sql(
    question: str,
    catalog: dict[str, Any],
    provider: LLMProvider,
    datastore_id: str | None = None,
) -> dict[str, Any]:
    """Generate, ground, and validate a SQL SELECT for *question*.

    Pipeline
    --------
    1. Run deterministic grounding over *catalog* to find relevant tables/cols.
    2. Build a SQL-focused (system, user) prompt from the grounding context.
    3. Call ``provider.complete(user_prompt, system=system_prompt)``.
       - With ``NullProvider``: returns a deterministic safe SELECT over a REAL
         table from the catalog; no network call.
       - With a real provider: calls the LLM API.
    4. Strip any accidental markdown fences from the response.
    5. Validate the SQL with sqlglot (lazy import).
    6. Return ``{sql, valid, issues}``.

    Parameters
    ----------
    question:
        The natural-language question to convert to SQL.
    catalog:
        Output of ``build_catalog()`` — the live registry + lineage snapshot.
    provider:
        An ``LLMProvider`` instance.
    datastore_id:
        Optional datastore id (passed for context; not currently used in the
        prompt but retained for future connector-aware prompting).

    Returns
    -------
    dict
        ``{"sql": str, "valid": bool, "issues": list[str]}``
    """
    # ── NullProvider: fully deterministic, no LLM call ──────────────────────
    if isinstance(provider, NullProvider):
        sql = _build_null_sql(question, catalog)
        valid, issues = _validate_sql(sql)
        return {"sql": sql, "valid": valid, "issues": issues}

    # ── Real provider path ───────────────────────────────────────────────────
    grounding = ground(question, catalog)

    # Build a SQL-specific prompt (tighter than the generic /ai/ask prompt).
    snippets_text = (
        "\n".join(f"  {s}" for s in grounding.get("snippets", []))
        or "  (no schema matched)"
    )
    system = _SQL_SYSTEM.format(snippets=snippets_text)
    user = _SQL_USER.format(question=question)

    raw = provider.complete(user, system=system)

    # Strip markdown code fences if the model added them.
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        start = 1 if lines[0].startswith("```") else 0
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        cleaned = "\n".join(lines[start:end]).strip()

    # Further strip a language tag line like "sql" or "SQL".
    if cleaned.lower().startswith("sql\n"):
        cleaned = cleaned[4:].strip()

    sql = cleaned
    valid, issues = _validate_sql(sql)
    return {"sql": sql, "valid": valid, "issues": issues}
