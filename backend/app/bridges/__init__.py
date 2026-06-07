"""Nubi reverse-tunnel bridge package.

Provides a TCP-over-WebSocket multiplexer so a bridge agent running inside a
customer VPC can dial OUT to the Nubi control plane and expose private database
hosts to the query engine — without the customer opening any inbound firewall
ports.

Public sub-modules
------------------
protocol  — binary frame codec (OPEN / DATA / CLOSE / READY / ERROR)
broker    — server-side singleton; manages connected agents and spawns ephemeral
            local TCP listeners that route traffic through the tunnel
agent     — customer-side process; connects to the control plane WS and proxies
            TCP inside the VPC

Typical import path::

    from app.bridges.broker import BridgeBroker, get_broker
    from app.bridges.protocol import encode_frame, decode_frame, FrameType
"""

from app.bridges.protocol import FrameType, encode_frame, decode_frame
from app.bridges.broker import BridgeBroker, get_broker

__all__ = [
    "FrameType",
    "encode_frame",
    "decode_frame",
    "BridgeBroker",
    "get_broker",
]
