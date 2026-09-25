"""Deterministic authenticated application client for the reservation MCP tool."""

from __future__ import annotations

import asyncio
import json
from argparse import ArgumentParser
from typing import Any, Protocol, cast
from uuid import UUID

from fastmcp import Client as FastMCPClient
from fastmcp.client.transports import StreamableHttpTransport
from langchain.mcp import MCPAdapter
from langchain_core.messages import ToolMessage

from parking_assistant.config import Settings
from parking_assistant.mcp.recorder import ReservationRecordResult


class MCPRecordingError(RuntimeError):
    """Safe application-facing MCP transport or tool failure."""


class ApprovedReservationRecordingClient(Protocol):
    """Application boundary for the post-approval side effect."""

    def record_if_approved(self, reservation_id: UUID) -> ReservationRecordResult: ...


class ReservationMCPClient:
    """Invoke only the fixed reservation recording tool; no agent chooses the call."""

    def __init__(self, settings: Settings) -> None:
        if settings.mcp_server_token is None:
            raise ValueError("MCP_SERVER_TOKEN must be configured")
        self._url = settings.mcp_server_url
        self._token = settings.mcp_server_token.get_secret_value()

    def record_if_approved(self, reservation_id: UUID) -> ReservationRecordResult:
        """Synchronously call the bounded MCP operation for application/API code."""
        try:
            return asyncio.run(self._record_if_approved(reservation_id))
        except MCPRecordingError:
            raise
        except Exception as error:
            raise MCPRecordingError("approved reservation recording is unavailable") from error

    async def _record_if_approved(self, reservation_id: UUID) -> ReservationRecordResult:
        headers = {"Authorization": f"Bearer {self._token}"}
        transport = StreamableHttpTransport(self._url, headers=headers)
        async with MCPAdapter(FastMCPClient(transport)) as adapter:
            tools = await adapter.list_tools()
            matching = [tool for tool in tools if tool.name == "record_approved_reservation"]
            if len(tools) != 1 or len(matching) != 1:
                raise MCPRecordingError("MCP server exposed an unexpected tool set")
            response = await matching[0].ainvoke(
                {
                    "name": "record_approved_reservation",
                    "args": {"reservation_id": str(reservation_id)},
                    "id": f"record-{reservation_id}",
                    "type": "tool_call",
                }
            )
        if not isinstance(response, ToolMessage) or response.status == "error":
            raise MCPRecordingError("MCP server refused reservation recording")
        try:
            artifact = cast(dict[str, Any], response.artifact)
            return ReservationRecordResult.model_validate(artifact["structured_content"])
        except (KeyError, TypeError, ValueError) as error:
            raise MCPRecordingError("MCP server returned an invalid recording result") from error


def main() -> None:
    """Invoke the authenticated recorder for one explicit reservation identifier."""
    parser = ArgumentParser(description="Record one PostgreSQL-approved reservation via MCP")
    parser.add_argument("reservation_id", type=UUID)
    arguments = parser.parse_args()
    from parking_assistant.config import get_settings

    result = ReservationMCPClient(get_settings()).record_if_approved(arguments.reservation_id)
    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))


if __name__ == "__main__":
    main()
