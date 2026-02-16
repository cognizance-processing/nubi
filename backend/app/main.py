import os
import tempfile
import json
import re
import time
import asyncio
from typing import Dict, Any, Optional, List, AsyncGenerator
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from supabase import create_client, Client
from jinja2 import Template
from google.cloud import bigquery
from google.oauth2 import service_account
import pandas as pd
from dotenv import load_dotenv
import requests
from html.parser import HTMLParser

load_dotenv()

app = FastAPI(title="Nubi Exploration Engine")

# Enable CORS for vanilla JS/HTML access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Supabase Client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    SUPABASE_URL = os.getenv("VITE_SUPABASE_URL")
    SUPABASE_SERVICE_ROLE_KEY = os.getenv("VITE_SUPABASE_ANON_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# Gemini API Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

BOARD_SYSTEM_INSTRUCTION = """You are a BI dashboard editor. You MUST output exactly ONE file: a single HTML document for an interactive dashboard.

RULES:
- Output ONLY the complete HTML file. No markdown code fences, no explanation, no "here is the code".
- Use Alpine.js (x-data, x-init, x-text, x-ref, etc.). Include Alpine from CDN: https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js
- For charts use Chart.js (CDN). For draggable/resizable widgets use Interact.js (CDN).
- Create business intelligence visualizations: KPI cards, charts (bar, line, pie), tables, filters
- Preserve or adapt these patterns from the dashboard builder:
  - body with x-data="boardManager()" and @resize.window="detectViewport()"
  - .board-canvas as the container; inside it, .widget elements that are position:absolute and use data-lg-x, data-lg-y, data-lg-w, data-lg-h (and data-md-*, data-sm-*) for layout
  - Each .widget must have x-data="canvasWidget()" and x-init="initWidget($el)" for drag/resize
  - boardManager() with viewport, init(), detectViewport(), applyLayout(); canvasWidget() with initWidget(el) using interact(el).draggable() and .resizable()
  - KPI cards: .kpi-card with x-data for value, label, trend; .kpi-label, .kpi-value, .kpi-trend.positive/.negative
  - Charts: .chart-container with x-data for chart component (e.g. barChart()), <canvas x-ref="chart">, and init() that calls new Chart(this.$refs.chart, { type, data, options })
- Keep the same dark theme (background #0a0e1a, --primary #6366f1), .widget styling, and existing <style> patterns.
- Focus the edit strictly on what the user asked for; change only what is needed and keep the rest of the dashboard structure intact.

TOOLS AVAILABLE:
You have access to these tools to help you build better dashboards:
1. list_datastores() - Get available database connections
2. list_boards() - Get available boards
3. list_board_queries(board_id) - Get all queries for a specific board
4. get_query_code(query_id) - Get the Python code for a specific query to understand what data it produces

Use these tools when you need to reference existing queries or understand the data structure available to the dashboard."""

EXPLORATION_SYSTEM_INSTRUCTION = """You are a data query assistant. You help write and edit Python code for data transformations.

CRITICAL OUTPUT RULE:
- You MUST ALWAYS output ONLY valid, executable Python code
- NEVER output conversational text, explanations, or questions
- If you need information (like a datastore ID), use the available function calls
- If function calls are not available and you lack information, output placeholder code with a comment

CONTEXT:
- You're working with Python code that queries BigQuery or PostgreSQL databases
- Code uses pandas DataFrames for data manipulation
- Queries are embedded using special @node comments
- Queries belong to boards and can be referenced by chats

CODE STRUCTURE:
- Use comment-based metadata for queries:
  # @node: query_name
  # @type: query
  # @connector: <datastore_uuid>
  # @query: SELECT * FROM table_name WHERE condition = {{arg_name}}

- Query results are available as pandas DataFrames via 'query_result' variable
- Support common DataFrame operations: pivot, groupby, merge, filter, etc.
- The final result must be assigned to a variable named 'result'

TOOLS AVAILABLE:
You have access to these tools to help you write better code:
1. list_datastores() - Get available datastores. Use this when you need a valid datastore ID or don't know which datastores exist.
2. list_boards() - Get available boards
3. list_board_queries(board_id) - Get all queries for a specific board. Use this to see what other queries exist on the same board.
4. get_query_code(query_id) - Get the Python code for a specific query. Use this to understand how another query works or to reuse logic.

IMPORTANT: If you need a datastore ID and don't have one, call list_datastores() first using function calling. Once you get the response, generate the code with a valid datastore ID.

EXAMPLE:
```python
# @node: source_data
# @type: query
# @connector: abc-123
# @query: SELECT user_id, revenue FROM sales WHERE date >= {{start_date}}

# Result is injected as 'query_result' DataFrame
df = query_result

# @node: aggregate
# @type: transform
df_summary = df.groupby('user_id').agg({'revenue': 'sum'}).reset_index()

# @node: output
# @type: output
result = df_summary
```

RULES:
- Output ONLY Python code. No markdown, no explanations outside comments.
- Keep existing @node structure unless explicitly asked to change it
- Use descriptive variable names
- Add comments explaining complex transformations
- Always ensure 'result' variable exists at the end
- Use pandas best practices (avoid loops, use vectorized operations)
- If you need a datastore ID, call list_datastores() first to get valid options
- If referencing another query on the same board, you can use list_board_queries() and get_query_code() to see its implementation"""

def get_bigquery_client(config: Dict[str, Any]):
    """Creates a BigQuery client from config."""
    project_id = config.get("project_id")
    keyfile_path_in_storage = config.get("keyfile_path")
    
    if keyfile_path_in_storage:
        try:
            res = supabase.storage.from_("secret-files").download(keyfile_path_in_storage)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
                tmp.write(res)
                tmp_path = tmp.name
            
            credentials = service_account.Credentials.from_service_account_file(tmp_path)
            client = bigquery.Client(credentials=credentials, project=project_id)
            return client
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to load keyfile: {str(e)}")
    
    return bigquery.Client(project=project_id)

async def _run_query_logic(datastore_id: str, query_template: str, context: Dict[str, Any]):
    """Internal helper to execute a templated query on a specific datastore."""
    try:
        store_res = supabase.table("datastores").select("*").eq("id", datastore_id).single().execute()
        if not store_res.data:
            raise HTTPException(status_code=404, detail="Datastore not found")
        datastore = store_res.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Datastore fetch error: {str(e)}")

    try:
        template = Template(query_template)
        rendered_sql = template.render(**context)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Template error: {str(e)}")

    try:
        if datastore["type"] == "bigquery":
            client = get_bigquery_client(datastore["config"])
            query_job = client.query(rendered_sql)
            results = query_job.result()
            return [dict(row.items()) for row in results]
            
        elif datastore["type"] == "postgres":
            conn_str = datastore["config"].get("connection_string")
            if not conn_str:
                raise HTTPException(status_code=400, detail="Postgres connection string missing")
            df = pd.read_sql(rendered_sql, conn_str)
            return df.to_dict(orient="records")
            
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported datastore type: {datastore['type']}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        error_msg = str(e)
        if "Table" in error_msg or "dataset" in error_msg or "syntax" in error_msg.lower():
            raise HTTPException(status_code=400, detail=f"Query error: {error_msg}")
        raise HTTPException(status_code=500, detail=f"Execution error: {error_msg}")

@app.post("/explore")
async def explore(
    query_id: str = Body(...),  # Changed from exploration_id to query_id
    args: Dict[str, Any] = Body(default={}),
    datastore_id: Optional[str] = Body(default=None)
):
    """
    Executes a Python query with embedded queries.
    Now queries are board_queries, not exploitations.
    """
    try:
        print(f"DEBUG: Starting query execution for query_id={query_id}")
        
        # 1. Fetch the board_query
        query_res = supabase.table("board_queries").select("*").eq("id", query_id).single().execute()
        if not query_res.data:
            raise HTTPException(status_code=404, detail="Query not found")
        
        query = query_res.data
        python_code = query.get("python_code", "")
        print(f"DEBUG: Found query {query.get('name')}")
        
        # 2. Parse @node comments to find queries
        import re
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
                    'connector': None,
                    'query': None,
                    'start_line': i
                }
            elif current_node:
                if line.strip().startswith('# @type:'):
                    current_node['type'] = line.split('# @type:')[1].strip()
                elif line.strip().startswith('# @connector:'):
                    current_node['connector'] = line.split('# @connector:')[1].strip()
                elif line.strip().startswith('# @query:'):
                    # Multi-line query support
                    query_line = line.split('# @query:')[1].strip()
                    current_node['query'] = query_line
                elif not line.strip().startswith('#') and current_node.get('query'):
                    # End of node metadata
                    pass
        
        if current_node:
            nodes.append(current_node)
        
        print(f"DEBUG: Parsed {len(nodes)} nodes")
        
        # 3. Initialize execution context
        full_context = {
            "args": args,
            "pd": pd,
            "json": json,
            **args
        }
        
        # 4. Execute queries for each query node
        for node in nodes:
            if node['type'] == 'query' and node['query']:
                # Resolve datastore: node connector > request override > first available
                active_datastore_id = node['connector'] or datastore_id
                
                if not active_datastore_id:
                    # Try to get first available datastore
                    ds_res = supabase.table("datastores").select("id").limit(1).execute()
                    if ds_res.data:
                        active_datastore_id = ds_res.data[0]['id']
                
                if active_datastore_id:
                    print(f"DEBUG: Executing query for node {node['name']}")
                    try:
                        result_data = await _run_query_logic(active_datastore_id, node['query'], full_context)
                        # Convert to DataFrame for the execution context
                        df = pd.DataFrame(result_data)
                        full_context['query_result'] = df
                        full_context[node['name']] = df
                    except Exception as e:
                        print(f"DEBUG: Query error for node {node['name']}: {str(e)}")
                        raise HTTPException(status_code=400, detail=f"Query error in node {node['name']}: {str(e)}")
                else:
                    print(f"DEBUG: No datastore for query node {node['name']}, skipping")
        
        # 5. Execute the full Python code
        try:
            exec(python_code, {}, full_context)
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=400, detail=f"Python execution error: {str(e)}")
        
        # 6. Extract and return result
        result = full_context.get('result')
        if result is None:
            raise HTTPException(status_code=400, detail="No 'result' variable found in Python code")
        
        # Convert DataFrame to records if needed
        if isinstance(result, pd.DataFrame):
            final_table = result.to_dict(orient='records')
        elif isinstance(result, list):
            final_table = result
        else:
            final_table = [{"result": str(result)}]
        
        return {
            "status": "success",
            "table": final_table,
            "count": len(final_table)
        }
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Query execution failed: {str(e)}")

