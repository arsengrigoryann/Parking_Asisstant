"""Deterministic reservation normalization and validation tests."""

import re
from datetime import UTC, datetime, timedelta

import pytest

from parking_assistant.reservations.models import (
    ReservationExtraction,
    ReservationField,
    ReservationSessionState,
)
from parking_assistant.reservations.validation import (
    ReservationValidator,
    normalize_car_number,
    normalize_name,
)

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
CAR_PATTERN = r"^[A-Z0-9]{4,12}$"


def validator() -> ReservationValidator:
    return ReservationValidator("Asia/Yerevan", CAR_PATTERN, lambda: NOW)


def test_name_validation_trims_unicode_and_rejects_invalid_values() -> None:
    assert normalize_name("  Արսեն   Գրիգորյան ") == "Արսեն Գրիգորյան"
    assert normalize_name("Anne-Marie O'Neil") == "Anne-Marie O'Neil"
    with pytest.raises(ValueError, match="non-empty"):
        normalize_name("   ")
    with pytest.raises(ValueError, match="invalid"):
        normalize_name("Arsen123")
    with pytest.raises(ValueError, match="letters"):
        normalize_name("---")


def test_car_number_normalization_is_configurable_and_practical() -> None:
    pattern = re.compile(CAR_PATTERN)

    assert normalize_car_number("35-ab 123", pattern) == "35AB123"
    with pytest.raises(ValueError, match="configured"):
        normalize_car_number("@@", pattern)


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (NOW + timedelta(hours=3), NOW + timedelta(hours=2), "end must be after"),
        (NOW + timedelta(hours=2), NOW + timedelta(hours=2), "end must be after"),
        (NOW - timedelta(hours=3), NOW - timedelta(hours=1), "entirely in the past"),
    ],
)
def test_invalid_period_does_not_corrupt_state(
    start: datetime,
    end: datetime,
    message: str,
) -> None:
    result = validator().merge(
        ReservationSessionState(),
        ReservationExtraction(start_datetime=start, end_datetime=end),
    )

    assert result.draft.start_datetime is None
    assert result.draft.end_datetime is None
    assert message in result.validation_errors[ReservationField.START_DATETIME]


def test_valid_future_period_is_timezone_normalized_and_complete() -> None:
    result = validator().merge(
        ReservationSessionState(),
        ReservationExtraction(
            first_name=" Arsen ",
            last_name="Grigoryan",
            car_number="35-ab-123",
            start_datetime=datetime(2026, 9, 25, 10, 0),
            end_datetime=datetime(2026, 9, 25, 13, 0),
        ),
    )
    details = validator().complete_details(result)

    assert result.complete is True
    assert details.car_number == "35AB123"
    assert details.start_datetime.utcoffset() == timedelta(hours=4)
    assert details.end_datetime > details.start_datetime


def test_aware_datetimes_are_converted_to_facility_timezone() -> None:
    result = validator().merge(
        ReservationSessionState(),
        ReservationExtraction(
            start_datetime=datetime(2026, 9, 25, 6, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 9, 25, 8, 0, tzinfo=UTC),
        ),
    )

    assert result.draft.start_datetime is not None
    assert result.draft.start_datetime.hour == 10
    assert str(result.draft.start_datetime.tzinfo) == "Asia/Yerevan"


def test_existing_value_requires_explicit_correction() -> None:
    first = validator().merge(
        ReservationSessionState(),
        ReservationExtraction(car_number="35AB123"),
    )
    rejected = validator().merge(first, ReservationExtraction(car_number="36CD456"))
    corrected = validator().merge(
        first,
        ReservationExtraction(
            car_number="36CD456",
            correction_fields=[ReservationField.CAR_NUMBER],
        ),
    )

    assert rejected.draft.car_number == "35AB123"
    assert ReservationField.CAR_NUMBER in rejected.validation_errors
    assert corrected.draft.car_number == "36CD456"
    assert corrected.validation_errors == {}


def test_validator_rejects_bad_configuration_and_incomplete_materialization() -> None:
    with pytest.raises(ValueError, match="unknown facility timezone"):
        ReservationValidator("Not/A-Timezone", CAR_PATTERN)
    with pytest.raises(ValueError, match="invalid RESERVATION_CAR_NUMBER_PATTERN"):
        ReservationValidator("Asia/Yerevan", "[")
    with pytest.raises(ValueError, match="not complete"):
        validator().complete_details(ReservationSessionState())
