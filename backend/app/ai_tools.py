import os
import re
import uuid
from typing import Dict, Any, Optional, List

from .db import get_pool
from .query_engine import execute_python_query


def _row_to_dict(row) -> Dict[str, Any]:
    """Convert an asyncpg Record to a dict, stringifying UUIDs."""
    return {k: str(v) if isinstance(v, uuid.UUID) else v for k, v in dict(row).items()}


# ---------------------------------------------------------------------------
# Tool declarations (Gemini format) — individual tools
# ---------------------------------------------------------------------------

TOOL_LIST_DATASTORES = {
    "name": "list_datastores",
    "description": "Get a list of available datastores (database connections) that can be used in queries. Returns datastore ID, name, and type (bigquery, postgres, mysql, mssql, athena, or duckdb).",
    "parameters": {"type": "object", "properties": {}}
}

TOOL_LIST_BOARD_QUERIES = {
    "name": "list_board_queries",
    "description": "Get all queries for a specific board. Returns query ID, name, and description.",
    "parameters": {
        "type": "object",
        "properties": {
            "board_id": {"type": "string", "description": "The UUID of the board to list queries for"}
        },
        "required": ["board_id"]
    }
}

TOOL_GET_CODE = {
    "name": "get_code",
    "description": "Get the full code for a board or query. For type='board' returns HTML/JS code. For type='query' returns Python code.",
    "parameters": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "description": "Entity type: 'board' or 'query'"},
            "id": {"type": "string", "description": "The UUID of the board or query"},
        },
        "required": ["type", "id"]
    }
}

TOOL_SEARCH_CODE = {
    "name": "search_code",
    "description": "Search for a pattern in a board's HTML or query's Python code. Returns matching lines with line numbers and context. Use instead of get_code when code is large. Tip: search for 'SECTION:' to find code organization markers.",
    "parameters": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "description": "Entity type: 'board' or 'query'"},
            "id": {"type": "string", "description": "The UUID of the board or query"},
            "search_term": {"type": "string", "description": "Text or regex pattern to search for"},
            "context_lines": {"type": "integer", "description": "Number of lines of context around each match (default: 3)"},
        },
        "required": ["type", "id", "search_term"]
    }
}

TOOL_CREATE_OR_UPDATE_QUERY = {
    "name": "create_or_update_query",
    "description": "Create a new query or update an existing one. Automatically tests the query after saving. For new queries, provide board_id and query_name. For updates, also provide query_id.",
    "parameters": {
        "type": "object",
        "properties": {
            "board_id": {"type": "string", "description": "The UUID of the board"},
            "query_name": {"type": "string", "description": "Descriptive name for the query"},
            "python_code": {"type": "string", "description": "The full Python code for the query including @node comments"},
            "description": {"type": "string", "description": "Optional description of what the query does"},
            "query_id": {"type": "string", "description": "Optional: existing query UUID to update instead of creating new"}
        },
        "required": ["board_id", "query_name", "python_code"]
    }
}

TOOL_DELETE_QUERY = {
    "name": "delete_query",
    "description": "Delete a query by its ID.",
    "parameters": {
        "type": "object",
        "properties": {
            "query_id": {"type": "string", "description": "The UUID of the query to delete"}
        },
        "required": ["query_id"]
    }
}

TOOL_GET_DATASTORE_SCHEMA = {
    "name": "get_datastore_schema",
    "description": "Get schema information for a datastore. Can drill down: no params = list datasets, dataset = list tables, dataset+table = list columns. ALWAYS use this before writing queries!",
    "parameters": {
        "type": "object",
        "properties": {
            "datastore_id": {"type": "string", "description": "The UUID of the datastore"},
            "dataset": {"type": "string", "description": "Optional: dataset/schema name to list tables in"},
            "table": {"type": "string", "description": "Optional: table name to get columns for (requires dataset)"}
        },
        "required": ["datastore_id"]
    }
}

TOOL_RUN_QUERY = {
    "name": "run_query",
    "description": "Run a query by its Python code and return sample results. Use this to quickly check data without saving. Returns first 5 rows on success or error details on failure.",
    "parameters": {
        "type": "object",
        "properties": {
            "python_code": {"type": "string", "description": "The complete Python query code to run (with @node comments)"}
        },
        "required": ["python_code"]
    }
}

