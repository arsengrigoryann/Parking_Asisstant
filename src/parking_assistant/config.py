"""Typed application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings shared by the application and infrastructure clients."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    rag_chunk_size: int = Field(default=900, ge=200, le=4000)
    rag_chunk_overlap: int = Field(default=120, ge=0, le=1000)
    rag_retrieval_limit: int = Field(default=5, ge=1, le=25)
    rag_hybrid_alpha: float = Field(default=0.5, ge=0.0, le=1.0)
    weaviate_knowledge_collection: str = "PublicParkingKnowledge"

    reservation_car_number_pattern: str = r"^[A-Z0-9]{4,12}$"
    admin_api_token: SecretStr | None = None
    admin_api_identity: str = Field(default="demo-admin", min_length=1, max_length=100)

    mcp_server_host: str = "127.0.0.1"
    mcp_server_port: int = Field(default=8765, ge=1, le=65535)
    mcp_server_token: SecretStr | None = None
    mcp_reservation_file: Path = Path("data/confirmed_reservations.txt")

    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "parking-assistant-local"
    langsmith_endpoint: AnyHttpUrl = AnyHttpUrl("https://api.smith.langchain.com")

    database_url: SecretStr

    weaviate_http_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8080")
    weaviate_grpc_host: str = "localhost"
    weaviate_grpc_port: int = Field(default=50051, ge=1, le=65535)
    weaviate_grpc_secure: bool = False
    weaviate_api_key: SecretStr | None = None

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        """Reject unsupported database drivers before a connection is attempted."""
        if not value.get_secret_value().startswith(("postgresql://", "postgresql+psycopg://")):
            msg = "DATABASE_URL must use PostgreSQL"
            raise ValueError(msg)
        return value

    @field_validator("mcp_server_host")
    @classmethod
    def validate_mcp_server_host(cls, value: str) -> str:
        """Keep the assignment MCP endpoint bound to the local machine."""
        if value not in {"127.0.0.1", "localhost", "::1"}:
            msg = "MCP_SERVER_HOST must be a localhost address"
            raise ValueError(msg)
        return value

    @property
    def mcp_server_url(self) -> str:
        """Return the configured local Streamable HTTP endpoint."""
        host = f"[{self.mcp_server_host}]" if ":" in self.mcp_server_host else self.mcp_server_host
        return f"http://{host}:{self.mcp_server_port}/mcp"

    @model_validator(mode="after")
    def validate_chunking(self) -> "Settings":
        """Ensure recursive chunks always make forward progress."""
        if self.rag_chunk_overlap >= self.rag_chunk_size:
            msg = "RAG_CHUNK_OVERLAP must be smaller than RAG_CHUNK_SIZE"
            raise ValueError(msg)
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide immutable-by-convention settings instance."""
    return Settings()
