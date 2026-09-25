"""Deterministic recording of database-authorized reservation confirmations."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from filelock import FileLock
from pydantic import BaseModel, ConfigDict

from parking_assistant.db.models import ReservationRequest, ReservationRequestStatus
from parking_assistant.reservations.submission import ReservationNotFoundError


class ReservationReader(Protocol):
    """Small authoritative read boundary used by the MCP recorder."""

    def get(self, reservation_id: UUID) -> ReservationRequest: ...


class ReservationNotApprovedError(RuntimeError):
    """Raised when PostgreSQL does not authorize confirmation recording."""


class InvalidApprovalError(RuntimeError):
    """Raised when an approved row lacks required authoritative data."""


class ReservationRecordResult(BaseModel):
    """PII-free result returned across the MCP boundary."""

    model_config = ConfigDict(frozen=True)

    reservation_id: UUID
    outcome: Literal["recorded", "already_recorded"]

    @property
    def recorded(self) -> bool:
        """Whether this invocation appended the canonical line."""
        return self.outcome == "recorded"


class ApprovedReservationRecorder:
    """Authorize from PostgreSQL and append one canonical line under a file lock."""

    def __init__(self, reservations: ReservationReader, output_path: Path) -> None:
        self._reservations = reservations
        self._output_path = output_path

    def record(self, reservation_id: UUID) -> ReservationRecordResult:
        """Record one approved reservation idempotently, never trusting caller PII."""
        try:
            reservation = self._reservations.get(reservation_id)
        except ReservationNotFoundError:
            raise
        if reservation.status is not ReservationRequestStatus.APPROVED:
            raise ReservationNotApprovedError("reservation is not approved")
        if reservation.decision_at is None:
            raise InvalidApprovalError("approved reservation has no decision timestamp")

        line = serialize_approved_reservation(reservation)
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(f"{self._output_path}.lock")
        with lock, self._output_path.open("a+", encoding="utf-8", newline="") as handle:
            handle.seek(0)
            if line in handle.read().splitlines():
                return ReservationRecordResult(
                    reservation_id=reservation_id,
                    outcome="already_recorded",
                )
            handle.seek(0, os.SEEK_END)
            handle.write(f"{line}\n")
            handle.flush()
            os.fsync(handle.fileno())
        return ReservationRecordResult(reservation_id=reservation_id, outcome="recorded")


def serialize_approved_reservation(reservation: ReservationRequest) -> str:
    """Serialize authoritative values as a stable, single-line UTC record."""
    if reservation.decision_at is None:
        raise InvalidApprovalError("approved reservation has no decision timestamp")
    name = f"{reservation.first_name} {reservation.last_name}"
    for value in (name, reservation.car_number):
        if any(separator in value for separator in ("|", "\r", "\n")):
            raise InvalidApprovalError("reservation contains unsafe file delimiters")
    start = _utc(reservation.start_datetime).isoformat(sep=" ", timespec="minutes")
    end = _utc(reservation.end_datetime).isoformat(sep=" ", timespec="minutes")
    approval = _utc(reservation.decision_at).isoformat(sep=" ", timespec="seconds")
    return f"{name} | {reservation.car_number} | {start}\N{EN DASH}{end} | {approval}"


def _utc(value: datetime) -> datetime:
    """Normalize PostgreSQL timestamps to UTC; SQLite test rows are UTC-naive."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
