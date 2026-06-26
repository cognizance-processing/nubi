"""Modal remote kernel runner — INTERNAL FORWARD-COMPAT STUB ONLY.

NOT ADVERTISED / NOT FOR PRODUCTION USE
-----------------------------------------
This module is an internal forward-compat skeleton.  ``ModalRunner.run()``
always raises ``AppError("kernel_unavailable", 503)`` regardless of
credentials or SDK availability.  Setting ``KERNEL_REMOTE_PROVIDER=modal``
will NOT produce a working remote kernel.

The only production-viable remote provider is E2B:
    KERNEL_REMOTE_PROVIDER=e2b
    E2B_API_KEY=e2b-...
    pip install e2b-code-interpreter

When to implement
-----------------
This stub exists so that the route layer can be code-complete (``_choose_runner``
has a Modal branch) without shipping a broken advertised feature.  To promote it
to a real provider:

1. Implement ``ModalRunner.run()`` using Modal's async SDK (stub + @modal.function).
2. Add ``modal`` to the list of valid ``KERNEL_REMOTE_PROVIDER`` values in config.py.
3. Update CAPABILITIES.md and the /compute/run docstring.
4. Run the test suite with a real Modal sandbox credential.

Modal SDK lazy-import note
--------------------------
The ``modal`` package is NOT a hard dependency.  It is imported lazily inside
``run()`` so that the server starts without it installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pyarrow as pa

from app.errors import AppError

if TYPE_CHECKING:  # resolves the string return annotation without a circular import
    from app.compute.runner import KernelResult


# ---------------------------------------------------------------------------
# Patchable indirection for tests
# ---------------------------------------------------------------------------


def _get_modal_module():
    """Return the ``modal`` module (lazy import).

    Separated so tests can monkeypatch this to inject a fake Modal module.

    Raises
    ------
    ImportError
        If the ``modal`` package is not installed.
    """
    import modal  # noqa: PLC0415  (lazy import)

    return modal


# ---------------------------------------------------------------------------
# ModalRunner
# ---------------------------------------------------------------------------


class ModalRunner:
    """Execute user code on Modal's serverless infrastructure.

    This is an adapter that follows the same :class:`~app.compute.runner.KernelResult`
    contract as :class:`~app.compute.remote_e2b.E2BRunner`.  E2B is the primary
    tested remote path; Modal support is provided as an alternative.

    Parameters
    ----------
    token_id:
        Modal token ID (from ``MODAL_TOKEN_ID`` env var).
    token_secret:
        Modal token secret (from ``MODAL_TOKEN_SECRET`` env var).
    timeout_s:
        Default execution timeout in seconds.
    """

    tier: str = "remote_kernel"

    def __init__(
        self,
        token_id: str,
        token_secret: str,
        timeout_s: int = 30,
    ) -> None:
        self._token_id = token_id
        self._token_secret = token_secret
        self._timeout_s = timeout_s
        # Do NOT connect at construction time.

    def run(
        self,
        code: str,
        inputs: dict[str, pa.Table],
        timeout_s: int,
    ) -> "KernelResult":  # type: ignore[name-defined]
        """Run *code* on Modal and return a :class:`~app.compute.runner.KernelResult`.

        Raises
        ------
        AppError("kernel_unavailable", 503)
            If ``modal`` is not installed or credentials are absent.
        AppError("kernel_error", 400)
            If execution fails.
        AppError("kernel_timeout", 504)
            If execution times out.
        """

        # ── 0. Guard: credentials required ────────────────────────────────────
        if not self._token_id or not self._token_secret:
            raise AppError(
                "kernel_unavailable",
                "remote kernel (Modal) not configured/installed: "
                "MODAL_TOKEN_ID or MODAL_TOKEN_SECRET is empty.",
                503,
            )

        # ── 1. Lazy-import Modal SDK ──────────────────────────────────────────
        try:
            _get_modal_module()  # availability check; raises if the SDK is absent
        except ImportError as exc:
            raise AppError(
                "kernel_unavailable",
                f"remote kernel (Modal) not configured/installed: {exc}",
                503,
            ) from exc

        # ── NOTE: Primary tested path is E2B. ─────────────────────────────────
        # The Modal execution body below is a forward-compatible stub.
        # A full Modal implementation would:
        #   1. Authenticate via modal.config.Config(token_id=..., token_secret=...).
        #   2. Define a modal.Stub + @modal.function wrapping the Arrow harness.
        #   3. Call the remote function with the serialised inputs and code.
        #   4. Deserialise the returned Arrow IPC bytes.
        # This requires Modal-specific async deployment semantics that differ
        # significantly from E2B's synchronous sandbox model.
        # TODO: implement full Modal body when Modal is chosen as the provider.

        raise AppError(
            "kernel_unavailable",
            "Modal remote kernel is not yet fully implemented.  "
            "Use KERNEL_REMOTE_PROVIDER=e2b for production remote execution.",
            503,
        )
