"""Route package for the Nubi API.

The auth agent (Wave B) attaches its router like this::

    from app.routes import api_router
    api_router.include_router(auth_router, prefix="/auth", tags=["auth"])

``main.py`` mounts ``api_router`` under ``/api/v1``.
"""

from fastapi import APIRouter

api_router = APIRouter()
