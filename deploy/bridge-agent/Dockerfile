# Nubi bridge agent — the customer-side half of the reverse tunnel.
#
# Build from the REPO ROOT (the context needs both backend/ and deploy/):
#
#     docker build -f deploy/bridge-agent/Dockerfile -t nubi/bridge-agent:latest .
#
# Why this image is four files and one dependency
# -----------------------------------------------
# The agent is a byte relay: it holds an outbound WebSocket to the control plane
# and pipes TCP through it. It imports nothing but the standard library plus
# `websockets`. Running it previously meant installing the ENTIRE backend —
# FastAPI, pyarrow, duckdb, asyncpg — none of which it touches. Shipping only
# what it uses keeps the thing a customer runs inside their network small enough
# to audit, and keeps its attack surface honest.
#
# The module path (app.bridges.agent) is preserved rather than vendored into a
# flat script so this image runs the SAME code as the repo, with no fork to keep
# in sync.

FROM python:3.13-slim

# websockets is the agent's only third-party dependency. Pinned to a major range
# so a rebuild can't silently pull a breaking client API.
RUN pip install --no-cache-dir "websockets>=12,<16"

WORKDIR /opt/nubi

# Only the two modules the agent actually needs, plus the package markers that
# keep `python -m app.bridges.agent` importable.
#
# app/bridges/__init__.py is SUBSTITUTED (see bridges_package_init.py): the
# repo's version re-exports the server-side broker, which would pull FastAPI and
# the rest of the backend into this image. protocol.py and agent.py — the code
# that actually runs — are copied verbatim, so this image cannot drift from the
# source tree.
COPY backend/app/__init__.py                        app/__init__.py
COPY deploy/bridge-agent/bridges_package_init.py    app/bridges/__init__.py
COPY backend/app/bridges/protocol.py                app/bridges/protocol.py
COPY backend/app/bridges/agent.py                   app/bridges/agent.py

# Run unprivileged. The agent needs no filesystem access and no privileged
# ports — it makes one outbound connection and dials TCP targets.
RUN useradd --system --uid 10001 --user-group --no-create-home nubi
USER 10001

# Unbuffered so `docker logs` shows connect/disconnect lines as they happen
# rather than holding them in a pipe buffer — this is the primary way an
# operator confirms the tunnel is up.
ENV PYTHONUNBUFFERED=1

# BRIDGE_ID, BRIDGE_TOKEN and CONTROL_PLANE_URL are required at runtime; the
# agent raises immediately if any is missing. BRIDGE_RECONNECT_DELAY is optional
# (default 5s).
ENTRYPOINT ["python", "-m", "app.bridges.agent"]
