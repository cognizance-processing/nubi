import os
import json
import tempfile
from typing import Dict, Any, Optional, List

import pandas as pd
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from jinja2 import Template
from fastapi import HTTPException
from google.cloud import bigquery
from google.oauth2 import service_account

from .db import get_pool
from .storage import get_storage_provider
from .helpers import clean_dataframe_for_json, ensure_dict

storage = get_storage_provider()

CONNSTRING_TYPES = ("postgres", "mysql", "mssql")
SA_TYPES = ("postgres", "mysql", "mssql", "athena", "duckdb")


# ---------------------------------------------------------------------------
# BigQuery client
# ---------------------------------------------------------------------------

async def get_bigquery_client(config):
    """Creates a BigQuery client from config."""
    config = ensure_dict(config)
    project_id = config.get("project_id")
    keyfile_path_in_storage = config.get("keyfile_path")

    if keyfile_path_in_storage:
        try:
            res = await storage.download("secret-files", keyfile_path_in_storage)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
                tmp.write(res)
                tmp_path = tmp.name

            credentials = service_account.Credentials.from_service_account_file(tmp_path)
            client = bigquery.Client(credentials=credentials, project=project_id)
            return client
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to load keyfile: {str(e)}")

    return bigquery.Client(project=project_id)


# ---------------------------------------------------------------------------
# Athena connection helper
# ---------------------------------------------------------------------------

def get_athena_connection_string(config: Dict[str, Any]) -> str:
    """Build a PyAthena SQLAlchemy connection string from config."""
    region = config.get("region", "us-east-1")
    s3_output = config.get("s3_output_location", "")
    database = config.get("database", "default")
    access_key = config.get("access_key_id", "")
    secret_key = config.get("secret_access_key", "")
    return (
        f"awsathena+rest://{access_key}:{secret_key}@athena.{region}.amazonaws.com:443/"
        f"{database}?s3_staging_dir={s3_output}"
    )


# ---------------------------------------------------------------------------
# Generic SQLAlchemy engine from datastore config
# ---------------------------------------------------------------------------

def get_duckdb_connection_string(config: Dict[str, Any]) -> str:
    """Build a DuckDB SQLAlchemy connection string from config."""
    file_path = config.get("file_path", "")
    if file_path:
        base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage", "secret-files")
        full_path = os.path.join(base_dir, file_path)
        return f"duckdb:///{full_path}"
    return "duckdb:///:memory:"


def get_sa_engine(ds_type: str, config: Dict[str, Any]):
    """Return a SQLAlchemy engine for any connection-string, Athena, or DuckDB datastore."""
    if ds_type == "athena":
        conn_str = get_athena_connection_string(config)
    elif ds_type == "duckdb":
        conn_str = get_duckdb_connection_string(config)
    else:
        conn_str = config.get("connection_string")
    if not conn_str:
        raise HTTPException(status_code=400, detail=f"Connection string missing for {ds_type}")
    return sa.create_engine(conn_str)


# ---------------------------------------------------------------------------
# Python node parsing
# ---------------------------------------------------------------------------

def parse_python_nodes(python_code: str) -> List[Dict[str, Any]]:
    """Parse @node comments from Python code. Supports both @datastore and @connector (legacy)."""
    lines = python_code.split('\n')
    nodes = []
    current_node = None

    for i, line in enumerate(lines):
        if line.strip().startswith('# @node:'):
            if current_node:
                nodes.append(current_node)
            current_node = {
                'name': line.split('# @node:')[1].strip(),
                'type': None,
                'datastore_id': None,
                'query': None,
                'start_line': i
            }
        elif current_node:
            if line.strip().startswith('# @type:'):
                current_node['type'] = line.split('# @type:')[1].strip()
            elif line.strip().startswith('# @datastore:'):
                current_node['datastore_id'] = line.split('# @datastore:')[1].strip()
            elif line.strip().startswith('# @connector:'):
                if not current_node['datastore_id']:
                    current_node['datastore_id'] = line.split('# @connector:')[1].strip()
            elif line.strip().startswith('# @query:'):
                query_line = line.split('# @query:')[1].strip()
                current_node['query'] = query_line
            elif not line.strip().startswith('#') and current_node.get('query'):
                pass

    if current_node:
        nodes.append(current_node)

    return nodes


