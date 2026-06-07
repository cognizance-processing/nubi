"""Binary frame protocol for the Nubi reverse-tunnel bridge.

Frame layout
------------
Every frame is length-prefixed::

    [4 bytes big-endian total length] [1 byte frame_type] [4 bytes stream_id] [payload]

Total length includes the 5-byte header (type + stream_id) BUT NOT the 4-byte
length prefix itself.  So::

    on-wire bytes = 4 + 5 + len(payload)
    total_length field = 5 + len(payload)

Frame types
-----------
OPEN  (0x01)  server → agent  open a new TCP stream to (host, port)
              payload: 2 bytes port (big-endian uint16) + NUL-terminated hostname

READY (0x02)  agent → server  TCP connection succeeded; stream ready for data
              payload: empty

ERROR (0x03)  agent → server  TCP connection failed or stream error
              payload: UTF-8 error message

DATA  (0x04)  bidirectional   raw TCP bytes
              payload: raw bytes (any length ≥ 1)

CLOSE (0x05)  bidirectional   close / half-close the stream
              payload: empty

All multi-byte integers are big-endian.  stream_id is a 32-bit unsigned integer
allocated by the broker and echoed by the agent.

Pure functions
--------------
encode_frame(frame_type, stream_id, payload) → bytes
decode_frame(data) → (frame_type, stream_id, payload, consumed_bytes)
    Returns (None, None, None, 0) when data does not yet contain a complete frame.
    Raises FrameError on invalid/corrupt frames.
"""

from __future__ import annotations

import struct
from enum import IntEnum


# ---------------------------------------------------------------------------
# Frame type constants
# ---------------------------------------------------------------------------


class FrameType(IntEnum):
    """Enumeration of all frame types recognised by the bridge protocol."""

    OPEN = 0x01
    READY = 0x02
    ERROR = 0x03
    DATA = 0x04
    CLOSE = 0x05


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HEADER_SIZE = 5   # 1-byte type + 4-byte stream_id
_LEN_PREFIX = 4    # bytes for the uint32 total-length field
_MIN_FRAME = _LEN_PREFIX + _HEADER_SIZE  # smallest valid on-wire frame


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class FrameError(Exception):
    """Raised when a frame cannot be decoded (corrupt or unknown type)."""


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def encode_frame(frame_type: FrameType | int, stream_id: int, payload: bytes = b"") -> bytes:
    """Encode a single frame into its on-wire binary representation.

    Parameters
    ----------
    frame_type:
        One of the :class:`FrameType` values (or a raw int for forward compat).
    stream_id:
        Unsigned 32-bit stream identifier.
    payload:
        Frame payload bytes.  Empty for READY/CLOSE frames.

    Returns
    -------
    bytes
        Complete on-wire frame (length prefix + header + payload).

    Raises
    ------
    struct.error
        If ``stream_id`` is out of range for a uint32 or total_length overflows
        a uint32.
    """
    total_length = _HEADER_SIZE + len(payload)  # does NOT include the 4-byte prefix
    header = struct.pack(">IBL", total_length, int(frame_type), stream_id)
    # ">I" (4) + "B" (1) + "L" (4) … but we want a clean layout.
    # Rewrite with explicit packing to avoid alignment surprises:
    #   >  big-endian
    #   I  4 bytes  total_length
    #   B  1 byte   frame_type
    #   I  4 bytes  stream_id
    header = struct.pack(">IBI", total_length, int(frame_type), stream_id)
    return header + payload


def encode_open(stream_id: int, host: str, port: int) -> bytes:
    """Convenience helper: encode an OPEN frame.

    Payload layout::

        [2 bytes big-endian uint16 port] [host as UTF-8, NUL-terminated]
    """
    host_bytes = host.encode("utf-8") + b"\x00"
    payload = struct.pack(">H", port) + host_bytes
    return encode_frame(FrameType.OPEN, stream_id, payload)


def decode_open_payload(payload: bytes) -> tuple[str, int]:
    """Decode the payload of an OPEN frame → (host, port).

    Raises
    ------
    FrameError
        If the payload is too short or the NUL terminator is missing.
    """
    if len(payload) < 3:  # 2-byte port + at least 1-byte host + NUL
        raise FrameError(f"OPEN payload too short: {len(payload)} bytes")
    port: int = struct.unpack(">H", payload[:2])[0]
    rest = payload[2:]
    nul = rest.find(b"\x00")
    if nul == -1:
        raise FrameError("OPEN payload missing NUL terminator in hostname")
    host = rest[:nul].decode("utf-8")
    return host, port


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def decode_frame(
    data: bytes | bytearray,
) -> tuple[FrameType | None, int | None, bytes | None, int]:
    """Try to decode one frame from the front of *data*.

    Returns
    -------
    (frame_type, stream_id, payload, consumed)
        ``consumed`` is the number of bytes read from *data*.
        If there is not yet enough data, all four values are ``None/0``.

    Raises
    ------
    FrameError
        If the frame_type byte is unknown or the frame header is corrupt.
    """
    if len(data) < _MIN_FRAME:
        return None, None, None, 0

    total_length, frame_type_byte, stream_id = struct.unpack_from(">IBI", data, 0)

    # total_length = header(5) + payload_len  ≥ 5
    if total_length < _HEADER_SIZE:
        raise FrameError(
            f"Corrupt frame: total_length={total_length} is less than header size {_HEADER_SIZE}"
        )

    on_wire = _LEN_PREFIX + total_length
    if len(data) < on_wire:
        return None, None, None, 0  # incomplete frame; need more data

    try:
        frame_type = FrameType(frame_type_byte)
    except ValueError as exc:
        raise FrameError(f"Unknown frame type byte: 0x{frame_type_byte:02x}") from exc

    payload_start = _LEN_PREFIX + _HEADER_SIZE
    payload_end = _LEN_PREFIX + total_length
    payload = bytes(data[payload_start:payload_end])

    return frame_type, stream_id, payload, on_wire
