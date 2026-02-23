import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Body, Depends, UploadFile, File, Request

from ..db import get_pool
from ..auth import get_current_user
from ..helpers import ensure_dict
from ..storage import get_storage_provider

router = APIRouter(tags=["crud"])

storage = get_storage_provider()


# ──────────────────────────────────────────────
# CRUD — Organizations
# ──────────────────────────────────────────────

@router.get("/organizations")
async def list_organizations(user=Depends(get_current_user)):
    pool = get_pool()
    rows = await pool.fetch(
        """SELECT o.*, om.role
           FROM organizations o
           JOIN organization_members om ON om.organization_id = o.id
           WHERE om.user_id = $1
           ORDER BY o.created_at ASC""",
        user["id"],
    )
    return [dict(r) for r in rows]


@router.post("/organizations")
async def create_organization(name: str = Body(..., embed=True), user=Depends(get_current_user)):
    pool = get_pool()
    row = await pool.fetchrow(
        "INSERT INTO organizations (name) VALUES ($1) RETURNING *", name
    )
    await pool.execute(
        "INSERT INTO organization_members (organization_id, user_id, role) VALUES ($1, $2, 'owner')",
        row["id"], user["id"],
    )
    return {**dict(row), "role": "owner"}


@router.patch("/organizations/{org_id}")
async def update_organization(org_id: str, name: str = Body(..., embed=True), user=Depends(get_current_user)):
    pool = get_pool()
    role = await pool.fetchval(
        "SELECT role FROM organization_members WHERE organization_id = $1 AND user_id = $2",
        org_id, user["id"],
    )
    if role not in ("owner", "admin"):
        raise HTTPException(403, "Not authorized to update this organization")
    await pool.execute("UPDATE organizations SET name = $1 WHERE id = $2", name, org_id)
    return {"success": True}


# ──────────────────────────────────────────────
# CRUD — Boards
# ──────────────────────────────────────────────

@router.get("/boards")
async def list_boards(organization_id: str, user=Depends(get_current_user)):
    pool = get_pool()
    rows = await pool.fetch(
        """SELECT b.* FROM boards b
           JOIN organization_members om ON om.organization_id = b.organization_id
           WHERE b.organization_id = $1 AND om.user_id = $2
           ORDER BY b.created_at DESC""",
        organization_id, user["id"],
    )
    return [dict(r) for r in rows]


@router.post("/boards")
async def create_board(
    name: str = Body(...),
    description: Optional[str] = Body(default=None),
    organization_id: str = Body(...),
    user=Depends(get_current_user),
):
    pool = get_pool()
    row = await pool.fetchrow(
        "INSERT INTO boards (name, description, profile_id, organization_id) VALUES ($1,$2,$3,$4) RETURNING *",
        name, description, user["id"], organization_id,
    )
    return dict(row)


@router.get("/boards/{board_id}")
async def get_board(board_id: str, user=Depends(get_current_user)):
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM boards WHERE id = $1", board_id)
    if not row:
        raise HTTPException(404, "Board not found")
    return dict(row)


@router.patch("/boards/{board_id}")
async def update_board(board_id: str, body: dict = Body(...), user=Depends(get_current_user)):
    pool = get_pool()
    allowed = {"name", "description"}
    sets, vals, idx = [], [], 1
    for k, v in body.items():
        if k in allowed:
            sets.append(f"{k} = ${idx}")
            vals.append(v)
            idx += 1
    if not sets:
        raise HTTPException(400, "Nothing to update")
    vals.append(board_id)
    await pool.execute(f"UPDATE boards SET {', '.join(sets)} WHERE id = ${idx}", *vals)
    row = await pool.fetchrow("SELECT * FROM boards WHERE id = $1", board_id)
    return dict(row) if row else {}


@router.get("/boards/{board_id}/code")
async def get_board_code_endpoint(board_id: str, user=Depends(get_current_user)):
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM board_code WHERE board_id = $1 ORDER BY version DESC LIMIT 1", board_id
    )
    return dict(row) if row else {"code": "", "version": 0}


@router.post("/boards/{board_id}/code")
async def save_board_code(board_id: str, code: str = Body(..., embed=True), user=Depends(get_current_user)):
    pool = get_pool()
    ver = await pool.fetchrow(
        "SELECT version FROM board_code WHERE board_id = $1 ORDER BY version DESC LIMIT 1", board_id
    )
    new_version = (ver["version"] + 1) if ver else 1
    row = await pool.fetchrow(
        "INSERT INTO board_code (board_id, version, code) VALUES ($1,$2,$3) RETURNING *",
        board_id, new_version, code,
    )
    return dict(row)


