"""Browser-captured board screenshots (PUT/GET /boards/{id}/thumbnail).

A screenshot is rendered data uploaded by a client, so what matters is who may
store one, what is accepted, and who it is then served to.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import Response

from app.dashboards import thumbnail_store as ts
from app.errors import AppError
from app.routes import export_share as es

WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 16
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
UPDATED = datetime(2026, 11, 16, 20, 40, tzinfo=timezone.utc)
USER = {"id": "u1"}


class _Request:
    def __init__(self, body: bytes = b"", headers: dict | None = None) -> None:
        self._body = body
        self.headers = headers or {}

    async def body(self) -> bytes:
        return self._body


class _Store:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload_bytes(self, data: bytes, key: str) -> str:
        self.objects[key] = data
        return f"gs://bucket/{key}"

    def download_bytes(self, key: str) -> bytes:
        return self.objects[key]

    def list(self, prefix: str = "") -> list[str]:
        return [k for k in self.objects if k.startswith(prefix)]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


@pytest.fixture
def board(monkeypatch):
    """One board in one org; tests mutate the dict to change spec/org."""
    state = {"org": "org-a", "spec": {"title": "T", "widgets": []}, "updated_at": UPDATED}

    async def _load_board(board_id, user_id, repo, request):
        return {"id": board_id, "name": "T", "updated_at": state["updated_at"]}

    async def _org(user_id, repo, request):
        return state["org"]

    async def _spec(board, org_id, repo):
        return state["spec"]

    monkeypatch.setattr(es, "_load_board", _load_board)
    monkeypatch.setattr(es, "resolve_org_id", _org)
    monkeypatch.setattr(es, "resolve_board_spec", _spec)
    monkeypatch.setattr(ts, "_client", lambda: None)
    es._SHOT_CACHE.clear()
    yield state
    es._SHOT_CACHE.clear()


async def _put(body: bytes, **kw) -> Response:
    return await es.put_board_thumbnail("b1", _Request(body), user=USER, repo=None, **kw)


async def _get(**kw) -> Response:
    return await es.board_thumbnail("b1", _Request(), user=USER, repo=None, **kw)


@pytest.fixture
def render(monkeypatch):
    """Stand in for the SVG route, so a miss is visible without Node."""
    async def _svg(board_id, request, theme=None, cached_only=False, user=None, repo=None):
        return Response(status_code=204) if cached_only else Response(content="<svg/>", media_type="image/svg+xml")

    monkeypatch.setattr(es, "board_thumbnail_svg", _svg)


def test_upload_requires_a_writer():
    route = next(r for r in es.router.routes if r.path.endswith("/{board_id}/thumbnail") and "PUT" in r.methods)
    assert any(d.dependency is es.require_writer for d in route.dependencies)


@pytest.mark.asyncio
async def test_uploaded_screenshot_is_served_in_either_theme(board, render):
    assert (await _put(WEBP)).status_code == 204
    for theme in ("light", "dark"):
        res = await _get(theme=theme)
        assert res.body == WEBP
        assert res.media_type == "image/webp"
        assert res.headers["X-Nubi-Thumb-Kind"] == "shot"
        assert res.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_png_is_accepted_and_typed_by_its_bytes(board, render):
    await _put(PNG)
    assert (await _get()).media_type == "image/png"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"", b"GIF89a" + b"\x00" * 20])
async def test_anything_but_webp_or_png_is_refused(board, body):
    with pytest.raises(AppError) as exc:
        await _put(body)
    assert exc.value.status == 415


@pytest.mark.asyncio
async def test_oversized_upload_is_refused(board):
    with pytest.raises(AppError) as exc:
        await _put(WEBP + b"\x00" * es._SHOT_MAX_BYTES)
    assert exc.value.status == 413


@pytest.mark.asyncio
async def test_picture_of_an_older_version_is_refused(board):
    with pytest.raises(AppError) as exc:
        await _put(WEBP, updated_at="2026-11-16T20:39:00+00:00")
    assert exc.value.status == 409
    # The same instant in another spelling is the same version.
    assert (await _put(WEBP, updated_at="2026-11-16T20:40:00Z")).status_code == 204


@pytest.mark.asyncio
async def test_without_a_screenshot_the_render_is_served(board, render):
    res = await _get()
    assert res.media_type == "image/svg+xml"
    assert res.headers["X-Nubi-Thumb-Kind"] == "render"
    assert (await _get(cached_only=True)).status_code == 204


@pytest.mark.asyncio
async def test_screenshot_does_not_outlive_the_version_it_shows(board, render):
    await _put(WEBP)
    board["spec"] = {"title": "T", "widgets": [{"id": "w1", "type": "text"}]}
    assert (await _get()).headers["X-Nubi-Thumb-Kind"] == "render"


@pytest.mark.asyncio
async def test_screenshot_does_not_cross_a_policy_boundary(board, render, monkeypatch):
    await _put(WEBP)
    monkeypatch.setattr(es, "_collect_claims", lambda user: {"policies": {"region": ["ZA"]}})
    assert (await _get()).headers["X-Nubi-Thumb-Kind"] == "render"


@pytest.mark.asyncio
async def test_screenshot_is_read_back_from_storage(board, render, monkeypatch):
    store = _Store()
    monkeypatch.setattr(ts, "_client", lambda: store)
    await _put(WEBP)
    assert len(store.objects) == 1
    es._SHOT_CACHE.clear()          # another worker, or after a restart
    assert (await _get()).body == WEBP


def test_pruning_superseded_renders_keeps_the_screenshot(monkeypatch):
    store = _Store()
    monkeypatch.setattr(ts, "_client", lambda: store)
    shot = ts.shot_key("b1", "hash", "fp")
    store.objects[shot] = WEBP
    store.objects["boards/b1/old-light-fp-r1.svg"] = b"<svg/>"
    ts.prune_other_versions("b1", {shot})
    assert list(store.objects) == [shot]


@pytest.mark.parametrize(
    "uri,expects_s3_kwargs",
    [("s3://bucket/", True), ("gs://bucket/", False), ("file:///tmp/x", False)],
)
def test_only_s3_storage_is_given_s3_credentials(monkeypatch, uri, expects_s3_kwargs):
    monkeypatch.setenv("NUBI_THUMBNAIL_STORAGE_URI", uri)
    assert bool(ts._creds()) is expects_s3_kwargs
