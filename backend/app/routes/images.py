"""Upload/serve dashboard branding images (logos, header images, "image" widgets).

POST /images
    Multipart file upload from the editor UI (or any authenticated client).
    Returns ``{id, url, content_type, size}``.

POST /images/from-url
    Fetch an image from a URL server-side (SSRF-guarded) and store it.
    Same response shape as ``POST /images``.

GET /images/{image_id}
    Streams back the raw bytes + content-type stored by either of the above
    (or by the ``upload_image`` AI/MCP tool — same storage, same ids).

    Deliberately UNAUTHENTICATED: a plain ``<img src="...">`` (in a dashboard's
    ``image``/``html`` widget) cannot attach an ``Authorization: Bearer``
    header, and this app has no cookie-session auth to piggyback on. Access
    control instead relies on ``image_id`` being an unguessable random token
    (``uuid4().hex``, 128 bits) — the same trust model as a signed-URL-free
    "unlisted" share link. Acceptable for local-dev branding assets; do not
    store anything sensitive through this path.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Response, UploadFile
from pydantic import BaseModel

from app.auth.deps import current_user
from app.dashboards.images import fetch_image_from_url, load_image, save_image_bytes
from app.errors import AppError
from app.routes import api_router

router = APIRouter(tags=["images"])


class ImageFromUrlIn(BaseModel):
    """Request body for ``POST /images/from-url``."""

    url: str


@router.post("", status_code=201)
async def upload_image(
    file: UploadFile = File(...),
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Upload an image file (multipart) and return its servable URL."""
    data = await file.read()
    content_type = file.content_type or ""
    result = save_image_bytes(data, content_type)
    return {**result, "url": f"/api/v1/images/{result['id']}"}


@router.post("/from-url", status_code=201)
async def upload_image_from_url(
    body: ImageFromUrlIn,
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Fetch an image from a URL (SSRF-guarded) and return its servable URL."""
    result = fetch_image_from_url(body.url)
    return {**result, "url": f"/api/v1/images/{result['id']}"}


@router.get("/{image_id}")
async def get_image(image_id: str) -> Response:
    """Return the stored image's raw bytes with its original content-type."""
    try:
        data, content_type = load_image(image_id)
    except FileNotFoundError:
        raise AppError("image_not_found", f"No image with id {image_id!r}.", 404) from None
    return Response(content=data, media_type=content_type)


api_router.include_router(router, prefix="/images")
