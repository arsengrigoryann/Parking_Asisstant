"""Deterministic normalization, validation, and safe reservation-state merging."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from parking_assistant.reservations.models import (
    REQUIRED_FIELDS,
    ReservationDetails,
    ReservationDraft,
    ReservationExtraction,
    ReservationField,
    ReservationSessionState,
)


def normalize_name(value: str) -> str:
    """Normalize whitespace and accept practical Unicode human-name characters."""
    normalized = " ".join(value.strip().split())
    if not normalized or len(normalized) > 100:
        raise ValueError("must be a non-empty name of at most 100 characters")
    allowed_punctuation = {" ", "-", "'", "\N{RIGHT SINGLE QUOTATION MARK}"}
    if not any(unicodedata.category(character).startswith("L") for character in normalized):
        raise ValueError("must contain letters")
    if any(
        not unicodedata.category(character).startswith(("L", "M"))
        and character not in allowed_punctuation
        for character in normalized
    ):
        raise ValueError("contains invalid name characters")
    return normalized


def normalize_car_number(value: str, pattern: re.Pattern[str]) -> str:
    """Normalize common separators and validate against the configured demo pattern."""
    normalized = re.sub(r"[\s-]+", "", value).upper()
    if not pattern.fullmatch(normalized):
        raise ValueError("must match the configured 4-12 letter/digit format")
    return normalized


def normalize_datetime(value: datetime, timezone: ZoneInfo) -> datetime:
    """Attach the facility zone to naive values or convert aware values into it."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone)
    return value.astimezone(timezone)


class ReservationValidator:
    """Apply deterministic field rules and merge without corrupting valid state."""

    def __init__(
        self,
        timezone_name: str,
        car_number_pattern: str,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        try:
            self.timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"unknown facility timezone: {timezone_name}") from error
        try:
            self._car_pattern = re.compile(car_number_pattern)
        except re.error as error:
            raise ValueError("invalid RESERVATION_CAR_NUMBER_PATTERN") from error
        self._now = now or (lambda: datetime.now(self.timezone))

    def reference_time(self) -> datetime:
        """Return the injected clock normalized into the facility timezone."""
        return normalize_datetime(self._now(), self.timezone)

    def merge(
        self,
        state: ReservationSessionState,
        extraction: ReservationExtraction,
    ) -> ReservationSessionState:
        """Validate one extraction and atomically merge valid new/corrected values."""
        values = state.draft.model_dump()
        errors: dict[ReservationField, str] = {}
        self._merge_text_fields(values, state.draft, extraction, errors)
        self._merge_period(values, state.draft, extraction, errors)
        draft = ReservationDraft.model_validate(values)
        missing = [field for field in REQUIRED_FIELDS if getattr(draft, field.value) is None]
        complete = not missing and not errors
        return ReservationSessionState(
            draft=draft,
            missing_fields=missing,
            validation_errors=errors,
            complete=complete,
        )

    def complete_details(self, state: ReservationSessionState) -> ReservationDetails:
        """Materialize the required schema only after state is complete."""
        if not state.complete:
            raise ValueError("reservation collection is not complete")
        return ReservationDetails.model_validate(state.draft.model_dump())

    def _may_replace(
        self,
        field: ReservationField,
        existing: object,
        candidate: object,
        extraction: ReservationExtraction,
        errors: dict[ReservationField, str],
    ) -> bool:
        if existing is None or existing == candidate:
            return True
        if field in extraction.correction_fields:
            return True
        errors[field] = "a different value requires an explicit correction"
        return False

    def _merge_text_fields(
        self,
        values: dict[str, object],
        draft: ReservationDraft,
        extraction: ReservationExtraction,
        errors: dict[ReservationField, str],
    ) -> None:
        normalizers: dict[ReservationField, Callable[[str], str]] = {
            ReservationField.FIRST_NAME: normalize_name,
            ReservationField.LAST_NAME: normalize_name,
            ReservationField.CAR_NUMBER: lambda value: normalize_car_number(
                value, self._car_pattern
            ),
        }
        for field, normalizer in normalizers.items():
            raw = getattr(extraction, field.value)
            if raw is None:
                continue
            try:
                candidate = normalizer(raw)
            except ValueError as error:
                errors[field] = str(error)
                continue
            existing = getattr(draft, field.value)
            if self._may_replace(field, existing, candidate, extraction, errors):
                values[field.value] = candidate

    def _merge_period(
        self,
        values: dict[str, object],
        draft: ReservationDraft,
        extraction: ReservationExtraction,
        errors: dict[ReservationField, str],
    ) -> None:
        start = (
            normalize_datetime(extraction.start_datetime, self.timezone)
            if extraction.start_datetime is not None
            else draft.start_datetime
        )
        end = (
            normalize_datetime(extraction.end_datetime, self.timezone)
            if extraction.end_datetime is not None
            else draft.end_datetime
        )
        supplied = {
            ReservationField.START_DATETIME: extraction.start_datetime is not None,
            ReservationField.END_DATETIME: extraction.end_datetime is not None,
        }
        for field, candidate in (
            (ReservationField.START_DATETIME, start),
            (ReservationField.END_DATETIME, end),
        ):
            if supplied[field] and not self._may_replace(
                field, getattr(draft, field.value), candidate, extraction, errors
            ):
                return
        if start is not None and end is not None:
            if start >= end:
                message = "reservation end must be after reservation start"
                errors[ReservationField.START_DATETIME] = message
                errors[ReservationField.END_DATETIME] = message
                return
            if end <= self.reference_time():
                message = "reservation period must not be entirely in the past"
                errors[ReservationField.START_DATETIME] = message
                errors[ReservationField.END_DATETIME] = message
                return
        if supplied[ReservationField.START_DATETIME]:
            values[ReservationField.START_DATETIME.value] = start
        if supplied[ReservationField.END_DATETIME]:
            values[ReservationField.END_DATETIME.value] = end
