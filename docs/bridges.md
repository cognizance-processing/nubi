# Bridges

![Nubi self-host topology: bridge agent in the VPC, Nubi backend in the cloud](illustration:SelfHostTopology)

A Nubi **bridge** is a lightweight agent that runs inside your VPC or on-prem network and proxies database connections to the Nubi backend over an outbound WebSocket tunnel. Private databases that are not reachable from the public internet can be queried without opening any inbound firewall ports.

---

## How it works

```
Bridge agent (in your VPC)
  ↕  outbound WebSocket → wss://<control-plane>/api/v1/bridges/{id}/connect
Nubi backend (BridgeBroker)
  ↕  local TCP proxy (127.0.0.1:<ephemeral-port>)
Connector (Postgres / MySQL / …)
```

1. The bridge agent starts inside your VPC and dials `WS /api/v1/bridges/{id}/connect`, supplying its secret token in the `X-Bridge-Token` header (or `?token=` query param).
2. The backend validates the token against `bridge.config["token"]`. On success it accepts the WebSocket and marks `status='online'`.
3. The `BridgeBroker` registers the live WebSocket in an **in-memory registry**. Connections do not persist across backend restarts — the agent must reconnect when the backend restarts.
4. When a query targets a connector with `network_mode="bridge"`, the backend calls `resolve_network_async()`, which calls `broker.open_tcp_proxy(bridge_id, host, port)`.
5. `open_tcp_proxy` starts an ephemeral TCP listener on `127.0.0.1` with an OS-assigned port and tunnels every TCP connection through the bridge agent to the target host inside the VPC.
6. The connector receives a `NetworkTarget(host="127.0.0.1", port=<local>, mode="bridge")` and connects as if the database were local.

If the bridge agent is not connected when a query is made, `open_tcp_proxy` raises `AppError("bridge_not_connected", 503)` and the request fails immediately rather than timing out silently.

---

## REST endpoints

All CRUD endpoints require a valid first-party Bearer token. Operations are org-scoped — users can only see and manage bridges that belong to their org.

| Method | Path | Status | Notes |
|--------|------|--------|-------|
| `POST` | `/api/v1/bridges` | 201 | Create a bridge record. |
| `GET` | `/api/v1/bridges` | 200 | List bridges for the caller's org. |
| `GET` | `/api/v1/bridges/{id}` | 200 / 404 | 404 if not found or wrong org. |
| `DELETE` | `/api/v1/bridges/{id}` | 204 / 404 | 404 if not found or wrong org. |
| `POST` | `/api/v1/bridges/{id}/heartbeat` | 200 / 404 | Updates `status='online'` and `last_seen_at`. |
| `WS` | `/api/v1/bridges/{id}/connect` | — | WebSocket endpoint for the bridge agent. |

### Bridge record shape

```json
{
  "id":           "uuid",
  "org_id":       "uuid",
  "created_by":   "uuid",
  "name":         "prod-vpc-bridge",
  "status":       "online",
  "last_seen_at": "2026-06-09T07:00:01+00:00",
  "config":       { "token": "secret-agent-token" },
  "created_at":   "2026-06-08T09:00:00+00:00",
  "updated_at":   "2026-06-09T07:00:01+00:00"
}
```

`status` is `"offline"` at creation. It transitions to `"online"` when the agent connects via WebSocket or sends a heartbeat. On disconnect, `status` is left unchanged (heartbeat TTL monitoring handles the offline transition in production).

> **Current milestone:** bridges are stored in an in-process in-memory store (backed by a Python dict, not the database). The interface is designed for a mechanical swap to the already-deployed DB `bridges` table (migration 0009); the swap is a pending code change in `backend/app/routes/bridges.py`.

---

## Setting up a bridge

### Step 1 — Create the bridge record

```json
POST /api/v1/bridges
Authorization: Bearer <first-party-token>

{
  "name": "prod-vpc-bridge",
  "config": {
    "token": "my-secret-agent-token"
  }
}
```

Save the returned `id` — the agent needs it.

### Step 2 — Configure the connector

Create or update a connector with `network_mode="bridge"` and `bridge_id` pointing at the bridge UUID:

```json
POST /api/v1/connectors
Authorization: Bearer <first-party-token>

{
  "name": "prod-private-postgres",
  "type": "postgres",
  "config": {
    "host":         "db.internal.corp",
    "port":         5432,
    "database":     "analytics",
    "user":         "readonly",
    "sslmode":      "require",
    "network_mode": "bridge",
    "bridge_id":    "<bridge-uuid>"
  },
  "secret": {
    "password": "db-password"
  }
}
```

Database credentials are stored separately in `connector_secrets` (AES-256-GCM encrypted) and are never mixed with the bridge token. See [Connector security](/docs/connector-security) for details.

### Step 3 — Start the bridge agent

Run the agent inside the VPC:

```bash
BRIDGE_ID=<bridge-uuid> \
BRIDGE_TOKEN=my-secret-agent-token \
CONTROL_PLANE_URL=wss://api.nubi.dev/api/v1 \
  python -m app.bridges.agent
```

Optional:

```bash
BRIDGE_RECONNECT_DELAY=5   # seconds between reconnect attempts (default 5)
```

The agent connects, authenticates, and is registered with the `BridgeBroker`. It reconnects automatically if the WebSocket drops.

---

## WebSocket authentication

The agent supplies its token in **one** of:

