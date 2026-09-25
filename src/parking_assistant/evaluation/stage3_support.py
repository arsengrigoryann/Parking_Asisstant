"""Shared real-service helpers for Stage 3 evidence and performance commands."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import uvicorn
from starlette.types import ASGIApp

from parking_assistant.reservations.models import ReservationDetails


def synthetic_details(label: str, *, minute_offset: int = 0) -> ReservationDetails:
    """Return unique, synthetic, future reservation details."""
    start = datetime.now(UTC) + timedelta(days=2, minutes=minute_offset)
    suffix = f"{label}{uuid4().hex}"[:9].upper()
    return ReservationDetails(
        first_name="Stage",
        last_name="Three",
        car_number=f"S3{suffix}"[:12],
        start_datetime=start,
        end_datetime=start + timedelta(hours=2),
    )


def free_local_port() -> int:
    """Reserve and release an ephemeral localhost port for a short-lived test server."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def running_server(app: ASGIApp, port: int) -> Iterator[None]:
    """Run one real uvicorn HTTP server in a background thread."""
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("Stage 3 evidence MCP server did not start")
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            raise RuntimeError("Stage 3 evidence MCP server did not stop")
