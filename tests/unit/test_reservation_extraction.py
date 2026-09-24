"""Structured reservation extraction tests using model-output fakes."""

from datetime import UTC, datetime
from typing import Any

import pytest

from parking_assistant.reservations.extraction import (
    ReservationExtractionError,
    ReservationExtractor,
)
from parking_assistant.reservations.models import ReservationField


class FakeStructuredModel:
    def __init__(self, output: dict[str, Any]) -> None:
        self.output = output
        self.messages: Any = None

    def invoke(self, messages: Any) -> dict[str, Any]:
        self.messages = messages
        return self.output


class FakeChatModel:
    def __init__(self, output: dict[str, Any]) -> None:
        self.structured = FakeStructuredModel(output)

    def with_structured_output(self, schema: Any, *, method: str) -> FakeStructuredModel:
        return self.structured


def extract(
    output: dict[str, Any], message: str = "current reservation message"
) -> tuple[Any, FakeChatModel]:
    model = FakeChatModel(output)
    result = ReservationExtractor(model).extract(
        message,
        timezone_name="Asia/Yerevan",
        reference_time=datetime(2026, 9, 24, 12, tzinfo=UTC),
        collected_fields={ReservationField.FIRST_NAME},
    )
    return result, model


def test_extracts_one_field() -> None:
    result, _ = extract({"car_number": "35AB123"})

    assert result.car_number == "35AB123"
    assert result.first_name is None


def test_extracts_multiple_fields() -> None:
    result, _ = extract(
        {"first_name": "Arsen", "last_name": "Grigoryan", "car_number": "35AB123"}
    )

    assert (result.first_name, result.last_name, result.car_number) == (
        "Arsen",
        "Grigoryan",
        "35AB123",
    )


def test_extracts_all_fields_and_correction_marker() -> None:
    result, model = extract(
        {
            "first_name": "Arsen",
            "last_name": "Grigoryan",
            "car_number": "36CD456",
            "start_datetime": "2026-09-25T10:00:00+04:00",
            "end_datetime": "2026-09-25T13:00:00+04:00",
            "correction_fields": ["car_number"],
        },
        "Tomorrow from 10:00 to 13:00; actually change my plate",
    )

    assert result.start_datetime is not None
    assert result.end_datetime is not None
    assert result.correction_fields == [ReservationField.CAR_NUMBER]
    assert "Already collected field names: first_name" in model.structured.messages[1].content


def test_extractor_rejects_blank_input_and_invalid_structured_output() -> None:
    model = FakeChatModel({"unexpected": "value"})
    extractor = ReservationExtractor(model)

    with pytest.raises(ValueError, match="must not be blank"):
        extractor.extract(
            " ",
            timezone_name="Asia/Yerevan",
            reference_time=datetime(2026, 9, 24, 12, tzinfo=UTC),
            collected_fields=set(),
        )
    with pytest.raises(ReservationExtractionError, match="invalid reservation extraction"):
        extractor.extract(
            "details",
            timezone_name="Asia/Yerevan",
            reference_time=datetime(2026, 9, 24, 12, tzinfo=UTC),
            collected_fields=set(),
        )


def test_date_without_explicit_time_discards_model_inferred_datetime() -> None:
    model = FakeChatModel({"start_datetime": "2026-09-25T00:00:00+04:00"})

    result = ReservationExtractor(model).extract(
        "I want to reserve tomorrow",
        timezone_name="Asia/Yerevan",
        reference_time=datetime(2026, 9, 24, 12, tzinfo=UTC),
        collected_fields=set(),
    )

    assert result.start_datetime is None
