"""Network-mode resolution for connector egress — M22-A.

Design
------
A datastore row carries two new fields (added by migration):

    ``network_mode``  TEXT  — one of: 'direct' | 'bridge' | 'ssh_tunnel'
                              | 'psc' | 'cloudsql_proxy'  (DEFAULT 'direct')
    ``bridge_id``     UUID  — FK → bridges.id; set when mode != 'direct'

Before building a connector, the query route calls ``resolve_network`` with the
datastore config dict and (if present) the pre-fetched bridge row.  The function
validates that the requested transport is available and returns a ``NetworkTarget``
that the connector factory receives.

Transport availability matrix (current milestone)
--------------------------------------------------
mode='direct'
    Egress goes directly from the Nubi backend to the database host/port.
    No extra infrastructure required.  ``NetworkTarget.host`` / ``.port``
    are taken verbatim from the datastore config.

mode in ('bridge', 'ssh_tunnel', 'psc', 'cloudsql_proxy')
    These modes require a provisioned bridge agent or tunnel process.
    The transport layer is NOT yet implemented; calling resolve_network with
    any of these modes raises ``AppError("network_mode_unavailable", 501)``.

    Stub points for future implementation
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    When a real tunnel transport is added, the implementor should:

    1. Add a branch in ``resolve_network`` for the new mode.
    2. Negotiate the tunnel (e.g. connect to the bridge WebSocket, start an
       SSH forward, acquire a Cloud SQL Connector socket).
    3. Return a ``NetworkTarget`` with ``host`` / ``port`` pointing at the
       local tunnel endpoint (e.g. ``"127.0.0.1"`` + ephemeral port).
    4. Ensure the tunnel is torn down in the connector's close/context-manager
       lifecycle — ``NetworkTarget.cleanup`` (a no-op callable by default) is
       provided for this purpose.

    The connector factories themselves need no changes — they always receive
    a plain ``(host, port)`` NetworkTarget and are agnostic of how the
    reachability was established.

Public API
----------
``resolve_network(datastore_config, bridge)`` → ``NetworkTarget``
    The only public entry point.  Safe to call from sync or async contexts
    (the function itself is synchronous; actual tunnel negotiation, when
    implemented, will be async and will need a wrapper).

``NetworkTarget``
    A lightweight dataclass holding ``host``, ``port``, and an optional
    ``cleanup`` callable.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

from app.errors import AppError

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

# Modes that are recognised by the schema but not yet transport-implemented.
_UNIMPLEMENTED_MODES: frozenset[str] = frozenset(
    {"ssh_tunnel", "psc", "cloudsql_proxy"}
)


@dataclass
class NetworkTarget:
    """Resolved network endpoint for a datastore connection.

    Attributes
    ----------
    host:
        Hostname or IP address that the connector should connect to.
        For ``direct`` mode this is taken verbatim from the datastore config.
        For tunnel modes (future) this is the local tunnel endpoint address.
    port:
        TCP port number (integer).  ``None`` is accepted and forwarded as-is
        to the connector (the connector applies its own default).
    mode:
        The ``network_mode`` that was resolved.  Informational; connectors
        do not need to inspect this.
    cleanup:
        A no-arg callable invoked after the connector is done to tear down
        any tunnel/proxy that was set up during resolution.  For ``direct``
        mode this is always a no-op.

    Notes
    -----
    Connectors must not hold a reference to this object beyond the lifetime
    of a single execute() call.  When tunnel transports are implemented,
    ``cleanup`` must be called (or the target used as a context manager) to
    avoid leaking OS-level connections.
    """

    host: str | None
    port: int | None
    mode: str
    cleanup: Callable[[], None] = field(default=lambda: None, compare=False)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def resolve_network_async(
    datastore_config: dict[str, Any],
    bridge: dict[str, Any] | None = None,
) -> NetworkTarget:
    """Async version of :func:`resolve_network`.

    For ``mode='bridge'`` this performs actual I/O (starting a local TCP proxy
    via the ``BridgeBroker``).  For all other modes it simply delegates to the
    synchronous helper.

    Parameters
    ----------
    datastore_config:
        See :func:`resolve_network`.
    bridge:
        The pre-fetched bridge row.  For ``bridge`` mode the ``id`` field is
        used to look up the connected agent in the ``BridgeBroker``.

    Returns
    -------
    NetworkTarget
        For ``bridge`` mode: a ``NetworkTarget`` pointing at a local ephemeral
        TCP proxy (127.0.0.1:port) with a ``cleanup`` coroutine that tears down
        the proxy.  The cleanup function is sync (wraps the async close in a
        fire-and-forget task) so it matches the ``Callable[[], None]`` signature.
    """
    mode: str = (datastore_config.get("network_mode") or "direct").strip().lower()

    if mode == "bridge":
        from app.bridges.broker import get_broker  # lazy import to avoid circular deps

        if bridge is None:
            raise AppError(
                "bridge_not_configured",
                "network_mode='bridge' requires a bridge_id in the datastore config "
                "and a pre-fetched bridge row.",
                400,
            )

        bridge_id: str = str(bridge.get("id") or datastore_config.get("bridge_id") or "")
        if not bridge_id:
            raise AppError(
                "bridge_id_missing",
                "network_mode='bridge' requires a valid bridge_id.",
                400,
            )

        host: str = datastore_config.get("host") or ""
        port_raw = datastore_config.get("port")
        if not host or port_raw is None:
            raise AppError(
                "bridge_target_missing",
                "network_mode='bridge' requires 'host' and 'port' in the datastore config.",
                400,
            )
        port = int(port_raw)

        broker = get_broker()
        if not broker.is_connected(bridge_id):
            raise AppError(
                "bridge_not_connected",
                f"Bridge {bridge_id!r} has no connected agent. "
                "Start the bridge agent process inside the VPC and wait for it to register.",
                501,
            )

        local_host, local_port = await broker.open_tcp_proxy(bridge_id, host, port)

        # Build a sync cleanup shim that fires off the async close.
        def _cleanup() -> None:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(broker.close_tcp_proxy(local_host, local_port))
                else:
                    loop.run_until_complete(broker.close_tcp_proxy(local_host, local_port))
            except Exception:
                pass

        return NetworkTarget(
            host=local_host,
            port=local_port,
            mode="bridge",
            cleanup=_cleanup,
        )

    # Fall through to the synchronous resolver for all other modes.
    return resolve_network(datastore_config, bridge)


def resolve_network(
    datastore_config: dict[str, Any],
    bridge: dict[str, Any] | None = None,
) -> NetworkTarget:
    """Resolve the effective network target for a datastore connection.

    Reads ``datastore_config["network_mode"]`` (default ``"direct"``) and
    returns a :class:`NetworkTarget` describing where the connector should
    actually connect.

    Parameters
    ----------
    datastore_config:
        The ``config`` dict stored on the datastore row.  Expected keys:

        ``network_mode`` (str, optional)
            Transport mode.  Defaults to ``"direct"`` when absent.
        ``bridge_id`` (str, optional)
            UUID of the bridge row.  Informational at this layer; the
            caller is responsible for fetching the bridge row and passing it
            as *bridge*.
        ``host`` (str, optional)
            Database host (used for ``direct`` mode).
        ``port`` (int, optional)
            Database port (used for ``direct`` mode).

    bridge:
        The pre-fetched bridge row dict (from the ``bridges`` table), or
        ``None`` if no bridge is associated with this datastore.  Currently
        unused because the tunnel transport is not yet implemented.  When
        real transport is added, this row supplies the bridge's endpoint
        (websocket URL, public key, etc.).

    Returns
    -------
    NetworkTarget
        For ``"direct"`` mode: ``host``/``port`` taken verbatim from the
        config.  For all other modes: raises before returning.

    Raises
    ------
    app.errors.AppError
        ``code="network_mode_unavailable"`` (501) if the requested mode is
        not ``"direct"``.  The error message names the specific mode and
        explains which infrastructure layer is missing.
    app.errors.AppError
        ``code="unknown_network_mode"`` (400) if the mode string is not in
        the known set.

    Examples
    --------
    >>> target = resolve_network({"host": "db.example.com", "port": 5432})
    >>> target.mode
    'direct'
    >>> target.host
    'db.example.com'

    >>> resolve_network({"network_mode": "bridge", "bridge_id": "abc"}, bridge={})
    # raises AppError("network_mode_unavailable", ..., 501)
    """
    mode: str = (datastore_config.get("network_mode") or "direct").strip().lower()

    # ── direct mode: pass-through ──────────────────────────────────────────────
    if mode == "direct":
        return NetworkTarget(
            host=datastore_config.get("host"),
            port=datastore_config.get("port"),
            mode="direct",
            # cleanup is a no-op (default in the dataclass)
        )

    # ── bridge mode (sync path — use resolve_network_async for real proxy) ────
    if mode == "bridge":
        # The synchronous path cannot start the async TCP proxy.
        # Callers that need a real proxy must use resolve_network_async().
        # We surface a clear 501 here so that any remaining sync callers
        # receive an informative error rather than a silent stub.
        from app.bridges.broker import get_broker  # lazy import

        bridge_id = str(
            (bridge or {}).get("id") or datastore_config.get("bridge_id") or ""
        )
        broker = get_broker()
        if bridge_id and broker.is_connected(bridge_id):
            raise AppError(
                "network_mode_unavailable",
                "network_mode='bridge' requires async resolution. "
                "Call resolve_network_async() from an async context. "
                "A bridge agent is connected and the tunnel is ready for use via the async path.",
                501,
            )
        raise AppError(
            "network_mode_unavailable",
            "network_mode='bridge' reachability requires a provisioned bridge agent. "
            "A provisioned bridge agent must be running and registered before 'bridge' mode "
            "can be used. Start the bridge agent process inside the VPC and wait for it to "
            "register. Use resolve_network_async() for full async proxy support.",
            501,
        )

    # ── not-yet-implemented tunnel/bridge modes ────────────────────────────────
    if mode in _UNIMPLEMENTED_MODES:
        _mode_hints: dict[str, str] = {
            "ssh_tunnel": (
                "The SSH tunnel transport has not been provisioned. "
                "Deploy the Nubi SSH bridge sidecar and register it via POST /bridges."
            ),
            "psc": (
                "Private Service Connect requires a VPC attachment and "
                "a provisioned PSC endpoint in your GCP project — "
                "automatic provisioning is not yet supported in this release."
            ),
            "cloudsql_proxy": (
                "The Cloud SQL Auth Proxy sidecar is not running alongside "
                "this instance. Deploy cloud-sql-proxy with the correct "
                "instance connection name and update network_mode to 'direct' "
                "pointing at 127.0.0.1 until native proxy support ships."
            ),
        }
        hint = _mode_hints.get(mode, f"Mode '{mode}' reachability is not yet implemented.")
        raise AppError(
            "network_mode_unavailable",
            f"network_mode='{mode}' reachability requires a provisioned "
            f"bridge/tunnel — not yet enabled. {hint}",
            501,
        )

    # ── unknown mode ───────────────────────────────────────────────────────────
    known = {"direct", "bridge"} | _UNIMPLEMENTED_MODES
    raise AppError(
        "unknown_network_mode",
        f"network_mode='{mode}' is not a recognised value. "
        f"Valid modes: {sorted(known)!r}.",
        400,
    )
