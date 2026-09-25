"""In-memory multi-turn reservation collection with deterministic state transitions."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Protocol
from uuid import uuid4

from parking_assistant.config import Settings
from parking_assistant.reservations.models import (
    REQUIRED_FIELDS,
    ReservationExtraction,
    ReservationField,
    ReservationSessionState,
    ReservationStatus,
    ReservationTurnResult,
)
from parking_assistant.reservations.validation import ReservationValidator

FIELD_LABELS = {
    ReservationField.FIRST_NAME: "first name",
    ReservationField.LAST_NAME: "surname",
    ReservationField.CAR_NUMBER: "car number",
    ReservationField.START_DATETIME: "reservation start time",
    ReservationField.END_DATETIME: "reservation end time",
}

_START_PATTERN = re.compile(
    r"\b(?:i\s+(?:want|need|would\s+like)|i'd\s+like|please|can\s+you)\s+"
    r"(?:to\s+)?(?:reserve|book)\b",
    re.IGNORECASE,
)
_PLATE_PATTERN = re.compile(r"\b(?:plate|car\s+number|license)\b", re.IGNORECASE)
_PERIOD_PATTERN = re.compile(r"\b(?:tomorrow|today|from|until|to)\b", re.IGNORECASE)
_CANCEL_PATTERN = re.compile(
    r"^\s*(?:please\s+)?(?:cancel|stop|exit|abandon)\s+(?:this\s+)?reservation\s*[.!]?\s*$",
    re.IGNORECASE,
)


def is_explicit_reservation_start(message: str) -> bool:
    """Detect clear first-person reservation starts without sending possible PII to routing."""
    has_reservation_details = _PLATE_PATTERN.search(message) and _PERIOD_PATTERN.search(message)
    return bool(_START_PATTERN.search(message) or has_reservation_details)


def is_reservation_cancellation(message: str) -> bool:
    """Distinguish flow cancellation from questions about the public cancellation policy."""
    return bool(_CANCEL_PATTERN.fullmatch(message))


@dataclass
class _SessionContext:
    state: ReservationSessionState
    validator: ReservationValidator
    timezone_name: str
    submission_key: str


class ReservationFieldExtractor(Protocol):
    def extract(
        self,
        message: str,
        *,
        timezone_name: str,
        reference_time: datetime,
        collected_fields: set[ReservationField],
    ) -> ReservationExtraction: ...


class ReservationCollectionService:
    """Own ephemeral reservation state and expose no persistence or approval behavior."""

    def __init__(
        self,
        extractor: ReservationFieldExtractor,
        settings: Settings,
        timezone_provider: Callable[[], str],
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._extractor = extractor
        self._settings = settings
        self._timezone_provider = timezone_provider
        self._now = now
        self._sessions: dict[str, _SessionContext] = {}
        self._lock = RLock()

    def has_session(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._sessions

    def state(self, session_id: str) -> ReservationSessionState | None:
        """Return immutable current state for application/tests, without logging it."""
        with self._lock:
            context = self._sessions.get(session_id)
            return context.state if context is not None else None

    def collect(self, session_id: str, message: str) -> ReservationTurnResult:
        """Start/continue one session, extracting all values supplied in this turn."""
        if not session_id.strip():
            raise ValueError("session_id must not be blank")
        if not message.strip():
            raise ValueError("reservation message must not be blank")
        with self._lock:
            context = self._sessions.get(session_id)
            if context is None:
                timezone_name = self._timezone_provider()
                context = _SessionContext(
                    state=ReservationSessionState(),
                    validator=ReservationValidator(
                        timezone_name,
                        self._settings.reservation_car_number_pattern,
                        self._now,
                    ),
                    timezone_name=timezone_name,
                    submission_key=str(uuid4()),
                )
                self._sessions[session_id] = context

            collected = {
                field
                for field in REQUIRED_FIELDS
                if getattr(context.state.draft, field.value) is not None
            }
            extraction = self._extractor.extract(
                message,
                timezone_name=context.timezone_name,
                reference_time=context.validator.reference_time(),
                collected_fields=collected,
            )
            context.state = context.validator.merge(context.state, extraction)
            return self._result(context)

    def submission_key(self, session_id: str) -> str:
        """Return the session's stable opaque non-PII escalation key."""
        with self._lock:
            context = self._sessions.get(session_id)
            if context is None:
                raise ValueError("reservation session does not exist")
            return context.submission_key

    def completed_result(self, session_id: str) -> ReservationTurnResult:
        """Return the validated complete result for explicit Stage 2 escalation."""
        with self._lock:
            context = self._sessions.get(session_id)
            if context is None or not context.state.complete:
                raise ValueError("reservation session is not complete")
            return self._result(context)

    def cancel(self, session_id: str) -> ReservationTurnResult:
        """Clear all in-memory PII for the session without touching booking policy RAG."""
        with self._lock:
            self._sessions.pop(session_id, None)
        return ReservationTurnResult(
            answer="Reservation collection cancelled. No reservation was created.",
            status=ReservationStatus.CANCELLED,
        )

    def _result(self, context: _SessionContext) -> ReservationTurnResult:
        state = context.state
        if state.complete:
            details = context.validator.complete_details(state)
            answer = (
                "I have:\n"
                f"Name: {details.first_name} {details.last_name}\n"
                f"Car: {details.car_number}\n"
                f"Period: {details.start_datetime.isoformat()} to "
                f"{details.end_datetime.isoformat()}\n\n"
                "The reservation details are ready for the next approval step. "
                "No reservation has been booked yet."
            )
            return ReservationTurnResult(
                answer=answer,
                status=ReservationStatus.COMPLETE,
                reservation=details,
            )

        requested = list(dict.fromkeys([*state.validation_errors, *state.missing_fields]))
        labels = [FIELD_LABELS[field] for field in requested]
        question = _missing_question(labels)
        error_prefix = ""
        if state.validation_errors:
            unique_errors = list(dict.fromkeys(state.validation_errors.values()))
            error_prefix = " ".join(unique_errors).capitalize() + ". "
        return ReservationTurnResult(
            answer=error_prefix + question,
            status=ReservationStatus.COLLECTING,
            missing_fields=state.missing_fields,
            validation_errors=state.validation_errors,
        )


def _missing_question(labels: list[str]) -> str:
    if not labels:
        return "Please provide valid reservation details."
    if len(labels) == 1:
        return f"What is your {labels[0]}?"
    return "Please provide your " + ", ".join(labels[:-1]) + f", and {labels[-1]}."
