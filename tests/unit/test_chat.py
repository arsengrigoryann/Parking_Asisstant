"""Unit tests for centralized chat-model configuration."""

import pytest

from parking_assistant.chat import ChatConfigurationError, create_chat_model
from parking_assistant.config import Settings


def settings(key: str | None = None) -> Settings:
    return Settings(
        database_url="postgresql://x:x@localhost/x",
        openai_api_key=key,
        openai_model="gpt-test-model",
        _env_file=None,
    )


def test_chat_factory_requires_api_key() -> None:
    with pytest.raises(ChatConfigurationError, match="OPENAI_API_KEY"):
        create_chat_model(settings())


def test_chat_factory_uses_configured_model_and_zero_temperature() -> None:
    model = create_chat_model(settings("test-key"))

    assert model.model_name == "gpt-test-model"
    assert model.temperature == 0.0
    assert model.openai_api_key is not None
    assert model.openai_api_key.get_secret_value() == "test-key"
