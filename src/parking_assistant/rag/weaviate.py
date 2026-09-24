"""Minimal authenticated Weaviate connection lifecycle for infrastructure checks."""

from collections.abc import Iterator
from contextlib import contextmanager

import weaviate
from weaviate import WeaviateClient
from weaviate.classes.init import Auth

from parking_assistant.config import Settings, get_settings


class WeaviateConfigurationError(ValueError):
    """Raised when required Weaviate connection settings are absent."""


@contextmanager
def connect_weaviate(settings: Settings | None = None) -> Iterator[WeaviateClient]:
    """Open an authenticated v4 client and always close it."""
    resolved_settings = settings or get_settings()
    if resolved_settings.weaviate_api_key is None:
        raise WeaviateConfigurationError("WEAVIATE_API_KEY is required")
    api_key = resolved_settings.weaviate_api_key.get_secret_value()
    if not api_key:
        raise WeaviateConfigurationError("WEAVIATE_API_KEY must not be empty")

    http_url = resolved_settings.weaviate_http_url
    if http_url.host is None:
        raise WeaviateConfigurationError("WEAVIATE_HTTP_URL must include a host")

    client = weaviate.connect_to_custom(
        http_host=http_url.host,
        http_port=http_url.port or (443 if http_url.scheme == "https" else 80),
        http_secure=http_url.scheme == "https",
        grpc_host=resolved_settings.weaviate_grpc_host,
        grpc_port=resolved_settings.weaviate_grpc_port,
        grpc_secure=resolved_settings.weaviate_grpc_secure,
        auth_credentials=Auth.api_key(api_key),
    )
    try:
        yield client
    finally:
        client.close()


def weaviate_is_ready(settings: Settings | None = None) -> bool:
    """Return authenticated readiness and close the underlying client."""
    with connect_weaviate(settings) as client:
        return client.is_ready()


def main() -> None:
    """Verify authenticated connectivity for developer use."""
    if not weaviate_is_ready():
        raise SystemExit("Weaviate connectivity check failed")
    print("Weaviate is ready and authentication succeeded")


if __name__ == "__main__":
    main()
