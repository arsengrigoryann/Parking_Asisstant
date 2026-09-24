"""Unit tests for bounded Stage 1C application routing."""

from datetime import time
from decimal import Decimal

import pytest

from parking_assistant.application import (
    RESERVATION_BOUNDARY_MESSAGE,
    UNSUPPORTED_MESSAGE,
    AssistantService,
)
from parking_assistant.config import Settings
from parking_assistant.db.models import PricingUnit, SpaceType, Weekday
from parking_assistant.db.queries import (
    AvailabilitySnapshot,
    OpeningHoursResult,
    PricingResult,
    SpaceTypeAvailability,
)
from parking_assistant.rag.answering import AnswerSource, GroundedAnswer
from parking_assistant.routing import DynamicSubtype, IntentDecision, IntentRoute


class FakeRouter:
    def __init__(self, decision: IntentDecision) -> None:
        self.decision = decision
        self.calls = 0

    def classify(self, query: str) -> IntentDecision:
        self.calls += 1
        return self.decision


class FakeStaticAnswerer:
    def __init__(self) -> None:
        self.calls = 0

    def answer(self, query: str) -> GroundedAnswer:
        self.calls += 1
        return GroundedAnswer(
            answer="Grounded static answer.",
            sources=[AnswerSource(document_id="doc", title="Document")],
        )


class FakeDynamicReader:
    def __init__(self) -> None:
        self.availability_calls = 0
        self.opening_calls = 0
        self.pricing_calls = 0

    def availability(self) -> AvailabilitySnapshot:
        self.availability_calls += 1
        return AvailabilitySnapshot(
            total_active=10,
            available=7,
            by_type=[
                SpaceTypeAvailability(
                    space_type=SpaceType.REGULAR,
                    total_active=10,
                    available=7,
                )
            ],
        )

    def opening_hours(self, weekday: Weekday) -> OpeningHoursResult:
        self.opening_calls += 1
        return OpeningHoursResult(
            weekday=weekday,
            opens_at=time(7),
            closes_at=time(23),
            is_closed=False,
        )

    def pricing(self) -> list[PricingResult]:
        self.pricing_calls += 1
        return [
            PricingResult(
                name="regular-hourly",
                space_type=None,
                unit=PricingUnit.PER_HOUR,
                duration_minutes=60,
                amount=Decimal("3.50"),
                currency="USD",
            )
        ]

    @property
    def total_calls(self) -> int:
        return self.availability_calls + self.opening_calls + self.pricing_calls


def settings() -> Settings:
    return Settings(
        database_url="postgresql://x:x@localhost/x",
        langsmith_tracing=False,
        _env_file=None,
    )


def service_for(
    decision: IntentDecision,
) -> tuple[AssistantService, FakeStaticAnswerer, FakeDynamicReader]:
    static = FakeStaticAnswerer()
    dynamic = FakeDynamicReader()
    return AssistantService(FakeRouter(decision), static, dynamic, settings()), static, dynamic


def test_static_route_uses_rag_and_not_dynamic_queries() -> None:
    service, static, dynamic = service_for(IntentDecision(route=IntentRoute.STATIC_INFORMATION))

    response = service.answer_query("Where is the parking?")

    assert response.route is IntentRoute.STATIC_INFORMATION
    assert response.sources[0].document_id == "doc"
    assert static.calls == 1
    assert dynamic.total_calls == 0


@pytest.mark.parametrize(
    ("subtype", "expected"),
    [
        (DynamicSubtype.AVAILABILITY, "7 of 10"),
        (DynamicSubtype.OPENING_HOURS, "07:00 to 23:00"),
        (DynamicSubtype.PRICING, "USD 3.50 per hour"),
    ],
)
def test_dynamic_routes_use_only_matching_database_query(
    subtype: DynamicSubtype,
    expected: str,
) -> None:
    decision = IntentDecision(
        route=IntentRoute.DYNAMIC_INFORMATION,
        dynamic_subtype=subtype,
        weekday=Weekday.MONDAY if subtype is DynamicSubtype.OPENING_HOURS else None,
    )
    service, static, dynamic = service_for(decision)

    response = service.answer_query("dynamic question")

    assert response.route is IntentRoute.DYNAMIC_INFORMATION
    assert response.dynamic_subtype is subtype
    assert expected in response.answer
    assert static.calls == 0
    assert dynamic.total_calls == 1


def test_reservation_route_stops_before_data_collection() -> None:
    service, static, dynamic = service_for(IntentDecision(route=IntentRoute.RESERVATION))

    response = service.answer_query("Reserve a space for me")

    assert response.answer == RESERVATION_BOUNDARY_MESSAGE
    assert response.sources == []
    assert static.calls == 0
    assert dynamic.total_calls == 0


def test_unsupported_route_uses_neither_rag_nor_database() -> None:
    service, static, dynamic = service_for(IntentDecision(route=IntentRoute.UNSUPPORTED))

    response = service.answer_query("Explain quantum computing")

    assert response.answer == UNSUPPORTED_MESSAGE
    assert static.calls == 0
    assert dynamic.total_calls == 0


def test_opening_hours_without_weekday_asks_for_one_without_querying_database() -> None:
    decision = IntentDecision(
        route=IntentRoute.DYNAMIC_INFORMATION,
        dynamic_subtype=DynamicSubtype.OPENING_HOURS,
    )
    service, static, dynamic = service_for(decision)

    response = service.answer_query("When are you open?")

    assert "specify a weekday" in response.answer
    assert static.calls == 0
    assert dynamic.total_calls == 0


def test_blank_query_is_rejected_before_classification() -> None:
    service, _, _ = service_for(IntentDecision(route=IntentRoute.UNSUPPORTED))

    with pytest.raises(ValueError, match="must not be blank"):
        service.answer_query("   ")