# ---------------------------------------------------------------------------
# Query execution
# ---------------------------------------------------------------------------

async def run_query_logic(datastore_id: str, query_template: str, context: Dict[str, Any]):
    """Internal helper to execute a templated query on a specific datastore."""
    try:
        pool = get_pool()
        row = await pool.fetchrow("SELECT * FROM datastores WHERE id = $1", datastore_id)
        if not row:
            raise HTTPException(status_code=404, detail="Datastore not found")
        datastore = dict(row)
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Datastore fetch error: {str(e)}")

    try:
        template = Template(query_template)
        rendered_sql = template.render(**context)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Template error: {str(e)}")

    try:
        ds_type = datastore["type"]
        ds_config = ensure_dict(datastore["config"])

        if ds_type == "bigquery":
            client = await get_bigquery_client(ds_config)
            query_job = client.query(rendered_sql)
            results = query_job.result()
            return [dict(row.items()) for row in results]

        elif ds_type in SA_TYPES:
            engine = get_sa_engine(ds_type, ds_config)
            df = pd.read_sql(rendered_sql, engine)
            return df.to_dict(orient="records")

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported datastore type: {ds_type}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        error_msg = str(e)
        if "Table" in error_msg or "dataset" in error_msg or "syntax" in error_msg.lower():
            raise HTTPException(status_code=400, detail=f"Query error: {error_msg}")
        raise HTTPException(status_code=500, detail=f"Execution error: {error_msg}")


async def test_query(python_code: str, limit_rows: int = 5, test_args: Dict[str, Any] = None) -> Dict[str, Any]:
    """Test execute a query and return first few rows or error."""
    try:
        nodes = parse_python_nodes(python_code)

        test_args = test_args or {}
        full_context = {"pd": pd, "json": json, "args": test_args, **test_args}

        for node in nodes:
            if node["type"] == "query":
                ds_id = node.get("datastore_id")
                if not ds_id:
                    return {"error": f"Node '{node['name']}' missing @datastore (use # @datastore: <uuid>)"}

                pool = get_pool()
                ds_row = await pool.fetchrow("SELECT * FROM datastores WHERE id = $1", ds_id)
                if not ds_row:
                    return {"error": f"Datastore {ds_id} not found"}

                datastore = dict(ds_row)
                query_template = node.get("query", "")

                template = Template(query_template)
                rendered_query = template.render(**test_args)

                ds_config = ensure_dict(datastore["config"])
                ds_type = datastore["type"]
                if ds_type == "bigquery":
                    client = await get_bigquery_client(ds_config)
                    query_job = client.query(rendered_query)
                    df = query_job.to_dataframe()
                elif ds_type in SA_TYPES:
                    engine = get_sa_engine(ds_type, ds_config)
                    df = pd.read_sql(rendered_query, engine)
                else:
                    return {"error": f"Unsupported datastore type: {ds_type}"}

                full_context['query_result'] = df
                full_context[node['name']] = df

        exec(python_code, {}, full_context)
        result_df = full_context.get('result')

        if result_df is None:
            return {"error": "Code did not produce a 'result' variable"}

        if isinstance(result_df, pd.DataFrame):
            sample_data = clean_dataframe_for_json(result_df.head(limit_rows))
            row_count = len(result_df)
            columns = list(result_df.columns)
        elif isinstance(result_df, list):
            sample_data = result_df[:limit_rows]
            row_count = len(result_df)
            columns = list(result_df[0].keys()) if result_df else []
        else:
            sample_data = [{"result": str(result_df)}]
            row_count = 1
            columns = ["result"]

        return {
            "success": True,
            "row_count": row_count,
            "sample_rows": sample_data,
            "columns": columns,
            "message": f"Query executed successfully. Returned {row_count} rows."
        }

    except Exception as e:
        error_str = str(e)
        error_msg = f"Query execution failed: {error_str}"

        if "Invalid NUMERIC value" in error_str or "Invalid FLOAT" in error_str or "Invalid INT" in error_str:
            error_msg += "\n\n💡 DATA QUALITY ISSUE: This column contains mixed data types (e.g., dates or text where numbers are expected). Solution: Use SAFE_CAST instead of CAST. Example: SAFE_CAST(column AS FLOAT64) returns NULL for invalid values instead of failing. You can also filter: WHERE SAFE_CAST(column AS FLOAT64) IS NOT NULL"

        return {
            "success": False,
            "error": error_str,
            "message": error_msg
        }