- `X-Bridge-Token: <token>` request header
- `?token=<token>` query parameter

The backend validates the token against `bridge.config["token"]`.

| Condition | WebSocket close code |
|-----------|----------------------|
| No token supplied | `4401` |
| Token does not match | `4401` |
| Bridge ID not found | `4404` |
| Token valid | Connection accepted |

---

## Network modes

| Mode | Status | Description |
|------|--------|-------------|
| `direct` | Available | Egress goes directly from the Nubi backend to the database. No extra infrastructure. |
| `bridge` | Available | Routes through the bridge agent via WebSocket TCP proxy. |
| `ssh_tunnel` | Planned (501) | SSH tunnel transport — not yet implemented. |
| `psc` | Planned (501) | GCP Private Service Connect — not yet implemented. |
| `cloudsql_proxy` | Planned (501) | Cloud SQL Auth Proxy — not yet implemented. |

Requesting an unimplemented mode returns `AppError("network_mode_unavailable", 501)` with a message describing what infrastructure is required.

### Sync vs async resolution

`resolve_network()` (sync) always returns `501` for `bridge` mode — it cannot start an async TCP proxy. The actual bridge proxy requires `resolve_network_async()`, which the query route calls. Callers that remain on the sync path will receive a clear `501` error pointing them to the async API.

---

## BridgeBroker and the TCP proxy protocol

`BridgeBroker` (`app.bridges.broker`) is a module-level singleton that holds the live registry of connected bridge WebSockets.

### Opening a proxy

When `resolve_network_async` needs a connection through a bridge:

1. `broker.open_tcp_proxy(bridge_id, host, port)` is called.
2. If no agent is registered for `bridge_id`, `AppError("bridge_not_connected", 503)` is raised immediately.
3. Otherwise, an ephemeral `asyncio` TCP listener starts on `127.0.0.1:<OS-assigned-port>`.
4. For every inbound TCP connection on that port, the broker allocates a `stream_id`, sends an **OPEN** frame to the agent, and waits up to 10 seconds for a **READY** (or **ERROR**) response.
5. Once the stream is ready, bytes flow bidirectionally as **DATA** frames until either side closes.
6. The caller receives `("127.0.0.1", local_port)` and passes it to the connector as a plain `NetworkTarget`.

Cleanup (stopping the TCP listener and sending a **CLOSE** frame) is triggered via `NetworkTarget.cleanup()` after the query completes.

### Binary frame protocol

Every frame is length-prefixed:

```
[4 bytes big-endian uint32 total_length] [1 byte frame_type] [4 bytes uint32 stream_id] [payload]
```

`total_length` = 5-byte header + payload length (does **not** include the 4-byte prefix itself). On-wire size = `4 + total_length`.

| Type | Value | Direction | Payload |
|------|-------|-----------|---------|
| `OPEN` | `0x01` | server → agent | 2-byte big-endian uint16 port + NUL-terminated UTF-8 hostname |
| `READY` | `0x02` | agent → server | empty |
| `ERROR` | `0x03` | agent → server | UTF-8 error message |
| `DATA` | `0x04` | bidirectional | raw bytes |
| `CLOSE` | `0x05` | bidirectional | empty |

`stream_id` is a 32-bit unsigned integer allocated by the broker; the agent echoes it back in every response frame. `decode_frame` returns `(None, None, None, 0)` on an incomplete buffer and raises `FrameError` on an unknown frame type byte.

### Agent-side behaviour

The `BridgeAgent` (`app.bridges.agent`) processes frames from the control plane:

- **OPEN** — dials `host:port` inside the VPC (10-second timeout), sends **READY** on success or **ERROR** on failure.
- **DATA** — writes bytes to the matching TCP socket.
- **CLOSE** — tears down the TCP connection and cancels the pump tasks.

Pump tasks (`tcp_to_ws` and `ws_sender`) run concurrently per stream; a close from either side terminates both.

---

## Live connection registry

The `BridgeBroker` registry is **in-memory only** — it is not persisted to the database. This means:

- If the backend process restarts, all bridge agents must reconnect. The agent reconnects automatically (configurable via `BRIDGE_RECONNECT_DELAY`).
- Multiple backend replicas (e.g. horizontal scaling) each hold their own independent registry. An agent connects to exactly one replica; queries routed to a different replica will not find the agent and will receive `503 bridge_not_connected`.

---

## Security

- The bridge token (`bridge.config["token"]`) is stored as plain JSON on the bridge row. It identifies the agent, not a data store, and has no database privileges. Rotate it by updating the bridge record and restarting the agent with the new token.
- The token is checked before the WebSocket handshake is accepted — unauthenticated agents are rejected before any data flows.
- `bridge_id` is non-secret and can appear in connector configs. Database passwords remain in `connector_secrets` (AES-256-GCM encrypted, org-scoped).
- The bridge agent only needs outbound access to the Nubi backend WebSocket URL and the target database host inside the VPC. No inbound ports need to be opened.
- All bridge CRUD operations enforce org-scoping: attempting to read, delete, or heartbeat a bridge belonging to a different org returns `404` (no information leak).

---

## Related docs

- [Connectors](/docs/connectors) — configuring a connector with `network_mode="bridge"`
- [Connector security](/docs/connector-security) — secret encryption, key rotation, network mode security
- [Self-hosting](/docs/self-host) — deploying the Nubi backend
