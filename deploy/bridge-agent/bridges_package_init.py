"""Nubi reverse-tunnel bridge package — agent-only package marker.

This file replaces ``app/bridges/__init__.py`` inside the bridge-agent image.

The repo's version re-exports the SERVER side for convenience::

    from app.bridges.broker import BridgeBroker, get_broker

``broker`` is the control-plane half — it pulls in FastAPI and the rest of the
backend, none of which the agent uses. Importing it here would drag the whole
server into an image whose entire job is relaying bytes, so the image ships this
marker instead.

Only this file differs from the repo. ``protocol.py`` and ``agent.py`` — the
code that actually runs — are copied verbatim, so the image can never drift from
the source tree.
"""