TOOL_EXECUTE_QUERY_DIRECT = {
    "name": "execute_query_direct",
    "description": "Execute a SQL query directly on a datastore and get results. Use this to explore data, debug queries, answer questions with data, or run ad-hoc queries. Returns up to 100 rows by default.",
    "parameters": {
        "type": "object",
        "properties": {
            "datastore_id": {"type": "string", "description": "The UUID of the datastore"},
            "sql_query": {"type": "string", "description": "The SQL query to execute"},
            "limit": {"type": "integer", "description": "Optional: max rows to return (default: 100, max: 1000)"}
        },
        "required": ["datastore_id", "sql_query"]
    }
}

TOOL_EDIT_CODE = {
    "name": "edit_code",
    "description": (
        "Make targeted search/replace edits to a board's HTML or query's Python code. "
        "Each edit specifies an exact 'search' string to find and a 'replace' string to substitute. "
        "The search string must match exactly one location in the code. "
        "Use get_code or search_code first to see the current code and find the right strings to target. "
        "Prefer small, focused edits over replacing large blocks."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "description": "Entity type: 'board' or 'query'"},
            "id": {"type": "string", "description": "The UUID of the board or query"},
            "edits": {
                "type": "array",
                "description": "Array of search/replace edits to apply sequentially",
                "items": {
                    "type": "object",
                    "properties": {
                        "search": {"type": "string", "description": "Exact string to find in the code (must match exactly once)"},
                        "replace": {"type": "string", "description": "Replacement string"},
                    },
                    "required": ["search", "replace"],
                },
            },
        },
        "required": ["type", "id", "edits"]
    }
}

TOOL_MANAGE_DATASTORE = {
    "name": "manage_datastore",
    "description": (
        "Manage datastores (database connections). Actions:\n"
        "- 'create': Create a new datastore. Requires name, type, config.\n"
        "- 'update': Update an existing datastore. Requires datastore_id, plus name/type/config to change.\n"
        "- 'test': Test connectivity. Requires datastore_id.\n"
        "- 'save_keyfile': Save a JSON keyfile (e.g. BigQuery service account key). Requires json_content. Returns a path to use in config.\n"
        "Supported types: postgres, mysql, bigquery, athena, mssql, duckdb. "
        "For postgres/mysql/mssql use connection_string in config. "
        "For bigquery use project_id and optionally keyfile_path. "
        "For athena use region, database, s3_output_location, access_key_id, secret_access_key."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "Action to perform: 'create', 'update', 'test', or 'save_keyfile'"},
            "datastore_id": {"type": "string", "description": "UUID of existing datastore (required for update and test)"},
            "name": {"type": "string", "description": "Display name for the datastore (for create/update)"},
            "type": {"type": "string", "description": "Database type: postgres, mysql, bigquery, athena, mssql, or duckdb (for create/update)"},
            "config": {"type": "object", "description": "Connection config object (for create/update)"},
            "json_content": {"type": "string", "description": "Full JSON keyfile content (for save_keyfile)"},
            "filename": {"type": "string", "description": "Descriptive filename for keyfile (default: keyfile.json)"},
        },
        "required": ["action"]
    }
}


# ---------------------------------------------------------------------------
# Tool sets by context
# ---------------------------------------------------------------------------

def _wrap_tools(tool_list: list) -> list:
    """Wrap tool declarations in Gemini tools format."""
    return [{"function_declarations": tool_list}]


_BOARD_QUERY_TOOLS = [
    TOOL_LIST_DATASTORES,
    TOOL_LIST_BOARD_QUERIES,
    TOOL_GET_CODE,
    TOOL_SEARCH_CODE,
    TOOL_EDIT_CODE,
    TOOL_CREATE_OR_UPDATE_QUERY,
    TOOL_DELETE_QUERY,
    TOOL_GET_DATASTORE_SCHEMA,
    TOOL_RUN_QUERY,
    TOOL_EXECUTE_QUERY_DIRECT,
]