async def execute_query_direct(datastore_id: str, sql_query: str, limit: int = 100) -> Dict[str, Any]:
    """Execute a SQL query directly on a datastore and return results."""
    try:
        limit = min(limit, 1000)

        pool = get_pool()
        ds_row = await pool.fetchrow("SELECT * FROM datastores WHERE id = $1", datastore_id)
        if not ds_row:
            return {"success": False, "error": f"Datastore {datastore_id} not found"}
        datastore = dict(ds_row)

        ds_config = ensure_dict(datastore["config"])
        ds_type = datastore["type"]
        if ds_type == "bigquery":
            client = await get_bigquery_client(ds_config)
            query_job = client.query(sql_query)
            df = query_job.to_dataframe()
        elif ds_type in SA_TYPES:
            engine = get_sa_engine(ds_type, ds_config)
            df = pd.read_sql(sql_query, engine)
        else:
            return {
                "success": False,
                "error": f"Unsupported datastore type: {ds_type}"
            }

        total_rows = len(df)
        limited_df = df.head(limit)

        sample_data = clean_dataframe_for_json(limited_df)
        columns = list(df.columns)

        return {
            "success": True,
            "datastore_name": datastore.get("name", "Unknown"),
            "datastore_type": datastore["type"],
            "total_rows": total_rows,
            "returned_rows": len(sample_data),
            "columns": columns,
            "data": sample_data,
            "message": f"Query executed successfully. Returned {len(sample_data)} of {total_rows} rows.",
            "truncated": total_rows > limit
        }

    except Exception as e:
        error_str = str(e)
        error_msg = f"Query execution failed: {error_str}"

        if "Invalid NUMERIC value" in error_str or "Invalid FLOAT" in error_str or "Invalid INT" in error_str:
            error_msg += "\n\n💡 DATA QUALITY ISSUE: This column contains mixed data types. Try using SAFE_CAST instead of CAST, or use execute_query_direct to inspect the column values: SELECT DISTINCT problematic_column LIMIT 20"

        return {
            "success": False,
            "error": error_str,
            "message": error_msg
        }


# ---------------------------------------------------------------------------
# Schema introspection
# ---------------------------------------------------------------------------

async def get_datastore_schema(datastore_id: str, dataset: Optional[str] = None, table: Optional[str] = None) -> Dict[str, Any]:
    """Get schema information for a datastore."""
    try:
        pool = get_pool()
        row = await pool.fetchrow("SELECT * FROM datastores WHERE id = $1", datastore_id)
        if not row:
            return {"error": "Datastore not found"}
        datastore = dict(row)

        ds_type = datastore["type"]
        if ds_type == "bigquery":
            schema_result = await get_bigquery_schema(datastore, dataset, table)
        elif ds_type in SA_TYPES:
            schema_result = await get_sql_schema(datastore, dataset, table)
        else:
            return {"error": f"Unsupported datastore type: {ds_type}"}

        return {
            "success": True,
            "datastore_id": datastore_id,
            "datastore_name": datastore.get("name"),
            "type": ds_type,
            "schema": schema_result
        }
    except Exception as e:
        return {"error": str(e)}


