"""Typed application error and FastAPI exception-handler registration.

Usage
-----
Raise ``AppError`` anywhere in route/service code::

    raise AppError("USER_NOT_FOUND", "No user with that email.", status=404)

Register the handler once during app startup::

    from app.errors import register_handlers
    register_handlers(app)

All unhandled ``AppError`` instances are serialised as::

    {"error": {"code": "...", "message": "..."}}
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    pass  # avoid circular imports at runtime


class AppError(Exception):
    """Domain-level application error with a machine-readable code.

    Parameters
    ----------
    code:
        Stable, upper-snake-case error code (e.g. ``"INVALID_CREDENTIALS"``).
    message:
        Human-readable description — **must not** contain secrets.
    status:
        HTTP status code that will be used in the response (default 400).
    """

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    """Convert an ``AppError`` into the standard error envelope."""
    return JSONResponse(
        status_code=exc.status,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


def register_handlers(app: FastAPI) -> None:
    """Attach all application-level exception handlers to *app*.

    Call this once, early in ``main.py``, before the first request arrives.

    Parameters
    ----------
    app:
        The FastAPI application instance.
    """
    app.add_exception_handler(AppError, _app_error_handler)  # type: ignore[arg-type]
