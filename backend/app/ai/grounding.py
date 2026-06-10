"""Deterministic AI grounding over the lineage catalog (M7-B).

All functions in this module are pure / deterministic — no LLM calls, no
network I/O.  The grounding pipeline runs entirely on local data (registry
+ lineage graph) and uses simple token-overlap scoring to rank relevant
tables and columns.

Public API
----------
build_catalog() -> dict
    Build a snapshot catalog from the live query registry and lineage graph.

ground(question, catalog) -> dict
    Tokenize *question* and score each table/column by token overlap.
    Return the top-N most relevant tables, columns, related queries, and
    short "snippet" strings ready to inject into an LLM prompt.

build_prompt(question, grounding) -> tuple[str, str]
    Produce (system, user) prompt strings that include ONLY the grounded
    tables/columns — the anti-hallucination grounding principle.

Scoring algorithm
-----------------
For each candidate (table name, column name, or query output alias) we
compute a score as the sum of:

  substring_bonus  = 2 per question token that is a substring of the
                     candidate name (e.g. "order" matches "orders")
  token_bonus      = 3 per candidate token that exactly matches a
                     question token (e.g. "orders" token "orders" exactly
                     matches question token "orders")

Question tokens = lowercase words stripped of punctuation, length >= 2.
Candidates with score 0 are excluded from the output.
Tables are ranked by their aggregate score (sum of best column score +
table-name score).  Top ``MAX_TABLES`` tables are returned.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum number of tables to include in a grounding context.
MAX_TABLES: int = 5

#: Maximum number of (table, column) pairs to include in grounding context.
MAX_COLUMNS: int = 20

#: Maximum number of related query IDs to include.
MAX_QUERIES: int = 5


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^a-z0-9_]")


def _tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, return tokens of length >= 2.

    Parameters
    ----------
    text:
        Any natural-language string (question, column name, etc.).

    Returns
    -------
    list[str]
        Non-empty lowercase tokens with length >= 2 after punctuation removal.

    Examples
    --------
    >>> _tokenize("Show me orders by tenant!")
    ['show', 'me', 'orders', 'by', 'tenant']
    >>> _tokenize("tenant_id")
    ['tenant', 'id', 'tenant_id']
    """
    lower = text.lower()
    # Split on whitespace/punctuation boundaries.
    raw_tokens = _PUNCT_RE.sub(" ", lower).split()
    # Also include the original (underscore-joined) as a token for column names.
    tokens: list[str] = []
    for tok in raw_tokens:
        if len(tok) >= 2:
            tokens.append(tok)
    # For underscore-containing names include the whole name as one token too.
    if "_" in lower:
        whole = lower.replace("-", "_")
        if len(whole) >= 2:
            tokens.append(whole)
    return list(dict.fromkeys(tokens))  # de-duplicate, preserve order


def _score(candidate: str, question_tokens: list[str]) -> float:
    """Score *candidate* (a name like a table or column name) against
    *question_tokens*.

    Scoring
    -------
    - +3 per question token that exactly matches a candidate token.
    - +2 per question token that is a non-trivial substring of the candidate
      (minimum token length 3 to avoid noise from "id", "by", etc.).

    Parameters
    ----------
    candidate:
        A table name, column name, or output alias (any case).
    question_tokens:
        Tokenized question words (from ``_tokenize``).

    Returns
    -------
    float
        A non-negative score; 0 means no match.
    """
    cand_tokens = set(_tokenize(candidate))
    cand_lower = candidate.lower()
    score = 0.0
    for qtok in question_tokens:
        # Exact token match.
        if qtok in cand_tokens:
            score += 3.0
        # Substring match (only for tokens of length >= 3 to reduce noise).
        elif len(qtok) >= 3 and qtok in cand_lower:
            score += 2.0
    return score


# ---------------------------------------------------------------------------
# Catalog builder
# ---------------------------------------------------------------------------


