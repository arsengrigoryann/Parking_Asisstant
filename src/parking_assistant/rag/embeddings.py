"""Single construction point for application-provided OpenAI embeddings."""

from langchain_openai import OpenAIEmbeddings

from parking_assistant.config import Settings, get_settings


class EmbeddingConfigurationError(ValueError):
    """Raised when embeddings are requested without credentials."""


def create_embeddings(settings: Settings | None = None) -> OpenAIEmbeddings:
    """Create the configured embedding client without exposing its API key."""
    resolved = settings or get_settings()
    if resolved.openai_api_key is None or not resolved.openai_api_key.get_secret_value():
        raise EmbeddingConfigurationError("OPENAI_API_KEY is required for embeddings")
    return OpenAIEmbeddings(
        model=resolved.openai_embedding_model,
        openai_api_key=resolved.openai_api_key,
    )
