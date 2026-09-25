"""Deterministic submission and lifecycle service for reservation approval requests."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from parking_assistant.config import Settings
from parking_assistant.db.models import ReservationRequest, ReservationRequestStatus
from parking_assistant.reservations.models import (
    ReservationDetails,
    ReservationExtraction,
    ReservationSessionState,
)
from parking_assistant.reservations.repository import ReservationRepository
from parking_assistant.reservations.validation import ReservationValidator


class ReservationNotFoundError(LookupError):
    """Raised when a reservation request identifier does not exist."""


class ReservationConflictError(RuntimeError):
    """Raised when a requested lifecycle or idempotency operation conflicts."""


class InvalidReservationError(ValueError):
    """Raised when incomplete or invalid reservation details are submitted."""


class ReservationSubmissionService:
    """Validate, persist, and decide reservation requests without LLM involvement."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        repository: ReservationRepository | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._repository = repository or ReservationRepository()
        self._now = now or (lambda: datetime.now(UTC))

    def submit_for_approval(
        self,
        details: ReservationDetails,
        *,
        facility_id: UUID,
        idempotency_key: str,
    ) -> ReservationRequest:
        """Persist a complete validated draft as pending, idempotently by opaque key."""
        digest = _idempotency_digest(idempotency_key)
        session = self._session_factory()
        try:
            existing = self._repository.get_by_idempotency_digest(session, digest)
            facility = self._repository.get_facility(session, facility_id)
            if facility is None:
                raise InvalidReservationError("active parking facility does not exist")
            normalized = self._validate_details(details, facility.timezone)
            if existing is not None:
                self._ensure_same_submission(existing, normalized, facility_id)
                return existing
            request = ReservationRequest(
                first_name=normalized.first_name,
                last_name=normalized.last_name,
                car_number=normalized.car_number,
                start_datetime=normalized.start_datetime,
                end_datetime=normalized.end_datetime,
                facility_id=facility_id,
                status=ReservationRequestStatus.PENDING_APPROVAL,
                idempotency_digest=digest,
            )
            self._repository.create_pending(session, request)
            session.commit()
            return request
        except IntegrityError:
            session.rollback()
            existing = self._repository.get_by_idempotency_digest(session, digest)
            if existing is None:
                raise
            normalized = self._validate_details(details, existing.facility.timezone)
            self._ensure_same_submission(existing, normalized, facility_id)
            return existing
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get(self, reservation_id: UUID) -> ReservationRequest:
        with self._session_factory() as session:
            request = self._repository.get_by_id(session, reservation_id)
            if request is None:
                raise ReservationNotFoundError("reservation request not found")
            session.expunge(request)
            return request

    def list_pending(self) -> list[ReservationRequest]:
        with self._session_factory() as session:
            requests = self._repository.list_pending(session)
            for request in requests:
                session.expunge(request)
            return requests

    def approve(self, reservation_id: UUID, *, decision_by: str) -> ReservationRequest:
        return self._decide(
            reservation_id,
            ReservationRequestStatus.APPROVED,
            decision_by=decision_by,
        )

    def reject(
        self,
        reservation_id: UUID,
        *,
        decision_by: str,
        reason: str | None = None,
    ) -> ReservationRequest:
        normalized_reason = reason.strip() if reason is not None else None
        if normalized_reason == "":
            normalized_reason = None
        if normalized_reason is not None and len(normalized_reason) > 500:
            raise ValueError("rejection reason must be at most 500 characters")
        return self._decide(
            reservation_id,
            ReservationRequestStatus.REJECTED,
            decision_by=decision_by,
            rejection_reason=normalized_reason,
        )

    def cancel(self, reservation_id: UUID, *, decision_by: str) -> ReservationRequest:
        return self._decide(
            reservation_id,
            ReservationRequestStatus.CANCELLED,
            decision_by=decision_by,
        )

    def _decide(
        self,
        reservation_id: UUID,
        target: ReservationRequestStatus,
        *,
        decision_by: str,
        rejection_reason: str | None = None,
    ) -> ReservationRequest:
        administrator = decision_by.strip()
        if not administrator or len(administrator) > 100:
            raise ValueError("administrator identity must be 1-100 characters")
        session = self._session_factory()
        try:
            changed = self._repository.transition_pending(
                session,
                reservation_id,
                target,
                decision_at=self._now(),
                decision_by=administrator,
                rejection_reason=rejection_reason,
            )
            request = self._repository.get_by_id(session, reservation_id)
            if request is None:
                raise ReservationNotFoundError("reservation request not found")
            if not changed and request.status != target:
                raise ReservationConflictError(
                    f"cannot transition {request.status.value} to {target.value}"
                )
            session.commit()
            return request
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _validate_details(
        self, details: ReservationDetails, timezone_name: str
    ) -> ReservationDetails:
        if not isinstance(details, ReservationDetails):
            raise InvalidReservationError("submission requires complete ReservationDetails")
        validator = ReservationValidator(
            timezone_name,
            self._settings.reservation_car_number_pattern,
            self._now,
        )
        state = validator.merge(
            ReservationSessionState(),
            ReservationExtraction.model_validate(details.model_dump()),
        )
        if not state.complete:
            message = next(iter(state.validation_errors.values()), "reservation is incomplete")
            raise InvalidReservationError(message)
        return validator.complete_details(state)

    @staticmethod
    def _ensure_same_submission(
        existing: ReservationRequest,
        details: ReservationDetails,
        facility_id: UUID,
    ) -> None:
        same = (
            existing.facility_id == facility_id
            and existing.first_name == details.first_name
            and existing.last_name == details.last_name
            and existing.car_number == details.car_number
            and _same_datetime(existing.start_datetime, details.start_datetime)
            and _same_datetime(existing.end_datetime, details.end_datetime)
        )
        if not same:
            raise ReservationConflictError("idempotency key was used for different details")


def _idempotency_digest(key: str) -> str:
    normalized = key.strip()
    if not normalized or len(normalized) > 200:
        raise ValueError("idempotency key must be 1-200 characters")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _same_datetime(left: datetime, right: datetime) -> bool:
    """Compare instants while tolerating SQLite's test-only loss of timezone metadata."""
    left_aware = left.tzinfo is not None and left.utcoffset() is not None
    right_aware = right.tzinfo is not None and right.utcoffset() is not None
    if left_aware and right_aware:
        return left.astimezone(UTC) == right.astimezone(UTC)
    return left.replace(tzinfo=None) == right.replace(tzinfo=None)
