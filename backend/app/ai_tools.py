import os
import re
import uuid
from typing import Dict, Any, Optional, List

from .db import get_pool
from .query_engine import test_query as _run_test


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

TOOL_GET_QUERY_CODE = {
    "name": "get_query_code",
    "description": "Get the Python code for a specific query. Returns query ID, name, and full Python code.",
    "parameters": {
        "type": "object",
        "properties": {
            "query_id": {"type": "string", "description": "The UUID of the query to get code for"}
        },
        "required": ["query_id"]
    }
}

TOOL_GET_BOARD_CODE = {
    "name": "get_board_code",
    "description": "Get the HTML/JavaScript code for a specific board. Returns board ID, name, and full HTML code.",
    "parameters": {
        "type": "object",
        "properties": {
            "board_id": {"type": "string", "description": "The UUID of the board to get code for"}
        },
        "required": ["board_id"]
    }
}

TOOL_SEARCH_BOARD_CODE = {
    "name": "search_board_code",
    "description": "Search for a pattern in a board's HTML code. Returns matching lines with line numbers. Use this instead of get_board_code when the code is large and you only need specific sections (e.g. find a widget, function, or style).",
    "parameters": {
        "type": "object",
        "properties": {
            "board_id": {"type": "string", "description": "The UUID of the board"},
            "search_term": {"type": "string", "description": "Text or regex pattern to search for in the board code"},
            "context_lines": {"type": "integer", "description": "Number of lines of context to show around each match (default: 3)"}
        },
        "required": ["board_id", "search_term"]
    }
}

TOOL_SEARCH_QUERY_CODE = {
    "name": "search_query_code",
    "description": "Search for a pattern in a query's Python code. Returns matching lines with line numbers. Use this instead of get_query_code when the code is large and you only need specific sections.",
    "parameters": {
        "type": "object",
        "properties": {
            "query_id": {"type": "string", "description": "The UUID of the query"},
            "search_term": {"type": "string", "description": "Text or regex pattern to search for in the query code"},
            "context_lines": {"type": "integer", "description": "Number of lines of context to show around each match (default: 3)"}
        },
        "required": ["query_id", "search_term"]
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

TOOL_CREATE_OR_UPDATE_DATASTORE = {
    "name": "create_or_update_datastore",
    "description": "Create a new datastore or update an existing one. For new datastores, provide name, type, and config. For updates, also provide datastore_id. Supported types: postgres, mysql, bigquery, athena, mssql, duckdb. For postgres/mysql/mssql use connection_string in config. For bigquery use project_id and optionally keyfile_path. For athena use region, database, s3_output_location, access_key_id, secret_access_key.",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Display name for the datastore"},
            "type": {"type": "string", "description": "Database type: postgres, mysql, bigquery, athena, mssql, or duckdb"},
            "config": {"type": "object", "description": "Connection config object"},
            "datastore_id": {"type": "string", "description": "Optional: existing datastore UUID to update instead of creating new"}
        },
        "required": ["name", "type", "config"]
    }
}

TOOL_TEST_DATASTORE = {
    "name": "test_datastore",
    "description": "Test connectivity to a datastore. Returns success or failure with error details.",
    "parameters": {
        "type": "object",
        "properties": {
            "datastore_id": {"type": "string", "description": "The UUID of the datastore to test"}
        },
        "required": ["datastore_id"]
    }
}

TOOL_SAVE_KEYFILE = {
    "name": "save_keyfile",
    "description": "Save a JSON keyfile (e.g. BigQuery service account key) that was provided in the chat. Pass the full JSON content and it will be stored securely. Returns the stored path to use in datastore config.",
    "parameters": {
        "type": "object",
        "properties": {
            "json_content": {"type": "string", "description": "The full JSON keyfile content to save"},
            "filename": {"type": "string", "description": "Optional: descriptive filename (default: keyfile.json)"}
        },
        "required": ["json_content"]
    }
}


# ---------------------------------------------------------------------------
# Contextual tool sets — each context gets only relevant tools
# ---------------------------------------------------------------------------

def _wrap_tools(tool_list: list) -> list:
    """Wrap tool declarations in Gemini tools format."""
    return [{"function_declarations": tool_list}]