_DATASTORE_TOOLS = [
    TOOL_LIST_DATASTORES,
    TOOL_GET_DATASTORE_SCHEMA,
    TOOL_EXECUTE_QUERY_DIRECT,
    TOOL_MANAGE_DATASTORE,
]

_GENERAL_TOOLS = [
    TOOL_LIST_DATASTORES,
    TOOL_LIST_BOARD_QUERIES,
    TOOL_GET_CODE,
    TOOL_SEARCH_CODE,
    TOOL_GET_DATASTORE_SCHEMA,
    TOOL_EXECUTE_QUERY_DIRECT,
    TOOL_MANAGE_DATASTORE,
]

ALL_TOOLS = _BOARD_QUERY_TOOLS + [TOOL_MANAGE_DATASTORE]
GEMINI_TOOLS = _wrap_tools(ALL_TOOLS)

_BOARD_QUERY_GEMINI = _wrap_tools(_BOARD_QUERY_TOOLS)
_DATASTORE_GEMINI = _wrap_tools(_DATASTORE_TOOLS)
_GENERAL_GEMINI = _wrap_tools(_GENERAL_TOOLS)


def get_tools_for_context(context: str) -> list:
    """Return tool set appropriate for the page context."""
    if context in ("board", "query"):
        return _BOARD_QUERY_GEMINI
    elif context == "datastore":
        return _DATASTORE_GEMINI
    else:
        return _GENERAL_GEMINI


# ---------------------------------------------------------------------------
# DB helper functions called by AI tool dispatch
# ---------------------------------------------------------------------------

