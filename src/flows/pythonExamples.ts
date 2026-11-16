/**
 * pythonExamples.js — canned Python task snippets for the FlowBuilder
 * snippet picker. Each entry has a label and a code string.
 *
 * Convention: the runtime injects `inputs` (dict of upstream task results)
 * and `params` (flow-level params dict). Assign the task output to `result`.
 */

export const PYTHON_EXAMPLES = [
  {
    label: 'Extract archive (zip/tar)',
    code: `# Extract archive (zip / tar / tar.gz)
# inputs["fetch_file"] should be a local or mounted path to the archive.
# Extracted files are written to /tmp/extracted/ (or change dest_dir).
from __future__ import annotations
import os, zipfile, tarfile, pathlib

src = inputs.get("fetch_file", {}).get("path") or params.get("archive_path", "")
dest_dir = pathlib.Path(params.get("dest_dir", "/tmp/extracted"))
dest_dir.mkdir(parents=True, exist_ok=True)

if not src:
    raise ValueError("No archive path — set inputs.fetch_file.path or params.archive_path")

if zipfile.is_zipfile(src):
    with zipfile.ZipFile(src) as zf:
        zf.extractall(dest_dir)
elif tarfile.is_tarfile(src):
    with tarfile.open(src) as tf:
        tf.extractall(dest_dir)
else:
    raise ValueError(f"Unrecognised archive format: {src}")

files = [str(p) for p in dest_dir.rglob("*") if p.is_file()]
result = {"dest_dir": str(dest_dir), "files": files, "count": len(files)}
`,
  },
  {
    label: 'HTTP fetch JSON',
    code: `# Fetch JSON from an HTTP endpoint and return it as the task result.
# Set params.url or hard-code the URL below.
from __future__ import annotations
import urllib.request, json

url = params.get("url") or "https://httpbin.org/json"
headers = {"Accept": "application/json", "User-Agent": "nubi-flow/1.0"}

req = urllib.request.Request(url, headers=headers)
with urllib.request.urlopen(req, timeout=30) as resp:
    body = resp.read().decode()

result = {"url": url, "status": resp.status, "data": json.loads(body)}
`,
  },
  {
    label: 'Transform rows',
    code: `# Transform a list of rows produced by an upstream task.
# Expects inputs["query_task"] = {"rows": [...], "columns": [...]}
from __future__ import annotations

upstream = inputs.get("query_task", {})
rows = upstream.get("rows", [])
cols = upstream.get("columns", [])

def to_dict(row):
    return dict(zip(cols, row)) if cols else row

transformed = []
for row in rows:
    rec = to_dict(row)
    # ── your transformation logic here ──────────────────
    rec["_processed"] = True
    # ────────────────────────────────────────────────────
    transformed.append(rec)

result = {"rows": transformed, "count": len(transformed)}
`,
  },
  {
    label: 'Transform with pandas (DataFrame)',
    code: `# Operate on an upstream cell's rows as a pandas DataFrame and return a DataFrame.
# \`dataframes\` maps each upstream key whose result has {rows, columns} to a
# pandas.DataFrame. Returning a DataFrame is auto-serialised to {rows, columns, row_count}.
from __future__ import annotations

df = dataframes.get("query_task")
if df is None:
    raise ValueError("Upstream 'query_task' produced no rows/columns")

# ── your DataFrame transformation here ──────────────────
df = df[df["value"] > 0].copy()
df["value_x2"] = df["value"] * 2
# ────────────────────────────────────────────────────────

result = df   # auto-serialised to {rows, columns, row_count}
`,
  },
  {
    label: 'Call agent (template)',
    code: `# Call an agent from a Python cell (the 'agent' kind replacement).
# Shape a prompt from upstream rows and hand it to the agent route. \`dataframes\`
# maps each upstream key with {rows, columns} to a pandas.DataFrame.
from __future__ import annotations

df = dataframes.get("query_task")
summary = "" if df is None else df.head(20).to_csv(index=False)
prompt = f"Summarise these rows:\\n{summary}"

# Option A — return the shaped prompt for a downstream agent step:
result = {"prompt": prompt}

# Option B — call the agent inline (uncomment; requires the agent route):
# import json, urllib.request
# req = urllib.request.Request(
#     params.get("agent_url", "http://localhost:8000/agent/run"),
#     data=json.dumps({"prompt": prompt, "max_steps": 4}).encode(),
#     headers={"Content-Type": "application/json"},
# )
# with urllib.request.urlopen(req, timeout=120) as resp:
#     result = {"answer": json.loads(resp.read().decode())}
`,
  },
]
