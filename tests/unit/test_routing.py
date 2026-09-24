"""Unit tests for validated structured intent routing."""

from typing import Any

import pytest

from parking_assistant.db.models import Weekday
from parking_assistant.routing import (
    DynamicSubtype,
    IntentClassificationError,
    IntentDecision,
    IntentRoute,
    IntentRouter,
)


class FakeStructuredModel:
    def __init__(self, output: Any) -> None:
        self.output = output
        self.messages: Any = None

    def invoke(self, messages: Any) -> Any:
        self.messages = messages
        return self.output


class FakeChatModel:
    def __init__(self, output: Any) -> None:
        self.structured = FakeStructuredModel(output)
        self.schema: Any = None
        self.method: str | None = None

    def with_structured_output(self, schema: Any, *, method: str) -> FakeStructuredModel:
        self.schema = schema
        self.method = method
        return self.structured


@pytest.mark.parametrize(
    ("output", "route", "subtype"),
    [
        ({"route": "STATIC_INFORMATION"}, IntentRoute.STATIC_INFORMATION, None),
        (
            {"route": "DYNAMIC_INFORMATION", "dynamic_subtype": "AVAILABILITY"},
            IntentRoute.DYNAMIC_INFORMATION,
            DynamicSubtype.AVAILABILITY,
        ),
        (
            {
                "route": "DYNAMIC_INFORMATION",
                "dynamic_subtype": "OPENING_HOURS",
                "weekday": "monday",
            },
            IntentRoute.DYNAMIC_INFORMATION,
            DynamicSubtype.OPENING_HOURS,
        ),
        ({"route": "RESERVATION"}, IntentRoute.RESERVATION, None),
        ({"route": "UNSUPPORTED"}, IntentRoute.UNSUPPORTED, None),
    ],
)
def test_router_classifies_valid_structured_outputs(
    output: dict[str, str],
    route: IntentRoute,
    subtype: DynamicSubtype | None,
) -> None:
    model = FakeChatModel(output)

    decision = IntentRouter(model).classify("example query")

    assert decision.route is route
    assert decision.dynamic_subtype is subtype
    assert model.schema is IntentDecision
    assert model.method == "json_schema"
    if subtype is DynamicSubtype.OPENING_HOURS:
        assert decision.weekday is Weekday.MONDAY


def test_router_rejects_invalid_structured_model_output() -> None:
    router = IntentRouter(
        FakeChatModel({"route": "DYNAMIC_INFORMATION", "dynamic_subtype": "not-real"})
    )

    with pytest.raises(IntentClassificationError, match="invalid intent"):
        router.classify("How many spaces are free?")


def test_intent_schema_rejects_dynamic_fields_on_static_route() -> None:
    with pytest.raises(ValueError, match="only for DYNAMIC_INFORMATION"):
        IntentDecision(
            route=IntentRoute.STATIC_INFORMATION,
            dynamic_subtype=DynamicSubtype.PRICING,
        )


def test_intent_schema_requires_subtype_and_scopes_weekday() -> None:
    with pytest.raises(ValueError, match="dynamic_subtype is required"):
        IntentDecision(route=IntentRoute.DYNAMIC_INFORMATION)
    with pytest.raises(ValueError, match="weekday is valid only"):
        IntentDecision(
            route=IntentRoute.DYNAMIC_INFORMATION,
            dynamic_subtype=DynamicSubtype.PRICING,
            weekday=Weekday.MONDAY,
        )
