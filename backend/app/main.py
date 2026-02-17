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
from decimal import Decimal
from datetime import datetime, date

load_dotenv()

app = FastAPI(title="Nubi Exploration Engine")

# JSON serialization helper for BigQuery/Pandas types
def convert_to_json_serializable(obj):
    """Convert non-JSON-serializable types to JSON-compatible formats."""
    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, (datetime, date)):
        return obj.isoformat()
    elif isinstance(obj, bytes):
        return obj.decode('utf-8', errors='ignore')
    elif pd.isna(obj):
        return None
    return obj

def clean_dataframe_for_json(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Convert DataFrame to JSON-serializable list of dicts."""
    # Replace NaN/NaT with None
    df = df.replace({pd.NaT: None, float('nan'): None})
    
    # Convert to dict
    records = df.to_dict('records')
    
    # Clean each record
    cleaned_records = []
    for record in records:
        cleaned_record = {k: convert_to_json_serializable(v) for k, v in record.items()}
        cleaned_records.append(cleaned_record)
    
    return cleaned_records

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

BOARD_SYSTEM_INSTRUCTION = """You are an intelligent BI assistant, similar to Cursor AI for data. You can perform MULTIPLE operations in a single request.

CORE PRINCIPLE: BE PROACTIVE WITH TOOLS
When you lack context or information, ALWAYS use tools to investigate rather than refusing to help or asking the user. You have access to all the information you need through tools - use them!

YOUR CAPABILITIES:
1. Create queries: Use create_or_update_query() tool
2. Update queries: Use create_or_update_query() tool with query_id
3. Delete queries: Use delete_query() tool
4. Edit board layout: Output dashboard code after tools
5. Get information: Use list/get tools (USE THESE FREQUENTLY!)

🎯 CREATING A COMPLETE BOARD/DASHBOARD (CRITICAL):
When user asks to "create a dashboard" or "create a board", you MUST do BOTH:

1. CREATE THE QUERIES FIRST:
   - Explore schema: get_datastore_schema() to understand available data
   - Create queries: Use create_or_update_query() for each metric/chart needed
   - Test queries: Use test_query() to verify they work
   - Remember the query_ids returned - you'll need them for the board HTML!

2. THEN CREATE THE BOARD HTML:
   - Output complete HTML with widgets positioned using grid pattern
   - Each widget fetches data using the query_ids you just created
   - Use proper placement: KPIs at top (y="0"), charts below (y="200"+)
   
Example workflow:
- User: "Create a sales dashboard"
- You: 
  1. Create query: "Total Revenue" → get query_id abc-123
  2. Create query: "Orders by Month" → get query_id def-456
  3. Output board HTML with:
     - KPI widget at x="0" y="0" using query_id="abc-123"
     - Chart widget at x="0" y="200" using query_id="def-456"

NEVER create just queries or just board HTML - always create BOTH when asked for a dashboard!

📍 WIDGET PLACEMENT STRATEGY:
When creating or modifying boards with widgets, position them using a simple grid pattern:
- Use data-lg-x, data-lg-y (position), data-lg-w, data-lg-h (size) attributes
- Leave 20px gaps between widgets for clean spacing
- Common sizes: KPIs = 300x180, Small Charts = 500x300, Large Charts = 640x350
- Horizontal layout: x="0", x="320", x="640" (300px wide + 20px gap)
- Vertical layout: y="0", y="200", y="520" (varies by widget height + 20px gap)
- 2-column dashboard: Row 1: KPIs at x="0", x="320", x="640"; Row 2: Charts at x="0" and x="660"
- Widgets automatically position themselves on load - no JavaScript calculation needed in generated HTML!

🚨 CRITICAL WORKFLOW FOR CREATING QUERIES:
Step 1: Call get_datastore_schema(datastore_id) to see available datasets
Step 2: Call get_datastore_schema(datastore_id, dataset="dataset_name") to see tables
Step 3: Call get_datastore_schema(datastore_id, dataset="dataset_name", table="table_name") to see columns
Step 4: EXPLORE THE DATA - Call execute_query_direct(datastore_id, "SELECT * FROM dataset.table LIMIT 5") to see actual data
Step 5: Run more exploratory queries to understand data patterns (e.g., date ranges, distinct values, aggregations)
Step 6: Generate Python code using the EXACT table/column names from schema AND insights from data exploration
Step 7: Start with a SIMPLE query first (e.g. SELECT col1, col2 FROM table LIMIT 10)
Step 8: Call test_query(python_code) to verify it works
Step 9: If test fails, use execute_query_direct() to debug with simpler SQL queries
Step 10: Once basic query works, build up complexity (add GROUP BY, aggregations, etc.)
Step 11: Call create_or_update_query() with the validated code
Step 12: ALWAYS provide a brief text summary after tool operations

SCHEMA EXPLORATION STRATEGY:
- ALWAYS drill down to column level before writing queries
- First: get_datastore_schema(datastore_id) → see datasets
- Then: get_datastore_schema(datastore_id, dataset="X") → see tables in dataset
- Then: get_datastore_schema(datastore_id, dataset="X", table="Y") → see actual columns
- Then: execute_query_direct(datastore_id, "SELECT * FROM X.Y LIMIT 5") → see actual data
- NEVER guess column names or data patterns - always verify with schema AND sample data

QUERY FAILURE STRATEGY:
If a query fails:
1. Use execute_query_direct(datastore_id, "SELECT * FROM dataset.table LIMIT 5") to verify the table exists and see data
2. Check if column names are correct by looking at schema again
3. Use execute_query_direct() to test simplified SQL versions (no WHERE, no GROUP BY)
4. Simplify: remove WHERE clauses, GROUP BY, joins
5. Build back up once the simple query works

ZERO ROWS STRATEGY (CRITICAL):
If test_query returns 0 rows, this is NOT a success - investigate immediately:
1. Use execute_query_direct(datastore_id, "SELECT * FROM dataset.table LIMIT 10") to confirm data exists
2. Check if column names used in WHERE/GROUP BY are correct via get_datastore_schema
3. Use execute_query_direct() to check actual values: "SELECT DISTINCT column_name FROM dataset.table LIMIT 20"
4. Check if the table has the data the user expects (maybe different table or column names)
5. Use execute_query_direct() to test filters: remove WHERE conditions one by one
6. Look at sample data to understand the actual values in columns
7. Fix the query and test again until rows are returned
8. Update the saved query with the fixed code using create_or_update_query with query_id
NEVER just report "0 rows" and stop. Always investigate and fix using execute_query_direct() for rapid exploration.

FOLLOW-UP / CONTINUATION STRATEGY:
When the user asks follow-up questions (e.g. "no rows returned", "fix it", "why is it empty"):
1. First call list_board_queries(board_id) to see what queries exist
2. Call get_query_code(query_id) to get the actual code of the relevant query
3. Call get_datastore_schema to verify tables/columns
4. Use execute_query_direct() to explore the data and diagnose issues quickly
5. Call test_query with a simplified version to diagnose the issue
6. Fix and update the query using create_or_update_query with the query_id
Always be proactive - use tools to investigate rather than asking the user for information you can look up.

VAGUE/AMBIGUOUS REQUEST STRATEGY (CRITICAL):
When the user's request lacks context (e.g., "limit to 100", "make it faster", "fix it", "change the chart"):
1. IMMEDIATELY call list_board_queries(board_id) to see all available queries
2. If modifying a query, call get_query_code(query_id) to see the current code
3. Use the tool results to infer what the user wants
4. If there's only one query, assume they mean that one
5. If multiple queries exist, pick the most recently created/updated one or ask for clarification
NEVER output an error or refuse to help - always try to infer intent from available context first.

Example:
- User: "limit to top 100"
- You: Call list_board_queries() → see "Sales per Business Unit" query
- You: Call get_query_code(that_id) → see it's missing LIMIT and ORDER BY
- You: Update the query to add "ORDER BY TotalSales DESC LIMIT 100"
- You: Provide summary of what you did

CONTEXT PROVIDED:
- CURRENT_BOARD_ID: The UUID to use for board_id parameter
- Available datastores: names, types, and IDs
- Available queries: names, IDs, descriptions

FLEXIBLE EXECUTION:
You can do MULTIPLE things at once:
- Create 2 queries AND edit the board
- Update 1 query AND delete another
- ANY combination of operations

QUERY CODE FORMAT:
```
# @node: name
# @type: query
# @datastore: <actual_datastore_uuid>
# @query: SELECT actual_column FROM dataset.actual_table WHERE x = {{arg_name}}

# args is a dict available at runtime with values passed via REST API
# Use Jinja2 syntax {{arg_name}} in SQL for dynamic values
# In Python code, access as: args.get('arg_name', default_value)
result = query_result
```

BIGQUERY DATA QUALITY HANDLING (CRITICAL):
- Real-world data often has quality issues (dates in numeric columns, nulls, invalid values)
- ALWAYS use SAFE_CAST instead of CAST to handle conversion errors gracefully
- SAFE_CAST returns NULL for invalid conversions instead of throwing errors
- Examples:
  * SAFE_CAST(amount AS FLOAT64) - handles non-numeric values
  * SAFE_CAST(REPLACE(price, ',', '') AS NUMERIC) - handles formatted numbers
  * WHERE SAFE_CAST(value AS FLOAT64) IS NOT NULL - filter out bad data
- When you see errors like "Invalid NUMERIC value: 1900-02-13", it means the column has mixed data types
- Use execute_query_direct() to inspect actual data: SELECT DISTINCT column LIMIT 20 to see what values exist

ARGS SYSTEM:
- Queries can accept runtime arguments via the `args` dict
- In SQL: Use Jinja2 template syntax: {{arg_name}} 
- In Python: Use args.get('key', default) or args['key']
- Args are passed by the frontend/REST API at execution time
- Example: # @query: SELECT * FROM dataset.events WHERE status = '{{status}}'
- Don't rely on args for basic queries - use them for filters/parameters

TOOLS:
1. get_datastore_schema(datastore_id, dataset?, table?) - Get available tables/columns
2. execute_query_direct(datastore_id, sql_query, limit?) - Run SQL directly to explore data (USE THIS before creating queries!)
3. test_query(python_code) - Test query execution and see sample results
4. create_or_update_query(board_id, query_name, python_code, description, query_id?)
5. delete_query(query_id)
6. list_datastores()
7. list_board_queries(board_id)
8. get_query_code(query_id)

🎨 BOARD HTML/VISUALIZATION EDITING:

CRITICAL: You CAN and SHOULD edit board HTML code directly when the user asks for layout changes, visualizations, or styling!

BOARD ARCHITECTURE:
Boards use a canvas-based system with absolutely positioned widgets:
- Alpine.js for reactivity
- Chart.js for visualizations
- Interact.js for drag-and-drop and resizing
- Widgets are positioned absolutely on a .board-canvas
- Responsive layouts use data attributes (data-lg-x, data-md-x, data-sm-x)
- Position widgets programmatically: set data-lg-x/y/w/h, initWidget() applies them automatically
- Use simple grid patterns with 20px gaps for clean layouts

COMPLETE BOARD TEMPLATE:
```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Board</title>
  <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/interactjs/dist/interact.min.js"></script>
  <style>
    :root {
      --grid-gap: 1.5rem;
      --primary: #6366f1;
    }
    
    * { margin: 0; padding: 0; box-sizing: border-box; }
    
    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: #0a0e1a;
      color: #f9fafb;
      padding: 2rem;
      min-height: 100vh;
      overflow-x: hidden;
    }
    
    .board-canvas {
      position: relative;
      width: 100%;
      height: 2000px;
      max-width: 1400px;
      margin: 0 auto;
    }
    
    .widget {
      position: absolute;
      touch-action: none;
      user-select: none;
      min-width: 150px;
      min-height: 80px;
      cursor: grab;
      transition: outline 0.2s ease, transform 0.1s ease;
      z-index: 1;
    }
    
    .widget:active { cursor: grabbing; z-index: 10; }
    .widget.interacting { outline: 2px solid var(--primary); }
    
    .widget::after {
      content: '';
      position: absolute;
      right: 4px;
      bottom: 4px;
      width: 12px;
      height: 12px;
      border-right: 2px solid rgba(255, 255, 255, 0.3);
      border-bottom: 2px solid rgba(255, 255, 255, 0.3);
      cursor: nwse-resize;
    }
    
    .kpi-card, .chart-container {
      background: rgba(17, 24, 39, 0.7);
      backdrop-filter: blur(12px);
      border: 1px solid #1f2937;
      border-radius: 0.75rem;
      padding: 1.5rem;
      height: 100%;
      width: 100%;
    }
    
    .kpi-card { display: flex; flex-direction: column; justify-content: center; }
    .kpi-label { color: #9ca3af; font-size: 0.875rem; margin-bottom: 0.5rem; }
    .kpi-value { font-size: 2rem; font-weight: 700; margin-bottom: 0.5rem; }
    .kpi-trend { font-size: 0.875rem; font-weight: 600; }
    .kpi-trend.positive { color: #10b981; }
    .kpi-trend.negative { color: #ef4444; }
    
    .chart-container canvas { max-height: 100%; width: 100% !important; }
  </style>
</head>
<body x-data="boardManager()" @resize.window="detectViewport()">
  <div class="board-canvas">
    <!-- Widgets go here -->
    
  </div>

  <script>
    function boardManager() {
      return {
        viewport: 'lg',
        init() {
          this.detectViewport();
          window.addEventListener('message', (e) => {
            if (e.data.type === 'GET_HTML') {
              const canvas = document.querySelector('.board-canvas').cloneNode(true);
              canvas.querySelectorAll('.interacting').forEach(el => el.classList.remove('interacting'));
              const fullHTML = document.documentElement.outerHTML;
              window.parent.postMessage({ type: 'SYNC_HTML', html: fullHTML }, '*');
            }
          });
        },
        detectViewport() {
          const w = window.innerWidth;
          let newV = 'lg';
          if (w <= 375) newV = 'sm';
          else if (w <= 768) newV = 'md';
          
          if (newV !== this.viewport) {
            this.viewport = newV;
            document.body.setAttribute('data-viewport', newV);
            this.applyLayout();
          }
        },
        applyLayout() {
          document.querySelectorAll('.widget').forEach(el => {
            const v = this.viewport;
            const x = el.getAttribute('data-' + v + '-x') || '0';
            const y = el.getAttribute('data-' + v + '-y') || '0';
            const w = el.getAttribute('data-' + v + '-w') || '300';
            const h = el.getAttribute('data-' + v + '-h') || '200';
            
            el.style.transform = 'translate(' + x + 'px, ' + y + 'px)';
            el.style.width = w.includes('%') ? w : w + 'px';
            el.style.height = h + 'px';
          });
        },
        // Find next available position that doesn't overlap with existing widgets
        findAvailablePosition(width = 300, height = 200, viewport = 'lg') {
          const widgets = document.querySelectorAll('.widget');
          const occupied = Array.from(widgets).map(w => ({
            x: parseFloat(w.getAttribute('data-' + viewport + '-x') || 0),
            y: parseFloat(w.getAttribute('data-' + viewport + '-y') || 0),
            w: parseFloat(w.getAttribute('data-' + viewport + '-w') || 300),
            h: parseFloat(w.getAttribute('data-' + viewport + '-h') || 200)
          }));
          
          const gap = 20; // spacing between widgets
          const maxWidth = 1400; // max canvas width
          
          // Try grid positions with spacing
          for (let y = 0; y < 2000; y += 50) {
            for (let x = 0; x < maxWidth - width; x += 50) {
              if (!this.overlaps(x, y, width, height, occupied, gap)) {
                return { x, y };
              }
            }
          }
          
          // Fallback: stack vertically at the end
          const maxY = Math.max(0, ...occupied.map(r => r.y + r.h));
          return { x: 0, y: maxY + gap };
        },
        // Check if a position overlaps with any existing widgets
        overlaps(x, y, w, h, occupied, gap = 0) {
          return occupied.some(rect => 
            !(x + w + gap <= rect.x || x >= rect.x + rect.w + gap ||
              y + h + gap <= rect.y || y >= rect.y + rect.h + gap)
          );
        }
      }
    }

    function canvasWidget() {
      return {
        initWidget(el) {
          const getV = () => document.body.getAttribute('data-viewport') || 'lg';
          
          // Apply initial position from data attributes
          const applyInitialPosition = () => {
            const v = getV();
            const x = el.getAttribute('data-' + v + '-x') || '0';
            const y = el.getAttribute('data-' + v + '-y') || '0';
            const w = el.getAttribute('data-' + v + '-w') || '300';
            const h = el.getAttribute('data-' + v + '-h') || '200';
            
            el.style.transform = 'translate(' + x + 'px, ' + y + 'px)';
            el.style.width = w + 'px';
            el.style.height = h + 'px';
          };
          
          // Apply position immediately on init
          applyInitialPosition();
          
          interact(el)
            .draggable({
              inertia: true,
              listeners: {
                start: (e) => e.target.classList.add('interacting'),
                end: (e) => e.target.classList.remove('interacting'),
                move: (event) => {
                  const target = event.target;
                  const v = getV();
                  const x = (parseFloat(target.getAttribute('data-' + v + '-x')) || 0) + event.dx;
                  const y = (parseFloat(target.getAttribute('data-' + v + '-y')) || 0) + event.dy;

                  target.style.transform = 'translate(' + x + 'px, ' + y + 'px)';
                  target.setAttribute('data-' + v + '-x', x);
                  target.setAttribute('data-' + v + '-y', y);
                }
              }
            })
            .resizable({
              edges: { left: false, right: true, bottom: true, top: false },
              listeners: {
                start: (e) => e.target.classList.add('interacting'),
                end: (e) => e.target.classList.remove('interacting'),
                move: (event) => {
                  const target = event.target;
                  const v = getV();
                  
                  target.style.width = event.rect.width + 'px';
                  target.style.height = event.rect.height + 'px';
                  
                  target.setAttribute('data-' + v + '-w', event.rect.width);
                  target.setAttribute('data-' + v + '-h', event.rect.height);
                }
              }
            })
        }
      }
    }
  </script>
</body>
</html>
```

WIDGET EXAMPLES:

1. KPI/METRIC CARD:
```html
<div class="widget" data-type="kpi" 
     data-lg-x="0" data-lg-y="0" data-lg-w="300" data-lg-h="200"
     x-data="canvasWidget()" x-init="initWidget($el)">
  <div class="kpi-card" x-data="{ value: 1234, label: 'Total Users', trend: '+12%' }">
    <div class="kpi-label" x-text="label"></div>
    <div class="kpi-value" x-text="value.toLocaleString()"></div>
    <div class="kpi-trend positive" x-text="trend"></div>
  </div>
</div>
```

2. BAR CHART:
```html
<div class="widget" data-type="chart"
     data-lg-x="320" data-lg-y="0" data-lg-w="500" data-lg-h="300"
     x-data="canvasWidget()" x-init="initWidget($el)">
  <div class="chart-container" x-data="barChart()">
    <canvas x-ref="chart"></canvas>
  </div>
</div>

<script>
function barChart() {
  return {
    init() {
      new Chart(this.$refs.chart, {
        type: 'bar',
        data: {
          labels: ['Jan', 'Feb', 'Mar', 'Apr', 'May'],
          datasets: [{
            label: 'Sales',
            data: [12, 19, 3, 5, 2],
            backgroundColor: 'rgba(99, 102, 241, 0.8)'
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false
        }
      });
    }
  }
}
</script>
```

3. LINE CHART:
```html
<div class="widget" data-type="chart"
     data-lg-x="0" data-lg-y="220" data-lg-w="500" data-lg-h="300"
     x-data="canvasWidget()" x-init="initWidget($el)">
  <div class="chart-container" x-data="lineChart()">
    <canvas x-ref="chart"></canvas>
  </div>
</div>

<script>
function lineChart() {
  return {
    init() {
      new Chart(this.$refs.chart, {
        type: 'line',
        data: {
          labels: ['Jan', 'Feb', 'Mar', 'Apr', 'May'],
          datasets: [{
            label: 'Revenue',
            data: [30, 45, 35, 50, 40],
            borderColor: 'rgb(99, 102, 241)',
            tension: 0.4
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false
        }
      });
    }
  }
}
</script>
```

4. PIE CHART:
```html
<div class="widget" data-type="chart"
     data-lg-x="520" data-lg-y="220" data-lg-w="400" data-lg-h="350"
     x-data="canvasWidget()" x-init="initWidget($el)">
  <div class="chart-container" x-data="pieChart()">
    <canvas x-ref="chart"></canvas>
  </div>
</div>

<script>
function pieChart() {
  return {
    init() {
      new Chart(this.$refs.chart, {
        type: 'pie',
        data: {
          labels: ['Product A', 'Product B', 'Product C'],
          datasets: [{
            data: [300, 50, 100],
            backgroundColor: [
              'rgba(99, 102, 241, 0.8)',
              'rgba(139, 92, 246, 0.8)',
              'rgba(236, 72, 153, 0.8)'
            ]
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false
        }
      });
    }
  }
}
</script>
```

5. PROGRAMMATIC AUTO-PLACEMENT EXAMPLE:
When adding widgets dynamically (e.g., via a button click or AI generation), use the auto-placement function:

```html
<!-- Example: Button to add a new KPI widget -->
<div x-data="{ 
  addWidget() {
    const board = Alpine.$data(document.querySelector('[x-data*=boardManager]'));
    const pos = board.findAvailablePosition(300, 200); // width, height
    
    const canvas = document.querySelector('.board-canvas');
    const newWidget = document.createElement('div');
    newWidget.className = 'widget';
    newWidget.setAttribute('data-type', 'kpi');
    newWidget.setAttribute('data-lg-x', pos.x);
    newWidget.setAttribute('data-lg-y', pos.y);
    newWidget.setAttribute('data-lg-w', '300');
    newWidget.setAttribute('data-lg-h', '200');
    newWidget.innerHTML = `
      <div class="kpi-card" x-data="{ value: Math.floor(Math.random() * 10000), label: 'New Metric' }">
        <div class="kpi-label" x-text="label"></div>
        <div class="kpi-value" x-text="value.toLocaleString()"></div>
      </div>
    `;
    
    canvas.appendChild(newWidget);
    Alpine.initTree(newWidget); // Initialize Alpine.js on new element
  }
}">
  <button @click="addWidget()">Add KPI Widget</button>
</div>
```

NOTE: When AI generates board code with multiple widgets, simply use a grid pattern:
- First widget: data-lg-x="0" data-lg-y="0"
- Second widget: data-lg-x="420" data-lg-y="0" (400px wide + 20px gap)
- Third widget: data-lg-x="0" data-lg-y="320" (300px tall + 20px gap)

6. COMPLETE DASHBOARD EXAMPLE (Multi-widget with Proper Placement):
```html
<!-- Inside .board-canvas -->

<!-- Row 1: Three KPI Cards -->
<div class="widget" data-type="kpi" 
     data-lg-x="0" data-lg-y="0" data-lg-w="300" data-lg-h="180"
     x-data="canvasWidget()" x-init="initWidget($el)">
  <div class="kpi-card" x-data="{ value: 45231, label: 'Total Revenue' }">
    <div class="kpi-label" x-text="label"></div>
    <div class="kpi-value" x-text="'$' + value.toLocaleString()"></div>
  </div>
</div>

<div class="widget" data-type="kpi" 
     data-lg-x="320" data-lg-y="0" data-lg-w="300" data-lg-h="180"
     x-data="canvasWidget()" x-init="initWidget($el)">
  <div class="kpi-card" x-data="{ value: 1834, label: 'Total Orders' }">
    <div class="kpi-label" x-text="label"></div>
    <div class="kpi-value" x-text="value.toLocaleString()"></div>
  </div>
</div>

<div class="widget" data-type="kpi" 
     data-lg-x="640" data-lg-y="0" data-lg-w="300" data-lg-h="180"
     x-data="canvasWidget()" x-init="initWidget($el)">
  <div class="kpi-card" x-data="{ value: 12.4, label: 'Conversion Rate' }">
    <div class="kpi-label" x-text="label"></div>
    <div class="kpi-value" x-text="value + '%'"></div>
  </div>
</div>

<!-- Row 2: Two Charts -->
<div class="widget" data-type="chart"
     data-lg-x="0" data-lg-y="200" data-lg-w="640" data-lg-h="350"
     x-data="canvasWidget()" x-init="initWidget($el)">
  <!-- Left chart content -->
</div>

<div class="widget" data-type="chart"
     data-lg-x="660" data-lg-y="200" data-lg-w="640" data-lg-h="350"
     x-data="canvasWidget()" x-init="initWidget($el)">
  <!-- Right chart content -->
</div>
```
PLACEMENT CALCULATION:
- Row 1: KPIs are 300px wide, positioned at x=0, x=320 (0+300+20), x=640 (320+300+20)
- Row 2: Charts are 640px wide, positioned at x=0, x=660 (0+640+20)
- Vertical: KPIs are 180px tall, charts start at y=200 (180+20)

FETCHING QUERY DATA:
```javascript
// Inside a widget's Alpine.js component
// IMPORTANT: DON'T use 'await' in init() - let all widgets load in parallel!
function dataWidget() {
  return {
    data: [],
    loading: true,
    error: null,
    
    init() {
      // No await here - starts loading immediately without blocking other widgets
      this.loadData();
    },
    
    async loadData() {
      try {
        this.loading = true;
        const backendUrl = 'http://localhost:8000';
        
        const response = await fetch(`${backendUrl}/explore`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            query_id: 'your-query-id-here',
            args: {}  // Optional filter arguments
          })
        });
        
        const result = await response.json();
        
        if (result.error) {
          this.error = result.error;
          return;
        }
        
        this.data = result.result || [];
        this.renderChart();
        
      } catch (err) {
        this.error = err.message;
      } finally {
        this.loading = false;
      }
    },
    
    renderChart() {
      // Chart rendering logic
    }
  }
}
```

RESPONSIVE POSITIONING:
Widgets use data attributes for each viewport:
- Desktop (lg): `data-lg-x`, `data-lg-y`, `data-lg-w`, `data-lg-h`
- Tablet (md): `data-md-x`, `data-md-y`, `data-md-w`, `data-md-h`
- Mobile (sm): `data-sm-x`, `data-sm-y`, `data-sm-w`, `data-sm-h`

Example responsive widget:
```html
<div class="widget"
     data-lg-x="0" data-lg-y="0" data-lg-w="400" data-lg-h="300"
     data-md-x="0" data-md-y="0" data-md-w="350" data-md-h="250"
     data-sm-x="0" data-sm-y="0" data-sm-w="300" data-sm-h="200"
     x-data="canvasWidget()" x-init="initWidget($el)">
  <!-- Widget content -->
</div>
```

WIDGET POSITIONING:
- All widgets MUST be inside `.board-canvas`
- Position using data attributes, NOT inline styles
- X/Y are absolute pixel offsets from top-left
- W/H are width and height in pixels
- Drag and resize is handled automatically by interact.js via canvasWidget()

AUTO-PLACEMENT (PROGRAMMATIC):
🎯 RECOMMENDED APPROACH FOR AI-GENERATED BOARDS:
When you generate board HTML code with multiple widgets, use a SIMPLE GRID PATTERN:

Strategy 1: Horizontal Grid (side-by-side):
- Widget 1: data-lg-x="0"   data-lg-y="0"   data-lg-w="300" data-lg-h="200"
- Widget 2: data-lg-x="320" data-lg-y="0"   data-lg-w="300" data-lg-h="200"  (300 + 20px gap)
- Widget 3: data-lg-x="640" data-lg-y="0"   data-lg-w="300" data-lg-h="200"  (640 + 20px gap)

Strategy 2: Vertical Grid (stacked):
- Widget 1: data-lg-x="0" data-lg-y="0"   data-lg-w="500" data-lg-h="300"
- Widget 2: data-lg-x="0" data-lg-y="320" data-lg-w="500" data-lg-h="300"  (300 + 20px gap)
- Widget 3: data-lg-x="0" data-lg-y="640" data-lg-w="500" data-lg-h="300"

Strategy 3: 2-Column Layout (most common for dashboards):
- KPI 1: data-lg-x="0"   data-lg-y="0"   data-lg-w="300" data-lg-h="180"
- KPI 2: data-lg-x="320" data-lg-y="0"   data-lg-w="300" data-lg-h="180"
- KPI 3: data-lg-x="640" data-lg-y="0"   data-lg-w="300" data-lg-h="180"
- Chart: data-lg-x="0"   data-lg-y="200" data-lg-w="640" data-lg-h="350"
- Chart: data-lg-x="660" data-lg-y="200" data-lg-w="640" data-lg-h="350"

IMPORTANT: 
- Always leave 20px gap between widgets (add 20 to width/height when calculating next position)
- Keep canvas width max 1400px (typical: 640px for half-width charts, 300px for KPIs)
- KPIs are typically 300x180, Charts are typically 500x300 or 640x350
- initWidget() automatically applies these positions, so widgets appear exactly where specified

ADVANCED: Dynamic Placement (JavaScript runtime only)
If adding widgets dynamically via button clicks or custom scripts:
```javascript
const board = Alpine.$data(document.querySelector('[x-data*="boardManager"]'));
const pos = board.findAvailablePosition(400, 300); // width, height
// Returns next available position that doesn't overlap
```

WHEN USER ASKS FOR VISUALIZATION CHANGES:
1. Get the current board code (it's provided in the request)
2. Identify which widget/chart needs to be changed
3. Update the chart type, data mapping, or styling
4. Return the COMPLETE modified HTML
5. NEVER say "I cannot edit the code" - you absolutely can and MUST!

COMMON REQUESTS AND HOW TO HANDLE:
- "Make it a pie chart" → Change type: 'bar' to type: 'pie' in Chart config
- "Add a KPI card" → Add new widget with class="widget" and canvasWidget() init
  - Calculate position: if last widget ends at x=320, new widget starts at x=340 (320 + 20px gap)
  - Use grid pattern: first KPI at x="0", second at x="320", third at x="640"
- "Create a dashboard" or "Create a board" → DO BOTH:
  1. First: Create all needed queries using create_or_update_query() tool (explore schema, test queries)
  2. Then: Output board HTML with widgets in 2-column layout
     - Row 1: 3 KPIs (x="0", x="320", x="640")
     - Row 2: 2 charts (x="0" y="200", x="660" y="200")
     - Each widget uses query_id from step 1
  3. NEVER create just queries OR just HTML - always create BOTH!
- "Change colors" → Update backgroundColor in chart datasets
- "Show top 10" → Modify data: data.slice(0, 10)
- "Move chart to the right" → Adjust data-lg-x value (remember 20px gaps)
- "Make it bigger" → Increase data-lg-w and data-lg-h values
- "Add loading state" → Use x-show="loading" and x-show="!loading"

🚀 CRITICAL: PARALLEL DATA LOADING
When creating widgets that fetch data, ALWAYS use this pattern for parallel loading:
```javascript
init() {
  this.loadData();  // NO await here - allows all widgets to load simultaneously
}
```
NEVER use:
```javascript
async init() {
  await this.loadData();  // BAD - causes sequential loading
}
```
This ensures all dashboard widgets load their data in parallel, not one at a time!

DATA VISUALIZATION WITH LIVE DATA:
```html
<div class="widget" data-lg-x="0" data-lg-y="0" data-lg-w="500" data-lg-h="300"
     x-data="canvasWidget()" x-init="initWidget($el)">
  <div class="chart-container" x-data="liveChart()">
    <div x-show="loading" style="text-align: center; padding: 40px;">Loading...</div>
    <div x-show="error" style="color: #ef4444; padding: 20px;" x-text="error"></div>
    <canvas x-ref="chart" x-show="!loading && !error"></canvas>
  </div>
</div>

<script>
function liveChart() {
  return {
    loading: true,
    error: null,
    data: [],
    
    init() {
      // No await - allows all widgets to start loading in parallel
      this.loadData();
    },
    
    async loadData() {
      try {
        const backendUrl = 'http://localhost:8000';
        const response = await fetch(backendUrl + '/explore', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            query_id: 'PUT-ACTUAL-QUERY-ID-HERE',
            args: {}
          })
        });
        const result = await response.json();
        if (result.error) throw new Error(result.error);
        this.data = result.result || [];
        this.renderChart();
      } catch (err) {
        this.error = err.message;
      } finally {
        this.loading = false;
      }
    },
    
    renderChart() {
      if (this.data.length === 0) return;
      new Chart(this.$refs.chart, {
        type: 'bar',
        data: {
          labels: this.data.map(row => row.category_column),
          datasets: [{
            label: 'Value',
            data: this.data.map(row => row.value_column),
            backgroundColor: 'rgba(99, 102, 241, 0.8)'
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false
        }
      });
    }
  }
}
</script>
```

RECOVERY FROM CORRUPTED CODE:
If the board code is broken, missing, or user requests to "start fresh", use the complete template from above with boardManager() and canvasWidget() functions included.

🎯 COMPLETE DASHBOARD CREATION WORKFLOW:
When user asks to "create a dashboard" or "create a board showing X":
1. FIRST: Create the queries
   - Use get_datastore_schema() to explore available data
   - Use create_or_update_query() to create each query needed (KPIs, charts)
   - Use test_query() to verify they work
   - Save the query_ids returned - you need them for step 2!
   
2. THEN: Create the board HTML
   - Output complete HTML with widgets
   - Each widget fetches from the query_ids you created in step 1
   - Position widgets using grid pattern (20px gaps)
   - Follow the critical output rules below

3. ALWAYS DO BOTH STEPS! Never create just queries or just HTML.

Example:
User: "Create a sales dashboard"
Step 1: Create queries → "Total Sales" (id: abc), "Sales by Month" (id: def)
Step 2: Output HTML with KPI widget using abc and chart widget using def

CRITICAL OUTPUT RULES FOR BOARD EDITING:
🚨 WHEN THE USER ASKS FOR BOARD/HTML CHANGES:
1. Output ONLY the complete HTML code - NO explanations before or after
2. Start IMMEDIATELY with <!DOCTYPE html> or the opening tag
3. Do NOT write things like "Here's the updated code:" or "I've made the following changes:"
4. Do NOT wrap in markdown unless you include the full HTML in the code block
5. The ENTIRE response should be valid HTML that can be directly rendered

✅ CORRECT:
```html
<!DOCTYPE html>
<html>
...complete code...
</html>
```

❌ WRONG:
"I've updated the board to add a chart. Here's the new code:
```html
<!DOCTYPE html>
..."

TECHNICAL REQUIREMENTS:
- ALWAYS return complete HTML when editing boards
- Use Chart.js for visualizations, Alpine.js for reactivity, Interact.js for drag/drop
- Widgets MUST have class="widget" and data-lg-x/y/w/h attributes
- Widgets MUST call: x-data="canvasWidget()" x-init="initWidget($el)" (this applies initial position automatically)
- Put all widgets inside <div class="board-canvas">
- Include boardManager() and canvasWidget() functions in <script> section
- boardManager() has findAvailablePosition(width, height) for auto-placement
- Fetch data from /explore endpoint with query_id
- Handle loading and error states with Alpine.js x-show
- NEVER refuse to edit the HTML - that's your primary job!"""

EXPLORATION_SYSTEM_INSTRUCTION = """You are an intelligent data query assistant, like Cursor AI for data. You help write and edit Python code for data transformations.

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
  # @datastore: <datastore_uuid>
  # @query: SELECT * FROM dataset.table_name WHERE condition = '{{arg_name}}'

- Query results are available as pandas DataFrames via 'query_result' variable
- Support common DataFrame operations: pivot, groupby, merge, filter, etc.
- The final result must be assigned to a variable named 'result'

ARGS SYSTEM:
- Queries can accept runtime arguments via the `args` dict (Python variable available at execution time)
- In SQL: Use Jinja2 template syntax: {{arg_name}} for dynamic values
- In Python code: Use args.get('key', default_value) or args['key']
- Args are passed by the frontend/REST API during execution
- Don't require args for basic queries - use them only for optional filters/parameters
- Example SQL with args: SELECT * FROM dataset.events WHERE status = '{{status}}'
- Example Python with args: start_date = args.get('start_date', '2024-01-01')

SCHEMA EXPLORATION - CRITICAL:
Before writing ANY query, you MUST explore the schema thoroughly:
1. Call get_datastore_schema(datastore_id) to see datasets/schemas
2. Call get_datastore_schema(datastore_id, dataset="X") to see tables
3. Call get_datastore_schema(datastore_id, dataset="X", table="Y") to see ACTUAL column names
4. NEVER guess column names - always verify from schema
5. Use the EXACT column names from the schema response

DATA EXPLORATION - MANDATORY BEFORE CREATING QUERIES:
When creating a new query (not just editing), you MUST explore the actual data first:
1. After getting schema, use execute_query_direct() to run simple exploratory queries
2. Check what data actually exists: execute_query_direct(datastore_id, "SELECT * FROM dataset.table LIMIT 5")
3. Understand data ranges/patterns: execute_query_direct(datastore_id, "SELECT MIN(date), MAX(date), COUNT(*) FROM dataset.table")
4. Verify column values: execute_query_direct(datastore_id, "SELECT DISTINCT category FROM dataset.table LIMIT 20")
5. Look at sample aggregations to understand the data shape
6. Use insights from exploratory queries to build the final saved query
7. This ensures your final query returns meaningful, accurate results

QUERY STRATEGY - START SIMPLE:
1. First use execute_query_direct() to explore: "SELECT * FROM dataset.table LIMIT 10"
2. Then write a simple Python query with known columns from exploration
3. Test it with test_query()
4. If it works, add complexity (GROUP BY, WHERE, aggregations)
5. If it fails, go back to execute_query_direct() to verify data structure
6. Check error messages carefully - they often tell you exact column/table names

UNDERSTANDING USER REFERENCES:
- When user says "for BigQuery" or "use the postgres datastore", match by name/type from available datastores
- When user says "for the events query" or "use data from user_activity", match query by name
- Context will show available datastores (name, type, ID) and queries (name, ID, description)
- Always use the UUID from context in @datastore field, never make up UUIDs

TOOLS AVAILABLE:
You have access to these tools to help you write better code:
1. list_datastores() - Get available datastores with IDs
2. list_boards() - Get available boards
3. list_board_queries(board_id) - Get all queries for a specific board
4. get_query_code(query_id) - Get the Python code for a specific query
5. get_board_code(board_id) - Get the HTML/JavaScript code for a specific board
6. get_datastore_schema(datastore_id, dataset?, table?) - ALWAYS use this to explore schema before writing queries
7. execute_query_direct(datastore_id, sql_query, limit?) - Run exploratory SQL queries to understand data (MANDATORY before creating new queries)
8. test_query(python_code) - Test query execution

DATASTORE SELECTION:
- If you need a datastore ID and don't have one, ALWAYS call list_datastores() FIRST
- After getting the list, if there's only one datastore, use it
- If there are multiple, use the most appropriate one based on user context
- The datastore UUIDs must be used in the @datastore field

EXAMPLE:
```python
# @node: source_data
# @type: query
# @datastore: abc-123
# @query: SELECT user_id, score FROM dataset_name.events WHERE created_at >= '{{start_date}}'

# Result is injected as 'query_result' DataFrame
df = query_result

# Access args for additional filtering
min_score = args.get('min_score', 0)
df = df[df['score'] >= min_score]

# @node: aggregate
# @type: transform
df_summary = df.groupby('user_id').agg({'score': 'sum'}).reset_index()

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
- If you need a datastore ID, call list_datastores() first
- ALWAYS explore schema with get_datastore_schema before writing queries
- For BigQuery: ALWAYS use fully qualified table names: dataset.table
- For PostgreSQL: Use schema.table format

BIGQUERY DATA TYPE HANDLING (CRITICAL):
- Real-world data often has quality issues (dates in numeric columns, nulls, mixed types)
- ALWAYS use SAFE_CAST instead of CAST to handle invalid values gracefully
- SAFE_CAST returns NULL for invalid conversions instead of failing
- Example: SAFE_CAST(amount_column AS FLOAT64) instead of CAST(amount_column AS FLOAT64)
- Filter out NULLs if needed: WHERE SAFE_CAST(amount AS FLOAT64) IS NOT NULL
- For string-to-number conversions with special chars, use: SAFE_CAST(REPLACE(REPLACE(value, ',', ''), '-', '') AS FLOAT64)"""

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
                # Legacy support: treat @connector as @datastore
                if not current_node['datastore_id']:
                    current_node['datastore_id'] = line.split('# @connector:')[1].strip()
            elif line.strip().startswith('# @query:'):
                query_line = line.split('# @query:')[1].strip()
                current_node['query'] = query_line
            elif not line.strip().startswith('#') and current_node.get('query'):
                pass  # End of node metadata
    
    if current_node:
        nodes.append(current_node)
    
    return nodes

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
    Datastore ID is optional - will auto-select first available datastore if not provided or invalid.
    Returns: {"result": [...], "error": null} or {"result": null, "error": "error message"}
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
        nodes = parse_python_nodes(python_code)
        
        print(f"DEBUG: Parsed {len(nodes)} nodes")
        
        # 3. Initialize execution context
        full_context = {
            "args": args,
            "pd": pd,
            "json": json,
            **args
        }
        
        # 4. Execute queries for each query node in parallel
        # Helper to check if a string is a valid UUID
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
            
            # Resolve datastore: node datastore_id > request override > first available
            node_ds = node['datastore_id'] if is_valid_uuid(node['datastore_id']) else None
            request_ds = datastore_id if is_valid_uuid(datastore_id) else None
            active_datastore_id = node_ds or request_ds
            
            if not active_datastore_id:
                # Try to get first available datastore
                ds_res = supabase.table("datastores").select("id").limit(1).execute()
                if ds_res.data:
                    active_datastore_id = ds_res.data[0]['id']
                    print(f"DEBUG: Auto-selected first available datastore: {active_datastore_id}")
            
            if not active_datastore_id:
                error_msg = f"No datastore available for query node '{node['name']}'. Please connect a datastore first or provide a valid datastore_id."
                print(f"DEBUG: {error_msg}")
                raise HTTPException(status_code=400, detail=error_msg)
            
            print(f"DEBUG: Executing query for node {node['name']} with datastore {active_datastore_id}")
            try:
                result_data = await _run_query_logic(active_datastore_id, node['query'], full_context)
                # Convert to DataFrame for the execution context
                df = pd.DataFrame(result_data)
                return (node['name'], df)
            except Exception as e:
                print(f"DEBUG: Query error for node {node['name']}: {str(e)}")
                raise HTTPException(status_code=400, detail=f"Query error in node {node['name']}: {str(e)}")
        
        # Execute all query nodes in parallel
        query_tasks = [execute_node(node) for node in nodes]
        query_results = await asyncio.gather(*query_tasks)
        
        # Add results to context
        for result in query_results:
            if result:
                node_name, df = result
                full_context['query_result'] = df  # Last one wins, for backwards compatibility
                full_context[node_name] = df
        
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
            "result": final_table,
            "error": None
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
    """
    Extract code from markdown code blocks or raw text.
    Handles cases where AI returns explanatory text with code.
    """
    if not raw:
        return ""
    
    trimmed = raw.strip()
    
    # Strategy 1: Look for ```html or ``` code fences
    code_blocks = []
    
    # Find all code blocks (```html...``` or ```...```)
    import re
    pattern = r'```(?:html)?\s*\n(.*?)```'
    matches = re.findall(pattern, trimmed, re.DOTALL)
    
    for match in matches:
        code_blocks.append(match.strip())
    
    # Strategy 2: If no code blocks found, look for HTML directly (<!DOCTYPE or <html)
    if not code_blocks:
        # Look for HTML content even without code fences
        html_start = -1
        if '<!DOCTYPE' in trimmed:
            html_start = trimmed.find('<!DOCTYPE')
        elif '<html' in trimmed.lower():
            html_start = trimmed.lower().find('<html')
        
        if html_start != -1:
            # Extract from HTML start to end
            html_content = trimmed[html_start:]
            # Try to find the closing </html> tag
            html_end = html_content.lower().rfind('</html>')
            if html_end != -1:
                code_blocks.append(html_content[:html_end + 7].strip())
            else:
                code_blocks.append(html_content.strip())
    
    # Strategy 3: Return the largest code block (most likely to be the actual code)
    if code_blocks:
        # Filter out blocks that are clearly just text/explanations (too short, no HTML tags)
        valid_blocks = [
            block for block in code_blocks 
            if len(block) > 50 and ('<' in block or 'DOCTYPE' in block)
        ]
        
        if valid_blocks:
            # Return the longest block (most complete code)
            return max(valid_blocks, key=len)
        elif code_blocks:
            return max(code_blocks, key=len)
    
    # Strategy 4: Fallback - if it looks like HTML, return as-is
    if '<!DOCTYPE' in trimmed or '<html' in trimmed.lower():
        return trimmed
    
    # No code found, return original
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
            user_message = f"User request: {user_prompt}\n\nGenerate a new board HTML from scratch that fulfills this request, using the board builder patterns (Alpine.js, boardManager, canvasWidget, KPI/chart widgets). Use a clean grid layout: position widgets with 20px gaps, e.g., KPIs at x='0', x='320', x='640' and charts below at y='200' or y='520'."
        
        # Build conversation history
        chat_history = chat[-50:] if chat else []
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
                "maxOutputTokens": 65536,
                "responseMimeType": "text/plain"
            }
        }
        
        response = requests.post(
            GEMINI_URL,
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            json=payload,
            timeout=120  # Increased from 60 seconds
        )
        
        if not response.ok:
            raise HTTPException(status_code=502, detail=f"Gemini API error: {response.text}")
        
        data = response.json()
        candidate = data.get("candidates", [{}])[0]
        finish_reason = candidate.get("finishReason", "")
        raw_text = candidate.get("content", {}).get("parts", [{}])[0].get("text", "")
        
        # Check for MAX_TOKENS
        if finish_reason == "MAX_TOKENS":
            raise HTTPException(status_code=502, detail="Response was too long. Try breaking your request into smaller tasks or use the streaming endpoint (/board-helper-stream) which can handle longer responses.")
        
        # Check for blocked responses
        if finish_reason in ("SAFETY", "RECITATION", "OTHER"):
            raise HTTPException(status_code=502, detail=f"Gemini response was blocked ({finish_reason}). Try rephrasing your request.")
        
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

async def _get_board_code(board_id: str) -> Dict[str, Any]:
    """Get the HTML/JavaScript code for a specific board."""
    try:
        # Get board info
        board_res = supabase.table("boards").select("id, name").eq("id", board_id).single().execute()
        if not board_res.data:
            return {"error": "Board not found"}
        
        # Get latest board code
        code_res = supabase.table("board_code").select("code").eq("board_id", board_id).order("version", desc=True).limit(1).maybeSingle().execute()
        
        return {
            "id": board_res.data["id"],
            "name": board_res.data["name"],
            "code": code_res.data["code"] if code_res.data else ""
        }
    except Exception as e:
        return {"error": str(e)}

async def _create_or_update_query(board_id: str, query_name: str, python_code: str, description: str = "", query_id: Optional[str] = None) -> Dict[str, Any]:
    """Create a new query or update an existing one."""
    try:
        if query_id:
            # Update existing query
            res = supabase.table("board_queries").update({
                "name": query_name,
                "python_code": python_code,
                "description": description,
                "updated_at": "now()"
            }).eq("id", query_id).execute()
            
            if res.data:
                return {
                    "success": True,
                    "action": "updated",
                    "query_id": query_id,
                    "name": query_name,
                    "message": f"Query '{query_name}' updated successfully"
                }
            else:
                return {"error": f"Update failed - no data returned. Response: {res}"}
        else:
            # Create new query
            print(f"DEBUG: Creating query with board_id={board_id}, name={query_name}")
            res = supabase.table("board_queries").insert({
                "board_id": board_id,
                "name": query_name,
                "python_code": python_code,
                "description": description
            }).execute()
            
            print(f"DEBUG: Insert response: {res}")
            
            if res.data and len(res.data) > 0:
                return {
                    "success": True,
                    "action": "created",
                    "query_id": res.data[0]["id"],
                    "name": query_name,
                    "message": f"Query '{query_name}' created successfully"
                }
            else:
                return {"error": f"Insert failed - no data returned. Response: {res}"}
        
        return {"error": "Failed to save query - unknown error"}
    except Exception as e:
        print(f"DEBUG: Exception in _create_or_update_query: {str(e)}")
        return {"error": str(e)}

async def _delete_query(query_id: str) -> Dict[str, Any]:
    """Delete a query."""
    try:
        res = supabase.table("board_queries").delete().eq("id", query_id).execute()
        return {
            "success": True,
            "message": f"Query deleted successfully"
        }
    except Exception as e:
        return {"error": str(e)}

async def _get_datastore_schema(datastore_id: str, dataset: Optional[str] = None, table: Optional[str] = None) -> Dict[str, Any]:
    """Get schema information for a datastore."""
    try:
        # Get datastore info
        res = supabase.table("datastores").select("*").eq("id", datastore_id).single().execute()
        if not res.data:
            return {"error": "Datastore not found"}
        
        datastore = res.data
        
        # Get schema based on datastore type
        if datastore["type"] == "bigquery":
            schema_result = await _get_bigquery_schema(datastore, dataset, table)
            return {
                "success": True,
                "datastore_id": datastore_id,
                "datastore_name": datastore.get("name"),
                "type": "bigquery",
                "schema": schema_result
            }
        elif datastore["type"] == "postgres":
            schema_result = await _get_postgres_schema(datastore, dataset, table)
            return {
                "success": True,
                "datastore_id": datastore_id,
                "datastore_name": datastore.get("name"),
                "type": "postgres",
                "schema": schema_result
            }
        else:
            return {"error": f"Unsupported datastore type: {datastore['type']}"}
    except Exception as e:
        return {"error": str(e)}

async def _test_query(python_code: str, limit_rows: int = 5, test_args: Dict[str, Any] = None) -> Dict[str, Any]:
    """Test execute a query and return first few rows or error."""
    try:
        # Parse the code to extract nodes
        nodes = parse_python_nodes(python_code)
        
        # Execute query nodes and get result
        test_args = test_args or {}
        full_context = {"pd": pd, "json": json, "args": test_args, **test_args}
        
        for node in nodes:
            if node["type"] == "query":
                ds_id = node.get("datastore_id")
                if not ds_id:
                    return {"error": f"Node '{node['name']}' missing @datastore (use # @datastore: <uuid>)"}
                
                # Get datastore
                ds_res = supabase.table("datastores").select("*").eq("id", ds_id).single().execute()
                if not ds_res.data:
                    return {"error": f"Datastore {ds_id} not found"}
                
                datastore = ds_res.data
                query_template = node.get("query", "")
                
                # Render query with Jinja2 (using test args)
                template = Template(query_template)
                rendered_query = template.render(**test_args)
                
                # Execute query
                if datastore["type"] == "bigquery":
                    client = get_bigquery_client(datastore["config"])
                    query_job = client.query(rendered_query)
                    df = query_job.to_dataframe()
                elif datastore["type"] == "postgres":
                    import sqlalchemy as sa
                    conn_str = datastore["config"].get("connection_string")
                    engine = sa.create_engine(conn_str)
                    df = pd.read_sql(rendered_query, engine)
                else:
                    return {"error": f"Unsupported datastore type: {datastore['type']}"}
                
                full_context['query_result'] = df
                full_context[node['name']] = df
        
        # Execute full code
        exec(python_code, {}, full_context)
        result_df = full_context.get('result')
        
        if result_df is None:
            return {"error": "Code did not produce a 'result' variable"}
        
        # Convert to dict for JSON serialization
        if isinstance(result_df, pd.DataFrame):
            # Clean data for JSON serialization (handles Decimal, datetime, NaN, etc.)
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
        
        # Add helpful hints for common BigQuery errors
        if "Invalid NUMERIC value" in error_str or "Invalid FLOAT" in error_str or "Invalid INT" in error_str:
            error_msg += "\n\n💡 DATA QUALITY ISSUE: This column contains mixed data types (e.g., dates or text where numbers are expected). Solution: Use SAFE_CAST instead of CAST. Example: SAFE_CAST(column AS FLOAT64) returns NULL for invalid values instead of failing. You can also filter: WHERE SAFE_CAST(column AS FLOAT64) IS NOT NULL"
        
        return {
            "success": False,
            "error": error_str,
            "message": error_msg
        }

async def _execute_query_direct(datastore_id: str, sql_query: str, limit: int = 100) -> Dict[str, Any]:
    """Execute a SQL query directly on a datastore and return results."""
    try:
        # Enforce max limit
        limit = min(limit, 1000)
        
        # Get datastore
        ds_res = supabase.table("datastores").select("*").eq("id", datastore_id).single().execute()
        if not ds_res.data:
            return {
                "success": False,
                "error": f"Datastore {datastore_id} not found"
            }
        
        datastore = ds_res.data
        
        # Execute query based on datastore type
        if datastore["type"] == "bigquery":
            client = get_bigquery_client(datastore["config"])
            query_job = client.query(sql_query)
            df = query_job.to_dataframe()
        elif datastore["type"] == "postgres":
            import sqlalchemy as sa
            conn_str = datastore["config"].get("connection_string")
            engine = sa.create_engine(conn_str)
            df = pd.read_sql(sql_query, engine)
        else:
            return {
                "success": False,
                "error": f"Unsupported datastore type: {datastore['type']}"
            }
        
        # Convert to dict for JSON serialization
        total_rows = len(df)
        limited_df = df.head(limit)
        
        # Clean data for JSON serialization (handles Decimal, datetime, NaN, etc.)
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
        
        # Add helpful hints for common BigQuery errors
        if "Invalid NUMERIC value" in error_str or "Invalid FLOAT" in error_str or "Invalid INT" in error_str:
            error_msg += "\n\n💡 DATA QUALITY ISSUE: This column contains mixed data types. Try using SAFE_CAST instead of CAST, or use execute_query_direct to inspect the column values: SELECT DISTINCT problematic_column LIMIT 20"
        
        return {
            "success": False,
            "error": error_str,
            "message": error_msg
        }

    """Update board HTML code."""
    try:
        # Get current max version
        version_res = supabase.table("board_code").select("version").eq("board_id", board_id).order("version", desc=True).limit(1).maybeSingle().execute()
        
        next_version = 1
        if version_res.data:
            next_version = version_res.data["version"] + 1
        
        # Insert new version
        res = supabase.table("board_code").insert({
            "board_id": board_id,
            "code": html_code,
            "version": next_version
        }).execute()
        
        if res.data:
            return {
                "success": True,
                "board_id": board_id,
                "version": next_version,
                "message": f"Board code updated to version {next_version}"
            }
        
        return {"error": "Failed to update board code"}
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
        },
        {
            "name": "get_board_code",
            "description": "Get the dashboard code for a specific board. Returns the board's ID, name, and full code. Use this when you need to see the current board implementation or when referencing another board.",
            "parameters": {
                "type": "object",
                "properties": {
                    "board_id": {
                        "type": "string",
                        "description": "The UUID of the board to get code for"
                    }
                },
                "required": ["board_id"]
            }
        },
        {
            "name": "create_or_update_query",
            "description": "Create a new query or update an existing query on a board. Use this when the user asks to 'create a query', 'make a query', 'add a query', or 'update a query'. Returns success status and query ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "board_id": {
                        "type": "string",
                        "description": "The UUID of the board this query belongs to"
                    },
                    "query_name": {
                        "type": "string",
                        "description": "Name of the query (e.g., 'Sales Analysis', 'Revenue Report')"
                    },
                    "python_code": {
                        "type": "string",
                        "description": "The complete Python code for the query including @node comments"
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional description of what the query does"
                    },
                    "query_id": {
                        "type": "string",
                        "description": "Optional: UUID of existing query to update. Omit to create new query."
                    }
                },
                "required": ["board_id", "query_name", "python_code"]
            }
        },
        {
            "name": "delete_query",
            "description": "Delete a query from a board. Use this when the user asks to 'delete a query', 'remove a query', or 'delete the X query'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query_id": {
                        "type": "string",
                        "description": "The UUID of the query to delete"
                    }
                },
                "required": ["query_id"]
            }
        },
        {
            "name": "get_datastore_schema",
            "description": "Get schema information (datasets, tables, columns) for a datastore. ALWAYS call this BEFORE creating a query to know what tables and columns are available. Returns dataset/schema names, table names, and column information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "datastore_id": {
                        "type": "string",
                        "description": "The UUID of the datastore to get schema for"
                    },
                    "dataset": {
                        "type": "string",
                        "description": "Optional: specific dataset/schema name to get detailed info for (BigQuery dataset or PostgreSQL schema)"
                    },
                    "table": {
                        "type": "string",
                        "description": "Optional: specific table name to get column details for (requires dataset parameter)"
                    }
                },
                "required": ["datastore_id"]
            }
        },
        {
            "name": "test_query",
            "description": "Test execute a query to validate it works and see sample results. ALWAYS call this AFTER creating a query to verify it works correctly. Returns first 5 rows on success or error details on failure. If you get 'Invalid NUMERIC value' or CAST errors, use SAFE_CAST instead and call execute_query_direct to inspect the problematic column.",
            "parameters": {
                "type": "object",
                "properties": {
                    "python_code": {
                        "type": "string",
                        "description": "The complete Python query code to test (with @node comments)"
                    }
                },
                "required": ["python_code"]
            }
        },
        {
            "name": "execute_query_direct",
            "description": "Execute a SQL query directly on a datastore and get results. CRITICAL: Use this BEFORE creating new queries to explore and understand the data (see actual values, date ranges, patterns, data types). Use when you get CAST errors to inspect actual column values (SELECT DISTINCT column LIMIT 20). Also use for answering user questions with data, or running ad-hoc queries without saving them. Returns up to 100 rows by default. Perfect for 'show me...', 'how many...', 'what are...' type questions and for debugging data quality issues.",
            "parameters": {
                "type": "object",
                "properties": {
                    "datastore_id": {
                        "type": "string",
                        "description": "The UUID of the datastore to run the query on"
                    },
                    "sql_query": {
                        "type": "string",
                        "description": "The SQL query to execute (plain SQL, not Python code)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Optional: Maximum number of rows to return (default: 100, max: 1000)"
                    }
                },
                "required": ["datastore_id", "sql_query"]
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
    board_id: Optional[str] = Body(default=None),
    datastore_id: Optional[str] = Body(default=None),
    query_id: Optional[str] = Body(default=None),
    # Settings
    max_tool_iterations: int = Body(default=200),  # Increased from 25 -> 50 -> 200
    temperature: float = Body(default=0.3),
    max_output_tokens: int = Body(default=65536)
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
                    code, user_prompt, chat, api_key, datastore_id, query_id, board_id,
                    max_tool_iterations, temperature, max_output_tokens
                ):
                    yield f"data: {json.dumps(event)}\n\n"
            else:
                # Board helper streaming
                yield f"data: {json.dumps({'type': 'thinking', 'content': 'Analyzing your request...'})}\n\n"
                await asyncio.sleep(0.1)
                
                # Generate code using existing logic
                system_instruction = BOARD_SYSTEM_INSTRUCTION
                
                # Add context information if board_id is provided
                context_info = ""
                if board_id:
                    context_info = f"\n\n=== CURRENT CONTEXT ===\nCURRENT_BOARD_ID = '{board_id}'\n(Use this value for the board_id parameter in create_or_update_query)\n"
                    
                    # Fetch board queries to provide context
                    board_queries = await _get_board_queries(board_id)
                    is_continuation = len(chat) > 0
                    if board_queries:
                        context_info += f"\nAvailable queries on this board:\n"
                        for q in board_queries:
                            context_info += f"- {q['name']} (ID: {q['id']}): {q.get('description', 'No description')}\n"
                        
                        # For continuation messages, include actual query code so AI can troubleshoot
                        if is_continuation:
                            context_info += f"\n--- QUERY CODE (for troubleshooting) ---\n"
                            for q in board_queries:
                                query_detail = await _get_query_code(q['id'])
                                if 'code' in query_detail:
                                    context_info += f"\n[{q['name']}] (query_id: {q['id']}):\n{query_detail['code']}\n"
                            context_info += f"--- END QUERY CODE ---\n"
                    
                    # Also fetch available datastores for context
                    datastores = await _get_available_datastores()
                    if datastores:
                        context_info += f"\nAvailable datastores:\n"
                        for ds in datastores:
                            context_info += f"- {ds['name']} (Type: {ds['type']}, ID: {ds['id']})\n"
                        context_info += "(Use the ID value for @datastore in query code)\n"
                    
                    context_info += "===================\n"
                    system_instruction = system_instruction + context_info
                
                user_message = f"User request: {user_prompt}"
                if code:
                    user_message += f"\n\nNote: Current board code is available if needed."
                
                # Add context hints for very short/vague messages
                if len(user_prompt.strip()) < 30 and board_queries:
                    user_message += f"\n\nNote: There are {len(board_queries)} queries on this board. If the request is unclear, use list_board_queries and get_query_code to understand which query the user wants to modify."
                
                chat_history = chat[-50:] if chat else []
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
                    "tools": GEMINI_TOOLS,
                    "generationConfig": {
                        "temperature": 0.3,
                        "maxOutputTokens": 65536,
                        "responseMimeType": "text/plain"
                    }
                }
                
                yield f"data: {json.dumps({'type': 'progress', 'content': '🤖 Processing your request...'})}\n\n"
                
                # Function calling loop (similar to exploration)
                # Use parameter value, not hardcoded
                tool_iteration = 0
                edited_code = None
                raw_text = ""  # Initialize to prevent UnboundLocalError
                query_created = False  # Track if we created a query
                any_tools_called = False  # Track if any tools were called across iterations
                last_tool_results = []  # Track last tool results for fallback
                
                while tool_iteration < max_tool_iterations:
                    tool_iteration += 1
                    
                    # Warn when approaching iteration limit
                    if tool_iteration == max_tool_iterations - 5:
                        yield f"data: {json.dumps({'type': 'progress', 'content': f'⚠️ Approaching iteration limit ({tool_iteration}/{max_tool_iterations})...'})}\n\n"
                    
                    response = requests.post(
                        GEMINI_URL,
                        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                        json=payload,
                        timeout=120  # Increased from 60 seconds
                    )
                    
                    if not response.ok:
                        yield f"data: {json.dumps({'type': 'error', 'content': f'Gemini API error: {response.text}'})}\n\n"
                        return
                    
                    data = response.json()
                    candidate = data.get("candidates", [{}])[0]
                    finish_reason = candidate.get("finishReason", "")
                    content = candidate.get("content", {})
                    parts = content.get("parts", [])
                    
                    # Check for blocked responses
                    if finish_reason in ("SAFETY", "RECITATION", "OTHER"):
                        print(f"DEBUG: Gemini response blocked: finishReason={finish_reason}")
                        yield f"data: {json.dumps({'type': 'error', 'content': f'Gemini response was blocked ({finish_reason}). Try rephrasing your request.'})}\n\n"
                        return
                    
                    # Check for function calls
                    function_calls = [p for p in parts if "functionCall" in p]
                    
                    if function_calls:
                        any_tools_called = True
                        last_tool_results = []
                        # Execute function calls
                        function_responses = []
                        for fc in function_calls:
                            func_name = fc["functionCall"]["name"]
                            
                            # Send tool call started event
                            yield f"data: {json.dumps({'type': 'tool_call', 'tool': func_name, 'status': 'started', 'args': fc['functionCall'].get('args', {})})}\n\n"
                            
                            if func_name == "list_datastores":
                                result = await _get_available_datastores()
                                yield f"data: {json.dumps({'type': 'progress', 'content': f'✓ Found {len(result)} datastores'})}\n\n"
                            elif func_name == "list_boards":
                                result = await _get_available_boards()
                                yield f"data: {json.dumps({'type': 'progress', 'content': f'✓ Found {len(result)} boards'})}\n\n"
                            elif func_name == "list_board_queries":
                                board_id_arg = fc["functionCall"].get("args", {}).get("board_id")
                                result = await _get_board_queries(board_id_arg) if board_id_arg else []
                                yield f"data: {json.dumps({'type': 'progress', 'content': f'✓ Found {len(result)} queries'})}\n\n"
                            elif func_name == "get_query_code":
                                query_id_arg = fc["functionCall"].get("args", {}).get("query_id")
                                result = await _get_query_code(query_id_arg) if query_id_arg else {"error": "Missing query_id"}
                                if "error" not in result:
                                    query_name = result.get("name", "query")
                                    yield f"data: {json.dumps({'type': 'progress', 'content': f'✓ Retrieved code for {query_name}'})}\n\n"
                                else:
                                    error_msg = result["error"]
                                    yield f"data: {json.dumps({'type': 'progress', 'content': f'✗ {error_msg}'})}\n\n"
                            elif func_name == "get_board_code":
                                board_id_arg = fc["functionCall"].get("args", {}).get("board_id")
                                result = await _get_board_code(board_id_arg) if board_id_arg else {"error": "Missing board_id"}
                                if "error" not in result:
                                    board_name = result.get("name", "board")
                                    yield f"data: {json.dumps({'type': 'progress', 'content': f'✓ Retrieved code for {board_name}'})}\n\n"
                                else:
                                    error_msg = result["error"]
                                    yield f"data: {json.dumps({'type': 'progress', 'content': f'✗ {error_msg}'})}\n\n"
                            elif func_name == "create_or_update_query":
                                args = fc["functionCall"].get("args", {})
                                result = await _create_or_update_query(
                                    board_id=args.get("board_id"),
                                    query_name=args.get("query_name"),
                                    python_code=args.get("python_code"),
                                    description=args.get("description", ""),
                                    query_id=args.get("query_id")
                                )
                                if result.get("success"):
                                    yield f"data: {json.dumps({'type': 'tool_result', 'tool': 'create_or_update_query', 'status': 'success', 'result': result})}\n\n"
                                    query_created = True  # Mark that we created a query
                                else:
                                    err = result.get("error", "Failed to save query")
                                    yield f"data: {json.dumps({'type': 'tool_result', 'tool': 'create_or_update_query', 'status': 'error', 'error': str(err)})}\n\n"
                            elif func_name == "delete_query":
                                query_id_arg = fc["functionCall"].get("args", {}).get("query_id")
                                result = await _delete_query(query_id_arg) if query_id_arg else {"error": "Missing query_id"}
                                if result.get("success"):
                                    yield f"data: {json.dumps({'type': 'tool_result', 'tool': 'delete_query', 'status': 'success', 'result': result})}\n\n"
                                    query_created = True  # Mark that we did query operations
                                else:
                                    err = result.get("error", "Failed to delete query")
                                    yield f"data: {json.dumps({'type': 'tool_result', 'tool': 'delete_query', 'status': 'error', 'error': str(err)})}\n\n"
                            elif func_name == "get_datastore_schema":
                                args = fc["functionCall"].get("args", {})
                                result = await _get_datastore_schema(
                                    datastore_id=args.get("datastore_id"),
                                    dataset=args.get("dataset"),
                                    table=args.get("table")
                                )
                                if result.get("success"):
                                    yield f"data: {json.dumps({'type': 'tool_result', 'tool': 'get_datastore_schema', 'status': 'success', 'result': result})}\n\n"
                                else:
                                    err = result.get("error", "Failed to get schema")
                                    yield f"data: {json.dumps({'type': 'tool_result', 'tool': 'get_datastore_schema', 'status': 'error', 'error': err})}\n\n"
                            elif func_name == "test_query":
                                args = fc["functionCall"].get("args", {})
                                result = await _test_query(args.get("python_code", ""))
                                if result.get("success"):
                                    row_count = result.get("row_count", 0)
                                    if row_count == 0:
                                        result["warning"] = "ZERO ROWS RETURNED. This likely means the query has incorrect column names, table names, or filter conditions. You MUST investigate: check the schema, try a broader query (SELECT * FROM table LIMIT 10), and fix the issue."
                                    yield f"data: {json.dumps({'type': 'tool_result', 'tool': 'test_query', 'status': 'success', 'result': result})}\n\n"
                                else:
                                    err = result.get("error", "Test failed")
                                    yield f"data: {json.dumps({'type': 'tool_result', 'tool': 'test_query', 'status': 'error', 'error': err})}\n\n"
                            elif func_name == "execute_query_direct":
                                args = fc["functionCall"].get("args", {})
                                result = await _execute_query_direct(
                                    datastore_id=args.get("datastore_id", ""),
                                    sql_query=args.get("sql_query", ""),
                                    limit=args.get("limit", 100)
                                )
                                if result.get("success"):
                                    row_count = result.get("returned_rows", 0)
                                    total = result.get("total_rows", 0)
                                    truncated = result.get("truncated", False)
                                    msg = f"✓ Executed query: {row_count} rows returned"
                                    if truncated:
                                        msg += f" (truncated from {total} total rows)"
                                    yield f"data: {json.dumps({'type': 'progress', 'content': msg})}\n\n"
                                    yield f"data: {json.dumps({'type': 'tool_result', 'tool': 'execute_query_direct', 'status': 'success', 'result': result})}\n\n"
                                else:
                                    err = result.get("error", "Query execution failed")
                                    yield f"data: {json.dumps({'type': 'progress', 'content': f'✗ Query failed: {err}'})}\n\n"
                                    yield f"data: {json.dumps({'type': 'tool_result', 'tool': 'execute_query_direct', 'status': 'error', 'error': err})}\n\n"
                            else:
                                result = {"error": f"Unknown function: {func_name}"}
                            
                            last_tool_results.append({"tool": func_name, "result": result})
                            function_responses.append({
                                "functionResponse": {
                                    "name": func_name,
                                    "response": {"result": result}
                                }
                            })
                        
                        # Add function responses to conversation
                        contents.append({"role": "model", "parts": function_calls})
                        contents.append({"role": "user", "parts": function_responses})
                        
                        # Check if any test_query returned 0 rows - nudge AI to investigate
                        zero_row_tests = [r for r in last_tool_results 
                                         if r["tool"] == "test_query" 
                                         and r["result"].get("success") 
                                         and r["result"].get("row_count", 0) == 0]
                        if zero_row_tests:
                            yield f"data: {json.dumps({'type': 'progress', 'content': '⚠️ Query returned 0 rows - investigating...'})}\n\n"
                            contents.append({"role": "user", "parts": [{"text": "WARNING: The test_query returned 0 rows. This is NOT acceptable - the user expects data. You MUST investigate why: 1) Call get_datastore_schema to check actual table/column names, 2) Try a simple SELECT * FROM dataset.table LIMIT 10 via test_query to confirm data exists, 3) Fix the query with correct names/filters, 4) Test again, 5) Update the saved query with create_or_update_query using the query_id."}]})
                        
                        payload["contents"] = contents
                        
                        # Continue loop
                        continue
                    
                    # Extract text response - check all parts for text
                    raw_text = ""
                    for p in parts:
                        if "text" in p:
                            raw_text += p["text"]
                    
                    # Handle MAX_TOKENS - continue the generation
                    if finish_reason == "MAX_TOKENS":
                        if tool_iteration < max_tool_iterations:
                            yield f"data: {json.dumps({'type': 'progress', 'content': '⏩ Continuing generation (hit token limit)...'})}\n\n"
                            
                            # Add the partial response and request continuation
                            contents.append({
                                "role": "model",
                                "parts": [{"text": raw_text}] if raw_text else parts
                            })
                            contents.append({
                                "role": "user",
                                "parts": [{"text": "Please continue from where you left off. Output the rest of the code or response."}]
                            })
                            payload["contents"] = contents
                            continue
                        else:
                            yield f"data: {json.dumps({'type': 'error', 'content': 'Response was too long and hit iteration limit. Try breaking your request into smaller tasks.'})}\n\n"
                            return
                    
                    if not raw_text:
                        # If tools were called, Gemini may need a nudge to continue its task
                        if any_tools_called and tool_iteration < max_tool_iterations:
                            yield f"data: {json.dumps({'type': 'progress', 'content': '🔄 Continuing...'})}\n\n"
                            
                            # Provide specific guidance based on what was done
                            if query_created:
                                nudge_text = "You successfully created/updated a query. Now provide a brief, helpful summary to the user explaining: 1) What query was created/updated, 2) What data it shows, 3) How they can use it. Keep it concise and friendly (2-3 sentences max)."
                            else:
                                nudge_text = "Continue with the original task. Use the information from the tool calls above to complete the user's request. If you need to create or update a query, call create_or_update_query now. If you need to generate board HTML, output it now."
                            
                            contents.append({"role": "user", "parts": [{"text": nudge_text}]})
                            payload["contents"] = contents
                            continue
                        
                        # No tools called and no response - give it ONE chance to use tools before erroring
                        if not any_tools_called and tool_iteration == 1:
                            yield f"data: {json.dumps({'type': 'progress', 'content': '🔍 Gathering context...'})}\n\n"
                            
                            # Nudge to use tools to gather context
                            nudge_text = "The user's request may be unclear or vague. Before responding, you MUST gather context by calling the appropriate tools:\n\n"
                            nudge_text += "1. Call list_board_queries(board_id) to see what queries exist\n"
                            nudge_text += "2. If modifying a query, call get_query_code(query_id) to see its current code\n"
                            nudge_text += "3. Use the information from these tools to understand what the user wants\n"
                            nudge_text += "4. Then complete the user's request\n\n"
                            nudge_text += "DO NOT refuse to help or ask for clarification - use the tools to figure it out!"
                            
                            contents.append({"role": "user", "parts": [{"text": nudge_text}]})
                            payload["contents"] = contents
                            continue
                        
                        # Still no tools called after nudge - provide helpful error
                        if not any_tools_called:
                            # Check if finish_reason gives us more info
                            error_details = ""
                            if finish_reason == "MAX_TOKENS":
                                error_details = " (Response was too long)"
                            elif finish_reason == "STOP":
                                error_details = " (AI chose not to respond - your message may be too vague)"
                            
                            print(f"DEBUG: Gemini returned no content. finish_reason={finish_reason}, any_tools={any_tools_called}")
                            
                            yield f"data: {json.dumps({'type': 'error', 'content': f'I could not understand your request{error_details}. Please provide more context, such as:\\n\\n• Which query do you want to modify?\\n• What should be limited to 100 rows?\\n• What should the data be sorted by?\\n\\nTip: Try being more specific, like \"limit the sales query to top 100 by amount\"'})}\n\n"
                            return
                        
                        # Exhausted retries after tools
                        yield f"data: {json.dumps({'type': 'final', 'code': '', 'message': 'Tools executed but could not generate a final response. Please try again with more details.'})}\n\n"
                        return
                    
                    edited_code = strip_markdown_code_block(raw_text.strip())
                    
                    # Validate that we actually got HTML code, not just explanatory text
                    is_html = edited_code and ('<!DOCTYPE' in edited_code or '<html' in edited_code.lower())
                    is_explanation = len(edited_code) < 100 or (not '<' in edited_code)
                    
                    # If AI returned explanatory text instead of HTML, nudge it to output HTML
                    if not is_html or is_explanation:
                        if tool_iteration < max_tool_iterations:
                            yield f"data: {json.dumps({'type': 'progress', 'content': '⚠️ Received text instead of HTML, requesting code...'})}\n\n"
                            
                            contents.append({
                                "role": "model",
                                "parts": [{"text": raw_text}]
                            })
                            contents.append({
                                "role": "user",
                                "parts": [{"text": "You provided explanatory text, but I need the actual HTML code. Please output ONLY the complete HTML code (starting with <!DOCTYPE html>) without any explanations. Output the code now."}]
                            })
                            payload["contents"] = contents
                            continue
                        else:
                            yield f"data: {json.dumps({'type': 'error', 'content': 'AI returned explanatory text instead of HTML code. Please try rephrasing your request.'})}\n\n"
                            return
                    
                    break
                
                # If we created a query, send final response without HTML validation
                if query_created:
                    final_message = raw_text.strip() if raw_text else "Query operation completed successfully."
                    yield f"data: {json.dumps({'type': 'final', 'code': '', 'message': final_message})}\n\n"
                    return
                
                if not edited_code:
                    yield f"data: {json.dumps({'type': 'error', 'content': 'Failed to generate code'})}\n\n"
                    return
                
                # Check if we hit iteration limit
                if tool_iteration >= max_tool_iterations:
                    print(f"WARNING: Hit iteration limit ({max_tool_iterations}) - task may be incomplete")
                    yield f"data: {json.dumps({'type': 'progress', 'content': f'⚠️ Reached iteration limit ({max_tool_iterations}). Code may be incomplete.'})}\n\n"
                
                yield f"data: {json.dumps({'type': 'progress', 'content': f'✓ Code generated ({len(edited_code)} characters)'})}\n\n"
                
                # Validate HTML
                yield f"data: {json.dumps({'type': 'thinking', 'content': 'Validating code structure...'})}\n\n"
                await asyncio.sleep(0.1)
                yield f"data: {json.dumps({'type': 'progress', 'content': '🔍 Checking code...'})}\n\n"
                
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
                print(f"DEBUG: Stream completed successfully after {tool_iteration} iterations")
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"ERROR: Stream failed with exception: {str(e)}")
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
    
    return StreamingResponse(generate_stream(), media_type="text/event-stream")


async def _exploration_helper_stream(
    code: str,
    user_prompt: str,
    chat: List[Dict[str, str]],
    api_key: str,
    datastore_id: Optional[str],
    query_id: Optional[str],
    board_id: Optional[str] = None,
    max_tool_iterations: int = 200,  # Increased from 25 -> 50 -> 200
    temperature: float = 0.3,
    max_output_tokens: int = 65536
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Streaming version of query helper with function calling support.
    Tries up to 5 times, progressively simplifying queries on failure.
    Yields events: thinking, progress, code_delta, test_result, final, needs_user_input
    """
    max_attempts = 5
    
    # Step 1: Deep schema discovery
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
                    
                    # Build rich schema info with tables and columns
                    schema_parts = [f"BigQuery project datasets:"]
                    for ds in datasets:
                        tables = ds.get('tables', [])
                        schema_parts.append(f"\nDataset: {ds['name']} ({len(tables)} tables)")
                        for t in tables[:20]:
                            schema_parts.append(f"  - {ds['name']}.{t['name']}")
                    schema_info = "\n".join(schema_parts)
                    
                    yield {"type": "progress", "content": f"✓ Found {len(datasets)} BigQuery datasets with tables"}
                    
                elif datastore["type"] == "postgres":
                    schema_result = await _get_postgres_schema(datastore, None, None)
                    schemas = schema_result.get('schemas', [])
                    schema_parts = [f"PostgreSQL schemas:"]
                    for s in schemas:
                        schema_parts.append(f"  - {s['name']}")
                    schema_info = "\n".join(schema_parts)
                    yield {"type": "progress", "content": f"✓ Found {len(schemas)} PostgreSQL schemas"}
        except Exception as e:
            yield {"type": "progress", "content": f"⚠️ Schema fetch failed: {str(e)}"}
    
    # Add context information
    context_info = ""
    
    # Always fetch available datastores for context
    try:
        datastores = await _get_available_datastores()
        if datastores:
            context_info += f"\n\nAvailable datastores:\n"
            for ds in datastores:
                context_info += f"- {ds['name']} (Type: {ds['type']}, ID: {ds['id']})\n"
            context_info += "\nWhen user mentions a datastore by name (e.g., 'BigQuery prod', 'PostgreSQL analytics'), use the corresponding ID in the @datastore field."
    except Exception as e:
        yield {"type": "progress", "content": f"⚠️ Could not fetch datastores: {str(e)}"}
    
    if board_id:
        try:
            board_queries = await _get_board_queries(board_id)
            if board_queries:
                context_info += f"\n\nCONTEXT: You are working on a query for board ID '{board_id}'. Other queries on this board:\n"
                for q in board_queries:
                    if query_id and q['id'] == query_id:
                        continue  # Skip the current query
                    context_info += f"- {q['name']} (ID: {q['id']}): {q.get('description', 'No description')}\n"
                context_info += "\nWhen user mentions a query by name, you can reference it using get_query_code(query_id)."
        except Exception as e:
            yield {"type": "progress", "content": f"⚠️ Could not fetch board context: {str(e)}"}
    
    # Get current query info if query_id is provided
    if query_id:
        try:
            query_res = await supabase.table("board_queries").select("name, description, board_id").eq("id", query_id).maybeSingle().execute()
            if query_res.data:
                query_name = query_res.data.get("name", "query")
                context_info = f"\n\nCONTEXT: You are editing query '{query_name}' (ID: {query_id})." + context_info
                # Get board_id from query if not provided
                if not board_id and query_res.data.get("board_id"):
                    board_id = query_res.data["board_id"]
                    board_queries = await _get_board_queries(board_id)
                    if board_queries:
                        context_info += f"\n\nThis query belongs to board ID '{board_id}'. Other queries on this board:\n"
                        for q in board_queries:
                            if q['id'] == query_id:
                                continue
                            context_info += f"- {q['name']} (ID: {q['id']}): {q.get('description', 'No description')}\n"
        except Exception as e:
            yield {"type": "progress", "content": f"⚠️ Could not fetch query info: {str(e)}"}
    
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
                user_message += f"\n\nAvailable database schema:\n{schema_info}\n\nIMPORTANT: Use fully qualified table names (dataset.table for BigQuery)."
            
            if context_info:
                user_message += context_info
            
            if attempt > 1 and 'last_error' in locals():
                if attempt == 2:
                    user_message += f"\n\nPrevious attempt failed with error:\n{last_error}\n\nPlease fix the code. Try a SIMPLER query - remove complex aggregations, GROUP BY, or WHERE clauses. Start with a basic SELECT of key columns with LIMIT 100. Use get_datastore_schema(datastore_id, dataset, table) to verify exact column names."
                else:
                    user_message += f"\n\nMultiple attempts have failed. Last error:\n{last_error}\n\nTry the SIMPLEST possible query: SELECT * FROM dataset.table LIMIT 10. Use get_datastore_schema to re-check exact table and column names before writing the query."
            
            # Build conversation
            chat_history = chat[-50:] if chat else []
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
                
                # Warn when approaching iteration limit
                if tool_iteration == max_tool_iterations - 5:
                    yield {"type": "progress", "content": f"⚠️ Approaching iteration limit ({tool_iteration}/{max_tool_iterations})..."}
                
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
                    timeout=120  # Increased from 60 seconds
                )
                
                if not response.ok:
                    raise Exception(f"Gemini API error: {response.text}")
                
                data = response.json()
                candidate = data.get("candidates", [{}])[0]
                finish_reason = candidate.get("finishReason", "")
                content = candidate.get("content", {})
                parts = content.get("parts", [])
                
                # Check for blocked responses
                if finish_reason in ("SAFETY", "RECITATION", "OTHER"):
                    print(f"DEBUG: Gemini response blocked: finishReason={finish_reason}")
                    raise Exception(f"Gemini response was blocked ({finish_reason}). Try rephrasing your request.")
                
                # Check if AI wants to call a function
                function_calls = [p for p in parts if "functionCall" in p]
                
                if function_calls:
                    # Execute function calls
                    function_responses = []
                    for fc in function_calls:
                        func_name = fc["functionCall"]["name"]
                        func_args = fc["functionCall"].get("args", {})
                        
                        # Send tool_call started event (for UI display)
                        yield {"type": "tool_call", "tool": func_name, "status": "started", "args": func_args}
                        
                        # Execute the function
                        if func_name == "list_datastores":
                            result = await _get_available_datastores()
                            yield {"type": "tool_result", "tool": func_name, "status": "success", "result": {"datastores": result, "message": f"Found {len(result)} datastores"}}
                        elif func_name == "list_boards":
                            result = await _get_available_boards()
                            yield {"type": "tool_result", "tool": func_name, "status": "success", "result": {"boards": result, "message": f"Found {len(result)} boards"}}
                        elif func_name == "list_board_queries":
                            board_id_arg = func_args.get("board_id")
                            result = await _get_board_queries(board_id_arg) if board_id_arg else []
                            yield {"type": "tool_result", "tool": func_name, "status": "success", "result": {"queries": result, "message": f"Found {len(result)} queries"}}
                        elif func_name == "get_query_code":
                            query_id_arg = func_args.get("query_id")
                            result = await _get_query_code(query_id_arg) if query_id_arg else {"error": "Missing query_id"}
                            if "error" not in result:
                                yield {"type": "tool_result", "tool": func_name, "status": "success", "result": result}
                            else:
                                yield {"type": "tool_result", "tool": func_name, "status": "error", "error": result["error"]}
                        elif func_name == "get_board_code":
                            board_id_arg = func_args.get("board_id")
                            result = await _get_board_code(board_id_arg) if board_id_arg else {"error": "Missing board_id"}
                            if "error" not in result:
                                yield {"type": "tool_result", "tool": func_name, "status": "success", "result": result}
                            else:
                                yield {"type": "tool_result", "tool": func_name, "status": "error", "error": result["error"]}
                        elif func_name == "create_or_update_query":
                            call_args = func_args
                            result = await _create_or_update_query(
                                board_id=call_args.get("board_id"),
                                query_name=call_args.get("query_name"),
                                python_code=call_args.get("python_code"),
                                description=call_args.get("description", ""),
                                query_id=call_args.get("query_id")
                            )
                            if result.get("success"):
                                yield {"type": "tool_result", "tool": func_name, "status": "success", "result": result}
                            else:
                                yield {"type": "tool_result", "tool": func_name, "status": "error", "error": result.get("error", "Failed to save query")}
                        elif func_name == "delete_query":
                            query_id_arg = func_args.get("query_id")
                            result = await _delete_query(query_id_arg) if query_id_arg else {"error": "Missing query_id"}
                            if result.get("success"):
                                yield {"type": "tool_result", "tool": func_name, "status": "success", "result": result}
                            else:
                                yield {"type": "tool_result", "tool": func_name, "status": "error", "error": result.get("error", "Failed to delete query")}
                        elif func_name == "get_datastore_schema":
                            call_args = func_args
                            result = await _get_datastore_schema(
                                datastore_id=call_args.get("datastore_id"),
                                dataset=call_args.get("dataset"),
                                table=call_args.get("table")
                            )
                            if result.get("success"):
                                yield {"type": "tool_result", "tool": func_name, "status": "success", "result": result}
                            else:
                                yield {"type": "tool_result", "tool": func_name, "status": "error", "error": result.get("error", "Failed to get schema")}
                        elif func_name == "test_query":
                            call_args = func_args
                            result = await _test_query(call_args.get("python_code", ""))
                            if result.get("success"):
                                yield {"type": "tool_result", "tool": func_name, "status": "success", "result": result}
                            else:
                                yield {"type": "tool_result", "tool": func_name, "status": "error", "error": result.get("error", "Test failed")}
                        elif func_name == "execute_query_direct":
                            call_args = func_args
                            result = await _execute_query_direct(
                                datastore_id=call_args.get("datastore_id", ""),
                                sql_query=call_args.get("sql_query", ""),
                                limit=call_args.get("limit", 100)
                            )
                            if result.get("success"):
                                row_count = result.get("returned_rows", 0)
                                total = result.get("total_rows", 0)
                                truncated = result.get("truncated", False)
                                msg = f"✓ Executed query: {row_count} rows returned"
                                if truncated:
                                    msg += f" (truncated from {total} total rows)"
                                yield {"type": "progress", "content": msg}
                                yield {"type": "tool_result", "tool": func_name, "status": "success", "result": result}
                            else:
                                err = result.get("error", "Query execution failed")
                                yield {"type": "progress", "content": f"✗ Query failed: {err}"}
                                yield {"type": "tool_result", "tool": func_name, "status": "error", "error": err}
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
                raw_text = ""
                for p in text_parts:
                    raw_text += p.get("text", "")
                
                # Handle MAX_TOKENS - continue the generation
                if finish_reason == "MAX_TOKENS":
                    if tool_iteration < max_tool_iterations:
                        yield {"type": "progress", "content": "⏩ Continuing generation (hit token limit)..."}
                        
                        # Add the partial response and request continuation
                        contents.append({
                            "role": "model",
                            "parts": [{"text": raw_text}] if raw_text else parts
                        })
                        contents.append({
                            "role": "user",
                            "parts": [{"text": "Please continue from where you left off. Output the rest of the code."}]
                        })
                        continue
                    else:
                        raise Exception("Response was too long and hit iteration limit. Try breaking your request into smaller tasks.")
                
                if raw_text.strip():
                    raw_text_stripped = raw_text.strip()
                    # Check if it's conversational text (not code)
                    if not raw_text_stripped.startswith(('#', '@', 'import', 'from', 'def', 'class', 'if', 'for', 'while', 'try', 'with', 'result', '```')):
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
                
                # No text - prompt Gemini to respond after tool results
                if tool_iteration < max_tool_iterations:
                    yield {"type": "progress", "content": "⚠️ No response from AI, reprompting..."}
                    contents.append({
                        "role": "user",
                        "parts": [{"text": "Based on the schema information above, please generate the Python query code now. Output ONLY valid Python code with @node comments."}]
                    })
                    continue
                
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
    max_attempts = 5
    
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
                user_message += f"\n\nAvailable database schema:\n{schema_info}\n\nIMPORTANT: Use fully qualified table names (dataset.table for BigQuery, schema.table for Postgres)."
            
            # Add error context if this is a retry
            if attempt > 1 and 'last_error' in locals():
                if attempt == 2:
                    user_message += f"\n\nPrevious attempt failed with error:\n{last_error}\n\nPlease fix the code. Try a SIMPLER query - fewer columns, no aggregation, basic SELECT with LIMIT."
                else:
                    user_message += f"\n\nMultiple attempts failed. Last error:\n{last_error}\n\nTry SELECT * FROM dataset.table LIMIT 10 to verify the table works first."
            
            # Build conversation history
            chat_history = chat[-50:] if chat else []
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
                    "temperature": 0.2 if attempt > 1 else 0.3,
                    "maxOutputTokens": 65536,
                    "responseMimeType": "text/plain"
                }
            }
            
            response = requests.post(
                GEMINI_URL,
                headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                json=payload,
                timeout=120  # Increased from 60 seconds
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
    """Get BigQuery schema information with enriched details."""
    client = get_bigquery_client(datastore["config"])
    
    if table and dataset:
        # Get specific table schema with column details
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
        # List tables in dataset with column counts
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
        # List datasets with their tables
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

async def _get_postgres_schema(datastore: Dict[str, Any], database: Optional[str], table: Optional[str]):
    """Get PostgreSQL schema information with enriched details."""
    import sqlalchemy as sa
    from sqlalchemy import inspect as sa_inspect
    
    conn_str = datastore["config"].get("connection_string")
    if not conn_str:
        raise HTTPException(status_code=400, detail="Postgres connection string missing")
    
    engine = sa.create_engine(conn_str)
    inspector = sa_inspect(engine)
    
    if table:
        # Get specific table schema with column details
        schema = database or "public"
        columns = inspector.get_columns(table, schema=schema)
        
        column_info = []
        for col in columns:
            column_info.append({
                "name": col["name"],
                "type": str(col["type"]),
                "nullable": col["nullable"],
                "default": str(col.get("default", "")) if col.get("default") else None
            })
        
        return {
            "type": "table_schema",
            "schema": schema,
            "table": table,
            "columns": column_info
        }
    
    elif database:
        # List tables in schema with column counts
        table_names = inspector.get_table_names(schema=database)
        tables = []
        for t in table_names:
            table_info = {"name": t, "type": "table"}
            try:
                cols = inspector.get_columns(t, schema=database)
                table_info["column_count"] = len(cols)
            except:
                pass
            tables.append(table_info)
        return {
            "type": "tables",
            "schema": database,
            "tables": tables
        }
    
    else:
        # List schemas with their tables
        schema_names = inspector.get_schema_names()
        schemas = []
        for s in schema_names:
            schema_info = {"name": s}
            try:
                table_names = inspector.get_table_names(schema=s)
                schema_info["tables"] = [{"name": t, "type": "table"} for t in table_names[:50]]
                schema_info["table_count"] = len(table_names)
            except:
                schema_info["tables"] = []
                schema_info["table_count"] = 0
            schemas.append(schema_info)
        return {
            "type": "schemas",
            "schemas": schemas
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