def build_catalog() -> dict[str, Any]:
    """Build an in-memory catalog from the query registry and lineage graph.

    The catalog is a plain dict that can be cached and passed to ``ground()``.
    It is computed synchronously and is safe to recompute on each request
    (the underlying registry and graph are fast to build).

    Returns
    -------
    dict
        Structure::

            {
                "tables": {
                    "<table_name>": ["<col1>", "<col2>", ...]
                },
                "queries": [
                    {
                        "id": "<query_id>",
                        "name": "<human name>",
                        "tables": ["<table>", ...],
                        "outputs": ["<output_col>", ...],
                        "datastore": "<datastore_id>" | None,
                        "params": [
                            {
                                "name": "<param>",
                                "type": "<text|number|date|...>",
                                "default": <any>,
                                "required": <bool>,
                                "options_query_id": "<id>" | None,
                            },
                            ...
                        ],
                        "output_schema": [
                            {"name": "<col>", "type": "<text|number|...>"},
                            ...
                        ],
                    },
                    ...
                ]
            }

        ``tables`` maps real table names to the union of all column names
        referenced across registered queries that touch that table.

        ``queries`` is a list of lightweight query descriptors (no SQL text,
        which could be large) useful for relevance ranking.  Each descriptor
        ALSO carries the declared ``params`` and ``output_schema`` pulled
        straight off the :class:`RegisteredQuery` so an agent binding a widget
        to a query knows the real parameter names and output column names —
        the chief source of invalid specs is guessing these.
    """
    from app.lineage.graph import build_graph  # noqa: PLC0415
    from app.queries.registry import get_query_registry  # noqa: PLC0415

    registry = get_query_registry()
    all_queries = registry.all()
    graph = build_graph(all_queries)

    # ── Build tables → columns mapping ─────────────────────────────────────
    tables: dict[str, list[str]] = {}
    for query_id, detail in graph.queries.items():
        if "error" in detail:
            continue
        for col_ref in detail.get("columns", []):
            tbl = col_ref.get("table")
            col = col_ref.get("column")
            if tbl and col:
                tables.setdefault(tbl, [])
                if col not in tables[tbl]:
                    tables[tbl].append(col)

    # Sort columns for determinism.
    for col_list in tables.values():
        col_list.sort()

    # ── Build queries descriptor list ────────────────────────────────────────
    queries_list: list[dict[str, Any]] = []
    for rq in all_queries:
        detail = graph.queries.get(rq.id, {})
        queries_list.append(
            {
                "id": rq.id,
                "name": rq.name,
                "tables": detail.get("tables", []),
                "outputs": detail.get("outputs", []),
                # NEW: real declared params + output schema off the RegisteredQuery
                # so agents author specs against real names instead of guessing.
                "datastore": rq.datastore_id,
                "params": _params_to_dicts(rq.params),
                "output_schema": _output_schema_to_dicts(rq.output_schema),
            }
        )

    return {"tables": tables, "queries": queries_list}


# ---------------------------------------------------------------------------
# RegisteredQuery → portable dict helpers (params / output schema)
# ---------------------------------------------------------------------------


def _params_to_dicts(params: Any) -> list[dict[str, Any]]:
    """Serialise a ``RegisteredQuery.params`` tuple to plain JSON-able dicts.

    Each entry carries ``name``, ``type``, ``default``, ``required`` and
    ``options_query_id`` — everything an agent needs to bind a parameter to a
    widget/variable.  Tolerant of ``None``/empty input (returns ``[]``).
    """
    out: list[dict[str, Any]] = []
    for p in params or ():
        out.append(
            {
                "name": p.name,
                "type": p.type,
                "default": p.default,
                "required": p.required,
                "options_query_id": p.options_query_id,
            }
        )
    return out


def _output_schema_to_dicts(output_schema: Any) -> list[dict[str, Any]]:
    """Serialise a ``RegisteredQuery.output_schema`` tuple to plain dicts.

    Each entry carries the output column ``name`` and its portable ``type``
    (``text|number|bool|date|timestamp|json``).  ``None`` (no declared
    contract) and empty tuples both yield ``[]``.
    """
    out: list[dict[str, Any]] = []
    for c in output_schema or ():
        out.append({"name": c.name, "type": c.type})
    return out


# ---------------------------------------------------------------------------
# Grounding retrieval
# ---------------------------------------------------------------------------


