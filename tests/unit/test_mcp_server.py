"""MCP schema and HTTP authentication tests."""

import asyncio
import logging
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError

from parking_assistant.config import Settings
from parking_assistant.mcp.recorder import (
    InvalidApprovalError,
    ReservationNotApprovedError,
    ReservationRecordResult,
)
from parking_assistant.mcp.server import build_mcp_server, create_http_app
from parking_assistant.reservations.submission import ReservationNotFoundError

TOKEN = "synthetic-mcp-secret"


class SuccessfulRecorder:
    def record(self, reservation_id: UUID) -> ReservationRecordResult:
        return ReservationRecordResult(reservation_id=reservation_id, outcome="recorded")


def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url="postgresql://test:test@localhost/test",
        mcp_server_token=TOKEN,
        mcp_reservation_file=tmp_path / "confirmed.txt",
        _env_file=None,
    )


def test_tool_schema_accepts_only_reservation_id() -> None:
    tools = asyncio.run(build_mcp_server(SuccessfulRecorder()).list_tools())  # type: ignore[arg-type]

    assert len(tools) == 1
    assert tools[0].name == "record_approved_reservation"
    assert set(tools[0].input_schema["properties"]) == {"reservation_id"}


def test_missing_and_invalid_tokens_are_rejected_without_leaking_secret(
    tmp_path: Path,
    caplog: object,
) -> None:
    caplog.set_level(logging.INFO)  # type: ignore[attr-defined]
    app = create_http_app(
        settings=settings(tmp_path),
        recorder=SuccessfulRecorder(),  # type: ignore[arg-type]
    )

    with TestClient(app) as client:
        missing = client.post("/mcp", json={})
        invalid = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {TOKEN}-wrong"},
            json={},
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert TOKEN not in missing.text
    assert TOKEN not in invalid.text
    assert TOKEN not in caplog.text  # type: ignore[attr-defined]


def test_valid_token_reaches_mcp_protocol(tmp_path: Path) -> None:
    app = create_http_app(
        settings=settings(tmp_path),
        recorder=SuccessfulRecorder(),  # type: ignore[arg-type]
    )

    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "server/discover"},
        )

    assert response.status_code != 401


def test_tool_maps_expected_authorization_failures_to_safe_mcp_errors() -> None:
    class FailingRecorder:
        def __init__(self, error: Exception) -> None:
            self.error = error

        def record(self, _: UUID) -> ReservationRecordResult:
            raise self.error

    errors = [
        ReservationNotFoundError("private lookup detail"),
        ReservationNotApprovedError("reservation is not approved"),
        InvalidApprovalError("approved reservation has no decision timestamp"),
    ]
    for error in errors:
        server = build_mcp_server(FailingRecorder(error))  # type: ignore[arg-type]
        with pytest.raises(ToolError) as captured:
            asyncio.run(
                server.call_tool(
                    "record_approved_reservation",
                    {"reservation_id": str(UUID(int=1))},
                )
            )
        assert "private lookup detail" not in str(captured.value)
