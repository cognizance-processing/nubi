# Nubi Stitch Engine (Python/FastAPI)

The Stitch Engine is a high-performance query execution and data stitching service. It allows you to chain templated SQL queries and Python logic into a single atomic "Flow".

## Setup

1.  **Install Dependencies**:
    ```bash
    cd backend
    pip install -r requirements.txt
    ```

2.  **Environment Variables**:
    Update `backend/.env` with your `DATABASE_URL`, `JWT_SECRET`, and `GEMINI_API_KEY` (see `.env.example`).

3.  **Run the Server**:
    ```bash
    python main.py
    ```

## API Endpoint: `/stitch`

**Method**: `POST`

**Request Body**:
```json
{
  "chain_id": "uuid-of-chain",
  "args": {
    "date_from": "2023-01-01",
    "region": "US"
  },
  "datastore_id": "optional-override-datastore-uuid"
}
```

**Response**:
```json
{
  "status": "success",
  "table": [
    { "id": 1, "name": "Item A", "value": 100 },
    { "id": 2, "name": "Item B", "value": 200 }
  ],
  "count": 2
}
```

## How it works

1.  **Start Context**: The engine merges `default_args` from the DB with your request `args`.
2.  **Python Logic**: Each step can run Python code. It has access to `pd` (Pandas), `json`, and the current `context`. You can set `context['result'] = ...` to manually override the final output.
3.  **Templated SQL**: SQL triggers after the Python logic in a step. It uses the updated context (including results from previous steps) via Jinja2 syntax.
4.  **Datastore Priority**: 
    1.  Step-level `datastore_id`
    2.  Request-level `datastore_id`
    3.  Chain-level `default_datastore_id`

## Vanilla JS Usage

```javascript
async function runStitch(chainId, args = {}) {
  const response = await fetch('http://localhost:8000/stitch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      chain_id: chainId,
      args: args
    })
  });

  const { status, table, count } = await response.json();
  if (status === 'success') {
    renderTable(table);
  }
}
```
