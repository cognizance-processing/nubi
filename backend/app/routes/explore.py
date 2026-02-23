import asyncio
import json
from typing import Dict, Any, Optional

import pandas as pd
from jinja2 import Template
from fastapi import APIRouter, HTTPException, Body

from ..db import get_pool
from ..helpers import ensure_dict
from ..query_engine import (
    get_bigquery_client, get_sa_engine, parse_python_nodes, run_query_logic,
    SA_TYPES,
)

router = APIRouter(tags=["explore"])


@router.post("/explore")
async def explore(
    query_id: str = Body(...),
    args: Dict[str, Any] = Body(default={}),
    datastore_id: Optional[str] = Body(default=None)
):
    """
    Executes a Python query with embedded queries.
    Datastore ID is optional - will auto-select first available datastore if not provided or invalid.
    Returns: {"result": [...], "error": null} or {"result": null, "error": "error message"}
    """
    try:
        print(f"DEBUG: Starting query execution for query_id={query_id}")

        pool = get_pool()
        query_row = await pool.fetchrow("SELECT * FROM board_queries WHERE id = $1", query_id)
        if not query_row:
            raise HTTPException(status_code=404, detail="Query not found")

        query = dict(query_row)
        python_code = query.get("python_code", "")
        print(f"DEBUG: Found query {query.get('name')}")

        nodes = parse_python_nodes(python_code)

        print(f"DEBUG: Parsed {len(nodes)} nodes")

        full_context = {
            "args": args,
            "pd": pd,
            "json": json,
            **args
        }

        def is_valid_uuid(val):
            if not val or not isinstance(val, str):
                return False
            try:
                import uuid
                uuid.UUID(val)
                return True
            except (ValueError, AttributeError):
                return False

        async def execute_node(node):
            """Execute a single query node and return (node_name, dataframe)"""
            if node['type'] != 'query' or not node['query']:
                return None

            node_ds = node['datastore_id'] if is_valid_uuid(node['datastore_id']) else None
            request_ds = datastore_id if is_valid_uuid(datastore_id) else None
            active_datastore_id = node_ds or request_ds

            if not active_datastore_id:
                ds_row = await pool.fetchrow("SELECT id FROM datastores LIMIT 1")
                if ds_row:
                    active_datastore_id = str(ds_row['id'])
                    print(f"DEBUG: Auto-selected first available datastore: {active_datastore_id}")

            if not active_datastore_id:
                error_msg = f"No datastore available for query node '{node['name']}'. Please connect a datastore first or provide a valid datastore_id."
                print(f"DEBUG: {error_msg}")
                raise HTTPException(status_code=400, detail=error_msg)

            print(f"DEBUG: Executing query for node {node['name']} with datastore {active_datastore_id}")
            try:
                result_data = await run_query_logic(active_datastore_id, node['query'], full_context)
                df = pd.DataFrame(result_data)
                return (node['name'], df)
            except Exception as e:
                print(f"DEBUG: Query error for node {node['name']}: {str(e)}")
                raise HTTPException(status_code=400, detail=f"Query error in node {node['name']}: {str(e)}")

        query_tasks = [execute_node(node) for node in nodes]
        query_results = await asyncio.gather(*query_tasks)

        for result in query_results:
            if result:
                node_name, df = result
                full_context['query_result'] = df
                full_context[node_name] = df

        try:
            exec(python_code, {}, full_context)
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=400, detail=f"Python execution error: {str(e)}")

        result = full_context.get('result')
        if result is None:
            raise HTTPException(status_code=400, detail="No 'result' variable found in Python code")

        if isinstance(result, pd.DataFrame):
            final_table = result.to_dict(orient='records')
        elif isinstance(result, list):
            final_table = result
        else:
            final_table = [{"result": str(result)}]

        return {
            "result": final_table,
            "error": None
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Query execution failed: {str(e)}")


@router.post("/test-datastore")
async def test_datastore(
    datastore_id: Optional[str] = Body(default=None),
    config: Optional[Dict[str, Any]] = Body(default=None),
    type: Optional[str] = Body(default=None)
):
    """Tests a datastore connection using either an existing ID or a raw config."""
    try:
        if datastore_id:
            pool = get_pool()
            row = await pool.fetchrow("SELECT * FROM datastores WHERE id = $1", datastore_id)
            if not row:
                raise HTTPException(status_code=404, detail="Datastore not found")
            datastore = dict(row)
        else:
            if not config or not type:
                raise HTTPException(status_code=400, detail="Missing configuration or type")
            datastore = {"type": type, "config": config}

        ds_config = ensure_dict(datastore["config"])
        ds_type = datastore["type"]

        if ds_type == "bigquery":
            client = await get_bigquery_client(ds_config)
            client.list_datasets(max_results=1)
            return {"status": "success", "message": "Connection successful"}

        elif ds_type in SA_TYPES:
            import sqlalchemy as sa
            engine = get_sa_engine(ds_type, ds_config)
            with engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
            return {"status": "success", "message": "Connection successful"}

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported datastore type: {ds_type}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Test failed: {str(e)}")


@router.post("/query")
async def query(
    datastore_id: str = Body(...),
    sql: str = Body(...),
    args: Dict[str, Any] = Body(default={})
):
    """
    Execute a direct SQL query against a datastore.
    Supports templating with Jinja2.
    """
    try:
        print(f"DEBUG: Executing query on datastore_id={datastore_id}")

        pool = get_pool()
        store_row = await pool.fetchrow("SELECT * FROM datastores WHERE id = $1", datastore_id)
        if not store_row:
            raise HTTPException(status_code=404, detail="Datastore not found")
        datastore = dict(store_row)

        try:
            template = Template(sql)
            rendered_sql = template.render(**args)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Template error: {str(e)}")

        print(f"DEBUG: Rendered SQL: {rendered_sql[:200]}...")

        ds_config = ensure_dict(datastore["config"])
        ds_type = datastore["type"]

        if ds_type == "bigquery":
            client = await get_bigquery_client(ds_config)
            query_job = client.query(rendered_sql)
            results = query_job.result()
            df = pd.DataFrame([dict(row.items()) for row in results])

        elif ds_type in SA_TYPES:
            engine = get_sa_engine(ds_type, ds_config)
            df = pd.read_sql(rendered_sql, engine)

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported datastore type: {ds_type}")

        return {
            "status": "success",
            "table": df.to_dict(orient='records'),
            "count": len(df),
            "columns": list(df.columns)
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")