@router.get("/boards/{board_id}/queries")
async def list_board_queries(board_id: str, user=Depends(get_current_user)):
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT * FROM board_queries WHERE board_id = $1 ORDER BY updated_at DESC", board_id
    )
    return [dict(r) for r in rows]


@router.post("/boards/{board_id}/queries")
async def create_query(board_id: str, name: str = Body(...), description: Optional[str] = Body(default=None), python_code: str = Body(default=""), ui_map: dict = Body(default={}), user=Depends(get_current_user)):
    pool = get_pool()
    row = await pool.fetchrow(
        "INSERT INTO board_queries (board_id, name, description, python_code, ui_map) VALUES ($1,$2,$3,$4,$5) RETURNING *",
        board_id, name, description, python_code, ui_map,
    )
    return dict(row)


# ──────────────────────────────────────────────
# CRUD — Queries
# ──────────────────────────────────────────────

@router.get("/queries/{query_id}")
async def get_query(query_id: str, user=Depends(get_current_user)):
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM board_queries WHERE id = $1", query_id)
    if not row:
        raise HTTPException(404, "Query not found")
    return dict(row)


@router.patch("/queries/{query_id}")
async def update_query(query_id: str, request: Request, user=Depends(get_current_user)):
    body = await request.json()
    pool = get_pool()
    sets, vals, idx = [], [], 1
    for col in ("name", "description", "python_code", "ui_map"):
        if col in body:
            sets.append(f"{col} = ${idx}")
            vals.append(body[col])
            idx += 1
    if not sets:
        raise HTTPException(400, "No fields to update")
    vals.append(query_id)
    await pool.execute(f"UPDATE board_queries SET {', '.join(sets)} WHERE id = ${idx}", *vals)
    row = await pool.fetchrow("SELECT * FROM board_queries WHERE id = $1", query_id)
    return dict(row) if row else {}


@router.delete("/queries/{query_id}")
async def delete_query_endpoint(query_id: str, user=Depends(get_current_user)):
    pool = get_pool()
    await pool.execute("DELETE FROM board_queries WHERE id = $1", query_id)
    return {"success": True}


# ──────────────────────────────────────────────
# CRUD — Datastores
# ──────────────────────────────────────────────

@router.get("/datastores")
async def list_datastores(organization_id: Optional[str] = None, user=Depends(get_current_user)):
    pool = get_pool()
    if organization_id:
        rows = await pool.fetch(
            "SELECT * FROM datastores WHERE user_id = $1 AND organization_id = $2 ORDER BY created_at DESC",
            user["id"], organization_id,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM datastores WHERE user_id = $1 ORDER BY created_at DESC", user["id"]
        )
    result = []
    for r in rows:
        d = dict(r)
        d["config"] = ensure_dict(d.get("config"))
        result.append(d)
    return result


@router.post("/datastores")
async def create_datastore(name: str = Body(...), type: str = Body(...), config: dict = Body(default={}), organization_id: Optional[str] = Body(default=None), user=Depends(get_current_user)):
    pool = get_pool()
    row = await pool.fetchrow(
        "INSERT INTO datastores (name, type, config, user_id, organization_id) VALUES ($1,$2,$3,$4,$5) RETURNING *",
        name, type, config, user["id"], organization_id,
    )
    return dict(row)


@router.get("/datastores/{datastore_id}")
async def get_datastore(datastore_id: str, user=Depends(get_current_user)):
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM datastores WHERE id = $1", datastore_id)
    if not row:
        raise HTTPException(404, "Datastore not found")
    d = dict(row)
    d["config"] = ensure_dict(d.get("config"))
    return d


@router.patch("/datastores/{datastore_id}")
async def update_datastore(datastore_id: str, request: Request, user=Depends(get_current_user)):
    body = await request.json()
    pool = get_pool()
    sets, vals, idx = [], [], 1
    for col in ("name", "type", "config"):
        if col in body:
            sets.append(f"{col} = ${idx}")
            vals.append(body[col])
            idx += 1
    if not sets:
        raise HTTPException(400, "No fields to update")
    vals.append(datastore_id)
    await pool.execute(f"UPDATE datastores SET {', '.join(sets)} WHERE id = ${idx}", *vals)
    row = await pool.fetchrow("SELECT * FROM datastores WHERE id = $1", datastore_id)
    if not row:
        return {}
    d = dict(row)
    d["config"] = ensure_dict(d.get("config"))
    return d


