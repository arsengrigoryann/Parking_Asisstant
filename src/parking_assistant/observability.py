"""Optional LangSmith trace scope driven by existing typed settings."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal

from langsmith import Client, trace, tracing_context

from parking_assistant.config import Settings


@contextmanager
def assistant_trace(
    settings: Settings,
    query: str,
    *,
    pii_detected: bool = False,
    entity_types: tuple[str, ...] = (),
) -> Iterator[Any | None]:
    """Trace one privacy-sanitized request and safe detection metadata."""
    if not settings.langsmith_tracing:
        yield None
        return
    if settings.langsmith_api_key is None or not settings.langsmith_api_key.get_secret_value():
        raise ValueError("LANGSMITH_API_KEY is required when LANGSMITH_TRACING is enabled")
    client = Client(
        api_url=str(settings.langsmith_endpoint),
        api_key=settings.langsmith_api_key.get_secret_value(),
    )
    try:
        with tracing_context(
            enabled=True,
            project_name=settings.langsmith_project,
            client=client,
        ), trace(
            "parking_assistant_request",
            run_type="chain",
            inputs={"query": query},
            metadata={
                "pii_detected": pii_detected,
                "entity_types": list(entity_types),
            },
            project_name=settings.langsmith_project,
            client=client,
        ) as run:
            yield run
    finally:
        client.flush(timeout=5)
        client.close(timeout=5)


@contextmanager
def trace_span(
    settings: Settings,
    name: str,
    *,
    run_type: Literal["tool", "chain", "llm", "retriever", "embedding", "prompt", "parser"],
    inputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[Any | None]:
    """Create one meaningful child span inside an enabled assistant trace."""
    if not settings.langsmith_tracing:
        yield None
        return
    with trace(
        name,
        run_type=run_type,
        inputs=inputs or {},
        metadata=metadata or {},
    ) as run:
        yield run