def ground(question: str, catalog: dict[str, Any]) -> dict[str, Any]:
    """Deterministic keyword/token-overlap grounding over *catalog*.

    This is the core anti-hallucination step: given a natural-language
    *question*, rank the catalog tables and columns by relevance and return
    only the top-N, discarding anything with zero relevance.  The LLM is
    then instructed to use ONLY these tables/columns, preventing fabrication
    of non-existent schema objects.

    Parameters
    ----------
    question:
        The user's natural-language question (e.g. "show me orders by tenant").
    catalog:
        Output of ``build_catalog()``.

    Returns
    -------
    dict
        GroundingContext with keys:

        ``relevant_tables``
            List of table name strings, ranked by score, up to ``MAX_TABLES``.
        ``relevant_columns``
            List of ``{"table": str, "column": str}`` dicts ranked by score,
            up to ``MAX_COLUMNS``.
        ``related_queries``
            List of query ID strings whose tables overlap with relevant tables,
            up to ``MAX_QUERIES``.
        ``related_query_details``
            Parallel list of richer descriptors for the same related queries —
            ``{id, name, params, output_schema}`` — so callers that want the
            real param/output names without re-reading the catalog can get them
            cheaply.  ADD-ONLY: ``related_queries`` (the ID-string list) is left
            untouched so existing callers / ``build_prompt`` keep working.
        ``snippets``
            List of short text strings like
            ``"table users(id, name, tenant_id)"`` — one per relevant table,
            ready to paste into an LLM prompt.

    Notes
    -----
    Tables with a total score of 0 are excluded entirely.  The scoring is
    documented in the module docstring.  The function is purely deterministic
    and synchronous — no network calls.
    """
    question_tokens = _tokenize(question)
    catalog_tables: dict[str, list[str]] = catalog.get("tables", {})
    catalog_queries: list[dict[str, Any]] = catalog.get("queries", [])

    # ── Score each table ────────────────────────────────────────────────────
    table_scores: dict[str, float] = {}
    for table_name, columns in catalog_tables.items():
        # Base score from table name alone.
        t_score = _score(table_name, question_tokens)
        # Boost from column names and outputs that match the question.
        for col in columns:
            col_s = _score(col, question_tokens)
            if col_s > 0:
                t_score += col_s * 0.5  # columns contribute at half weight to table score
        table_scores[table_name] = t_score

    # ── Score each (table, column) pair ─────────────────────────────────────
    col_scored: list[tuple[float, str, str]] = []  # (score, table, column)
    for table_name, columns in catalog_tables.items():
        t_base = _score(table_name, question_tokens)
        for col in columns:
            col_s = _score(col, question_tokens)
            # A column is relevant if either the table or the column itself
            # scores above zero.
            combined = col_s + t_base * 0.3
            if combined > 0:
                col_scored.append((combined, table_name, col))

    # ── Sort and select top results ──────────────────────────────────────────
    # Tables: sort by score descending, exclude score-0 entries.
    relevant_tables: list[str] = [
        t
        for t, s in sorted(table_scores.items(), key=lambda x: x[1], reverse=True)
        if s > 0
    ][:MAX_TABLES]

    # Columns: sort by score descending.
    col_scored.sort(key=lambda x: x[0], reverse=True)
    relevant_columns: list[dict[str, str]] = [
        {"table": t, "column": c}
        for _, t, c in col_scored[:MAX_COLUMNS]
    ]

    # ── Related query IDs ────────────────────────────────────────────────────
    relevant_table_set = set(relevant_tables)
    related_query_ids: list[str] = []
    related_query_details: list[dict[str, Any]] = []
    for qd in catalog_queries:
        if relevant_table_set.intersection(qd.get("tables", [])):
            related_query_ids.append(qd["id"])
            # Surface the richer descriptor (params + output schema) cheaply —
            # these may be absent on hand-built catalogs, so default safely.
            related_query_details.append(
                {
                    "id": qd["id"],
                    "name": qd.get("name", qd["id"]),
                    "params": qd.get("params", []),
                    "output_schema": qd.get("output_schema", []),
                }
            )
        if len(related_query_ids) >= MAX_QUERIES:
            break

    # ── Snippets: one short string per relevant table ───────────────────────
    snippets: list[str] = []
    for table_name in relevant_tables:
        cols = catalog_tables.get(table_name, [])
        col_str = ", ".join(cols) if cols else ""
        if col_str:
            snippets.append(f"table {table_name}({col_str})")
        else:
            snippets.append(f"table {table_name}")

    return {
        "relevant_tables": relevant_tables,
        "relevant_columns": relevant_columns,
        "related_queries": related_query_ids,
        "related_query_details": related_query_details,
        "snippets": snippets,
    }


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

_SYSTEM_TEMPLATE = """\
You are a SQL assistant for the Nubi analytics platform.
You generate syntactically correct SQL SELECT statements.

RULES (follow strictly):
1. Only reference tables and columns listed in the SCHEMA section below.
   Do NOT invent, hallucinate, or assume any table or column that is not listed.
2. Use standard SQL (Postgres dialect unless otherwise stated).
3. Output ONLY the SQL query — no explanation, no markdown fences.
4. If the question cannot be answered with the provided schema, respond with:
   -- Unable to generate SQL: insufficient schema information

SCHEMA (grounded from the Nubi lineage index):
{snippets}
""".strip()

_USER_TEMPLATE = """\
Question: {question}

Related registered queries (for reference, do not copy directly):
{related_queries_text}

Generate a SQL SELECT statement that answers the question using only the tables \
and columns listed in the SCHEMA.
""".strip()


def build_prompt(question: str, grounding: dict[str, Any]) -> tuple[str, str]:
    """Build (system, user) prompt strings from the grounding context.

    The system prompt includes ONLY the grounded tables/columns (schema
    snippets).  The user prompt includes the question and a hint about
    related registered queries.  This design minimises hallucination by
    anchoring the LLM to the real schema.

    Parameters
    ----------
    question:
        The original user question.
    grounding:
        The ``GroundingContext`` dict returned by ``ground()``.

    Returns
    -------
    tuple[str, str]
        ``(system_prompt, user_prompt)`` — pass these to
        ``LLMProvider.complete(user_prompt, system=system_prompt)``.
    """
    snippets = grounding.get("snippets", [])
    related_queries = grounding.get("related_queries", [])

    snippets_text = "\n".join(f"  {s}" for s in snippets) if snippets else "  (no tables matched)"
    related_queries_text = (
        ", ".join(related_queries) if related_queries else "(none)"
    )

    system = _SYSTEM_TEMPLATE.format(snippets=snippets_text)
    user = _USER_TEMPLATE.format(
        question=question,
        related_queries_text=related_queries_text,
    )

    return system, user
