"""Where user-supplied Python is allowed to run — the single source of truth.

Every path that executes caller-authored Python (the compute kernel, flow
Python cells, SDK compile, scheduled and worker-driven flow runs) asks this
module.  The rule:

* A configured remote sandbox (``KERNEL_REMOTE_PROVIDER=e2b`` + ``E2B_API_KEY``)
  is used in every environment.
* Running on the application host is a development convenience only: it
  needs ``ENV != "production"`` AND ``KERNEL_LOCAL_ENABLED``.  The local
  subprocess sandbox shares the host's user, network and filesystem, so it is
  never used in production.
* Otherwise the request is refused with 503.

``app.compute.sandbox.run_sandboxed`` — the only primitive that spawns such a
subprocess — enforces :func:`require_host_python` itself, so a new call site
cannot bypass the rule by forgetting to ask.
"""

from __future__ import annotations

from typing import Any

from app.errors import AppError


def _settings() -> Any:
    from app.config import get_settings  # noqa: PLC0415

    return get_settings()


def remote_sandbox_configured() -> bool:
    """True when a production-grade remote sandbox is configured."""
    settings = _settings()
    provider = (settings.KERNEL_REMOTE_PROVIDER or "").lower().strip()
    return provider == "e2b" and bool(settings.E2B_API_KEY)


def host_python_allowed() -> bool:
    """True when user Python may run in a subprocess on this host (dev only)."""
    settings = _settings()
    return bool(settings.KERNEL_LOCAL_ENABLED) and settings.ENV != "production"


def require_host_python(feature: str = "Code execution") -> None:
    """Raise 503 unless user Python may run on this host.

    Used by features whose executor only exists as a host subprocess (flow
    Python cells, SDK compile).  In production these stay unavailable until
    they run on the remote sandbox.
    """
    if host_python_allowed():
        return
    raise AppError(
        "kernel_disabled",
        f"{feature} is not available in this environment: user code only runs "
        "in a remote sandbox in production, and this feature has no remote "
        "sandbox configured for it.  For local development set "
        "ENV=development and KERNEL_LOCAL_ENABLED=true.",
        503,
    )


def spec_runs_host_python(spec: Any) -> bool:
    """True when a flow spec (dict / model dump) contains a Python cell anywhere,
    including inside map bodies and branches."""
    if hasattr(spec, "model_dump"):
        spec = spec.model_dump()
    stack = [spec]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("kind") == "python" or node.get("type") == "python":
                return True
            stack.extend(node.values())
        elif isinstance(node, (list, tuple)):
            stack.extend(node)
    return False


def require_spec_runnable(spec: Any) -> None:
    """Refuse (503) a flow spec whose Python cells could not run here."""
    if spec_runs_host_python(spec):
        require_host_python("Running Python cells")


def choose_runner() -> Any:
    """Return the kernel runner for this environment.

    1. Remote sandbox (E2B) when configured — any environment.
    2. Modal stub when configured (its ``run`` always raises 503).
    3. Local subprocess runner — development only (see module docstring).
    4. Otherwise ``AppError("kernel_disabled", 503)``.
    """
    settings = _settings()
    provider = (settings.KERNEL_REMOTE_PROVIDER or "").lower().strip()

    if provider == "e2b" and settings.E2B_API_KEY:
        from app.compute.remote_e2b import E2BRunner  # noqa: PLC0415

        # Plumb the custom attribution template explicitly (empty -> base image).
        return E2BRunner(
            api_key=settings.E2B_API_KEY,
            timeout_s=30,
            template=settings.E2B_TEMPLATE or None,
        )

    if provider == "modal" and settings.MODAL_TOKEN_ID and settings.MODAL_TOKEN_SECRET:
        from app.compute.remote_modal import ModalRunner  # noqa: PLC0415

        return ModalRunner(
            token_id=settings.MODAL_TOKEN_ID,
            token_secret=settings.MODAL_TOKEN_SECRET,
            timeout_s=30,
        )

    if host_python_allowed():
        from app.compute.runner import LocalSubprocessRunner  # noqa: PLC0415

        return LocalSubprocessRunner()

    raise AppError(
        "kernel_disabled",
        "No kernel runner is configured.  "
        "In production, set KERNEL_REMOTE_PROVIDER=e2b and E2B_API_KEY to "
        "enable safe remote code execution via E2B Firecracker microVMs.  "
        "For local development, set ENV=development and KERNEL_LOCAL_ENABLED=true.",
        503,
    )
