"""In-memory reservation lifecycle and PII isolation tests."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from parking_assistant.application import AssistantResponse, ConversationService
from parking_assistant.config import Settings
from parking_assistant.reservations.models import (
    ReservationExtraction,
    ReservationField,
    ReservationStatus,
)
from parking_assistant.reservations.service import (
    ReservationCollectionService,
    is_explicit_reservation_start,
)
from parking_assistant.routing import IntentRoute

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)


class QueueExtractor:
    def __init__(self, outputs: list[ReservationExtraction]) -> None:
        self.outputs = outputs
        self.messages: list[str] = []

    def extract(self, message: str, **kwargs: Any) -> ReservationExtraction:
        self.messages.append(message)
        return self.outputs.pop(0)


def settings() -> Settings:
    return Settings(
        database_url="postgresql://x:x@localhost/x",
        reservation_car_number_pattern=r"^[A-Z0-9]{4,12}$",
        langsmith_tracing=False,
        _env_file=None,
    )


def collection(outputs: list[ReservationExtraction]) -> ReservationCollectionService:
    return ReservationCollectionService(
        QueueExtractor(outputs),
        settings(),
        timezone_provider=lambda: "Asia/Yerevan",
        now=lambda: NOW,
    )


def test_partial_fields_persist_and_follow_up_asks_only_for_missing_period() -> None:
    service = collection(
        [ReservationExtraction(first_name="Arsen", last_name="Grigoryan", car_number="35AB123")]
    )

    result = service.collect("session", "I'm Arsen Grigoryan, plate 35AB123")
    state = service.state("session")

    assert result.status is ReservationStatus.COLLECTING
    assert state is not None and state.draft.first_name == "Arsen"
    assert result.missing_fields == [
        ReservationField.START_DATETIME,
        ReservationField.END_DATETIME,
    ]
    assert "first name" not in result.answer.lower()


def test_valid_fields_survive_invalid_turn_then_complete_and_correct() -> None:
    start = NOW + timedelta(days=1)
    end = start + timedelta(hours=3)
    service = collection(
        [
            ReservationExtraction(first_name="Arsen", last_name="Grigoryan", car_number="35AB123"),
            ReservationExtraction(start_datetime=end, end_datetime=start),
            ReservationExtraction(start_datetime=start, end_datetime=end),
            ReservationExtraction(
                car_number="36CD456",
                correction_fields=[ReservationField.CAR_NUMBER],
            ),
        ]
    )

    service.collect("session", "identity")
    invalid = service.collect("session", "invalid period")
    complete = service.collect("session", "valid period")
    corrected = service.collect("session", "Actually my plate is 36CD456")

    assert ReservationField.START_DATETIME in invalid.validation_errors
    assert complete.status is ReservationStatus.COMPLETE
    assert complete.reservation is not None
    assert complete.reservation.car_number == "35AB123"
    assert corrected.reservation is not None
    assert corrected.reservation.car_number == "36CD456"


def test_cancellation_clears_session_state() -> None:
    service = collection([ReservationExtraction(first_name="Arsen")])
    service.collect("session", "Arsen")

    result = service.cancel("session")

    assert result.status is ReservationStatus.CANCELLED
    assert service.state("session") is None


def test_escalation_key_is_stable_opaque_and_complete_result_is_guarded() -> None:
    start = NOW + timedelta(days=1)
    service = collection(
        [
            ReservationExtraction(first_name="Arsen"),
            ReservationExtraction(
                last_name="Grigoryan",
                car_number="35AB123",
                start_datetime=start,
                end_datetime=start + timedelta(hours=2),
            ),
        ]
    )
    service.collect("session", "partial")
    first_key = service.submission_key("session")

    with pytest.raises(ValueError, match="not complete"):
        service.completed_result("session")

    service.collect("session", "complete")
    result = service.completed_result("session")

    assert result.status is ReservationStatus.COMPLETE
    assert service.submission_key("session") == first_key
    assert "Arsen" not in first_key
    with pytest.raises(ValueError, match="does not exist"):
        service.submission_key("missing")


class FailIfCalledAssistant:
    def __init__(self) -> None:
        self.calls = 0

    def answer_query(self, message: str) -> AssistantResponse:
        self.calls += 1
        raise AssertionError("reservation PII must not reach normal routing or RAG")


def test_explicit_and_subsequent_reservation_messages_bypass_normal_assistant() -> None:
    reservations = collection(
        [
            ReservationExtraction(),
            ReservationExtraction(first_name="Arsen", last_name="Grigoryan", car_number="35AB123"),
        ]
    )
    assistant = FailIfCalledAssistant()
    conversation = ConversationService(assistant, reservations)  # type: ignore[arg-type]

    first = conversation.handle_message("I want to reserve tomorrow.", "session")
    second = conversation.handle_message("Arsen Grigoryan, plate 35AB123.", "session")

    assert first.route is IntentRoute.RESERVATION
    assert second.route is IntentRoute.RESERVATION
    assert assistant.calls == 0


def test_conversation_cancellation_and_normal_questions_remain_separate() -> None:
    class StaticAssistant:
        def answer_query(self, message: str) -> AssistantResponse:
            return AssistantResponse(answer="static", route=IntentRoute.STATIC_INFORMATION)

    reservations = collection([ReservationExtraction()])
    conversation = ConversationService(StaticAssistant(), reservations)  # type: ignore[arg-type]
    normal = conversation.handle_message("Where is the parking?", "normal")
    conversation.handle_message("I want to reserve a space.", "reservation")
    cancelled = conversation.handle_message("Cancel this reservation.", "reservation")

    assert normal.route is IntentRoute.STATIC_INFORMATION
    assert cancelled.reservation_status is ReservationStatus.CANCELLED
    assert reservations.state("reservation") is None


def test_stage_1c_reservation_route_starts_collection() -> None:
    class ReservationRoutingAssistant:
        def __init__(self) -> None:
            self.calls = 0

        def answer_query(self, message: str) -> AssistantResponse:
            self.calls += 1
            return AssistantResponse(answer="boundary", route=IntentRoute.RESERVATION)

    assistant = ReservationRoutingAssistant()
    reservations = collection([ReservationExtraction()])
    conversation = ConversationService(assistant, reservations)  # type: ignore[arg-type]

    result = conversation.handle_message("Could I get a parking space?", "session")

    assert assistant.calls == 1
    assert result.route is IntentRoute.RESERVATION
    assert result.reservation_status is ReservationStatus.COLLECTING
    assert reservations.has_session("session") is True


def test_complete_detail_start_detection_is_order_independent() -> None:
    assert is_explicit_reservation_start("Plate DEMO123, tomorrow from 10:00 to 12:00")
    assert is_explicit_reservation_start("Tomorrow from 10:00 to 12:00, plate DEMO123")
