"""Tests for authenticated Weaviate connection lifecycle."""

from typing import Any

import pytest
from pydantic import SecretStr

from parking_assistant.config import Settings
from parking_assistant.rag.weaviate import (
    WeaviateConfigurationError,
    connect_weaviate,
    weaviate_is_ready,
)


class FakeWeaviateClient:
    """Small lifecycle fake; real connectivity is covered by an integration test."""

    def __init__(self, ready: bool = True) -> None:
        self.ready = ready
        self.closed = False

    def is_ready(self) -> bool:
        return self.ready

    def close(self) -> None:
        self.closed = True


def settings_with_key(key: str | None) -> Settings:
    return Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        weaviate_api_key=SecretStr(key) if key is not None else None,
        _env_file=None,
    )


def test_connect_weaviate_requires_nonempty_api_key() -> None:
    with pytest.raises(
        WeaviateConfigurationError, match="required"
    ), connect_weaviate(settings_with_key(None)):
        pytest.fail("connection should not open")

    with pytest.raises(
        WeaviateConfigurationError, match="must not be empty"
    ), connect_weaviate(settings_with_key("")):
        pytest.fail("connection should not open")


def test_readiness_uses_configured_endpoints_and_closes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeWeaviateClient()
    captured: dict[str, Any] = {}

    def fake_connect(**kwargs: Any) -> FakeWeaviateClient:
        captured.update(kwargs)
        return fake

    monkeypatch.setattr("parking_assistant.rag.weaviate.weaviate.connect_to_custom", fake_connect)

    assert weaviate_is_ready(settings_with_key("local-test-key")) is True
    assert captured["http_host"] == "localhost"
    assert captured["http_port"] == 8080
    assert captured["grpc_port"] == 50051
    assert fake.closed is True