@router.delete("/datastores/{datastore_id}")
async def delete_datastore(datastore_id: str, user=Depends(get_current_user)):
    pool = get_pool()
    await pool.execute("DELETE FROM datastores WHERE id = $1", datastore_id)
    return {"success": True}


@router.post("/upload/keyfile")
async def upload_keyfile(file: UploadFile = File(...), user=Depends(get_current_user)):
    data = await file.read()
    path = f"{user['id']}/{uuid.uuid4()}.json"
    stored_path = await storage.upload("secret-files", path, data, "application/json")
    return {"path": stored_path}


# ──────────────────────────────────────────────
# CRUD — Chats
# ──────────────────────────────────────────────

@router.get("/chats")
async def list_chats(board_id: Optional[str] = None, organization_id: Optional[str] = None, user=Depends(get_current_user)):
    pool = get_pool()
    if board_id:
        rows = await pool.fetch(
            "SELECT * FROM chats WHERE user_id = $1 AND board_id = $2 ORDER BY updated_at DESC",
            user["id"], board_id,
        )
    elif organization_id:
        rows = await pool.fetch(
            "SELECT * FROM chats WHERE user_id = $1 AND organization_id = $2 ORDER BY updated_at DESC",
            user["id"], organization_id,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM chats WHERE user_id = $1 ORDER BY updated_at DESC", user["id"]
        )
    return [dict(r) for r in rows]


@router.post("/chats")
async def create_chat(title: str = Body(default="New chat"), board_id: Optional[str] = Body(default=None), organization_id: Optional[str] = Body(default=None), user=Depends(get_current_user)):
    pool = get_pool()
    row = await pool.fetchrow(
        "INSERT INTO chats (user_id, title, board_id, organization_id) VALUES ($1,$2,$3,$4) RETURNING *",
        user["id"], title, board_id, organization_id,
    )
    return dict(row)


@router.patch("/chats/{chat_id}")
async def update_chat(chat_id: str, title: str = Body(...), user=Depends(get_current_user)):
    pool = get_pool()
    await pool.execute("UPDATE chats SET title = $1 WHERE id = $2 AND user_id = $3", title, chat_id, user["id"])
    return {"success": True}


@router.get("/chats/{chat_id}/messages")
async def list_messages(chat_id: str, user=Depends(get_current_user)):
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT role, content, created_at FROM chat_messages WHERE chat_id = $1 ORDER BY created_at ASC", chat_id
    )
    return [dict(r) for r in rows]


@router.post("/chats/{chat_id}/messages")
async def create_message(chat_id: str, role: str = Body(...), content: str = Body(...), user=Depends(get_current_user)):
    pool = get_pool()
    row = await pool.fetchrow(
        "INSERT INTO chat_messages (chat_id, role, content) VALUES ($1,$2,$3) RETURNING *",
        chat_id, role, content,
    )
    return dict(row)


# ──────────────────────────────────────────────
# CRUD — Widgets
# ──────────────────────────────────────────────

@router.get("/widgets")
async def list_widgets(organization_id: Optional[str] = None, user=Depends(get_current_user)):
    pool = get_pool()
    if organization_id:
        rows = await pool.fetch(
            """SELECT * FROM widgets
               WHERE (user_id = $1 AND organization_id = $2) OR is_public = true
               ORDER BY updated_at DESC""",
            user["id"], organization_id,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM widgets WHERE user_id = $1 OR is_public = true ORDER BY updated_at DESC",
            user["id"],
        )
    return [dict(r) for r in rows]


@router.post("/widgets")
async def create_widget(name: str = Body(...), description: Optional[str] = Body(default=None), html_code: str = Body(default=""), is_public: bool = Body(default=False), organization_id: Optional[str] = Body(default=None), user=Depends(get_current_user)):
    pool = get_pool()
    row = await pool.fetchrow(
        "INSERT INTO widgets (user_id, name, description, html_code, is_public, organization_id) VALUES ($1,$2,$3,$4,$5,$6) RETURNING *",
        user["id"], name, description, html_code, is_public, organization_id,
    )
    return dict(row)


@router.get("/widgets/{widget_id}")
async def get_widget(widget_id: str, user=Depends(get_current_user)):
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM widgets WHERE id = $1", widget_id)
    if not row:
        raise HTTPException(404, "Widget not found")
    return dict(row)


