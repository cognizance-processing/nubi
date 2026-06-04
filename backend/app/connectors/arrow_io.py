"""Arrow IPC serialisation helpers.

Provides three functions for working with Arrow IPC stream bytes:

``table_to_ipc_bytes(table)``
    Materialise the full table to a ``bytes`` object.  Use for caching and for
    small result sets that fit comfortably in memory.

``table_to_ipc_stream(table)``
    Generator that yields ``bytes`` chunks (one per record batch) using the
    Arrow IPC stream format.  Use for HTTP streaming responses (e.g. FastAPI
    ``StreamingResponse``) so that large result sets are not buffered entirely
    in memory before the first byte is sent to the client.

``ipc_stream_from_bytes(data, chunk_size)``
    Generator that yields a pre-serialised Arrow IPC ``bytes`` object in
    fixed-size chunks.  Used to stream a cache HIT without deserialising and
    re-serialising the table.

Wire format
-----------
All functions use the **Arrow IPC stream format** (``pyarrow.ipc.new_stream``),
which is self-describing (schema included in the first message).  This matches
the ``Content-Type: application/vnd.apache.arrow.stream`` media type declared
by the query endpoint.

The IPC **file** format (random-access) is NOT used here because the stream
format is better suited to single-pass HTTP delivery and DuckDB-WASM
consumption.
"""

from __future__ import annotations

from typing import Generator

import pyarrow as pa
import pyarrow.ipc as pa_ipc


def table_to_ipc_bytes(table: pa.Table) -> bytes:
    """Serialise *table* to Arrow IPC stream format bytes.

    The schema message is written first, followed by one ``RecordBatch``
    message per batch in the table (pyarrow splits large tables at its own
    default chunk boundary when converting to batches).

    Parameters
    ----------
    table:
        The Arrow table to serialise.

    Returns
    -------
    bytes
        The complete Arrow IPC stream as a ``bytes`` object, including the
        EOS (end-of-stream) marker.

    Example
    -------
    ::

        import pyarrow as pa
        from app.connectors.arrow_io import table_to_ipc_bytes

        t = pa.table({"x": [1, 2, 3]})
        raw = table_to_ipc_bytes(t)
        assert isinstance(raw, bytes)
    """
    sink = pa.BufferOutputStream()
    with pa_ipc.new_stream(sink, table.schema) as writer:
        for batch in table.to_batches():
            writer.write_batch(batch)
    return sink.getvalue().to_pybytes()


def table_to_ipc_stream(
    table: pa.Table,
) -> Generator[bytes, None, None]:
    """Yield Arrow IPC stream chunks for *table*, one chunk per record batch.

    The first chunk contains the IPC stream header (schema message + first
    batch).  Subsequent chunks each contain one batch.  The generator closes
    the IPC writer after all batches have been yielded, which appends the EOS
    marker to the last chunk.

    This implementation buffers one batch at a time, making it suitable for
    large result sets delivered via HTTP ``StreamingResponse``.

    Parameters
    ----------
    table:
        The Arrow table to stream.

    Yields
    ------
    bytes
        One Arrow IPC chunk per record batch (the first chunk includes the
        schema message; the final close appends the EOS marker).

    Notes
    -----
    For M2, each batch is serialised into its own independent IPC stream
    chunk so that the client can begin parsing immediately.

    Example
    -------
    ::

        from fastapi.responses import StreamingResponse
        from app.connectors.arrow_io import table_to_ipc_stream

        return StreamingResponse(
            table_to_ipc_stream(table),
            media_type="application/vnd.apache.arrow.stream",
        )
    """
    batches = table.to_batches()

    # For an empty table we still need to emit schema + EOS so the reader can
    # determine column names and types.
    if not batches:
        sink = pa.BufferOutputStream()
        with pa_ipc.new_stream(sink, table.schema) as writer:
            # Write an empty batch to keep the reader happy.
            empty = pa.record_batch(
                [pa.array([], type=field.type) for field in table.schema],
                schema=table.schema,
            )
            writer.write_batch(empty)
        yield sink.getvalue().to_pybytes()
        return

    # Emit the complete IPC stream in a single yield so the client receives a
    # well-formed self-contained Arrow IPC stream message.  Each record batch
    # is written sequentially inside the same stream writer, preserving the
    # self-describing header + EOS structure required by Arrow IPC readers.
    sink = pa.BufferOutputStream()
    with pa_ipc.new_stream(sink, table.schema) as writer:
        for batch in batches:
            writer.write_batch(batch)
    yield sink.getvalue().to_pybytes()


def ipc_stream_from_bytes(
    data: bytes,
    chunk_size: int = 65536,
) -> Generator[bytes, None, None]:
    """Yield pre-serialised Arrow IPC bytes in fixed-size chunks.

    Use this to stream a **cache HIT** without deserialising and re-serialising
    the Arrow table.  The bytes are a complete, valid Arrow IPC stream
    (schema + batches + EOS) and are sent verbatim to the HTTP client.

    Parameters
    ----------
    data:
        Complete Arrow IPC stream bytes as returned by ``table_to_ipc_bytes``.
    chunk_size:
        Maximum number of bytes per yielded chunk.  Default: 65 536 (64 KiB).

    Yields
    ------
    bytes
        Up to *chunk_size* bytes per iteration.

    Example
    -------
    ::

        from fastapi.responses import StreamingResponse
        from app.connectors.arrow_io import ipc_stream_from_bytes

        cached = cache.get(key)   # bytes
        return StreamingResponse(
            ipc_stream_from_bytes(cached),
            media_type="application/vnd.apache.arrow.stream",
            headers={"X-Nubi-Cache": "HIT"},
        )
    """
    if not data:
        return
    offset = 0
    length = len(data)
    while offset < length:
        end = min(offset + chunk_size, length)
        yield data[offset:end]
        offset = end