@app.post("/test-datastore")
async def test_datastore(
    datastore_id: Optional[str] = Body(default=None),
    config: Optional[Dict[str, Any]] = Body(default=None),
    type: Optional[str] = Body(default=None)
):
    """Tests a datastore connection using either an existing ID or a raw config."""
    try:
        if datastore_id:
            res = supabase.table("datastores").select("*").eq("id", datastore_id).single().execute()
            if not res.data:
                raise HTTPException(status_code=404, detail="Datastore not found")
            datastore = res.data
        else:
            if not config or not type:
                raise HTTPException(status_code=400, detail="Missing configuration or type")
            datastore = {"type": type, "config": config}

        if datastore["type"] == "bigquery":
            client = get_bigquery_client(datastore["config"])
            # Simple metadata check to verify connection
            client.list_datasets(max_results=1)
            return {"status": "success", "message": "Connection successful"}
            
        elif datastore["type"] == "postgres":
            conn_str = datastore["config"].get("connection_string")
            import sqlalchemy as sa
            engine = sa.create_engine(conn_str)
            with engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
            return {"status": "success", "message": "Connection successful"}
            
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported datastore type: {datastore['type']}")
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Test failed: {str(e)}")

@app.post("/query")
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
        
        # Fetch datastore
        store_res = supabase.table("datastores").select("*").eq("id", datastore_id).single().execute()
        if not store_res.data:
            raise HTTPException(status_code=404, detail="Datastore not found")
        datastore = store_res.data
        
        # Template the query
        try:
            template = Template(sql)
            rendered_sql = template.render(**args)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Template error: {str(e)}")
        
        print(f"DEBUG: Rendered SQL: {rendered_sql[:200]}...")
        
        # Execute query
        if datastore["type"] == "bigquery":
            client = get_bigquery_client(datastore["config"])
            query_job = client.query(rendered_sql)
            results = query_job.result()
            df = pd.DataFrame([dict(row.items()) for row in results])
            
        elif datastore["type"] == "postgres":
            conn_str = datastore["config"].get("connection_string")
            if not conn_str:
                raise HTTPException(status_code=400, detail="Postgres connection string missing")
            df = pd.read_sql(rendered_sql, conn_str)
            
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported datastore type: {datastore['type']}")
        
        # Return results
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

def strip_markdown_code_block(raw: str) -> str:
    """Remove markdown code fences if present."""
    trimmed = raw.strip()
    fence = "```html" if trimmed.startswith("```html") else "```" if trimmed.startswith("```") else None
    if fence:
        end_idx = trimmed.find("```", len(fence))
        if end_idx != -1:
            return trimmed[trimmed.find("\n", len(fence)) + 1:end_idx].strip()
        return trimmed[trimmed.find("\n", len(fence)) + 1:].strip()
    return trimmed