@router.patch("/widgets/{widget_id}")
async def update_widget(widget_id: str, request: Request, user=Depends(get_current_user)):
    body = await request.json()
    pool = get_pool()
    owner = await pool.fetchval("SELECT user_id FROM widgets WHERE id = $1", widget_id)
    if str(owner) != str(user["id"]):
        raise HTTPException(403, "Not the owner of this widget")
    sets, vals, idx = [], [], 1
    for col in ("name", "description", "html_code", "is_public"):
        if col in body:
            sets.append(f"{col} = ${idx}")
            vals.append(body[col])
            idx += 1
    if not sets:
        raise HTTPException(400, "No fields to update")
    vals.append(widget_id)
    await pool.execute(f"UPDATE widgets SET {', '.join(sets)} WHERE id = ${idx}", *vals)
    row = await pool.fetchrow("SELECT * FROM widgets WHERE id = $1", widget_id)
    return dict(row) if row else {}


@router.delete("/widgets/{widget_id}")
async def delete_widget(widget_id: str, user=Depends(get_current_user)):
    pool = get_pool()
    owner = await pool.fetchval("SELECT user_id FROM widgets WHERE id = $1", widget_id)
    if str(owner) != str(user["id"]):
        raise HTTPException(403, "Not the owner of this widget")
    await pool.execute("DELETE FROM widgets WHERE id = $1", widget_id)
    return {"success": True}


# ──────────────────────────────────────────────
# Stats endpoint for dashboard
# ──────────────────────────────────────────────

@router.get("/stats")
async def get_stats(organization_id: str, user=Depends(get_current_user)):
    pool = get_pool()
    boards = await pool.fetchval(
        """SELECT count(*) FROM boards b
           JOIN organization_members om ON om.organization_id = b.organization_id
           WHERE b.organization_id = $1 AND om.user_id = $2""",
        organization_id, user["id"],
    )
    queries = await pool.fetchval(
        """SELECT count(*) FROM board_queries bq
           JOIN boards b ON b.id = bq.board_id
           WHERE b.organization_id = $1""",
        organization_id,
    )
    datastores = await pool.fetchval(
        "SELECT count(*) FROM datastores WHERE user_id = $1 AND organization_id = $2",
        user["id"], organization_id,
    )
    chats = await pool.fetchval(
        "SELECT count(*) FROM chats WHERE user_id = $1 AND organization_id = $2",
        user["id"], organization_id,
    )
    return {"boards": boards, "queries": queries, "datastores": datastores, "chats": chats}


# ──────────────────────────────────────────────
# CRUD — AI Usage
# ──────────────────────────────────────────────

@router.get("/usage")
async def get_usage(
    days: int = 30,
    user=Depends(get_current_user),
):
    """Aggregated token usage grouped by model for the authenticated user."""
    pool = get_pool()
    rows = await pool.fetch(
        """SELECT model, provider,
                  COUNT(*)::int              AS request_count,
                  COALESCE(SUM(input_tokens), 0)::int  AS total_input_tokens,
                  COALESCE(SUM(output_tokens), 0)::int AS total_output_tokens
           FROM ai_usage
           WHERE user_id = $1 AND created_at >= now() - make_interval(days => $2)
           GROUP BY model, provider
           ORDER BY COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0) DESC""",
        user["id"], days,
    )
    total_input = 0
    total_output = 0
    total_requests = 0
    models = []
    for r in rows:
        d = dict(r)
        total_input += d["total_input_tokens"]
        total_output += d["total_output_tokens"]
        total_requests += d["request_count"]
        models.append(d)
    return {
        "models": models,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_requests": total_requests,
        "days": days,
    }


@router.get("/usage/details")
async def get_usage_details(
    days: int = 7,
    limit: int = 100,
    user=Depends(get_current_user),
):
    """Detailed per-request usage log for the authenticated user."""
    pool = get_pool()
    rows = await pool.fetch(
        """SELECT id, model, provider, input_tokens, output_tokens, chat_id, created_at
           FROM ai_usage
           WHERE user_id = $1 AND created_at >= now() - make_interval(days => $2)
           ORDER BY created_at DESC
           LIMIT $3""",
        user["id"], days, limit,
    )
    return [dict(r) for r in rows]


@router.get("/usage/daily")
async def get_usage_daily(
    days: int = 30,
    user=Depends(get_current_user),
):
    """Daily aggregated usage for chart display."""
    pool = get_pool()
    rows = await pool.fetch(
        """SELECT date_trunc('day', created_at)::date AS day,
                  model,
                  COUNT(*)::int AS request_count,
                  COALESCE(SUM(input_tokens), 0)::int  AS input_tokens,
                  COALESCE(SUM(output_tokens), 0)::int AS output_tokens
           FROM ai_usage
           WHERE user_id = $1 AND created_at >= now() - make_interval(days => $2)
           GROUP BY day, model
           ORDER BY day ASC""",
        user["id"], days,
    )
    return [dict(r) for r in rows]
