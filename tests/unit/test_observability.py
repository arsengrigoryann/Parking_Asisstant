"""Tests for optional and privacy-safe LangSmith boundaries."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Never

import pytest

from parking_assistant.application import AssistantService
from parking_assistant.config import Settings
from parking_assistant.db.models import Weekday
from parking_assistant.observability import assistant_trace, trace_span
from parking_assistant.rag.answering import GroundedAnswer
from parking_assistant.routing import IntentDecision, IntentRoute


def test_enabled_tracing_requires_configured_key() -> None:
    settings = Settings(
        database_url="postgresql://x:x@localhost/x",
        langsmith_tracing=True,
        langsmith_api_key=None,
        _env_file=None,
    )

    with pytest.raises(ValueError, match="LANGSMITH_API_KEY"), assistant_trace(settings, "query"):
        pass


def test_disabled_child_span_has_no_langsmith_dependency() -> None:
    settings = Settings(
        database_url="postgresql://x:x@localhost/x",
        langsmith_tracing=False,
        _env_file=None,
    )

    with trace_span(settings, "intent_routing", run_type="chain") as run:
        assert run is None


def test_assistant_passes_only_sanitized_input_and_safe_metadata_to_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    @contextmanager
    def fake_trace(
        settings: Settings,
        query: str,
        *,
        pii_detected: bool = False,
        entity_types: tuple[str, ...] = (),
    ) -> Iterator[None]:
        captured.update(
            query=query,
            pii_detected=pii_detected,
            entity_types=entity_types,
        )
        yield None

    class Router:
        def classify(self, query: str) -> IntentDecision:
            return IntentDecision(route=IntentRoute.UNSUPPORTED)

    class Static:
        def answer(self, query: str) -> GroundedAnswer:
            raise AssertionError("static route is not expected")

    class Dynamic:
        def availability(self) -> Never:
            raise AssertionError("dynamic route is not expected")

        def opening_hours(self, weekday: Weekday) -> Never:
            raise AssertionError("dynamic route is not expected")

        def pricing(self) -> Never:
            raise AssertionError("dynamic route is not expected")

    settings = Settings(
        database_url="postgresql://x:x@localhost/x",
        langsmith_tracing=False,
        _env_file=None,
    )
    monkeypatch.setattr("parking_assistant.application.assistant_trace", fake_trace)
    service = AssistantService(Router(), Static(), Dynamic(), settings)

    service.answer_query("Policy for Test User, plate DEMO123")

    assert captured["query"] == "Policy for <PERSON>, plate <CAR_NUMBER>"
    assert captured["pii_detected"] is True
    assert captured["entity_types"] == ("CAR_NUMBER", "PERSON")
    assert "Test User" not in str(captured)
    assert "DEMO123" not in str(captured)