async def get_bigquery_schema(datastore: Dict[str, Any], dataset: Optional[str], table: Optional[str]):
    """Get BigQuery schema information with enriched details."""
    client = await get_bigquery_client(ensure_dict(datastore["config"]))

    if table and dataset:
        table_ref = client.dataset(dataset).table(table)
        table_obj = client.get_table(table_ref)

        columns = []
        for field in table_obj.schema:
            columns.append({
                "name": field.name,
                "type": field.field_type,
                "mode": field.mode,
                "description": field.description or ""
            })

        return {
            "type": "table_schema",
            "dataset": dataset,
            "table": table,
            "columns": columns,
            "row_count": table_obj.num_rows
        }

    elif dataset:
        tables_list = list(client.list_tables(dataset))
        tables = []
        for t in tables_list:
            table_info = {"name": t.table_id, "type": t.table_type}
            try:
                table_obj = client.get_table(t)
                table_info["column_count"] = len(table_obj.schema)
                table_info["row_count"] = table_obj.num_rows
            except:
                pass
            tables.append(table_info)
        return {
            "type": "tables",
            "dataset": dataset,
            "tables": tables
        }

    else:
        datasets_list = list(client.list_datasets())
        datasets = []
        for d in datasets_list:
            ds_info = {"name": d.dataset_id, "project": d.project}
            try:
                tables_list = list(client.list_tables(d.dataset_id, max_results=50))
                ds_info["tables"] = [{"name": t.table_id, "type": t.table_type} for t in tables_list]
                ds_info["table_count"] = len(ds_info["tables"])
            except:
                ds_info["tables"] = []
                ds_info["table_count"] = 0
            datasets.append(ds_info)
        return {
            "type": "datasets",
            "datasets": datasets
        }


async def get_sql_schema(datastore: Dict[str, Any], database: Optional[str], table: Optional[str]):
    """Get schema information for any SQLAlchemy-backed datastore (Postgres, MySQL, MSSQL, Athena)."""
    ds_type = datastore["type"]
    ds_config = ensure_dict(datastore["config"])
    engine = get_sa_engine(ds_type, ds_config)
    inspector = sa_inspect(engine)

    default_schema = "public" if ds_type == "postgres" else None

    if table:
        schema = database or default_schema
        try:
            columns = inspector.get_columns(table, schema=schema)
        except Exception:
            columns = inspector.get_columns(table)

        column_info = []
        for col in columns:
            column_info.append({
                "name": col["name"],
                "type": str(col["type"]),
                "nullable": col.get("nullable", True),
                "default": str(col.get("default", "")) if col.get("default") else None
            })

        return {
            "type": "table_schema",
            "schema": schema,
            "table": table,
            "columns": column_info
        }

    elif database:
        try:
            table_names = inspector.get_table_names(schema=database)
        except Exception:
            table_names = inspector.get_table_names()
        tables = []
        for t in table_names:
            table_info = {"name": t, "type": "table"}
            try:
                cols = inspector.get_columns(t, schema=database)
                table_info["column_count"] = len(cols)
            except Exception:
                pass
            tables.append(table_info)
        return {
            "type": "tables",
            "schema": database,
            "tables": tables
        }

    else:
        try:
            schema_names = inspector.get_schema_names()
        except Exception:
            schema_names = [default_schema or "default"]
        schemas = []
        for s in schema_names:
            schema_info = {"name": s}
            try:
                table_names = inspector.get_table_names(schema=s)
                schema_info["tables"] = [{"name": t, "type": "table"} for t in table_names[:50]]
                schema_info["table_count"] = len(table_names)
            except Exception:
                schema_info["tables"] = []
                schema_info["table_count"] = 0
            schemas.append(schema_info)
        return {
            "type": "schemas",
            "schemas": schemas
        }
