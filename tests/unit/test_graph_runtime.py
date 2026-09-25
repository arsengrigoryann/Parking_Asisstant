"""Checkpointer connection, setup, cleanup, and Studio discovery tests."""

from types import TracebackType
from typing import Any

import pytest

from parking_assistant.config import Settings
from parking_assistant.graph import runtime as runtime_module
from parking_assistant.graph.runtime import PostgresApprovalGraphProvider


class FakeCursor:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[str, ...]]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[str, ...]) -> None:
        self.statements.append((statement, parameters))


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_value = FakeCursor()

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self.cursor_value


class FakeSaver:
    setup_calls = 0

    def __init__(self, connection: object, *, serde: object) -> None:
        assert connection is not None
        assert serde is not None

    def setup(self) -> None:
        type(self).setup_calls += 1


def settings() -> Settings:
    return Settings(
        database_url="postgresql+psycopg://user:password@db.example:5433/parking",
        _env_file=None,
    )


def test_provider_uses_explicit_safe_connection_setup_and_thread_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections: list[FakeConnection] = []
    connect_kwargs: list[dict[str, Any]] = []

    def connect(**kwargs: Any) -> FakeConnection:
        connect_kwargs.append(kwargs)
        connection = FakeConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr("parking_assistant.graph.runtime.psycopg.connect", connect)
    monkeypatch.setattr(runtime_module, "PostgresSaver", FakeSaver)
    monkeypatch.setattr(
        runtime_module,
        "build_approval_graph",
        lambda reservations, checkpointer: "compiled-graph",
    )
    provider = PostgresApprovalGraphProvider(settings(), object())  # type: ignore[arg-type]

    provider.setup()
    with provider.open() as graph:
        assert graph == "compiled-graph"
    provider.delete_thread("safe-thread-id")

    assert FakeSaver.setup_calls == 1
    assert connect_kwargs[0]["host"] == "db.example"
    assert connect_kwargs[0]["port"] == 5433
    assert connect_kwargs[0]["connect_timeout"] == 5
    statements = connections[-1].cursor_value.statements
    assert len(statements) == 3
    assert all(parameters == ("safe-thread-id",) for _, parameters in statements)


def test_studio_entrypoint_exposes_real_workflow_topology() -> None:
    from parking_assistant.graph.studio import graph

    nodes = set(graph.get_graph().nodes)

    assert {"load_pending_request", "wait_for_human", "verify_decision"} <= nodes
