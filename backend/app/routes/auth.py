from typing import Optional

from fastapi import APIRouter, HTTPException, Body, Depends

from ..db import get_pool
from ..auth import (
    get_current_user, hash_password, verify_password,
    create_token, exchange_google_code,
)

router = APIRouter(tags=["auth"])


@router.post("/auth/signup")
async def auth_signup(email: str = Body(...), password: str = Body(...), full_name: Optional[str] = Body(default=None)):
    pool = get_pool()
    existing = await pool.fetchrow("SELECT id FROM users WHERE email = $1", email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")
    hashed = hash_password(password)
    row = await pool.fetchrow(
        "INSERT INTO users (email, password_hash, full_name) VALUES ($1,$2,$3) RETURNING id, email, full_name",
        email, hashed, full_name,
    )
    token = create_token(str(row["id"]), row["email"])
    return {"token": token, "user": {"id": str(row["id"]), "email": row["email"], "full_name": row["full_name"]}}


@router.post("/auth/signin")
async def auth_signin(email: str = Body(...), password: str = Body(...)):
    pool = get_pool()
    row = await pool.fetchrow("SELECT id, email, password_hash, full_name FROM users WHERE email = $1", email)
    if not row or not row["password_hash"]:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_token(str(row["id"]), row["email"])
    return {"token": token, "user": {"id": str(row["id"]), "email": row["email"], "full_name": row["full_name"]}}


@router.post("/auth/google")
async def auth_google(code: str = Body(...), redirect_uri: str = Body(...)):
    info = await exchange_google_code(code, redirect_uri)
    google_id = info.get("id")
    email = info.get("email")
    name = info.get("name")

    pool = get_pool()
    row = await pool.fetchrow("SELECT id, email, full_name FROM users WHERE google_id = $1", google_id)
    if not row:
        row = await pool.fetchrow("SELECT id, email, full_name, google_id FROM users WHERE email = $1", email)
        if row:
            await pool.execute("UPDATE users SET google_id=$1, full_name=COALESCE(full_name,$2) WHERE id=$3", google_id, name, row["id"])
        else:
            row = await pool.fetchrow(
                "INSERT INTO users (email, google_id, full_name) VALUES ($1,$2,$3) RETURNING id, email, full_name",
                email, google_id, name,
            )
    token = create_token(str(row["id"]), row["email"])
    return {"token": token, "user": {"id": str(row["id"]), "email": row["email"], "full_name": row.get("full_name", name)}}


@router.get("/auth/me")
async def auth_me(user=Depends(get_current_user)):
    return {"user": user}
