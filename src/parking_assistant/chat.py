"""Central construction point for the application's factual chat model."""

from langchain_openai import ChatOpenAI

from parking_assistant.config import Settings, get_settings


class ChatConfigurationError(ValueError):
    """Raised when model-backed behavior is requested without credentials."""


def create_chat_model(settings: Settings | None = None) -> ChatOpenAI:
    """Create one low-temperature chat model from typed application settings."""
    resolved = settings or get_settings()
    if resolved.openai_api_key is None or not resolved.openai_api_key.get_secret_value():
        raise ChatConfigurationError("OPENAI_API_KEY is required for chat-model operations")
    return ChatOpenAI(
        model_name=resolved.openai_model,
        openai_api_key=resolved.openai_api_key,
        temperature=0.0,
    )
