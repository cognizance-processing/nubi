from typing import Dict, Any, Optional

import pandas as pd
from jinja2 import Template
from fastapi import APIRouter, HTTPException, Body

from ..db import get_pool
from ..helpers import ensure_dict
from ..query_engine import (
    get_bigquery_client, get_sa_engine, execute_python_query, SA_TYPES,
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
        pool = get_pool()
        query_row = await pool.fetchrow("SELECT * FROM board_queries WHERE id = $1", query_id)
        if not query_row:
            return {"result": None, "error": "Query not found"}

        query = dict(query_row)
        python_code = query.get("python_code", "")

        exec_result = await execute_python_query(
            python_code, args=args, datastore_id=datastore_id, limit_rows=0
        )

        if not exec_result.get("success"):
            return {"result": None, "error": exec_result.get("error", "Unknown error")}

        return {
            "result": exec_result.get("sample_rows", []),
            "table": exec_result.get("sample_rows", []),
            "count": exec_result.get("row_count", 0),
            "columns": exec_result.get("columns", []),
            "error": None,
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"result": None, "error": f"Query execution failed: {str(e)}"}


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
