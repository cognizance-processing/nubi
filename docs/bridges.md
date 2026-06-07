# Bridges

A Nubi bridge is a lightweight agent that runs **inside your VPC or on-prem network** and proxies database connections to the Nubi backend via an outbound WebSocket tunnel. This lets you query private databases (not reachable from the public internet) without opening inbound firewall ports.

---

## How It Works

```
Nubi backend  ←──── WebSocket tunnel ←────  Bridge agent (in your VPC)
                                                      │
                                              Private database
                                             (Postgres, etc.)
```

1. The bridge agent process starts inside your VPC and calls `WS /api/v1/bridges/{id}/connect` on the Nubi backend.
2. The Nubi backend authenticates the agent using a `token` stored in the bridge's config.
3. Once the WebSocket is accepted, the backend's `BridgeBroker` registers the connection.
4. When a query targets a connector with `network_mode="bridge"`, the backend calls `resolve_network_async()`, which opens a local TCP proxy through the bridge agent's tunnel.
5. The connector receives a `NetworkTarget` pointing at `127.0.0.1:<local-port>` — it connects there as if the database were local.

---

## Bridge REST Endpoints

All endpoints require a valid first-party Bearer token. Bridges are org-scoped.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/bridges` | Create a bridge record. Returns 201. |
| `GET` | `/api/v1/bridges` | List all bridges for the caller's org. |
| `GET` | `/api/v1/bridges/{id}` | Fetch a single bridge. Returns 404 if not found or wrong org. |
| `DELETE` | `/api/v1/bridges/{id}` | Delete a bridge. Returns 204. |
| `POST` | `/api/v1/bridges/{id}/heartbeat` | Update `status='online'` and `last_seen_at`. Bridge agents call this on a regular interval (e.g. every 30 s). |
| `WS` | `/api/v1/bridges/{id}/connect` | WebSocket endpoint for bridge agents. |

---

## Bridge Record Shape

```json
{
  "id":           "uuid",
  "org_id":       "uuid",
  "created_by":   "uuid",
  "name":         "prod-vpc-bridge",
  "status":       "online",
  "last_seen_at": "2024-01-15T07:00:01+00:00",
  "config":       { "token": "secret-agent-token" },
  "created_at":   "2024-01-14T09:00:00+00:00",
  "updated_at":   "2024-01-15T07:00:01+00:00"
}
```

`status` is `"offline"` at creation and transitions to `"online"` when the agent connects or sends a heartbeat.

---

## Setting Up a Bridge

### Step 1 — Create the Bridge Record

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

Store the returned `id` — the bridge agent needs it.

### Step 2 — Configure the Connector

Create or update a connector with `network_mode="bridge"` and `bridge_id` set to the bridge's UUID:

```json
POST /api/v1/connectors
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

### Step 3 — Start the Bridge Agent

Run the bridge agent process inside your VPC, pointing it at the Nubi backend:

```bash
# Using the nubi CLI (when bridge agent CLI ships)
NUBI_API_URL=https://api.example.com \
BRIDGE_ID=<bridge-uuid> \
BRIDGE_TOKEN=my-secret-agent-token \
  nubi bridge-agent start

# Or directly with the Python agent module:
python -m nubi_bridge.agent \
  --backend wss://api.example.com/api/v1/bridges/<bridge-uuid>/connect \
  --token my-secret-agent-token
```

The agent connects via WebSocket, authenticates with the token, and is registered with the `BridgeBroker`.

---

## WebSocket Authentication

The bridge agent must supply its secret token in **one** of:

- `X-Bridge-Token: <token>` request header
- `?token=<token>` query parameter

The token is validated against `bridge.config["token"]`. If the bridge row does not exist or the token does not match, the WebSocket is closed with code `4401` (unauthorized). A missing bridge row returns `4404`.

---

## Network Modes

| Mode | Status | Description |
|------|--------|-------------|
| `direct` | Available | Egress goes directly from the Nubi backend to the database. No extra infrastructure needed. |
| `bridge` | Available | Routes through the Nubi bridge agent via WebSocket TCP proxy. |
| `ssh_tunnel` | Planned (501) | SSH tunnel transport — not yet implemented. |
| `psc` | Planned (501) | GCP Private Service Connect — not yet implemented. |
| `cloudsql_proxy` | Planned (501) | Cloud SQL Auth Proxy — not yet implemented. |

Requesting an unimplemented mode returns `501 Not Implemented` with a message explaining what infrastructure is required.

---

## Reachability Check

Before opening the TCP proxy, `resolve_network_async()` checks that the bridge agent is currently connected:

```python
if not broker.is_connected(bridge_id):
    raise AppError(
        "bridge_not_connected",
        "Bridge has no connected agent. Start the bridge agent inside the VPC.",
        501,
    )
```

If the agent has disconnected or has not yet started, queries will fail with a clear 501 rather than timing out.

---

## BridgeBroker — TCP Proxy Protocol

The `BridgeBroker` (`app.bridges.broker`) manages the collection of connected bridge WebSockets. When a query needs to open a TCP connection through the bridge:

1. `broker.open_tcp_proxy(bridge_id, target_host, target_port)` sends an **OPEN frame** to the bridge agent.
2. The bridge agent establishes the TCP connection to `target_host:target_port` and responds with a **READY frame** containing a local port.
3. Data flows as binary frames in both directions (**DATA frames**).
4. When the query is done, `broker.close_tcp_proxy(local_host, local_port)` sends a **CLOSE frame**.

The connector receives a plain `(host, port)` `NetworkTarget` and is agnostic of the tunnel — it connects to `127.0.0.1:<local-port>` as if the database were local.

---

## Security Notes

- The bridge agent token is stored in `bridge.config["token"]` (plain JSON in the `bridges` table). This is intentional: the token is NOT a database credential — it identifies the bridge agent, not a data store. Rotate it by updating the bridge record.
- The bridge token is checked before the WebSocket handshake is accepted — unauthenticated agents are rejected before any data flows.
- Bridge IDs are non-secret and stored in `datastores.config` alongside `network_mode`. The actual database credentials remain in `connector_secrets` (AES-256-GCM encrypted).
- In production, run the bridge agent with limited outbound egress: it only needs to reach the Nubi backend WebSocket URL and the target database host.