@app.post("/board-helper")
async def board_helper(
    code: Optional[str] = Body(default=""),
    user_prompt: str = Body(...),
    chat: List[Dict[str, str]] = Body(default=[]),
    gemini_api_key: Optional[str] = Body(default=None),
    context: str = Body(default="board"),  # "board" or "query"
    datastore_id: Optional[str] = Body(default=None),  # For query schema access
    query_id: Optional[str] = Body(default=None)  # For query testing
):
    """
    Edits board HTML or query Python code using Gemini AI.
    Context parameter determines the system instruction and output format.
    For queries: iteratively generates, tests, and fixes code with schema awareness.
    """
    try:
        api_key = gemini_api_key or GEMINI_API_KEY
        if not api_key:
            raise HTTPException(status_code=400, detail="Missing GEMINI_API_KEY")
        
        # Select system instruction based on context
        if context == "query":
            system_instruction = EXPLORATION_SYSTEM_INSTRUCTION
            code_type = "Python code"
            
            # For queries, use iterative testing
            return await _exploration_helper_with_testing(
                code, user_prompt, chat, api_key, datastore_id, query_id
            )
        else:
            system_instruction = BOARD_SYSTEM_INSTRUCTION
            code_type = "board HTML"
        
        # Standard board helper flow
        if code:
            user_message = f"User request: {user_prompt}\n\nCurrent {code_type} (edit this to fulfill the request):\n\n{code}"
        else:
            user_message = f"User request: {user_prompt}\n\nGenerate a new board HTML from scratch that fulfills this request, using the board builder patterns (Alpine.js, boardManager, canvasWidget, KPI/chart widgets)."
        
        # Build conversation history
        chat_history = chat[-20:] if chat else []
        contents = []
        for msg in chat_history:
            if not msg.get('content'):
                continue
            role = "model" if msg.get('role') == 'assistant' else "user"
            contents.append({"role": role, "parts": [{"text": msg['content']}]})
        contents.append({"role": "user", "parts": [{"text": user_message}]})
        
        # Call Gemini
        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 8192,
                "responseMimeType": "text/plain"
            }
        }
        
        response = requests.post(
            GEMINI_URL,
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            json=payload,
            timeout=60
        )
        
        if not response.ok:
            raise HTTPException(status_code=502, detail=f"Gemini API error: {response.text}")
        
        data = response.json()
        raw_text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        
        if not raw_text:
            raise HTTPException(status_code=502, detail="Gemini returned no content")
        
        edited_code = strip_markdown_code_block(raw_text.strip())
        
        return {
            "code": edited_code,
            "message": f"I've updated the {code_type} based on your request."
        }
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"AI helper failed: {str(e)}")


# Tool/Function call helpers
class SimpleHTMLValidator(HTMLParser):
    """Simple HTML validator using Python's built-in HTMLParser."""
    def __init__(self):
        super().__init__()
        self.errors = []
        self.warnings = []
        self.info = []
        self.tags_found = set()
        self.open_tags = []
        self.has_html = False
        self.has_head = False
        self.has_body = False
        
    def handle_starttag(self, tag, attrs):
        self.tags_found.add(tag)
        self.open_tags.append(tag)
        
        if tag == 'html':
            self.has_html = True
        elif tag == 'head':
            self.has_head = True
        elif tag == 'body':
            self.has_body = True
        
        # Check for common widget patterns
        for attr, value in attrs:
            if attr == 'class' and 'widget' in value:
                self.info.append("Found widget element")
                break
    
    def handle_endtag(self, tag):
        if self.open_tags and self.open_tags[-1] == tag:
            self.open_tags.pop()
        elif tag in self.open_tags:
            self.open_tags.remove(tag)
    
    def error(self, message):
        self.errors.append(message)

def _validate_html(html_code: str) -> Dict[str, Any]:
    """
    Validate HTML using Python's built-in HTMLParser.
    Returns validation results with warnings/errors.
    """
    try:
        parser = SimpleHTMLValidator()
        parser.feed(html_code)
        
        # Check basic structure
        if not parser.has_html:
            parser.warnings.append("Missing <html> tag")
        if not parser.has_head:
            parser.warnings.append("Missing <head> tag")
        if not parser.has_body:
            parser.warnings.append("Missing <body> tag")
        
        # Check for required CDN libraries
        html_lower = html_code.lower()
        
        # Check Alpine.js
        if 'x-data' in html_lower or 'x-init' in html_lower:
            if 'alpinejs' not in html_lower:
                parser.warnings.append("Alpine.js directives found but CDN not included")
            else:
                parser.info.append("✓ Alpine.js CDN included")
        
        # Check Chart.js
        if 'new chart' in html_lower or 'chart(' in html_lower:
            if 'chart.js' not in html_lower and 'chartjs' not in html_lower:
                parser.warnings.append("Chart.js code found but CDN not included")
            else:
                parser.info.append("✓ Chart.js CDN included")
        
        # Check Interact.js
        if 'interact(' in html_lower:
            if 'interactjs' not in html_lower and 'interact.js' not in html_lower:
                parser.warnings.append("Interact.js code found but CDN not included")
            else:
                parser.info.append("✓ Interact.js CDN included")
        
        # Count widgets
        widget_count = html_code.count('class="widget"') + html_code.count("class='widget'")
        if widget_count > 0:
            parser.info.append(f"Found {widget_count} widget(s)")
        
        # Count scripts and styles
        script_count = html_code.lower().count('<script')
        style_count = html_code.lower().count('<style')
        if script_count > 0:
            parser.info.append(f"Found {script_count} script tag(s)")
        if style_count > 0:
            parser.info.append(f"Found {style_count} style tag(s)")
        
        # Success metrics
        has_errors = len(parser.errors) > 0
        has_warnings = len(parser.warnings) > 0
        
        return {
            "valid": not has_errors,
            "errors": parser.errors,
            "warnings": parser.warnings,
            "info": parser.info,
            "summary": f"{'✓ Valid HTML' if not has_errors else '✗ Invalid HTML'} " + 
                      f"({len(parser.warnings)} warning{'s' if len(parser.warnings) != 1 else ''}, " +
                      f"{len(parser.errors)} error{'s' if len(parser.errors) != 1 else ''})"
        }
        
    except Exception as e:
        return {
            "valid": False,
            "errors": [f"Parse error: {str(e)}"],
            "warnings": [],
            "info": [],
            "summary": "✗ Failed to parse HTML"
        }

