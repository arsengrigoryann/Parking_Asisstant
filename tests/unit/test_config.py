"""Tests for typed application settings."""

import pytest
from pydantic import ValidationError

from parking_assistant.config import Settings


def test_settings_load_environment_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("WEAVIATE_GRPC_PORT", "50052")

    settings = Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        _env_file=None,
    )

    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.weaviate_grpc_port == 50052


def test_settings_reject_non_postgresql_database_url() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL must use PostgreSQL"):
        Settings(database_url="sqlite:///parking.db", _env_file=None)


def test_settings_repr_does_not_expose_secrets() -> None:
    secret = "do-not-print-this-key"

    settings = Settings(
        openai_api_key=secret,
        langsmith_api_key=secret,
        weaviate_api_key=secret,
        database_url=f"postgresql+psycopg://user:{secret}@localhost/db",
        _env_file=None,
    )

    assert secret not in repr(settings)


def test_example_environment_is_a_valid_configuration() -> None:
    settings = Settings(_env_file=".env.example")

    assert settings.app_env == "local"
    assert settings.database_url.get_secret_value().startswith("postgresql+psycopg://")
    assert settings.weaviate_http_url.host == "localhost"
    assert settings.openai_embedding_model == "text-embedding-3-small"


def test_settings_reject_invalid_chunk_overlap() -> None:
    with pytest.raises(ValidationError, match="RAG_CHUNK_OVERLAP"):
        Settings(
            database_url="postgresql://test:test@localhost/test",
            rag_chunk_size=200,
            rag_chunk_overlap=200,
            _env_file=None,
        )
