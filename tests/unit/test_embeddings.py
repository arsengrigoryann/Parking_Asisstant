"""Unit tests for centralized OpenAI embedding configuration."""

import pytest

from parking_assistant.config import Settings
from parking_assistant.rag.embeddings import EmbeddingConfigurationError, create_embeddings


def settings(key: str | None = None) -> Settings:
    return Settings(
        database_url="postgresql://x:x@localhost/x",
        openai_api_key=key,
        openai_embedding_model="custom-embedding-model",
        _env_file=None,
    )


def test_embedding_factory_requires_api_key() -> None:
    with pytest.raises(EmbeddingConfigurationError, match="OPENAI_API_KEY"):
        create_embeddings(settings())


def test_embedding_factory_uses_configured_model() -> None:
    embeddings = create_embeddings(settings("test-key"))

    assert embeddings.model == "custom-embedding-model"
    assert embeddings.openai_api_key is not None
    assert embeddings.openai_api_key.get_secret_value() == "test-key"
