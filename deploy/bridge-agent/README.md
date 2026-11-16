# Nubi bridge agent — deployment

The bridge agent lets Nubi query a database that has **no public endpoint** —
one inside a VPC, a private subnet, or on-prem.

It does that without any inbound firewall change. The agent runs inside your
network and dials **out** to Nubi over a single WebSocket. Nubi never connects
in; there is no port to open and no VPN to build.

```
   your network                     │              Nubi
                                    │
   ┌──────────┐    ┌───────────┐    │      ┌──────────────┐
   │ database │◀───│   agent   │────┼─────▶│ control plane│
   │  :3306   │    │           │  outbound │              │
   └──────────┘    └───────────┘  WebSocket└──────────────┘
                                    │
                              (opened from inside)
```

## What you need before you start

Three values, shown by Nubi when you create the bridge — in the connector form
under **"connect a private database"**, or under **Settings → Bridges**:

| Value | Notes |
|---|---|
| `BRIDGE_ID` | UUID of the bridge record |
| `BRIDGE_TOKEN` | Shown **once**. Stored only as a hash — rotate if lost. |
| `CONTROL_PLANE_URL` | Your Nubi host as a `wss://…/api/v1` URL |

The machine you run this on must be able to reach **(a)** your database and
**(b)** your Nubi host outbound on 443. Nothing else.

---

## Option 1 — Container (recommended)

```bash
docker run -d --name nubi-bridge-agent \
  --restart=unless-stopped \
  -e BRIDGE_ID=<uuid> \
  -e BRIDGE_TOKEN=<nubi_br_…> \
  -e CONTROL_PLANE_URL=wss://your-nubi-host/api/v1 \
  nubi/bridge-agent:latest
```

Confirm it is up:

```bash
docker logs nubi-bridge-agent      # → "bridge <id> connected"
```

The bridge also turns green in the Nubi UI within a few seconds.

`--restart=unless-stopped` is the important flag: it restarts the agent after a
crash, an OOM kill, or a host reboot. Without it, an agent that dies stays dead
and every query through that bridge fails with `bridge_not_connected`.

### Reaching the database

The agent dials the database from **inside the container**, so the container's
network must be able to see it:

| Database location | Flag |
|---|---|
| Another host on the network | works by default |
| Another container | `--network=<shared-net>`, address it by service name |
| The Docker host itself | `--network=host` (Linux), or `host.docker.internal` |

A database on `127.0.0.1` of the host is **not** reachable from a default-network
container — inside the container, `127.0.0.1` is the container.

### docker compose

```yaml
services:
  nubi-bridge-agent:
    image: nubi/bridge-agent:latest
    restart: unless-stopped
    environment:
      BRIDGE_ID: "<uuid>"
      BRIDGE_TOKEN: "<nubi_br_…>"
      CONTROL_PLANE_URL: "wss://your-nubi-host/api/v1"
```

### Building it yourself

Nothing here is opaque — the image is four files and one dependency
(`websockets`). Build from the repo root:

```bash
docker build -f deploy/bridge-agent/Dockerfile -t nubi/bridge-agent:latest .
```

---

## Option 2 — systemd (no container runtime)

```bash
sudo useradd --system --no-create-home nubi
sudo python3 -m venv /opt/nubi/venv
sudo /opt/nubi/venv/bin/pip install websockets

# Copy app/__init__.py, app/bridges/__init__.py, app/bridges/protocol.py
# and app/bridges/agent.py to /opt/nubi/app/… preserving that layout.

sudo install -D -m 0600 -o root -g root \
    bridge-agent.env.example /etc/nubi/bridge-agent.env
sudo $EDITOR /etc/nubi/bridge-agent.env          # fill in the three values

sudo cp nubi-bridge-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nubi-bridge-agent
```

```bash
systemctl status nubi-bridge-agent
journalctl -u nubi-bridge-agent -f
```

The unit runs unprivileged, keeps the token in a root-owned `0600` file (never
on the command line, where `ps` would expose it), restarts on failure, and
starts at boot.

---

## Verifying and troubleshooting

The bridge shows **online** in Nubi, and `last_seen_at` refreshes every ~30s
while the tunnel is up. A stale timestamp on an "online" bridge means the agent
went away without a clean disconnect.

| Symptom | Cause |
|---|---|
| `bridge_not_connected` (503) | No agent connected. Check it is running and can reach `CONTROL_PLANE_URL`. |
| Connects, then disconnects repeatedly | Usually the token was revoked or rotated; mint a fresh one. |
| `Cannot connect to <host>:<port>` | The agent is fine — it cannot reach the **database**. Check the container's network (above), and any internal firewall between agent and DB. |
| Bridge "online" but every query hangs ~10s then fails | You are running `nubi bridge start` (the CLI's file-ingest channel), not the query tunnel. Run `python -m app.bridges.agent` or this image. |

## Security notes

- **Outbound only.** No inbound port, no firewall exception, no VPN.
- **Least privilege.** Runs unprivileged, keeps no state on disk, and the
  systemd unit ships with the filesystem and kernel-surface locked down.
- **Scoped credential.** The token authenticates one bridge to one org, is
  stored hashed, and is re-validated every 30s on the live tunnel — revoking it
  drops the tunnel immediately rather than at next reconnect.
- **Database credentials never leave Nubi.** They are encrypted server-side and
  are not held by the agent, which only ever relays bytes.
- **Small by design.** The agent is the standard library plus `websockets`, so
  what runs inside your network is small enough to read end to end.
