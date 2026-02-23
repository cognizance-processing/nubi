"""
End-to-end test: sign in, create a board, create a query, save+execute it, print results.
"""

import json
import requests

BASE = "http://localhost:8000"

def main():
    # 1. Sign in
    print("=== Signing in ===")
    r = requests.post(f"{BASE}/auth/signin", json={
        "email": "imran@cognizance.vision",
        "password": "happy123",
    })
    r.raise_for_status()
    data = r.json()
    token = data["token"]
    user = data["user"]
    print(f"Logged in as {user['email']} (id: {user['id']})")

    headers = {"Authorization": f"Bearer {token}"}

    # 2. Get organization (pick the first one)
    print("\n=== Getting organization ===")
    r = requests.get(f"{BASE}/organizations", headers=headers)
    r.raise_for_status()
    orgs = r.json()
    if not orgs:
        print("No organizations found, creating one...")
        r = requests.post(f"{BASE}/organizations", headers=headers, json={"name": "Test Org"})
        r.raise_for_status()
        org = r.json()
    else:
        org = orgs[0]
    org_id = str(org["id"])
    print(f"Using org: {org.get('name', 'N/A')} (id: {org_id})")

    # 3. Get datastore (need BigQuery one for cog-scrapers)
    print("\n=== Getting datastores ===")
    r = requests.get(f"{BASE}/datastores", headers=headers, params={"organization_id": org_id})
    r.raise_for_status()
    datastores = r.json()
    ds_id = None
    for ds in datastores:
        print(f"  - {ds['name']} (type: {ds['type']}, id: {ds['id']})")
        if ds["type"] == "bigquery":
            ds_id = str(ds["id"])
    if not ds_id and datastores:
        ds_id = str(datastores[0]["id"])
    print(f"Using datastore: {ds_id}")

    # 4. Create a board
    print("\n=== Creating board ===")
    r = requests.post(f"{BASE}/boards", headers=headers, json={
        "name": "Test Board - Flow Test",
        "description": "Created by test_flow.py",
        "organization_id": org_id,
    })
    r.raise_for_status()
    board = r.json()
    board_id = str(board["id"])
    print(f"Board created: {board['name']} (id: {board_id})")

    # 5. Create a query with Python code that queries BigQuery
    print("\n=== Creating query (save + execute) ===")
    python_code = f"""# @node: billing_data
# @type: query
# @datastore: {ds_id}
# @query: SELECT * FROM `cog-scrapers.pnp_scraper.sonnedal_billing_tallies` LIMIT 10

df = query_result.copy()
df.columns = [c.strip().lower().replace(' ', '_') for c in df.columns]
numeric_cols = df.select_dtypes(include='number').columns.tolist()
summary = {{'total_rows': len(df), 'numeric_columns': numeric_cols}}
if numeric_cols:
    df['_row_total'] = df[numeric_cols].sum(axis=1)
    summary['grand_total'] = float(df['_row_total'].sum())
    summary['row_avg'] = float(df['_row_total'].mean())
result = df
"""

    r = requests.post(f"{BASE}/boards/{board_id}/queries", headers=headers, json={
        "name": "Sonnedal Billing Tallies",
        "description": "Test query - first 10 rows",
        "python_code": python_code,
    })
    r.raise_for_status()
    query = r.json()
    query_id = str(query["id"])
    print(f"Query saved: {query['name']} (id: {query_id})")

    # 6. Execute the query via /explore (same as dashboard)
    print("\n=== Executing query via /explore ===")
    r = requests.post(f"{BASE}/explore", json={
        "query_id": query_id,
        "args": {},
        "datastore_id": ds_id,
    })
    r.raise_for_status()
    explore_result = r.json()

    if explore_result.get("error"):
        print(f"\nERROR: {explore_result['error']}")
    else:
        rows = explore_result.get("result", [])
        columns = explore_result.get("columns", [])
        count = explore_result.get("count", len(rows))
        print(f"\nSuccess! {count} rows returned")
        if columns:
            print(f"Columns: {columns}")
        print("\n--- Results ---")
        for i, row in enumerate(rows):
            print(f"Row {i+1}: {json.dumps(row, indent=2, default=str)}")

    # 7. Cleanup: delete the board
    print("\n=== Cleanup ===")
    requests.delete(f"{BASE}/queries/{query_id}", headers=headers)
    print(f"Deleted query {query_id}")
    # Note: no board delete endpoint, leave it


if __name__ == "__main__":
    main()
