"""Read-only and privacy-boundary tests for the administrator LangChain component."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from parking_assistant.admin import review as review_module
from parking_assistant.admin.review import (
    REVIEW_NOTICE,
    AdminReviewAgent,
    AdminReviewBrief,
)
from parking_assistant.db.models import ReservationRequest, ReservationRequestStatus


class StructuredReviewer:
    def __init__(self) -> None:
        self.messages: object | None = None

    def invoke(self, messages: object) -> AdminReviewBrief:
        self.messages = messages
        return AdminReviewBrief(
            summary="Demo Driver requested parking for vehicle TEST123 during the stated period.",
            review_notice=REVIEW_NOTICE,
        )


class ChatModel:
    def __init__(self, reviewer: StructuredReviewer) -> None:
        self.reviewer = reviewer
        self.schema: object | None = None

    def with_structured_output(self, schema: object, *, method: str) -> StructuredReviewer:
        self.schema = schema
        assert method == "json_schema"
        return self.reviewer


class ReadOnlyReservations:
    def __init__(self) -> None:
        now = datetime(2026, 9, 24, 8, 0, tzinfo=UTC)
        self.request = ReservationRequest(
            id=uuid4(),
            first_name="Demo",
            last_name="Driver",
            car_number="TEST123",
            start_datetime=now + timedelta(days=1),
            end_datetime=now + timedelta(days=1, hours=2),
            facility_id=uuid4(),
            status=ReservationRequestStatus.PENDING_APPROVAL,
            idempotency_digest="a" * 64,
        )
        self.get_calls = 0

    def get(self, reservation_id: UUID) -> ReservationRequest:
        assert reservation_id == self.request.id
        self.get_calls += 1
        return self.request

    def get_facility_name(self, facility_id: UUID) -> str:
        assert facility_id == self.request.facility_id
        return "Synthetic Central Parking"


def test_admin_agent_loads_by_id_uses_structured_output_and_disables_tracing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reservations = ReadOnlyReservations()
    reviewer = StructuredReviewer()
    tracing_values: list[bool] = []

    class TraceContext:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *args: object) -> None:
            return None

    def tracing_context(*, enabled: bool) -> TraceContext:
        tracing_values.append(enabled)
        return TraceContext()

    monkeypatch.setattr(review_module, "tracing_context", tracing_context)
    agent = AdminReviewAgent(reservations, ChatModel(reviewer))

    package = agent.review(reservations.request.id)

    assert reservations.get_calls == 1
    assert tracing_values == [False]
    assert package.reservation.first_name == "Demo"
    assert package.reservation.car_number == "TEST123"
    assert package.reservation.facility_name == "Synthetic Central Parking"
    assert package.brief.summary != package.reservation.model_dump_json()
    assert reviewer.messages is not None
    prompt = str(reviewer.messages)
    assert "Synthetic Central Parking" in prompt
    assert str(reservations.request.facility_id) not in prompt


def test_review_schema_rejects_recommendations_and_guarantees() -> None:
    with pytest.raises(ValidationError, match="decision or guarantee"):
        AdminReviewBrief(summary="I recommend approval.", review_notice=REVIEW_NOTICE)
    with pytest.raises(ValidationError, match="decision or guarantee"):
        AdminReviewBrief(
            summary="Availability is guaranteed for this request.",
            review_notice=REVIEW_NOTICE,
        )
