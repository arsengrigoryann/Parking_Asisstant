"""Generate a small privacy-safe Stage 1 LangSmith trace set for presentation."""

from __future__ import annotations

from parking_assistant.application import create_assistant_service
from parking_assistant.config import get_settings

DEMO_QUERIES = (
    "Where is the parking located?",
    "What is the cancellation policy?",
    "How many parking spaces are currently available?",
    "Are you open on Monday?",
    "What does parking cost?",
    "Explain quantum computing.",
    "What is the cancellation policy for Demo User, plate DEMO123?",
)


def main() -> None:  # pragma: no cover - real LangSmith demo CLI
    settings = get_settings()
    if not settings.langsmith_tracing:
        raise ValueError("set LANGSMITH_TRACING=true before generating demo traces")
    with create_assistant_service(settings) as assistant:
        for query in DEMO_QUERIES:
            response = assistant.answer_query(query)
            print(f"{response.route.value}: {response.answer}")


if __name__ == "__main__":  # pragma: no cover
    main()
