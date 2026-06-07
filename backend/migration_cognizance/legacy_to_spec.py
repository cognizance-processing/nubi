"""legacy_to_spec.py — Transform the extracted legacy COGNIZANCE bundle into the
new Nubi DashboardSpec model + query/connector configs.

Input : backend/migration_cognizance/legacy_bundle.json  (produced by the extractor)
Output: backend/migration_cognizance/migration_artifact.json
        + a human-readable report printed to stdout.

The artifact is consumed by seed_cognizance.py.

Mapping summary
---------------
- Connector  : the single BigQuery connector -> one datastore (config + secret).
- Queries    : legacy Go-template SQL rendered to concrete BigQuery SQL using the
               per-query default substitution values stored in query_arguments.
               (Filter-fragment placeholders default to empty = the unfiltered view.)
- Dashboards : legacy widgets -> new Widget list.
               * data widgets (table/chart/kpi) keep their location.lg grid pos
                 (grid is 48-col; we set layout.cols = 48 to preserve positions 1:1).
               * renderToDrawer filter controls -> a slide-out "Filters" drawer
                 (drawer:true) + dashboard variables.
               * labels/dividers/images -> text/section widgets.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
BUNDLE = HERE / "legacy_bundle.json"
ARTIFACT = HERE / "migration_artifact.json"

PROJECT = "cog-analytics-etl-pipeline"

# ── Go text/template subset renderer ───────────────────────────────────────
_TMPL = re.compile(r"\{\{\s*\.([A-Za-z0-9_]+)\s*\}\}")


def _loadj(s):
    if not s:
        return {}
    for cand in (s, s.replace("\\\\", "\\")):
        try:
            v = json.loads(cand)
            if isinstance(v, dict):
                return v
        except Exception:
            continue
    return {}


_IF = re.compile(
    r"\{\{-?\s*if\s+(?P<cond>.+?)\s*-?\}\}"
    r"(?P<body>(?:(?!\{\{-?\s*if\b).)*?)"
    r"\{\{-?\s*end\s*-?\}\}",
    re.S,
)
_COND_NE = re.compile(r"\(\s*ne\s+\.([A-Za-z0-9_]+)\s+\"(.*?)\"\s*\)")
_COND_EQ = re.compile(r"\(\s*eq\s+\.([A-Za-z0-9_]+)\s+\"(.*?)\"\s*\)")
_COND_BARE = re.compile(r"^\.([A-Za-z0-9_]+)$")


def _val(vals, k):
    v = vals.get(k)
    return "" if v is None else str(v)


def _eval_cond(cond: str, vals: dict) -> bool:
    cond = cond.strip()
    m = _COND_NE.match(cond)
    if m:
        return _val(vals, m.group(1)) != m.group(2)
    m = _COND_EQ.match(cond)
    if m:
        return _val(vals, m.group(1)) == m.group(2)
    m = _COND_BARE.match(cond)
    if m:
        return _val(vals, m.group(1)) != ""
    # unknown condition -> treat as false (drop optional block) for a safe default
    return False


def _resolve_conditionals(sql: str, vals: dict) -> str:
    """Resolve Go-template `{{ if COND }}body{{ else }}alt{{ end }}` blocks,
    innermost-first, until none remain."""
    for _ in range(50):  # bounded fixpoint
        m = _IF.search(sql)
        if not m:
            break
        body = m.group("body")
        # split on a top-level {{ else }} (body has no nested if by construction)
        parts = re.split(r"\{\{-?\s*else\s*-?\}\}", body, maxsplit=1)
        chosen = parts[0] if _eval_cond(m.group("cond"), vals) else (parts[1] if len(parts) > 1 else "")
        sql = sql[: m.start()] + chosen + sql[m.end():]
    return sql


def render_sql(sql: str, vals: dict) -> tuple[str, set[str]]:
    """Render Go-template SQL to concrete SQL.

    1. Resolve `{{ if/else/end }}` conditionals against `vals` (defaults).
    2. Substitute `{{ .X }}` placeholders; unknown -> '' (filter defaults).
    """
    if not sql:
        return sql, set()
    missing: set[str] = set()

    def sub(m):
        k = m.group(1)
        v = vals.get(k)
        if v is None or v == "":
            missing.add(k)
            return ""
        return str(v)

    out = sql.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
    out = _resolve_conditionals(out, vals)
    out = _TMPL.sub(sub, out)
    return out, missing


# ── SQL projection parser (best-effort) ────────────────────────────────────
def _split_top_level(s: str, sep: str = ",") -> list[str]:
    """Split on `sep` at paren depth 0."""
    parts, depth, buf = [], 0, []
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def output_columns(sql: str) -> list[str]:
    """Extract output column names from the FINAL top-level SELECT of a query.

    Handles CTEs by taking the last `SELECT ... FROM` that sits at paren depth 0.
    Returns alias names (`expr AS alias` -> alias; bare `a.b` -> b; `x` -> x).
    """
    if not sql:
        return []
    text = sql
    # find depth-0 SELECT ... FROM spans; keep the last one
    best = None
    depth = 0
    i = 0
    low = text.lower()
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and low.startswith("select", i) and (i == 0 or not text[i - 1].isalnum()):
            # find matching FROM at depth 0 after this select
            j = i + 6
            d2 = 0
            while j < len(text):
                c2 = text[j]
                if c2 == "(":
                    d2 += 1
                elif c2 == ")":
                    d2 -= 1
                elif d2 == 0 and low.startswith("from", j) and not text[j - 1].isalnum():
                    best = text[i + 6 : j]
                    break
                j += 1
            i = j
            continue
        i += 1
    if best is None:
        return []
    cols = []
    for raw in _split_top_level(best):
        c = raw.strip().rstrip()
        if not c or c == "*":
            continue
        # strip trailing alias
        m = re.search(r"\bas\s+([A-Za-z0-9_]+)\s*$", c, re.I)
        if m:
            cols.append(m.group(1))
            continue
        # bare token / qualified token
        m = re.search(r"([A-Za-z0-9_]+)\s*$", c)
        if m:
            cols.append(m.group(1))
    return cols


# ── widget type mapping ────────────────────────────────────────────────────
CHART_MAP = {
    "ChartJSBar": ("bar", {}),
    "BasicApexColumnChart": ("bar", {}),
    "ChartJSLine": ("line", {}),
    "BasicApexLineChart": ("line", {}),
    "ChartJSArea": ("area", {}),
    "BasicApexAreaChart": ("area", {}),
    "ChartJSPie": ("pie", {}),
    "BasicApexPieChart": ("pie", {}),
    "ChartJSDoughnut": ("pie", {"innerRadius": "55%"}),
    "BasicApexDonutChart": ("pie", {"innerRadius": "55%"}),
    "ChartJSScatter": ("scatter", {}),
    "ChartJSBubble": ("scatter", {}),
    "ChartJSMixed": ("bar", {}),
    "ChartJSRadar": ("bar", {}),
    "BasicApexChart": ("bar", {}),
}
TABLE_TYPES = {"BasicTable"}
KPI_TYPES = {"BasicQuickStats", "BasicDateDisplay"}
FILTER_AC = {"BasicAutoCompleteFilter"}
FILTER_DATE = {"BasicDateRange"}
LABEL_TYPES = {"BasicLabel"}
DIVIDER_TYPES = {"BasicLineDivider"}
IMAGE_TYPES = {"BasicImage"}
GROUP_TYPES = {"BasicWidgetGroup", "BasicWidgetGroupStepper"}


def _pos(location):
    """legacy location -> new pos (1-based).

    `location` may be either {"lg": {x,y,w,h}} or a FLAT {x,y,w,h,...} object
    (group/drilldown children store the flat shape).
    """
    if not isinstance(location, dict):
        return None
    loc = location.get("lg") if "lg" in location else location
    if not isinstance(loc, dict):
        return None
    x, y, w, h = loc.get("x"), loc.get("y"), loc.get("w"), loc.get("h")
    if None in (x, y, w, h):
        return None
    return {"x": int(x) + 1, "y": int(y) + 1, "w": max(1, int(w)), "h": max(1, int(h))}


def _label_text(code):
    d = _loadj(code).get("basicWidget", {}) if code else {}
    return d.get("labelText") or d.get("defaultOptionsLabel") or ""


def _filter_meta(code):
    d = _loadj(code).get("basicWidget", {}) if code else {}
    return {
        "label": d.get("defaultOptionsLabel") or "",
        "multi": bool(d.get("selectMultiple")),
    }


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_") or "var"


def main():
    bundle = json.loads(BUNDLE.read_text())
    dashboards = bundle["dashboards"]
    widgets = bundle["widgets"]
    queries = {q["id"]: q for q in bundle["queries"]}
    connectors = bundle["connectors"]

    # per-query default substitution values (merge all query_arguments JSON)
    qvals = defaultdict(dict)
    for a in bundle["query_arguments"]:
        d = _loadj(a.get("argument"))
        if isinstance(d, dict):
            qvals[a["query_id"]].update(d)

    # ── render every query to concrete SQL ──
    out_queries = {}
    for qid, q in queries.items():
        sql = q.get("templated_query") or ""
        rendered, missing = render_sql(sql, qvals.get(qid, {}))
        cols = output_columns(rendered)
        out_queries[qid] = {
            "legacy_id": qid,
            "name": q.get("name") or "query",
            "sql": rendered,
            "columns": cols,
            "missing_params": sorted(missing),
            "runnable": "{{" not in rendered,
        }

    # widget -> its data query id (executorArgs.queryId)
    def widget_query(w):
        a = w.get("arguments") or {}
        return (a.get("executorArgs") or {}).get("queryId") if isinstance(a, dict) else None

    boards = []
    report = []
    for d in dashboards:
        did = d["id"]
        cfg = d.get("config") or {}
        grid_cols = int(cfg.get("gridColumns") or 48)
        dws = [w for w in widgets if w["dashboard_id"] == did]

        # ── group/drilldown modelling ──
        # A BasicWidgetGroup / *Stepper carries group:[{id,label}] listing child
        # widget ids. Those children render inside a drilldown drawer (keyed by the
        # group widget's id), revealed by a trigger placed on the grid.
        child_to_drawer = {}   # child widget id (full) -> drawer_group id
        group_ids = set()
        for w in dws:
            a = w.get("arguments") or {}
            if isinstance(a, dict) and a.get("type") in GROUP_TYPES:
                group_ids.add(w["id"])
                for child in (a.get("group") or []):
                    cid = child.get("id") if isinstance(child, dict) else None
                    if cid:
                        child_to_drawer.setdefault(cid, w["id"])

        spec_widgets = []
        variables = []
        drawer_count = 0
        skipped = []

        drilldown_count = 0

        def dg_id(full_id):
            return f"dg_{full_id[:8]}"

        for w in dws:
            a = w.get("arguments") or {}
            if not isinstance(a, dict):
                continue
            wtype = a.get("type")
            wid = w["id"][:8]
            qid = widget_query(w)
            pos = _pos(a.get("location"))
            name = w.get("name") or ""

            # Which drawer does this widget belong to?
            #   - renderToDrawer filter  -> the shared "filters" drawer
            #   - member of a widget group -> that group's drilldown drawer
            in_filters_drawer = bool(a.get("renderToDrawer")) and wtype in (FILTER_AC | FILTER_DATE)
            drilldown_group = child_to_drawer.get(w["id"])
            drawer_group = "filters" if in_filters_drawer else (dg_id(drilldown_group) if drilldown_group else None)
            in_drawer = drawer_group is not None

            def emit(wd):
                if in_drawer:
                    wd["drawer"] = True
                    wd["drawer_group"] = drawer_group
                    wd["order"] = a.get("order") or 0
                    # in-drawer widgets still carry a pos for in-panel layout
                    wd.setdefault("pos", pos or {"x": 1, "y": 1, "w": 12, "h": 4})
                spec_widgets.append(wd)

            # ---- group container / drilldown trigger ----
            if wtype in GROUP_TYPES:
                # A stepper/group with a grid pos becomes the on-grid drilldown
                # trigger that opens its own drawer (dg_<id>). Pure containers with
                # no grid pos are skipped (their children attach to the drawer).
                if pos is not None:
                    spec_widgets.append({
                        "id": f"w_{wid}", "type": "section", "query_id": "",
                        "props": {"title": name or "Drill down", "drilldown_group": dg_id(w["id"]), "trigger": True},
                        "pos": pos,
                    })
                    drilldown_count += 1
                else:
                    skipped.append((name, wtype, "group-container"))
                continue

            # ---- filters (drawer or inline) ----
            if wtype in FILTER_AC or wtype in FILTER_DATE:
                fm = _filter_meta(a.get("code"))
                is_date = wtype in FILTER_DATE
                var = slug(name or fm.get("label") or "filter")
                vtype = "daterange" if is_date else ("multiselect" if fm["multi"] else "select")
                variables.append({"name": var, "type": vtype})
                wd = {
                    "id": f"w_{wid}", "type": "filter", "subtype": vtype,
                    "target_var": var, "options_query_id": (None if is_date else (qid or None)),
                    "query_id": "", "props": {"label": fm.get("label") or name},
                    "pos": pos or {"x": 1, "y": 1, "w": 6, "h": 2},
                }
                emit(wd)
                if in_filters_drawer:
                    drawer_count += 1
                continue

            # ---- data + presentational widgets ----
            if pos is None and not in_drawer:
                skipped.append((name, wtype, "no-pos"))
                continue

            if wtype in TABLE_TYPES:
                emit({"id": f"w_{wid}", "type": "table", "query_id": qid or "",
                      "props": {"title": name, "limit": 100}, "pos": pos})
            elif wtype in CHART_MAP:
                ctype, cprops = CHART_MAP[wtype]
                cols = out_queries.get(qid, {}).get("columns", []) if qid else []
                x = cols[0] if cols else "x"
                y = cols[1] if len(cols) > 1 else (cols[0] if cols else "y")
                enc = {"x": x, "y": y}
                if len(cols) > 2:
                    enc["color"] = cols[2]
                emit({"id": f"w_{wid}", "type": "chart", "chart_type": ctype,
                      "query_id": qid or "", "encoding": enc,
                      "props": {"title": name, **cprops}, "pos": pos})
            elif wtype in KPI_TYPES:
                cols = out_queries.get(qid, {}).get("columns", []) if qid else []
                val = cols[0] if cols else "value"
                emit({"id": f"w_{wid}", "type": "kpi", "query_id": qid or "",
                      "encoding": {"value": val}, "props": {"label": name}, "pos": pos})
            elif wtype in LABEL_TYPES:
                emit({"id": f"w_{wid}", "type": "text",
                      "content": f"### {_label_text(a.get('code')) or name}",
                      "query_id": "", "pos": pos or {"x": 1, "y": 1, "w": 12, "h": 1}})
            elif wtype in DIVIDER_TYPES:
                emit({"id": f"w_{wid}", "type": "section",
                      "props": {"title": name, "divider": True},
                      "query_id": "", "pos": pos or {"x": 1, "y": 1, "w": grid_cols, "h": 1}})
            elif wtype in IMAGE_TYPES:
                emit({"id": f"w_{wid}", "type": "text", "content": f"*(image: {name})*",
                      "query_id": "", "pos": pos or {"x": 1, "y": 1, "w": 6, "h": 4}})
            else:
                skipped.append((name, wtype, "unmapped"))

        # de-dup variable names
        seen = set()
        uniq_vars = []
        for v in variables:
            if v["name"] in seen:
                continue
            seen.add(v["name"])
            uniq_vars.append(v)

        spec = {
            "version": 1,
            "title": d.get("name") or "Dashboard",
            "layout": {"cols": grid_cols, "row_height": 12, "compaction": "free", "margin_x": 6, "margin_y": 6},
            "variables": uniq_vars,
            "widgets": spec_widgets,
            "drawer": {"enabled": drawer_count > 0, "title": "Filters"},
        }
        boards.append({"legacy_id": did, "name": spec["title"], "spec": spec})
        report.append({
            "dashboard": spec["title"],
            "widgets": len([x for x in spec_widgets if not x.get("drawer")]),
            "drawer_filters": drawer_count,
            "drilldowns": drilldown_count,
            "skipped": skipped,
        })

    # ── connector / datastore ──
    c = connectors[0]
    datastore = {
        "legacy_id": c["id"],
        "name": "Cognizance BigQuery",
        "config": {"type": "bigquery", "projectId": PROJECT,
                   "project_id": PROJECT, "description": "Migrated COGNIZANCE BigQuery connector (scoped, read-only)."},
        "secret_keyfile": "keyfile.scoped.json",
    }

    artifact = {
        "datastore": datastore,
        "queries": list(out_queries.values()),
        "boards": boards,
    }
    ARTIFACT.write_text(json.dumps(artifact, indent=1, default=str))

    # ── report ──
    print(f"wrote {ARTIFACT.name}")
    print(f"queries: {len(out_queries)}  ({sum(1 for q in out_queries.values() if not q['runnable'])} not fully rendered)")
    print(f"boards: {len(boards)}")
    print(f"\n{'dashboard':45} {'grid':>5} {'filters':>7} {'drilldn':>7} {'skipped':>7}")
    for r in report:
        print(f"{r['dashboard'][:44]:45} {r['widgets']:>5} {r['drawer_filters']:>7} {r['drilldowns']:>7} {len(r['skipped']):>7}")
    # show skipped detail
    for r in report:
        if r["skipped"]:
            print(f"\n  skipped in {r['dashboard'][:40]}:")
            for nm, ty, why in r["skipped"][:12]:
                print(f"     - {ty:26} {why:8} {nm[:30]}")


if __name__ == "__main__":
    main()
