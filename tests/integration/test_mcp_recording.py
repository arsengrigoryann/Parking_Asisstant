"""Real PostgreSQL plus authenticated Streamable HTTP MCP integration coverage."""

import os
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy import delete
from starlette.types import ASGIApp

from parking_assistant.config import Settings
from parking_assistant.db.models import ReservationRequest
from parking_assistant.db.seed import FACILITY_ID, seed_data
from parking_assistant.db.session import (
    create_database_engine,
    create_session_factory,
    session_scope,
)
from parking_assistant.mcp.client import ReservationMCPClient
from parking_assistant.mcp.recorder import ApprovedReservationRecorder
from parking_assistant.mcp.server import create_http_app
from parking_assistant.reservations.models import ReservationDetails
from parking_assistant.reservations.submission import ReservationSubmissionService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 to use local services",
    ),
]

TOKEN = "synthetic-stage3-integration-token"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _running_server(app: ASGIApp, port: int) -> Iterator[None]:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("synthetic MCP server did not start")
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _details() -> ReservationDetails:
    start = datetime.now(UTC) + timedelta(days=2)
    return ReservationDetails(
        first_name="Synthetic",
        last_name="Mcpdriver",
        car_number="MCP1234",
        start_datetime=start,
        end_datetime=start + timedelta(hours=2),
    )


def test_real_authenticated_mcp_transport_records_once(tmp_path: Path) -> None:
    command.upgrade(Config("alembic.ini"), "head")
    port = _free_port()
    output = tmp_path / "confirmed.txt"
    settings = Settings(
        mcp_server_host="127.0.0.1",
        mcp_server_port=port,
        mcp_server_token=TOKEN,
        mcp_reservation_file=output,
    )
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    reservation_id: UUID | None = None
    try:
        with session_scope(factory) as session:
            seed_data(session)
        reservations = ReservationSubmissionService(factory, settings)
        pending = reservations.submit_for_approval(
            _details(),
            facility_id=FACILITY_ID,
            idempotency_key=f"stage3-integration-{uuid4()}",
        )
        reservation_id = pending.id
        reservations.approve(pending.id, decision_by="integration-admin")
        app = create_http_app(
            settings=settings,
            recorder=ApprovedReservationRecorder(reservations, output),
        )

        with _running_server(app, port):
            unauthorized = httpx.post(settings.mcp_server_url, json={}, timeout=5)
            client = ReservationMCPClient(settings)
            first = client.record_if_approved(pending.id)
            second = client.record_if_approved(pending.id)

        assert unauthorized.status_code == 401
        assert first.outcome == "recorded"
        assert second.outcome == "already_recorded"
        lines = output.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert lines[0].split(" | ")[2] == "Central Station Parking"
    finally:
        if reservation_id is not None:
            with session_scope(factory) as session:
                session.execute(
                    delete(ReservationRequest).where(ReservationRequest.id == reservation_id)
                )
        engine.dispose()
