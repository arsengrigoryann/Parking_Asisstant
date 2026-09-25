"""Official MCP v2 server exposing one approved-reservation recording tool."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any
from uuid import UUID

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import SecretStr
from sqlalchemy import Engine
from starlette.types import ASGIApp

from parking_assistant.config import Settings, get_settings
from parking_assistant.db.session import create_database_engine, create_session_factory
from parking_assistant.mcp.auth import MCPBearerAuthMiddleware
from parking_assistant.mcp.recorder import (
    ApprovedReservationRecorder,
    InvalidApprovalError,
    ReservationNotApprovedError,
    ReservationRecordResult,
)
from parking_assistant.reservations.submission import (
    ReservationNotFoundError,
    ReservationSubmissionService,
)

ServerLifespan = Callable[[MCPServer[Any]], AbstractAsyncContextManager[Any]]


def build_mcp_server(
    recorder: ApprovedReservationRecorder,
    *,
    lifespan: ServerLifespan | None = None,
) -> MCPServer[Any]:
    """Build a server with exactly one business tool."""
    server: MCPServer[Any] = MCPServer(
        "parking-approved-reservation-recorder",
        instructions="Records only PostgreSQL-approved reservations by identifier.",
        lifespan=lifespan,
    )

    @server.tool(structured_output=True)
    def record_approved_reservation(reservation_id: UUID) -> ReservationRecordResult:
        """Record an approved reservation using only its authoritative database identifier."""
        try:
            return recorder.record(reservation_id)
        except ReservationNotFoundError as error:
            raise ToolError("reservation does not exist") from error
        except (ReservationNotApprovedError, InvalidApprovalError) as error:
            raise ToolError(str(error)) from error

    return server


def create_http_app(
    *,
    settings: Settings | None = None,
    recorder: ApprovedReservationRecorder | None = None,
) -> ASGIApp:
    """Create the authenticated stateless Streamable HTTP application."""
    resolved = settings or get_settings()
    token = _required_token(resolved.mcp_server_token)
    engine: Engine | None = None
    lifespan: ServerLifespan | None = None
    if recorder is None:
        engine = create_database_engine(resolved)
        reservations = ReservationSubmissionService(
            create_session_factory(engine),
            resolved,
        )
        recorder = ApprovedReservationRecorder(reservations, resolved.mcp_reservation_file)

        @asynccontextmanager
        async def dispose_engine(_: MCPServer[Any]) -> AsyncIterator[None]:
            try:
                yield
            finally:
                assert engine is not None
                engine.dispose()

        lifespan = dispose_engine

    server = build_mcp_server(recorder, lifespan=lifespan)
    transport = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        host=resolved.mcp_server_host,
    )
    return MCPBearerAuthMiddleware(transport, token)


def run() -> None:
    """Run the localhost-only MCP Streamable HTTP endpoint."""
    settings = get_settings()
    _required_token(settings.mcp_server_token)
    server_app = create_http_app(settings=settings)
    import uvicorn

    uvicorn.run(server_app, host=settings.mcp_server_host, port=settings.mcp_server_port)


def _required_token(token: SecretStr | None) -> SecretStr:
    if token is None or not token.get_secret_value():
        raise RuntimeError("MCP_SERVER_TOKEN must be configured")
    return token


if __name__ == "__main__":
    run()
