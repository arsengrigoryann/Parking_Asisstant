"""Deterministic LangChain MCP client behavior without an autonomous agent."""

from __future__ import annotations

from typing import Any, ClassVar
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import ToolMessage

from parking_assistant.config import Settings
from parking_assistant.mcp.client import MCPRecordingError, ReservationMCPClient


class SyntheticTool:
    name = "record_approved_reservation"

    def __init__(self, response: object) -> None:
        self.response = response
        self.arguments: object = None

    async def ainvoke(self, arguments: object) -> object:
        self.arguments = arguments
        return self.response


class SyntheticAdapter:
    tools: ClassVar[list[SyntheticTool]] = []

    def __init__(self, _: object) -> None:
        pass

    async def __aenter__(self) -> SyntheticAdapter:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def list_tools(self) -> list[SyntheticTool]:
        return self.tools


def settings() -> Settings:
    return Settings(
        database_url="postgresql://test:test@localhost/test",
        mcp_server_token="synthetic-client-token",
        _env_file=None,
    )


def _patch_transport(monkeypatch: pytest.MonkeyPatch, tools: list[SyntheticTool]) -> None:
    SyntheticAdapter.tools = tools
    monkeypatch.setattr("parking_assistant.mcp.client.MCPAdapter", SyntheticAdapter)
    monkeypatch.setattr(
        "parking_assistant.mcp.client.StreamableHttpTransport",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "parking_assistant.mcp.client.FastMCPClient",
        lambda *_args, **_kwargs: object(),
    )


def _message(reservation_id: UUID, artifact: object) -> ToolMessage:
    return ToolMessage(
        content="synthetic",
        tool_call_id=f"record-{reservation_id}",
        artifact=artifact,
    )


def test_authenticated_langchain_client_returns_typed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reservation_id = uuid4()
    tool = SyntheticTool(
        _message(
            reservation_id,
            {
                "structured_content": {
                    "reservation_id": str(reservation_id),
                    "outcome": "recorded",
                }
            },
        )
    )
    _patch_transport(monkeypatch, [tool])

    result = ReservationMCPClient(settings()).record_if_approved(reservation_id)

    assert result.recorded is True
    call = tool.arguments
    assert isinstance(call, dict)
    assert call["args"] == {"reservation_id": str(reservation_id)}
    assert set(call["args"]) == {"reservation_id"}


@pytest.mark.parametrize(
    ("tools", "message"),
    [
        ([], "unexpected tool set"),
        ([SyntheticTool("not a tool message")], "refused reservation"),
        (
            [
                SyntheticTool(
                    ToolMessage(
                        content="refused",
                        tool_call_id="synthetic",
                        status="error",
                    )
                )
            ],
            "refused reservation",
        ),
        (
            [SyntheticTool(_message(uuid4(), {"wrong": "shape"}))],
            "invalid recording result",
        ),
    ],
)
def test_client_rejects_unexpected_tools_errors_and_invalid_results(
    monkeypatch: pytest.MonkeyPatch,
    tools: list[SyntheticTool],
    message: str,
) -> None:
    _patch_transport(monkeypatch, tools)

    with pytest.raises(MCPRecordingError, match=message):
        ReservationMCPClient(settings()).record_if_approved(uuid4())


def test_client_requires_token_and_wraps_transport_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    without_token = Settings(
        database_url="postgresql://test:test@localhost/test",
        _env_file=None,
    )
    with pytest.raises(ValueError, match="MCP_SERVER_TOKEN"):
        ReservationMCPClient(without_token)

    async def fail(_: Any, __: UUID) -> Any:
        raise OSError("synthetic private transport detail")

    monkeypatch.setattr(ReservationMCPClient, "_record_if_approved", fail)
    with pytest.raises(MCPRecordingError, match="recording is unavailable") as captured:
        ReservationMCPClient(settings()).record_if_approved(uuid4())
    assert "private" not in str(captured.value)