async def get_available_datastores(user_id: Optional[str] = None, org_id: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        pool = get_pool()
        if org_id:
            rows = await pool.fetch("SELECT id, name, type FROM datastores WHERE organization_id = $1", org_id)
        elif user_id:
            rows = await pool.fetch("SELECT id, name, type FROM datastores WHERE user_id = $1", user_id)
        else:
            rows = await pool.fetch("SELECT id, name, type FROM datastores")
        return [_row_to_dict(r) for r in rows]
    except Exception:
        return []

async def get_available_boards(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        pool = get_pool()
        rows = await pool.fetch("SELECT id, name FROM boards LIMIT 20")
        return [_row_to_dict(r) for r in rows]
    except Exception:
        return []

async def get_board_queries(board_id: str) -> List[Dict[str, Any]]:
    try:
        pool = get_pool()
        rows = await pool.fetch("SELECT id, name, description FROM board_queries WHERE board_id = $1", board_id)
        return [_row_to_dict(r) for r in rows]
    except Exception:
        return []

def _add_line_numbers(code: str) -> str:
    """Prepend line numbers to each line of code."""
    lines = code.split("\n")
    width = len(str(len(lines)))
    return "\n".join(f"{i + 1:>{width}}: {line}" for i, line in enumerate(lines))


async def get_query_code(query_id: str) -> Dict[str, Any]:
    try:
        pool = get_pool()
        row = await pool.fetchrow("SELECT id, name, python_code FROM board_queries WHERE id = $1", query_id)
        if row:
            code = row["python_code"] or ""
            total_lines = len(code.split("\n")) if code else 0
            return {
                "id": str(row["id"]), "name": row["name"],
                "code": _add_line_numbers(code) if code else "",
                "total_lines": total_lines,
            }
        return {"error": "Query not found"}
    except Exception as e:
        return {"error": str(e)}

async def get_board_code(board_id: str) -> Dict[str, Any]:
    try:
        pool = get_pool()
        board = await pool.fetchrow("SELECT id, name FROM boards WHERE id = $1", board_id)
        if not board:
            return {"error": "Board not found"}
        code_row = await pool.fetchrow(
            "SELECT code FROM board_code WHERE board_id = $1 ORDER BY version DESC LIMIT 1", board_id
        )
        code = code_row["code"] if code_row else ""
        total_lines = len(code.split("\n")) if code else 0
        return {
            "id": str(board["id"]), "name": board["name"],
            "code": _add_line_numbers(code) if code else "",
            "total_lines": total_lines,
        }
    except Exception as e:
        return {"error": str(e)}


def _search_lines(code: str, search_term: str, context_lines: int = 3) -> tuple:
    """Search code string for a pattern. Returns (match_count, total_lines, snippets)."""
    lines = code.split("\n")
    try:
        pattern = re.compile(search_term, re.IGNORECASE)
    except re.error:
        pattern = re.compile(re.escape(search_term), re.IGNORECASE)

    match_indices = [i for i, line in enumerate(lines) if pattern.search(line)]
    if not match_indices:
        return 0, len(lines), []

    snippets, used = [], set()
    for idx in match_indices:
        start = max(0, idx - context_lines)
        end = min(len(lines), idx + context_lines + 1)
        snippet_lines = []
        for i in range(start, end):
            if i not in used:
                prefix = ">>> " if i == idx else "    "
                snippet_lines.append(f"{prefix}{i + 1}: {lines[i]}")
                used.add(i)
        if snippet_lines:
            snippets.append("\n".join(snippet_lines))
    return len(match_indices), len(lines), snippets[:20]


async def search_board_code(board_id: str, search_term: str, context_lines: int = 3) -> Dict[str, Any]:
    """Search within a board's HTML code for a pattern."""
    try:
        pool = get_pool()
        board = await pool.fetchrow("SELECT id, name FROM boards WHERE id = $1", board_id)
        if not board:
            return {"error": "Board not found"}
        code_row = await pool.fetchrow(
            "SELECT code FROM board_code WHERE board_id = $1 ORDER BY version DESC LIMIT 1", board_id
        )
        code = code_row["code"] if code_row else ""
        if not code:
            return {"id": str(board["id"]), "type": "board", "matches": [], "total_lines": 0, "message": "Board has no code"}

        match_count, total_lines, snippets = _search_lines(code, search_term, context_lines)
        result = {"id": str(board["id"]), "name": board["name"], "type": "board", "total_lines": total_lines}
        if not match_count:
            result.update({"matches": [], "message": f"No matches found for '{search_term}'"})
        else:
            result.update({"match_count": match_count, "matches": snippets})
        return result
    except Exception as e:
        return {"error": str(e)}


async def search_query_code(query_id: str, search_term: str, context_lines: int = 3) -> Dict[str, Any]:
    """Search within a query's Python code for a pattern."""
    try:
        pool = get_pool()
        row = await pool.fetchrow("SELECT id, name, python_code FROM board_queries WHERE id = $1", query_id)
        if not row:
            return {"error": "Query not found"}

        code = row["python_code"] or ""
        if not code:
            return {"id": str(row["id"]), "type": "query", "matches": [], "total_lines": 0, "message": "Query has no code"}

        match_count, total_lines, snippets = _search_lines(code, search_term, context_lines)
        result = {"id": str(row["id"]), "name": row["name"], "type": "query", "total_lines": total_lines}
        if not match_count:
            result.update({"matches": [], "message": f"No matches found for '{search_term}'"})
        else:
            result.update({"match_count": match_count, "matches": snippets})
        return result
    except Exception as e:
        return {"error": str(e)}


async def create_or_update_query(board_id: str, query_name: str, python_code: str, description: str = "", query_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        pool = get_pool()
        if query_id:
            await pool.execute(
                "UPDATE board_queries SET name=$1, python_code=$2, description=$3 WHERE id=$4",
                query_name, python_code, description, query_id,
            )
            result = {
                "success": True, "action": "updated", "query_id": query_id,
                "name": query_name, "message": f"Query '{query_name}' updated successfully",
            }
        else:
            row = await pool.fetchrow(
                "INSERT INTO board_queries (board_id, name, python_code, description) VALUES ($1,$2,$3,$4) RETURNING id",
                board_id, query_name, python_code, description,
            )
            result = {
                "success": True, "action": "created", "query_id": str(row["id"]),
                "name": query_name, "message": f"Query '{query_name}' created successfully",
            }

        exec_result = await execute_python_query(python_code, limit_rows=10)
        result["test"] = exec_result
        if exec_result.get("success"):
            rc = exec_result.get("row_count", 0)
            result["message"] += f" — executed successfully, {rc} rows returned"
            if exec_result.get("sample_rows"):
                result["sample_rows"] = exec_result["sample_rows"]
            if exec_result.get("columns"):
                result["columns"] = exec_result["columns"]
        else:
            result["message"] += f" — saved but execution failed: {exec_result.get('error', 'unknown error')}"

        return result
    except Exception as e:
        print(f"DEBUG: Exception in create_or_update_query: {str(e)}")
        return {"error": str(e)}

async def delete_query(query_id: str) -> Dict[str, Any]:
    try:
        pool = get_pool()
        await pool.execute("DELETE FROM board_queries WHERE id = $1", query_id)
        return {"success": True, "message": "Query deleted successfully"}
    except Exception as e:
        return {"error": str(e)}

async def update_board_code(board_id: str, html_code: str) -> Dict[str, Any]:
    try:
        pool = get_pool()
        ver_row = await pool.fetchrow(
            "SELECT version FROM board_code WHERE board_id = $1 ORDER BY version DESC LIMIT 1", board_id
        )
        next_version = (ver_row["version"] + 1) if ver_row else 1
        await pool.execute(
            "INSERT INTO board_code (board_id, code, version) VALUES ($1, $2, $3)",
            board_id, html_code, next_version,
        )
        return {"success": True, "board_id": board_id, "version": next_version, "message": f"Board code updated to version {next_version}"}
    except Exception as e:
        return {"error": str(e)}


async def apply_code_edits(entity_type: str, entity_id: str, edits: List[Dict[str, str]]) -> Dict[str, Any]:
    """Apply a list of search/replace edits to board HTML or query Python code."""
    try:
        pool = get_pool()

        if entity_type == "board":
            board = await pool.fetchrow("SELECT id, name FROM boards WHERE id = $1", entity_id)
            if not board:
                return {"error": "Board not found"}
            code_row = await pool.fetchrow(
                "SELECT code FROM board_code WHERE board_id = $1 ORDER BY version DESC LIMIT 1", entity_id
            )
            code = code_row["code"] if code_row else ""
        elif entity_type == "query":
            row = await pool.fetchrow("SELECT id, name, python_code FROM board_queries WHERE id = $1", entity_id)
            if not row:
                return {"error": "Query not found"}
            code = row["python_code"] or ""
        else:
            return {"error": f"Unknown type '{entity_type}'. Use 'board' or 'query'."}

        if not code:
            return {"error": f"No existing code found for {entity_type} {entity_id}. Use get_code to create from scratch."}

        old_code = code
        applied = []
        failed = []

        for i, edit in enumerate(edits):
            search = edit.get("search", "")
            replace = edit.get("replace", "")
            if not search:
                failed.append({"index": i, "reason": "Empty search string"})
                continue
            count = code.count(search)
            if count == 0:
                failed.append({"index": i, "reason": f"Search string not found", "search_preview": search[:80]})
                continue
            if count > 1:
                failed.append({"index": i, "reason": f"Search string matched {count} times (must be unique)", "search_preview": search[:80]})
                continue
            code = code.replace(search, replace, 1)
            applied.append(i)

        if not applied:
            return {"error": "No edits could be applied", "failed": failed}

        if entity_type == "board":
            save_result = await update_board_code(entity_id, code)
            if save_result.get("error"):
                return save_result
        else:
            await pool.execute(
                "UPDATE board_queries SET python_code=$1 WHERE id=$2", code, entity_id
            )

        result = {
            "success": True,
            "type": entity_type,
            "id": entity_id,
            "edits_applied": len(applied),
            "edits_failed": len(failed),
            "old_code": old_code,
            "new_code": code,
            "total_lines": len(code.split("\n")),
            "message": f"Applied {len(applied)} edit(s) to {entity_type} code.",
        }
        if failed:
            result["failed"] = failed
        return result
    except Exception as e:
        return {"error": str(e)}


async def create_or_update_datastore(
    name: str, ds_type: str, config: Dict[str, Any],
    datastore_id: Optional[str] = None,
    user_id: Optional[str] = None, org_id: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        pool = get_pool()

        if datastore_id:
            existing = await pool.fetchrow("SELECT * FROM datastores WHERE id = $1", datastore_id)
            if not existing:
                return {"error": "Datastore not found"}
            updates, params, idx = [], [], 1
            if name is not None:
                updates.append(f"name = ${idx}"); params.append(name); idx += 1
            if ds_type is not None:
                updates.append(f"type = ${idx}"); params.append(ds_type); idx += 1
            if config is not None:
                updates.append(f"config = ${idx}"); params.append(config); idx += 1
            if not updates:
                return {"success": True, "datastore_id": datastore_id, "message": "No changes specified"}
            params.append(datastore_id)
            await pool.execute(f"UPDATE datastores SET {', '.join(updates)} WHERE id = ${idx}", *params)
            return {
                "success": True, "action": "updated", "datastore_id": datastore_id,
                "name": name or existing["name"],
                "message": f"Datastore updated successfully. ID: {datastore_id}",
            }

        if not user_id:
            row = await pool.fetchrow("SELECT id FROM users LIMIT 1")
            user_id = str(row["id"]) if row else None
        if not user_id:
            return {"error": "No user found to associate datastore with"}

        if org_id:
            row = await pool.fetchrow(
                "INSERT INTO datastores (name, type, config, user_id, organization_id) VALUES ($1,$2,$3,$4,$5) RETURNING *",
                name, ds_type, config, user_id, org_id,
            )
        else:
            row = await pool.fetchrow(
                "INSERT INTO datastores (name, type, config, user_id) VALUES ($1,$2,$3,$4) RETURNING *",
                name, ds_type, config, user_id,
            )
        return {
            "success": True, "action": "created",
            "datastore_id": str(row["id"]),
            "name": name, "type": ds_type,
            "message": f"Datastore '{name}' ({ds_type}) created successfully. ID: {row['id']}",
        }
    except Exception as e:
        return {"error": str(e)}


async def test_datastore_tool(datastore_id: str) -> Dict[str, Any]:
    try:
        from .helpers import ensure_dict
        from .query_engine import get_bigquery_client, get_sa_engine, SA_TYPES

        pool = get_pool()
        row = await pool.fetchrow("SELECT * FROM datastores WHERE id = $1", datastore_id)
        if not row:
            return {"success": False, "error": "Datastore not found"}

        datastore = dict(row)
        ds_config = ensure_dict(datastore["config"])
        ds_type = datastore["type"]

        if ds_type == "bigquery":
            client = await get_bigquery_client(ds_config)
            list(client.list_datasets(max_results=1))
        elif ds_type in SA_TYPES:
            import sqlalchemy as sa
            engine = get_sa_engine(ds_type, ds_config)
            with engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
        else:
            return {"success": False, "error": f"Unsupported type: {ds_type}"}

        return {"success": True, "message": f"Connection to '{datastore['name']}' ({ds_type}) successful"}
    except Exception as e:
        return {"success": False, "error": f"Connection failed: {str(e)}"}


async def save_keyfile_tool(json_content: str, user_id: Optional[str] = None, filename: str = "keyfile.json") -> Dict[str, Any]:
    """Save a JSON keyfile from chat to secure storage."""
    try:
        import json as json_mod
        json_mod.loads(json_content)
    except (ValueError, TypeError):
        return {"error": "Invalid JSON content. Please provide valid JSON."}

    try:
        from .storage import get_storage_provider
        storage = get_storage_provider()

        if not user_id:
            pool = get_pool()
            row = await pool.fetchrow("SELECT id FROM users LIMIT 1")
            user_id = str(row["id"]) if row else "unknown"

        path = f"{user_id}/{uuid.uuid4()}.json"
        stored_path = await storage.upload("secret-files", path, json_content.encode("utf-8"), "application/json")
        return {
            "success": True,
            "keyfile_path": stored_path,
            "message": f"Keyfile saved securely. Use this path in create_datastore config: keyfile_path = '{stored_path}'",
        }
    except Exception as e:
        return {"error": f"Failed to save keyfile: {str(e)}"}