async def _get_available_datastores(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get list of available datastores for the user."""
    try:
        query = supabase.table("datastores").select("id, name, type")
        if user_id:
            query = query.eq("user_id", user_id)
        res = query.execute()
        return res.data or []
    except Exception as e:
        return []

async def _get_available_boards(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get list of available boards for the user."""
    try:
        query = supabase.table("boards").select("id, name")
        if user_id:
            query = query.eq("user_id", user_id)
        res = query.limit(20).execute()
        return res.data or []
    except Exception as e:
        return []

async def _get_available_explorations(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get list of available queries for the user."""
    try:
        query = supabase.table("board_queries").select("id, name, description, board_id")
        if user_id:
            # Join with boards to filter by user
            query = query.eq("boards.profile_id", user_id)
        res = query.limit(20).execute()
        return res.data or []
    except Exception as e:
        return []

async def _get_board_queries(board_id: str) -> List[Dict[str, Any]]:
    """Get all queries for a specific board."""
    try:
        res = supabase.table("board_queries").select("id, name, description").eq("board_id", board_id).execute()
        return res.data or []
    except Exception as e:
        return []

async def _get_query_code(query_id: str) -> Dict[str, Any]:
    """Get the Python code for a specific query."""
    try:
        res = supabase.table("board_queries").select("id, name, python_code").eq("id", query_id).single().execute()
        if res.data:
            return {
                "id": res.data["id"],
                "name": res.data["name"],
                "code": res.data["python_code"]
            }
        return {"error": "Query not found"}
    except Exception as e:
        return {"error": str(e)}

# Gemini function/tool definitions
GEMINI_TOOLS = [{
    "function_declarations": [
        {
            "name": "list_datastores",
            "description": "Get a list of available datastores (database connections) that can be used in queries. Returns datastore ID, name, and type (bigquery or postgres). Use this when you need to know which datastores are available or to get a valid datastore ID.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "list_boards",
            "description": "Get a list of available boards. Returns board ID and name. Use this when the user asks about boards or needs to reference a board.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "list_board_queries",
            "description": "Get a list of all queries belonging to a specific board. Returns query ID, name, and description. Use this when you need to know what queries exist for a board or when the user asks about available queries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "board_id": {
                        "type": "string",
                        "description": "The UUID of the board to get queries for"
                    }
                },
                "required": ["board_id"]
            }
        },
        {
            "name": "get_query_code",
            "description": "Get the Python code for a specific query. Returns the query's ID, name, and full Python code. Use this when you need to see or reference the code of an existing query, or when the user asks about a specific query's implementation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query_id": {
                        "type": "string",
                        "description": "The UUID of the query to get code for"
                    }
                },
                "required": ["query_id"]
            }
        }
    ]
}]


@app.post("/board-helper-stream")
async def board_helper_stream(
    code: Optional[str] = Body(default=""),
    user_prompt: str = Body(...),
    chat: List[Dict[str, str]] = Body(default=[]),
    gemini_api_key: Optional[str] = Body(default=None),
    context: str = Body(default="board"),
    datastore_id: Optional[str] = Body(default=None),
    query_id: Optional[str] = Body(default=None),
    # Settings
    max_tool_iterations: int = Body(default=10),
    temperature: float = Body(default=0.3),
    max_output_tokens: int = Body(default=8192)
):
    """
    Streaming version of board-helper that sends progress events in real-time.
    Returns Server-Sent Events (SSE) stream with:
    - thinking: AI reasoning
    - progress: Step-by-step updates
    - code_delta: Code additions/deletions
    - final: Complete code and summary
    """
    async def generate_stream() -> AsyncGenerator[str, None]:
        try:
            api_key = gemini_api_key or GEMINI_API_KEY
            if not api_key:
                yield f"data: {json.dumps({'type': 'error', 'content': 'Missing GEMINI_API_KEY'})}\n\n"
                return
            
            if context == "query":
                async for event in _exploration_helper_stream(
                    code, user_prompt, chat, api_key, datastore_id, query_id,
                    max_tool_iterations, temperature, max_output_tokens
                ):
                    yield f"data: {json.dumps(event)}\n\n"
            else:
                # Board helper streaming with HTML validation
                yield f"data: {json.dumps({'type': 'thinking', 'content': 'Analyzing your request...'})}\n\n"
                await asyncio.sleep(0.1)
                
                # Generate code using existing logic
                system_instruction = BOARD_SYSTEM_INSTRUCTION
                user_message = f"User request: {user_prompt}\n\nCurrent board HTML:\n\n{code}" if code else f"User request: {user_prompt}\n\nGenerate a new board HTML from scratch."
                
                chat_history = chat[-20:] if chat else []
                contents = []
                for msg in chat_history:
                    if not msg.get('content'):
                        continue
                    role = "model" if msg.get('role') == 'assistant' else "user"
                    contents.append({"role": role, "parts": [{"text": msg['content']}]})
                contents.append({"role": "user", "parts": [{"text": user_message}]})
                
                payload = {
                    "contents": contents,
                    "systemInstruction": {"parts": [{"text": system_instruction}]},
                    "tools": GEMINI_TOOLS,  # Enable function calling for boards too
                    "generationConfig": {
                        "temperature": 0.3,
                        "maxOutputTokens": 8192,
                        "responseMimeType": "text/plain"
                    }
                }
                
                yield f"data: {json.dumps({'type': 'progress', 'content': '🤖 Generating HTML...'})}\n\n"
                
                # Function calling loop (similar to exploration)
                max_tool_iterations = 10
                tool_iteration = 0
                edited_code = None
                
                while tool_iteration < max_tool_iterations:
                    tool_iteration += 1
                    
                    response = requests.post(
                        GEMINI_URL,
                        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                        json=payload,
                        timeout=60
                    )
                    
                    if not response.ok:
                        yield f"data: {json.dumps({'type': 'error', 'content': f'Gemini API error: {response.text}'})}\n\n"
                        return
                    
                    data = response.json()
                    candidate = data.get("candidates", [{}])[0]
                    content = candidate.get("content", {})
                    parts = content.get("parts", [])
                    
                    # Check for function calls
                    function_calls = [p for p in parts if "functionCall" in p]
                    
                    if function_calls:
                        # Execute function calls
                        function_responses = []
                        for fc in function_calls:
                            func_name = fc["functionCall"]["name"]
                            
                            yield f"data: {json.dumps({'type': 'progress', 'content': f'🔧 Calling {func_name}()...'})}\n\n"
                            
                            if func_name == "list_datastores":
                                result = await _get_available_datastores()
                                yield f"data: {json.dumps({'type': 'progress', 'content': f'✓ Found {len(result)} datastores'})}\n\n"
                            elif func_name == "list_boards":
                                result = await _get_available_boards()
                                yield f"data: {json.dumps({'type': 'progress', 'content': f'✓ Found {len(result)} boards'})}\n\n"
                            elif func_name == "list_board_queries":
                                board_id = fc["functionCall"].get("args", {}).get("board_id")
                                result = await _get_board_queries(board_id) if board_id else []
                                yield f"data: {json.dumps({'type': 'progress', 'content': f'✓ Found {len(result)} queries'})}\n\n"
                            elif func_name == "get_query_code":
                                query_id = fc["functionCall"].get("args", {}).get("query_id")
                                result = await _get_query_code(query_id) if query_id else {"error": "Missing query_id"}
                                if "error" not in result:
                                    query_name = result.get("name", "query")
                                    yield f"data: {json.dumps({'type': 'progress', 'content': f'✓ Retrieved code for {query_name}'})}\n\n"
                                else:
                                    error_msg = result["error"]
                                    yield f"data: {json.dumps({'type': 'progress', 'content': f'✗ {error_msg}'})}\n\n"
                            else:
                                result = {"error": f"Unknown function: {func_name}"}
                            
                            function_responses.append({
                                "functionResponse": {
                                    "name": func_name,
                                    "response": {"result": result}
                                }
                            })
                        
                        # Add function responses to conversation
                        contents.append({"role": "model", "parts": function_calls})
                        contents.append({"role": "user", "parts": function_responses})
                        payload["contents"] = contents
                        
                        # Continue loop
                        continue
                    
                    # Extract text response
                    raw_text = parts[0].get("text", "") if parts else ""
                    
                    if not raw_text:
                        yield f"data: {json.dumps({'type': 'error', 'content': 'Gemini returned no content'})}\n\n"
                        return
                    
                    edited_code = strip_markdown_code_block(raw_text.strip())
                    break
                
                if not edited_code:
                    yield f"data: {json.dumps({'type': 'error', 'content': 'Failed to generate code'})}\n\n"
                    return
                
                yield f"data: {json.dumps({'type': 'progress', 'content': f'✓ HTML generated ({len(edited_code)} characters)'})}\n\n"
                
                # Validate HTML
                yield f"data: {json.dumps({'type': 'thinking', 'content': 'Let me validate the HTML...'})}\n\n"
                await asyncio.sleep(0.1)
                yield f"data: {json.dumps({'type': 'progress', 'content': '🔍 Validating HTML structure...'})}\n\n"
                
                validation = _validate_html(edited_code)
                
                # Show validation results
                if validation["valid"]:
                    summary_content = f'✓ {validation["summary"]}'
                    yield f"data: {json.dumps({'type': 'progress', 'content': summary_content})}\n\n"
                else:
                    summary_content = f'⚠️ {validation["summary"]}'
                    yield f"data: {json.dumps({'type': 'progress', 'content': summary_content})}\n\n"
                
                # Show errors
                for error in validation.get("errors", []):
                    error_content = f'  ✗ {error}'
                    yield f"data: {json.dumps({'type': 'progress', 'content': error_content})}\n\n"
                
                # Show warnings
                for warning in validation.get("warnings", []):
                    warning_content = f'  ⚠️ {warning}'
                    yield f"data: {json.dumps({'type': 'progress', 'content': warning_content})}\n\n"
                
                # Show info
                for info_item in validation.get("info", []):
                    info_content = f'  {info_item}'
                    yield f"data: {json.dumps({'type': 'progress', 'content': info_content})}\n\n"
                
                # Code delta
                if code:
                    yield f"data: {json.dumps({'type': 'code_delta', 'old_code': code, 'new_code': edited_code})}\n\n"
                
                # Final result
                message = f"✨ **HTML {'validated and ' if validation['valid'] else ''}generated!**"
                if validation.get("warnings"):
                    message += f"\n\n⚠️ Note: {len(validation['warnings'])} warning(s) - review the suggestions above."
                
                yield f"data: {json.dumps({'type': 'final', 'code': edited_code, 'message': message, 'validation': validation})}\n\n"
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
    
    return StreamingResponse(generate_stream(), media_type="text/event-stream")


async def _exploration_helper_stream(
    code: str,
    user_prompt: str,
    chat: List[Dict[str, str]],
    api_key: str,
    datastore_id: Optional[str],
    query_id: Optional[str],
    max_tool_iterations: int = 10,
    temperature: float = 0.3,
    max_output_tokens: int = 8192
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Streaming version of query helper with function calling support.
    Tries once, if test fails attempts one fix, then asks user to continue if still failing.
    Yields events: thinking, progress, code_delta, test_result, final, needs_user_input
    """
    max_attempts = 2  # Initial attempt + 1 auto-fix
    
    # Step 1: Schema discovery
    schema_info = None
    if datastore_id:
        try:
            yield {"type": "thinking", "content": "I need to understand your database schema first..."}
            await asyncio.sleep(0.1)
            yield {"type": "progress", "content": "🔍 Fetching database schema..."}
            
            res = supabase.table("datastores").select("*").eq("id", datastore_id).single().execute()
            if res.data:
                datastore = res.data
                if datastore["type"] == "bigquery":
                    schema_result = await _get_bigquery_schema(datastore, None, None)
                    datasets = schema_result.get('datasets', [])
                    schema_info = f"BigQuery datasets available: {', '.join([d['name'] for d in datasets])}"
                    yield {"type": "progress", "content": f"✓ Found {len(datasets)} BigQuery datasets"}
                elif datastore["type"] == "postgres":
                    schema_result = await _get_postgres_schema(datastore, None, None)
                    schemas = schema_result.get('schemas', [])
                    schema_info = f"PostgreSQL schemas available: {', '.join([s['name'] for s in schemas])}"
                    yield {"type": "progress", "content": f"✓ Found {len(schemas)} PostgreSQL schemas"}
        except Exception as e:
            yield {"type": "progress", "content": f"⚠️ Schema fetch failed: {str(e)}"}
    
    # Step 2: Iterative generation and testing
    for attempt in range(1, max_attempts + 1):
        try:
            if attempt == 1:
                yield {"type": "thinking", "content": "Now I'll write Python code to fulfill your request..."}
            else:
                yield {"type": "thinking", "content": "Let me fix the error and try again..."}
            
            await asyncio.sleep(0.1)
            yield {"type": "progress", "content": f"🤖 Generating Python code..."}
            
            # Build prompt
            if code:
                user_message = f"User request: {user_prompt}\n\nCurrent Python code:\n\n{code}"
            else:
                user_message = f"User request: {user_prompt}\n\nGenerate new Python query code from scratch using the @node comment structure for queries."
            
            if schema_info:
                user_message += f"\n\nAvailable database schema:\n{schema_info}\n\nUse fully qualified table names."
            
            if attempt > 1 and 'last_error' in locals():
                user_message += f"\n\nPrevious attempt failed with error:\n{last_error}\n\nPlease fix the code to resolve this error."
            
            # Build conversation
            chat_history = chat[-15:] if chat else []
            contents = []
            for msg in chat_history:
                if not msg.get('content'):
                    continue
                role = "model" if msg.get('role') == 'assistant' else "user"
                contents.append({"role": role, "parts": [{"text": msg['content']}]})
            contents.append({"role": "user", "parts": [{"text": user_message}]})
            
            # Function calling loop
            generated_code = None
            tool_iteration = 0
            
            while tool_iteration < max_tool_iterations:
                tool_iteration += 1
                
                # Call Gemini with tools
                payload = {
                    "contents": contents,
                    "systemInstruction": {"parts": [{"text": EXPLORATION_SYSTEM_INSTRUCTION}]},
                    "tools": GEMINI_TOOLS,
                    "generationConfig": {
                        "temperature": 0.2 if attempt > 1 else temperature,
                        "maxOutputTokens": max_output_tokens,
                        "responseMimeType": "text/plain"
                    }
                }
                
                response = requests.post(
                    GEMINI_URL,
                    headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                    json=payload,
                    timeout=60
                )
                
                if not response.ok:
                    raise Exception(f"Gemini API error: {response.text}")
                
                data = response.json()
                candidate = data.get("candidates", [{}])[0]
                content = candidate.get("content", {})
                parts = content.get("parts", [])
                
                # Check if AI wants to call a function
                function_calls = [p for p in parts if "functionCall" in p]
                
                if function_calls:
                    # Execute function calls
                    function_responses = []
                    for fc in function_calls:
                        func_name = fc["functionCall"]["name"]
                        func_args = fc["functionCall"].get("args", {})
                        
                        yield {"type": "progress", "content": f"🔧 Calling {func_name}()..."}
                        
                        # Execute the function
                        if func_name == "list_datastores":
                            result = await _get_available_datastores()
                            yield {"type": "progress", "content": f"✓ Found {len(result)} datastores"}
                        elif func_name == "list_boards":
                            result = await _get_available_boards()
                            yield {"type": "progress", "content": f"✓ Found {len(result)} boards"}
                        elif func_name == "list_board_queries":
                            board_id = fc["functionCall"].get("args", {}).get("board_id")
                            result = await _get_board_queries(board_id) if board_id else []
                            yield {"type": "progress", "content": f"✓ Found {len(result)} queries"}
                        elif func_name == "get_query_code":
                            query_id = fc["functionCall"].get("args", {}).get("query_id")
                            result = await _get_query_code(query_id) if query_id else {"error": "Missing query_id"}
                            if "error" not in result:
                                yield {"type": "progress", "content": f"✓ Retrieved code for {result.get('name', 'query')}"}
                            else:
                                yield {"type": "progress", "content": f"✗ {result['error']}"}
                        else:
                            result = {"error": f"Unknown function: {func_name}"}
                        
                        function_responses.append({
                            "functionResponse": {
                                "name": func_name,
                                "response": {"result": result}
                            }
                        })
                    
                    # Add function responses to conversation
                    contents.append({
                        "role": "model",
                        "parts": function_calls
                    })
                    contents.append({
                        "role": "user",
                        "parts": function_responses
                    })
                    
                    # Continue the loop to get the actual code
                    continue
                
                # No function calls - extract the text response
                text_parts = [p for p in parts if "text" in p]
                if text_parts:
                    raw_text = text_parts[0].get("text", "")
                    if raw_text:
                        # Check if it's conversational text (not code)
                        raw_text_stripped = raw_text.strip()
                        if raw_text_stripped and not raw_text_stripped.startswith(('#', 'import', 'from', 'def', 'class', 'if', 'for', 'while', 'try', 'with', 'result')):
                            # Looks like conversational text, not code - prompt for code
                            yield {"type": "progress", "content": f"⚠️ AI returned text instead of code, reprompting..."}
                            contents.append({
                                "role": "model",
                                "parts": [{"text": raw_text}]
                            })
                            contents.append({
                                "role": "user",
                                "parts": [{"text": "You must output ONLY valid Python code. Do not output explanations or questions. Generate the Python code now."}]
                            })
                            continue
                        
                        generated_code = strip_markdown_code_block(raw_text_stripped)
                        break
                
                # No text either - something went wrong
                if tool_iteration >= max_tool_iterations:
                    raise Exception("Too many tool iterations without code generation")
            
            if not generated_code:
                raise Exception("Failed to generate code")
            
            yield {"type": "progress", "content": f"✓ Code generated ({len(generated_code)} characters)"}
            
            # Show code diff if we have previous code
            if code:
                yield {"type": "code_delta", "old_code": code, "new_code": generated_code}
            else:
                yield {"type": "code_delta", "old_code": "", "new_code": generated_code}
            
            # Step 3: Test the code
            if query_id or datastore_id:
                yield {"type": "thinking", "content": "Let me test this code to make sure it works..."}
                await asyncio.sleep(0.1)
                yield {"type": "progress", "content": "🧪 Testing the generated code..."}
                
                test_query_id = query_id
                if not test_query_id:
                    yield {"type": "progress", "content": "  Creating temporary test query..."}
                    user_res = await supabase.auth.get_user()
                    user_id = user_res.user.id if user_res.user else None
                    if user_id:
                        # Need a board to create a query - get first available or create temp
                        board_res = supabase.table("boards").select("id").limit(1).execute()
                        if board_res.data:
                            board_id = board_res.data[0]['id']
                            temp_res = supabase.table("board_queries").insert({
                                "name": f"_test_{int(time.time())}",
                                "board_id": board_id,
                                "python_code": generated_code,
                                "ui_map": {}
                            }).execute()
                            if temp_res.data:
                                test_query_id = temp_res.data[0]['id']
                
                if test_query_id:
                    try:
                        # Update the query
                        supabase.table("board_queries").update({
                            "python_code": generated_code
                        }).eq("id", test_query_id).execute()
                        
                        # Run the query
                        test_response = requests.post(
                            "http://localhost:8000/explore",
                            json={
                                "query_id": test_query_id,
                                "args": {},
                                "datastore_id": datastore_id
                            },
                            timeout=30
                        )
                        
                        if test_response.ok:
                            test_data = test_response.json()
                            row_count = test_data.get("count", 0)
                            yield {"type": "progress", "content": f"✓ Test passed! Query returned {row_count} rows"}
                            yield {"type": "test_result", "success": True, "row_count": row_count}
                            
                            # Sample data
                            if test_data.get("table") and len(test_data["table"]) > 0:
                                first_row = test_data["table"][0]
                                sample = ", ".join([f"{k}={v}" for k, v in list(first_row.items())[:3]])
                                yield {"type": "progress", "content": f"  Sample: {sample}..."}
                            
                            # Success!
                            yield {
                                "type": "final",
                                "code": generated_code,
                                "message": f"✨ **Success!** Code generated and tested{' on first try' if attempt == 1 else ' after fixing the error'}.",
                                "test_passed": True,
                                "attempts": attempt
                            }
                            return
                        else:
                            error_data = test_response.json()
                            last_error = error_data.get("detail", "Unknown error")
                            yield {"type": "progress", "content": f"❌ Test failed: {last_error}"}
                            yield {"type": "test_result", "success": False, "error": last_error}
                            
                            if attempt == max_attempts:
                                # Max attempts reached - ask user what to do
                                yield {
                                    "type": "needs_user_input",
                                    "code": generated_code,
                                    "error": last_error,
                                    "message": f"I tried to fix the error but it's still failing. The issue is:\n\n```\n{last_error}\n```\n\nWould you like me to:\n1. Try again with more information\n2. Use the code as-is (you can fix it manually)\n3. Take a different approach",
                                    "test_passed": False
                                }
                                yield {
                                    "type": "final",
                                    "code": generated_code,
                                    "message": f"⚠️ **Code generated but not working yet.**\n\nError: {last_error[:200]}...\n\nLet me know how you'd like to proceed!",
                                    "test_passed": False,
                                    "attempts": attempt,
                                    "error": last_error
                                }
                                return
                            
                            # Continue to next attempt (one fix attempt)
                            continue
                            
                    except Exception as test_error:
                        last_error = str(test_error)
                        yield {"type": "progress", "content": f"❌ Test execution error: {last_error}"}
                        yield {"type": "test_result", "success": False, "error": last_error}
                        
                        if attempt == max_attempts:
                            yield {
                                "type": "needs_user_input",
                                "code": generated_code,
                                "error": last_error,
                                "message": f"I tried to fix the error but it's still failing. Error:\n\n```\n{last_error}\n```\n\nHow would you like to proceed?",
                                "test_passed": False
                            }
                            yield {
                                "type": "final",
                                "code": generated_code,
                                "message": f"⚠️ **Code generated but testing failed.**\n\nError: {last_error[:200]}...\n\nLet me know if you want me to try a different approach!",
                                "test_passed": False,
                                "attempts": attempt,
                                "error": last_error
                            }
                            return
                        continue
            else:
                # No testing
                yield {"type": "progress", "content": "⚠️ Skipping test (no datastore or query ID)"}
                yield {
                    "type": "final",
                    "code": generated_code,
                    "message": "Code generated (not tested).",
                    "test_passed": None,
                    "attempts": attempt
                }
                return
                
        except Exception as e:
            last_error = str(e)
            yield {"type": "progress", "content": f"❌ Generation error: {last_error}"}
            
            if attempt == max_attempts:
                yield {
                    "type": "needs_user_input",
                    "error": last_error,
                    "message": f"I encountered an error:\n\n```\n{last_error}\n```\n\nShould I try a different approach?",
                    "test_passed": False
                }
                yield {"type": "error", "content": f"Failed after {max_attempts} attempts. Last error: {last_error}"}
                return
            
            continue


async def _exploration_helper_with_testing(
    code: str,
    user_prompt: str,
    chat: List[Dict[str, str]],
    api_key: str,
    datastore_id: Optional[str],
    query_id: Optional[str]
) -> Dict[str, Any]:
    """
    Enhanced query helper that:
    1. Fetches schema from datastore
    2. Generates Python code with AI
    3. Tests the code
    4. If it fails, attempts to fix it (max 3 attempts)
    5. Returns progress log showing all attempts
    """
    progress_log = []
    max_attempts = 3
    
    # Step 1: Get schema information if datastore is provided
    schema_info = None
    if datastore_id:
        try:
            progress_log.append("🔍 Fetching database schema...")
            res = supabase.table("datastores").select("*").eq("id", datastore_id).single().execute()
            if res.data:
                datastore = res.data
                if datastore["type"] == "bigquery":
                    schema_result = await _get_bigquery_schema(datastore, None, None)
                    schema_info = f"BigQuery datasets available: {', '.join([d['name'] for d in schema_result.get('datasets', [])])}"
                    progress_log.append(f"✓ Found {len(schema_result.get('datasets', []))} datasets")
                elif datastore["type"] == "postgres":
                    schema_result = await _get_postgres_schema(datastore, None, None)
                    schema_info = f"PostgreSQL schemas available: {', '.join([s['name'] for s in schema_result.get('schemas', [])])}"
                    progress_log.append(f"✓ Found {len(schema_result.get('schemas', []))} schemas")
        except Exception as e:
            progress_log.append(f"⚠️  Schema fetch failed: {str(e)}")
    
    # Step 2: Generate code with AI
    for attempt in range(1, max_attempts + 1):
        try:
            progress_log.append(f"\n🤖 Attempt {attempt}/{max_attempts}: Generating Python code...")
            
            # Build user message with schema context
            if code:
                user_message = f"User request: {user_prompt}\n\nCurrent Python code:\n\n{code}"
            else:
                user_message = f"User request: {user_prompt}\n\nGenerate new Python query code from scratch using the @node comment structure for queries."
            
            if schema_info:
                user_message += f"\n\nAvailable database schema:\n{schema_info}\n\nUse fully qualified table names (e.g., dataset.table for BigQuery, schema.table for Postgres)."
            
            # Add error context if this is a retry
            if attempt > 1 and 'last_error' in locals():
                user_message += f"\n\nPrevious attempt failed with error:\n{last_error}\n\nPlease fix the code to resolve this error."
            
            # Build conversation history
            chat_history = chat[-15:] if chat else []
            contents = []
            for msg in chat_history:
                if not msg.get('content'):
                    continue
                role = "model" if msg.get('role') == 'assistant' else "user"
                contents.append({"role": role, "parts": [{"text": msg['content']}]})
            contents.append({"role": "user", "parts": [{"text": user_message}]})
            
            # Call Gemini
            payload = {
                "contents": contents,
                "systemInstruction": {"parts": [{"text": EXPLORATION_SYSTEM_INSTRUCTION}]},
                "generationConfig": {
                    "temperature": 0.2 if attempt > 1 else 0.3,  # Lower temp for fixes
                    "maxOutputTokens": 8192,
                    "responseMimeType": "text/plain"
                }
            }
            
            response = requests.post(
                GEMINI_URL,
                headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                json=payload,
                timeout=60
            )
            
            if not response.ok:
                raise Exception(f"Gemini API error: {response.text}")
            
            data = response.json()
            raw_text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            
            if not raw_text:
                raise Exception("Gemini returned no content")
            
            generated_code = strip_markdown_code_block(raw_text.strip())
            progress_log.append(f"✓ Code generated ({len(generated_code)} characters)")
            
            # Step 3: Test the generated code
            if query_id or datastore_id:
                progress_log.append("🧪 Testing the generated code...")
                
                # Create a temporary query or use the provided one
                test_query_id = query_id
                if not test_query_id:
                    # Create temp query for testing
                    progress_log.append("  Creating temporary test query...")
                    user_res = await supabase.auth.get_user()
                    user_id = user_res.user.id if user_res.user else None
                    if user_id:
                        # Need a board to create a query
                        board_res = supabase.table("boards").select("id").limit(1).execute()
                        if board_res.data:
                            board_id = board_res.data[0]['id']
                            temp_res = supabase.table("board_queries").insert({
                                "name": f"_test_{int(time.time())}",
                                "board_id": board_id,
                                "python_code": generated_code,
                                "ui_map": {}
                            }).execute()
                            if temp_res.data:
                                test_query_id = temp_res.data[0]['id']
                
                if test_query_id:
                    try:
                        # Update the query with the new code
                        supabase.table("board_queries").update({
                            "python_code": generated_code
                        }).eq("id", test_query_id).execute()
                        
                        # Run the query
                        backendUrl = "http://localhost:8000"
                        test_response = requests.post(
                            f"{backendUrl}/explore",
                            json={
                                "query_id": test_query_id,
                                "args": {},
                                "datastore_id": datastore_id
                            },
                            timeout=30
                        )
                        
                        if test_response.ok:
                            test_data = test_response.json()
                            row_count = test_data.get("count", 0)
                            progress_log.append(f"✓ Test passed! Query returned {row_count} rows")
                            
                            # Show sample of first row
                            if test_data.get("table") and len(test_data["table"]) > 0:
                                first_row = test_data["table"][0]
                                sample = ", ".join([f"{k}={v}" for k, v in list(first_row.items())[:3]])
                                progress_log.append(f"  Sample: {sample}...")
                            
                            # Success! Return the code
                            return {
                                "code": generated_code,
                                "message": "✨ Code generated and tested successfully!\n\n" + "\n".join(progress_log),
                                "progress": progress_log,
                                "test_passed": True,
                                "attempts": attempt
                            }
                        else:
                            # Test failed
                            error_data = test_response.json()
                            last_error = error_data.get("detail", "Unknown error")
                            progress_log.append(f"❌ Test failed: {last_error}")
                            
                            if attempt == max_attempts:
                                # Max attempts reached
                                return {
                                    "code": generated_code,
                                    "message": f"⚠️  Generated code but testing failed after {max_attempts} attempts.\n\n" + "\n".join(progress_log) + f"\n\nLast error: {last_error}",
                                    "progress": progress_log,
                                    "test_passed": False,
                                    "attempts": attempt,
                                    "error": last_error
                                }
                            
                            # Continue to next attempt
                            continue
                            
                    except Exception as test_error:
                        last_error = str(test_error)
                        progress_log.append(f"❌ Test execution error: {last_error}")
                        
                        if attempt == max_attempts:
                            return {
                                "code": generated_code,
                                "message": f"⚠️  Generated code but testing failed.\n\n" + "\n".join(progress_log),
                                "progress": progress_log,
                                "test_passed": False,
                                "attempts": attempt,
                                "error": last_error
                            }
                        continue
            else:
                # No testing possible without query_id or datastore_id
                progress_log.append("⚠️  Skipping test (no datastore or query ID)")
                return {
                    "code": generated_code,
                    "message": "Code generated (not tested).\n\n" + "\n".join(progress_log),
                    "progress": progress_log,
                    "test_passed": None,
                    "attempts": attempt
                }
                
        except Exception as e:
            last_error = str(e)
            progress_log.append(f"❌ Generation error: {last_error}")
            
            if attempt == max_attempts:
                raise Exception(f"Failed after {max_attempts} attempts. Last error: {last_error}")
            
            continue
    
    # Should never reach here
    raise Exception("Unexpected error in exploration helper")

@app.post("/get-schema")
async def get_schema(
    datastore_id: Optional[str] = Body(default=None),
    connector_id: Optional[str] = Body(default=None),
    database: Optional[str] = Body(default=None),
    table: Optional[str] = Body(default=None)
):
    """
    Get schema information for a BigQuery or PostgreSQL database.
    Can use either datastore_id or connector_id (they're the same thing).
    If database and table are provided, returns detailed table schema.
    Otherwise returns list of databases/datasets and tables.
    """
    try:
        # Accept both datastore_id and connector_id
        ds_id = datastore_id or connector_id
        if not ds_id:
            raise HTTPException(status_code=400, detail="Missing datastore_id or connector_id")
        
        # Fetch datastore
        res = supabase.table("datastores").select("*").eq("id", ds_id).single().execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Datastore not found")
        
        datastore = res.data
        
        if datastore["type"] == "bigquery":
            return await _get_bigquery_schema(datastore, database, table)
        elif datastore["type"] == "postgres":
            return await _get_postgres_schema(datastore, database, table)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported datastore type: {datastore['type']}")
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Schema retrieval failed: {str(e)}")

async def _get_bigquery_schema(datastore: Dict[str, Any], dataset: Optional[str], table: Optional[str]):
    """Get BigQuery schema information."""
    client = get_bigquery_client(datastore["config"])
    
    if table and dataset:
        # Get specific table schema
        table_ref = client.dataset(dataset).table(table)
        table_obj = client.get_table(table_ref)
        
        schema_info = []
        for field in table_obj.schema:
            schema_info.append({
                "name": field.name,
                "type": field.field_type,
                "mode": field.mode,
                "description": field.description or ""
            })
        
        return {
            "type": "table_schema",
            "dataset": dataset,
            "table": table,
            "schema": schema_info,
            "row_count": table_obj.num_rows
        }
    
    elif dataset:
        # List tables in dataset
        tables = list(client.list_tables(dataset))
        return {
            "type": "tables",
            "dataset": dataset,
            "tables": [{"name": t.table_id, "type": t.table_type} for t in tables]
        }
    
    else:
        # List datasets
        datasets = list(client.list_datasets())
        return {
            "type": "datasets",
            "datasets": [{"name": d.dataset_id, "project": d.project} for d in datasets]
        }

async def _get_postgres_schema(datastore: Dict[str, Any], database: Optional[str], table: Optional[str]):
    """Get PostgreSQL schema information."""
    import sqlalchemy as sa
    from sqlalchemy import inspect
    
    conn_str = datastore["config"].get("connection_string")
    if not conn_str:
        raise HTTPException(status_code=400, detail="Postgres connection string missing")
    
    engine = sa.create_engine(conn_str)
    inspector = inspect(engine)
    
    if table:
        # Get specific table schema
        schema = database or "public"
        columns = inspector.get_columns(table, schema=schema)
        
        schema_info = []
        for col in columns:
            schema_info.append({
                "name": col["name"],
                "type": str(col["type"]),
                "nullable": col["nullable"],
                "default": str(col.get("default", "")) if col.get("default") else None
            })
        
        return {
            "type": "table_schema",
            "schema": schema,
            "table": table,
            "columns": schema_info
        }
    
    elif database:
        # List tables in schema
        tables = inspector.get_table_names(schema=database)
        return {
            "type": "tables",
            "schema": database,
            "tables": [{"name": t, "type": "table"} for t in tables]
        }
    
    else:
        # List schemas
        schemas = inspector.get_schema_names()
        return {
            "type": "schemas",
            "schemas": [{"name": s} for s in schemas]
        }

@app.post("/generate-chat-title")
async def generate_chat_title(
    user_prompt: str = Body(...),
    context: str = Body(default="general"),  # "board" or "query" or "general"
    gemini_api_key: Optional[str] = Body(default=None)
):
    """
    Generate a concise chat title based on the user's first message.
    Like ChatGPT/Cursor - creates a short, descriptive title.
    """
    try:
        api_key = gemini_api_key or GEMINI_API_KEY
        if not api_key:
            # Fallback to simple title
            return {"title": f"{context.capitalize()}: {user_prompt[:30]}..."}
        
        # Build prompt for title generation
        system_instruction = """You are a title generator. Given a user's message, create a concise, descriptive title (2-5 words max).

Rules:
- Output ONLY the title text, nothing else
- No quotes, no markdown, no explanations
- Keep it short (2-5 words)
- Make it descriptive and specific
- Use title case

Examples:
- User: "Create a query to get top 10 customers by revenue" → Title: "Top Customers Query"
- User: "Add a KPI card showing sales" → Title: "Sales KPI Card"
- User: "Fix the date filter" → Title: "Fix Date Filter"
- User: "Make it responsive" → Title: "Responsive Layout"
"""
        
        contents = [{
            "role": "user",
            "parts": [{"text": f"Generate a title for this chat:\n\n{user_prompt}"}]
        }]
        
        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 50,
                "responseMimeType": "text/plain"
            }
        }
        
        response = requests.post(
            GEMINI_URL,
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            json=payload,
            timeout=10
        )
        
        if not response.ok:
            # Fallback
            return {"title": f"{user_prompt[:40]}..."}
        
        data = response.json()
        raw_title = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        
        if not raw_title:
            # Fallback
            return {"title": f"{user_prompt[:40]}..."}
        
        # Clean up the title
        title = raw_title.strip().strip('"').strip("'")
        
        # Limit length
        if len(title) > 50:
            title = title[:47] + "..."
        
        return {"title": title}
    
    except Exception as e:
        print(f"Title generation error: {str(e)}")
        # Fallback to first few words
        words = user_prompt.split()[:5]
        return {"title": " ".join(words) + ("..." if len(user_prompt.split()) > 5 else "")}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