def get_tools_for_context(context: str) -> list:
    """Return the Gemini-format tool list appropriate for the given page context."""
    if context == "board":
        return _wrap_tools([
            TOOL_LIST_DATASTORES,
            TOOL_LIST_BOARD_QUERIES,
            TOOL_GET_QUERY_CODE,
            TOOL_GET_BOARD_CODE,
            TOOL_SEARCH_BOARD_CODE,
            TOOL_SEARCH_QUERY_CODE,
            TOOL_CREATE_OR_UPDATE_QUERY,
            TOOL_DELETE_QUERY,
            TOOL_GET_DATASTORE_SCHEMA,
            TOOL_RUN_QUERY,
            TOOL_EXECUTE_QUERY_DIRECT,
        ])
    elif context == "query":
        return _wrap_tools([
            TOOL_LIST_DATASTORES,
            TOOL_LIST_BOARD_QUERIES,
            TOOL_GET_QUERY_CODE,
            TOOL_SEARCH_QUERY_CODE,
            TOOL_GET_DATASTORE_SCHEMA,
            TOOL_RUN_QUERY,
            TOOL_EXECUTE_QUERY_DIRECT,
            TOOL_CREATE_OR_UPDATE_QUERY,
            TOOL_DELETE_QUERY,
        ])
    elif context == "datastore":
        return _wrap_tools([
            TOOL_LIST_DATASTORES,
            TOOL_GET_DATASTORE_SCHEMA,
            TOOL_EXECUTE_QUERY_DIRECT,
            TOOL_TEST_DATASTORE,
            TOOL_CREATE_OR_UPDATE_DATASTORE,
            TOOL_SAVE_KEYFILE,
        ])
    else:
        return _wrap_tools([
            TOOL_LIST_DATASTORES,
            TOOL_LIST_BOARD_QUERIES,
            TOOL_GET_DATASTORE_SCHEMA,
            TOOL_EXECUTE_QUERY_DIRECT,
            TOOL_CREATE_OR_UPDATE_DATASTORE,
            TOOL_TEST_DATASTORE,
            TOOL_SAVE_KEYFILE,
        ])


# Keep GEMINI_TOOLS as full set for backward compat
GEMINI_TOOLS = _wrap_tools([
    TOOL_LIST_DATASTORES,
    TOOL_LIST_BOARD_QUERIES,
    TOOL_GET_QUERY_CODE,
    TOOL_GET_BOARD_CODE,
    TOOL_SEARCH_BOARD_CODE,
    TOOL_SEARCH_QUERY_CODE,
    TOOL_CREATE_OR_UPDATE_QUERY,
    TOOL_DELETE_QUERY,
    TOOL_GET_DATASTORE_SCHEMA,
    TOOL_RUN_QUERY,
    TOOL_EXECUTE_QUERY_DIRECT,
    TOOL_CREATE_OR_UPDATE_DATASTORE,
    TOOL_TEST_DATASTORE,
    TOOL_SAVE_KEYFILE,
])


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

async def get_query_code(query_id: str) -> Dict[str, Any]:
    try:
        pool = get_pool()
        row = await pool.fetchrow("SELECT id, name, python_code FROM board_queries WHERE id = $1", query_id)
        if row:
            return {"id": str(row["id"]), "name": row["name"], "code": row["python_code"]}
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
        return {
            "id": str(board["id"]),
            "name": board["name"],
            "code": code_row["code"] if code_row else "",
        }
    except Exception as e:
        return {"error": str(e)}


async def search_board_code(board_id: str, search_term: str, context_lines: int = 3) -> Dict[str, Any]:
    """Search within a board's HTML code for a pattern, returning matching lines with context."""
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
            return {"board_id": str(board["id"]), "matches": [], "total_lines": 0, "message": "Board has no code"}

        lines = code.split("\n")
        try:
            pattern = re.compile(search_term, re.IGNORECASE)
        except re.error:
            pattern = re.compile(re.escape(search_term), re.IGNORECASE)

        match_indices = [i for i, line in enumerate(lines) if pattern.search(line)]
        if not match_indices:
            return {
                "board_id": str(board["id"]),
                "board_name": board["name"],
                "matches": [],
                "total_lines": len(lines),
                "message": f"No matches found for '{search_term}'"
            }

        snippets = []
        used = set()
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

        return {
            "board_id": str(board["id"]),
            "board_name": board["name"],
            "match_count": len(match_indices),
            "total_lines": len(lines),
            "matches": snippets[:20],
        }
    except Exception as e:
        return {"error": str(e)}


async def search_query_code(query_id: str, search_term: str, context_lines: int = 3) -> Dict[str, Any]:
    """Search within a query's Python code for a pattern, returning matching lines with context."""
    try:
        pool = get_pool()
        row = await pool.fetchrow("SELECT id, name, python_code FROM board_queries WHERE id = $1", query_id)
        if not row:
            return {"error": "Query not found"}

        code = row["python_code"] or ""
        if not code:
            return {"query_id": str(row["id"]), "matches": [], "total_lines": 0, "message": "Query has no code"}

        lines = code.split("\n")
        try:
            pattern = re.compile(search_term, re.IGNORECASE)
        except re.error:
            pattern = re.compile(re.escape(search_term), re.IGNORECASE)

        match_indices = [i for i, line in enumerate(lines) if pattern.search(line)]
        if not match_indices:
            return {
                "query_id": str(row["id"]),
                "query_name": row["name"],
                "matches": [],
                "total_lines": len(lines),
                "message": f"No matches found for '{search_term}'"
            }

        snippets = []
        used = set()
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

        return {
            "query_id": str(row["id"]),
            "query_name": row["name"],
            "match_count": len(match_indices),
            "total_lines": len(lines),
            "matches": snippets[:20],
        }
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

        test_result = await _run_test(python_code)
        result["test"] = test_result
        if test_result.get("success"):
            result["message"] += f" — test passed, {test_result.get('row_count', 0)} rows returned"
        else:
            result["message"] += f" — saved but test failed: {test_result.get('error', 'unknown error')}"

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
