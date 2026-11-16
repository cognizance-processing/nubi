"""Reads that take data out of the product, and profile changes, leave an audit trail."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

ORG = str(uuid.uuid4())
USER = str(uuid.uuid4())


@pytest.mark.parametrize(
    "path,action",
    [
        ("/api/v1/boards/b1/export.csv", "board.export"),
        ("/api/v1/boards/b1/export.json", "board.export"),
        ("/api/v1/boards/b1/export.pdf", "board.export"),
        ("/api/v1/export/board/b1", "resource.export"),
        ("/api/v1/projects/p1/export", "project.export"),
        ("/api/v1/boards/b1/thumbnail.svg", "board.thumbnail"),
        ("/api/v1/embed/config/b1", "embed.config"),
        ("/api/v1/query/registry", "query_registry.list"),
    ],
)
def test_sensitive_reads_are_audited(path, action):
    from app.middleware.audit import _audited_read

    assert _audited_read("GET", path) == action


@pytest.mark.parametrize("path", ["/api/v1/boards", "/api/v1/boards/b1", "/api/v1/query/registry/q1", "/api/v1/health"])
def test_ordinary_reads_are_not(path):
    from app.middleware.audit import _audited_read

    assert _audited_read("GET", path) is None


def test_profile_changes_are_audited_but_sign_in_is_not():
    from app.middleware.audit import _should_audit

    assert _should_audit("PATCH", "/api/v1/auth/me") is True
    assert _should_audit("POST", "/api/v1/auth/login") is False
    assert _should_audit("POST", "/api/v1/auth/refresh") is False


def _app():
    from app.middleware.audit import register_audit_middleware

    app = FastAPI()
    register_audit_middleware(app)

    @app.get("/api/v1/boards/{board_id}/export.csv")
    async def export(board_id: str, request: Request):
        # What the shared org resolver does for every authenticated route.
        request.state.resolved_org_id = ORG
        request.state.resolved_user_id = USER
        return {"ok": True}

    @app.get("/api/v1/boards/{board_id}")
    async def read(board_id: str, request: Request):
        request.state.resolved_org_id = ORG
        request.state.resolved_user_id = USER
        return {"ok": True}

    return TestClient(app)


def test_export_is_recorded_with_the_resolved_org_and_user():
    record = AsyncMock()
    with patch("app.audit.record_audit", new=record):
        client = _app()
        assert client.get("/api/v1/boards/b1/export.csv").status_code == 200
        assert client.get("/api/v1/boards/b1").status_code == 200
    assert record.await_count == 1
    kwargs = record.await_args.kwargs
    assert kwargs["action"] == "board.export"
    assert kwargs["org_id"] == ORG and kwargs["actor_user_id"] == USER
    assert kwargs["resource_id"] == "b1"


@pytest.mark.asyncio
async def test_the_org_resolver_records_what_it_resolved():
    from starlette.requests import Request as StarletteRequest

    from app.repos.memory import InMemoryRepo
    from app.routes._org import resolve_org_id

    repo = InMemoryRepo()
    repo.seed_org_member(ORG, USER, "member")
    request = StarletteRequest({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b"", "state": {}})
    assert await resolve_org_id(USER, repo, request) == ORG
    assert request.state.resolved_org_id == ORG and request.state.resolved_user_id == USER
